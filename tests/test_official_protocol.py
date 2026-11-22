"""Synthetic V2 envelope tests; no HTTP, credentials, or live center are used."""
import sqlite3
from contextlib import contextmanager
import hashlib
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import test_official_readonly as prior
import test_official_bindings as binding_tests

core = prior.core
protocol = prior.module("official_protocol")
bindings = binding_tests.bindings_module
ENV, OTHER_ENV = prior.ENV, prior.OTHER_ENV
GROUP = prior.GROUP
USER = prior.USER
SERVER = binding_tests.SERVER
SERVER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OP = binding_tests.OP_A
TOKEN = binding_tests.TOKEN_A
RES = binding_tests.RES_A


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-protocol-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "center.sqlite3"
        with self.db() as connection:
            connection.executescript(binding_tests.SCHEMA)
        self.store = bindings.OfficialBindings(lambda: sqlite3.connect(self.path), placeholder="?")
        self.scope = {"protocol_version": 2, "environment_id": ENV, "group_id": GROUP, "server_id": SERVER}
        self.auth = lambda scope, request: True
        self.visible = lambda operation, scope, request: operation["target_user_id"] == USER
        self.now = 1000.0
        self.api = self.make_api()

    def capabilities(self, scope):
        return {"protocol_version": 2, "scope": scope.wire(), "storage_type": "qq_official",
            "storage_mode": "hybrid", "schema_version": 1, "game_key_rule": "ascii_lower",
            "multi_account": True, "operation_results": True, "revision_sync": False, "authority": "center"}

    def make_api(self, **kwargs):
        values = dict(store=self.store, expected_scope=self.scope, authorize=self.auth, visible=self.visible,
            query_authorize=lambda *_: USER, capabilities=self.capabilities, clock=lambda: self.now)
        values.update(kwargs)
        return protocol.OfficialBindingProtocol(**values)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def bind(self, game, operation, token, user=USER, server=SERVER):
        request_key = hashlib.sha256(game.lower().encode("ascii")).hexdigest()
        return self.store.prepare_bind(operation_id=operation, request_key=request_key,
            environment_id=ENV, group_id=GROUP, actor_kind="qq_member", actor_id=user, user_id=user,
            server_id=server, token_generation=token, game_id=game, policy_revision="1",
            target_server_ids=[server], authorization={"scope": "self", "policy_revision": "1"})

    def commit(self, operation=OP, token=TOKEN, game="Player_A", server=SERVER):
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=operation,
            reservation_id=RES, token_generation=token)
        return self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=operation,
            policy_revision="1")

    def test_unauthorized_and_version_mismatch_fail_before_storage(self):
        request = dict(self.scope)
        self.assertEqual(self.api.dispatch("GET", "/api/v2/binding/capabilities", request)["body"]["code"], "UNAUTHORIZED")
        request["protocol_version"] = 1
        mismatch = self.api.dispatch("GET", "/api/v2/binding/capabilities", request, authenticated=True)
        self.assertEqual((mismatch["status"], mismatch["body"]["code"]), (400, "NOT_SUPPORTED"))

    def test_capabilities_and_token_not_supported_are_explicit(self):
        response = self.api.dispatch("GET", "/api/v2/binding/capabilities", dict(self.scope), authenticated=True)
        self.assertEqual((response["status"], response["body"]["code"]), (200, "OK"))
        data = response["body"]["data"]
        self.assertEqual((data["protocol_version"], data["multi_account"], data["authority"]), (2, True, "center"))
        token = self.api.dispatch("POST", "/api/v2/binding/token/validate", dict(self.scope, token_code="123456"), authenticated=True)
        self.assertEqual((token["status"], token["body"]["code"]), (400, "NOT_SUPPORTED"))
        self.assertNotIn("123456", str(token))

    def test_check_returns_only_active_accounts_with_cursor_and_no_user_id(self):
        self.bind("Player_A", OP, TOKEN)
        self.commit()
        self.bind("Player_B", binding_tests.OP_B, binding_tests.TOKEN_B)
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=binding_tests.OP_B,
            reservation_id=binding_tests.RES_B, token_generation=binding_tests.TOKEN_B)
        self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=binding_tests.OP_B, policy_revision="1")
        response = self.api.dispatch("GET", "/api/v2/binding/check", dict(self.scope, user_id=USER, limit=1), authenticated=True)
        self.assertEqual(response["body"]["code"], "OK")
        self.assertEqual(response["body"]["data"]["game_ids"], ["Player_A"])
        self.assertEqual(response["body"]["data"]["bindings"][0]["revision"], "1")
        cursor = response["body"]["data"]["next_cursor"]
        next_page = self.api.dispatch("GET", "/api/v2/binding/check", dict(self.scope, user_id=USER, limit=1, cursor=cursor), authenticated=True)
        self.assertEqual(next_page["body"]["data"]["game_ids"], ["Player_B"])
        self.assertNotIn(USER, str(next_page["body"]["data"]))

    def test_operation_visibility_is_checked_against_group_and_authorizer(self):
        self.bind("Player_A", OP, TOKEN)
        self.commit()
        visible = self.api.dispatch("GET", f"/api/v2/binding/operations/{OP}", dict(self.scope), authenticated=True)
        self.assertEqual(visible["body"]["code"], "SYNC_PENDING")
        hidden_api = self.make_api(visible=lambda *_: False)
        hidden = hidden_api.dispatch("GET", f"/api/v2/binding/operations/{OP}", dict(self.scope), authenticated=True)
        self.assertEqual((hidden["status"], hidden["body"]["code"]), (401, "UNAUTHORIZED"))
        foreign = hidden_api.dispatch("GET", f"/api/v2/binding/operations/{OP}",
            dict(self.scope, group_id="SYNTHETIC_OTHER_GROUP"), authenticated=True)
        self.assertEqual(foreign["body"]["code"], "SCOPE_MISMATCH")

    def test_sync_is_downlink_effects_and_never_an_external_ack_write(self):
        self.bind("Player_A", OP, TOKEN)
        operation = self.commit()
        ack = {"game_key": "player_a", "result": "applied", "requested_revision": "1",
               "effective_revision": "1", "persisted": True, "whitelist": "APPLIED", "online": "APPLIED"}
        calls = []
        def apply(scope, op_id, effects):
            calls.append((scope, op_id, effects))
            return {"protocol_version": 2, "operation_id": op_id, "results": [ack]}
        api = self.make_api(sync_handler=apply, capabilities=lambda s: dict(self.capabilities(s), revision_sync=True))
        response = api.dispatch("POST", "/api/v2/binding/sync",
            dict(self.scope, operation_id=OP, effects=operation["effects"]), authenticated=True)
        self.assertEqual(response["body"]["code"], "OK")
        self.assertEqual(response["body"]["data"]["results"], [ack])
        self.assertEqual(len(calls), 1)
        rejected = api.dispatch("POST", "/api/v2/binding/sync",
            dict(self.scope, operation_id=OP, results=[ack]), authenticated=True)
        self.assertEqual(rejected["body"]["code"], "INVALID_INPUT")
        self.assertEqual(len(calls), 1)
        unchanged = self.store.get_operation(ENV, OP, group_id=GROUP)
        self.assertEqual(unchanged["followup"]["delivery"][SERVER]["mirror"], "PENDING")
        wrong_server = self.api.dispatch("POST", "/api/v2/binding/sync",
            dict(self.scope, server_id=SERVER_B, operation_id=OP, results=[ack]), authenticated=True)
        self.assertEqual(wrong_server["body"]["code"], "SCOPE_MISMATCH")

    def test_protocol_never_falls_back_to_old_storage(self):
        no_store = self.make_api(store=None)
        check = no_store.dispatch("GET", "/api/v2/binding/check", dict(self.scope, game_id="Player_A"), authenticated=True)
        self.assertEqual(check["body"]["code"], "NOT_SUPPORTED")

    def test_no_capability_probe_cannot_claim_hybrid_or_image_revision_support(self):
        api = self.make_api(capabilities=None)
        response = api.dispatch("GET", protocol.PREFIX + "capabilities", self.scope, authenticated=True)
        self.assertEqual(response["body"]["code"], "NOT_SUPPORTED")
        broken = self.make_api(capabilities=lambda s: dict(self.capabilities(s), revision_sync=True))
        response = broken.dispatch("GET", protocol.PREFIX + "capabilities", self.scope, authenticated=True)
        self.assertEqual(response["body"]["code"], "SCHEMA_MISMATCH")
        unsupported_local = self.make_api(capabilities=lambda s: dict(self.capabilities(s), token_reservation_query=True))
        response = unsupported_local.dispatch("GET", protocol.PREFIX + "capabilities", self.scope, authenticated=True)
        self.assertEqual(response["body"]["code"], "SCHEMA_MISMATCH")

    def test_missing_scope_or_auth_and_malformed_requests_return_safe_envelopes(self):
        for request in (None, [], {}, dict(self.scope, group_id=""), dict(self.scope, protocol_version=2.0)):
            response = self.api.dispatch("GET", protocol.PREFIX + "check", request, authenticated=True)
            self.assertFalse(response["body"]["success"])
            self.assertIn(response["body"]["code"], ("INVALID_INPUT", "NOT_SUPPORTED"))
        self.assertEqual(self.api.dispatch("GET", "anything", None)["body"]["code"], "UNAUTHORIZED")
        api = self.make_api(expected_scope=None)
        response = api.dispatch("GET", protocol.PREFIX + "capabilities", self.scope, authenticated=True)
        self.assertEqual(response["body"]["code"], "NOT_SUPPORTED")

    def test_unknown_exception_with_code_attribute_never_leaks_exception_text(self):
        class PrivateError(Exception): code = "DB_UNAVAILABLE"
        def failing(*_): raise PrivateError("SYNTHETIC_PRIVATE_TOKEN")
        api = self.make_api(authorize=failing)
        response = api.dispatch("GET", protocol.PREFIX + "capabilities", self.scope, authenticated=True)
        self.assertEqual(response["body"]["code"], "STORAGE_ERROR")
        self.assertNotIn("SYNTHETIC_PRIVATE", str(response))

    def test_single_account_check_returns_revoked_revision_and_no_owner_identity(self):
        self.bind("Player_A", OP, TOKEN)
        self.commit()
        with self.db() as connection:
            connection.execute("UPDATE bindings_qqofficial SET state='revoked', revision=2")
        response = self.api.dispatch("GET", protocol.PREFIX + "check", dict(self.scope, game_id="PLAYER_A"), authenticated=True)
        data = response["body"]["data"]
        self.assertEqual((data["bound"], data["revision"], data["state"]), (False, "2", "revoked"))
        self.assertNotIn(USER, str(data))
        empty = self.api.dispatch("GET", protocol.PREFIX + "check", dict(self.scope, game_id="Missing"), authenticated=True)
        self.assertIsNone(empty["body"]["data"]["revision"])

    def seed_accounts(self):
        with self.db() as connection:
            for key in ("a", "b", "c"):
                connection.execute("INSERT INTO bindings_qqofficial VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                    (ENV, GROUP, key, key.upper(), USER, "active", 1, OP, "2026-11-18", "2026-11-18"))

    def test_cursor_pins_snapshot_and_has_scope_requester_query_and_ttl(self):
        self.seed_accounts()
        first = self.api.dispatch("GET", protocol.PREFIX + "check", dict(self.scope, user_id=USER, limit=1), authenticated=True)
        cursor = first["body"]["data"]["next_cursor"]
        self.assertNotEqual(cursor, "a")
        with self.db() as connection:
            connection.execute("UPDATE bindings_qqofficial SET state='revoked',revision=2 WHERE game_key='b'")
        page = dict(self.scope, user_id=USER, limit=1, cursor=cursor)
        second = self.api.dispatch("GET", protocol.PREFIX + "check", page, authenticated=True)
        self.assertEqual(second["body"]["data"]["game_ids"], ["B"])
        for values in (dict(page, user_id="OTHER_USER"), dict(page, limit=2)):
            self.assertEqual(self.api.dispatch("GET", protocol.PREFIX + "check", values, authenticated=True)["body"]["code"], "CONFLICT")
        self.api.query_authorize = lambda *_: "ANOTHER_PRINCIPAL"
        self.assertEqual(self.api.dispatch("GET", protocol.PREFIX + "check", page, authenticated=True)["body"]["code"], "CONFLICT")
        self.api.query_authorize = lambda *_: USER
        self.now += 601
        self.assertEqual(self.api.dispatch("GET", protocol.PREFIX + "check", page, authenticated=True)["body"]["code"], "CONFLICT")

    def test_query_authorization_is_checked_on_each_page_and_cannot_be_request_actor(self):
        self.seed_accounts()
        first = self.api.dispatch("GET", protocol.PREFIX + "check", dict(self.scope, user_id=USER, limit=1), authenticated=True)
        self.api.query_authorize = lambda *_: False
        response = self.api.dispatch("GET", protocol.PREFIX + "check",
            dict(self.scope, user_id=USER, limit=1, cursor=first["body"]["data"]["next_cursor"]), authenticated=True)
        self.assertEqual(response["body"]["code"], "UNAUTHORIZED")
        forged = self.api.dispatch("GET", protocol.PREFIX + "check", dict(self.scope, user_id=USER, actor="admin"), authenticated=True)
        self.assertEqual(forged["body"]["code"], "INVALID_INPUT")

    def test_local_authority_and_uncommitted_effects_never_reach_sync_handler(self):
        self.bind("Player_A", OP, TOKEN)
        api = self.make_api(sync_handler=prior.forbidden,
            capabilities=lambda s: dict(self.capabilities(s), revision_sync=True))
        response = api.dispatch("POST", protocol.PREFIX + "sync", dict(self.scope, operation_id=OP, effects=[]), authenticated=True)
        self.assertEqual(response["body"]["code"], "CONFLICT")
        api.capability_probe = lambda s: dict(self.capabilities(s), storage_mode="local", authority="local", revision_sync=True)
        response = api.dispatch("POST", protocol.PREFIX + "sync", dict(self.scope, operation_id=OP, effects=[]), authenticated=True)
        self.assertEqual(response["body"]["code"], "NOT_SUPPORTED")

    def test_prepared_and_reserved_are_202_pending_not_success(self):
        self.bind("Player_A", OP, TOKEN)
        for state in ("PREPARED", "RESERVED"):
            response = self.api.dispatch("GET", protocol.PREFIX + "operations/" + OP, self.scope, authenticated=True)
            self.assertEqual((response["status"], response["body"]["code"], response["body"]["success"]), (202, state, False))
            if state == "PREPARED":
                self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP, reservation_id=RES, token_generation=TOKEN)


if __name__ == "__main__":
    unittest.main()
