"""No real sockets: synthetic HTTP connections, identities, and credentials only."""
from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_official_readonly as prior
import test_official_bindings as binding_tests

transport = prior.module("official_transport")
ENV, GROUP, USER = prior.ENV, prior.GROUP, prior.USER
SERVER, OP, TOKEN, RES = binding_tests.SERVER, binding_tests.OP_A, binding_tests.TOKEN_A, binding_tests.RES_A
SCOPE = transport.BindingScope(ENV, GROUP, SERVER)
CREDENTIAL = "SYNTHETIC_NOT_A_REAL_API_TOKEN"


def envelope(data=None, *, code="OK", success=True):
    return {"success": success, "code": code, "message": "SYNTHETIC_PRIVATE_MESSAGE",
            "data": dict(protocol_version=2, scope=SCOPE.wire(), **(data or {}))}


def capability(mode="hybrid"):
    return envelope(dict(storage_type="qq_official", storage_mode=mode, schema_version=1,
        game_key_rule="ascii_lower", multi_account=True, operation_results=True, revision_sync=True,
        authority="local" if mode == "local" else "center"))


class Response:
    def __init__(self, payload=None, *, status=200, raw=None, length=None, read_error=None):
        self.raw = raw if raw is not None else json.dumps(payload or envelope()).encode("utf-8")
        self.status, self.offset, self.closed = status, 0, False
        self.length = str(len(self.raw)) if length is None else length
        self.read_error = read_error
    def getheader(self, key): return self.length if key == "Content-Length" else None
    def read1(self, size):
        if self.read_error: raise self.read_error
        chunk = self.raw[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk
    def close(self): self.closed = True


class Connection:
    def __init__(self, response=None, *, connect_error=None, request_error=None, response_error=None):
        self.response = response or Response()
        self.connect_error, self.request_error, self.response_error = connect_error, request_error, response_error
        self.requests, self.timeouts, self.debug, self.closed, self.connected = [], [], None, False, False
        self.sock = SimpleNamespace(settimeout=self.timeouts.append)
        self.auto_open = 1
    def connect(self):
        if self.connect_error: raise self.connect_error
        self.connected = True
    def set_debuglevel(self, value): self.debug = value
    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        if self.request_error: raise self.request_error
    def getresponse(self):
        if self.response_error: raise self.response_error
        return self.response
    def close(self): self.closed = True


class TransportTests(unittest.TestCase):
    def make(self, connection=None, *, clock=lambda: 0, **timeouts):
        self.connection = connection or Connection()
        self.factories = []
        def factory(target, timeout):
            self.factories.append((target, timeout))
            return self.connection
        target = transport.ModTarget(SCOPE, "synthetic.example.invalid", 8080)
        return transport.V2ModClient(target, connection_factory=factory, clock=clock, **timeouts)

    def reserve(self, client):
        return client.request("token/reserve", dict(operation_id=OP, user_id=USER, game_key="player_a",
            token_generation=TOKEN, token_code="123456"), token=CREDENTIAL)

    def test_validate_uses_json_post_and_keeps_code_credentials_out_of_url(self):
        client = self.make()
        client.request("token/validate", {"token_code": "123456"}, token=CREDENTIAL)
        args, kwargs = self.connection.requests[0]
        self.assertEqual(args, ("POST", "/api/v2/binding/token/validate"))
        body = json.loads(kwargs["body"])
        self.assertEqual(body, dict(SCOPE.wire(), token_code="123456"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + CREDENTIAL)
        self.assertEqual(self.connection.debug, 0)
        self.assertEqual(self.connection.auto_open, 0)
        self.assertTrue(self.connection.closed)
        self.assertNotIn(CREDENTIAL, repr(client.__dict__))

    def test_official_whitelist_setting_is_a_typed_single_boolean_post(self):
        client = self.make(Connection(Response(envelope(dict(
            enabled=False, persisted=True, online_players=3, released=1)))))
        data = client.request("settings/whitelist", {"enabled": False}, token=CREDENTIAL)
        args, kwargs = self.connection.requests[0]
        self.assertEqual(args, ("POST", "/api/v2/binding/settings/whitelist"))
        self.assertEqual(json.loads(kwargs["body"]), dict(SCOPE.wire(), enabled=False))
        self.assertEqual(data["enabled"], False)
        self.assertEqual(data["released"], 1)
        for bad in ({"enabled": "false"}, {"enabled": 0}, {"enabled": None}, {}):
            client = self.make()
            with self.subTest(bad=bad), self.assertRaises(prior.core.OfficialError):
                client.request("settings/whitelist", bad, token=CREDENTIAL)
            self.assertFalse(self.factories)

    def test_official_whitelist_reply_requires_exact_typed_fields(self):
        for payload in (dict(enabled=False, persisted=True, online_players=0, released=0, extra=True),
                        dict(enabled=False, persisted=True, online_players=0),
                        dict(enabled="false", persisted=True, online_players=0, released=0),
                        dict(enabled=False, persisted=None, online_players=0, released=0),
                        dict(enabled=False, persisted=True, online_players=-1, released=0),
                        dict(enabled=False, persisted=True, online_players=0, released=True)):
            client = self.make(Connection(Response(envelope(payload))))
            with self.subTest(payload=payload), self.assertRaises(transport.TransportError) as caught:
                client.request("settings/whitelist", {"enabled": False}, token=CREDENTIAL)
            self.assertEqual(caught.exception.code, "SCHEMA_MISMATCH")

    def test_get_scopes_are_encoded_and_have_no_post_body(self):
        client = self.make()
        client.request("check", {"user_id": USER, "limit": 10}, token=CREDENTIAL)
        args, kwargs = self.connection.requests[0]
        self.assertEqual(args[0], "GET")
        values = parse_qs(urlsplit(args[1]).query)
        self.assertEqual(values["group_id"], [GROUP])
        self.assertEqual(values["server_id"], [SERVER])
        self.assertIsNone(kwargs["body"])
        self.assertNotIn("token", args[1])

    def test_unapproved_endpoints_fields_and_bad_codes_fail_before_connection(self):
        for endpoint, data in (("../bind", {}), ("bind?token=123456", {}), ("token/reserve", {}),
                               ("capabilities", {"token_code": "123456"}), ("check", {"token_code": "123456"}),
                               ("token/validate", {"token_code": "１２３４５６"}),
                               ("token/validate", {"token_code": "123456", "environment_id": prior.OTHER_ENV})):
            client = self.make()
            with self.subTest(endpoint=endpoint), self.assertRaises(prior.core.OfficialError):
                client.request(endpoint, data, token=CREDENTIAL)
            self.assertFalse(self.factories)

    def test_missing_token_and_header_injection_refuse_before_connection(self):
        for token in ("", "header\r\nInjection", None):
            client = self.make()
            with self.assertRaises(transport.TransportError) as caught:
                client.request("capabilities", {}, token=token)
            self.assertEqual(caught.exception.code, "UNAUTHORIZED")
            self.assertFalse(self.factories)

    def test_connect_failure_is_determinate_unavailable_without_replay(self):
        client = self.make(Connection(connect_error=TimeoutError("SYNTHETIC_PRIVATE_CREDENTIAL")))
        with self.assertRaises(transport.TransportError) as caught: self.reserve(client)
        self.assertEqual(caught.exception.code, "DB_UNAVAILABLE")
        self.assertFalse(caught.exception.may_have_executed)
        self.assertFalse(self.connection.requests)
        self.assertEqual(len(self.factories), 1)
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_post_partial_send_timeout_and_connection_reset_are_unknown_once(self):
        for error in (TimeoutError("PRIVATE"), ConnectionResetError("PRIVATE")):
            client = self.make(Connection(request_error=error))
            with self.assertRaises(transport.TransportError) as caught: self.reserve(client)
            self.assertEqual((caught.exception.code, caught.exception.may_have_executed), ("RESULT_UNKNOWN", True))
            self.assertEqual(len(self.connection.requests), 1)
            self.assertTrue(self.connection.closed)

    def test_read_only_timeout_does_not_become_invalid_code_or_mutation_unknown(self):
        client = self.make(Connection(response_error=TimeoutError()))
        with self.assertRaises(transport.TransportError) as caught:
            client.request("token/validate", {"token_code": "123456"}, token=CREDENTIAL)
        self.assertEqual(caught.exception.code, "DB_UNAVAILABLE")
        self.assertFalse(caught.exception.may_have_executed)

    def test_auth_wait_permission_change_prevents_request(self):
        calls = []
        client = self.make()
        def check():
            calls.append(1)
            if len(calls) == 2: raise binding_tests.bindings_module.BindingError("UNAUTHORIZED", "revoked")
        with self.assertRaises(prior.core.OfficialError):
            client.request("capabilities", {}, token=CREDENTIAL, revalidate=check)
        self.assertEqual(self.connection.requests, [])
        self.assertTrue(self.connection.closed)

    def test_redirects_auth_and_rate_limit_never_follow_or_use_message_body(self):
        for status, expected in ((302, "RESULT_UNKNOWN"), (401, "UNAUTHORIZED"), (403, "UNAUTHORIZED"), (429, "DB_UNAVAILABLE")):
            client = self.make(Connection(Response(status=status, raw=b"SYNTHETIC_PRIVATE_BODY")))
            with self.assertRaises(transport.TransportError) as caught: self.reserve(client)
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertEqual(len(self.connection.requests), 1)

    def test_oversized_chunked_or_content_length_response_is_rejected(self):
        for response in (Response(raw=b"x", length=str(transport.MAX_JSON_BYTES + 1)),
                         Response(raw=b"x" * (transport.MAX_JSON_BYTES + 1), length="")):
            if response.length == "": response.length = None
            client = self.make(Connection(response))
            with self.assertRaises(transport.TransportError) as caught: self.reserve(client)
            self.assertEqual(caught.exception.code, "RESULT_UNKNOWN")
            self.assertEqual(len(self.connection.requests), 1)
            self.assertTrue(response.closed)

    def test_duplicate_nonfinite_invalid_utf8_and_truncated_json_refuse(self):
        for raw in (b'{"success":true,"success":false}', b'{"x":NaN}', b'\xff', b'{'):
            client = self.make(Connection(Response(raw=raw)))
            with self.assertRaises(transport.TransportError) as caught:
                client.request("capabilities", {}, token=CREDENTIAL)
            self.assertEqual(caught.exception.code, "STORAGE_ERROR")
        client = self.make(Connection(Response(raw=b"{}", length="50")))
        with self.assertRaises(transport.TransportError) as caught: self.reserve(client)
        self.assertEqual(caught.exception.category, "truncated_response")

    def test_foreign_or_missing_scope_and_old_version_never_count_as_success(self):
        for updates in ({"scope": dict(SCOPE.wire(), group_id=GROUP.lower())}, {"scope": None}, {"protocol_version": 1}, {"protocol_version": 2.0}):
            data = envelope()
            data["data"].update(updates)
            client = self.make(Connection(Response(data)))
            with self.assertRaises(transport.TransportError): client.request("capabilities", {}, token=CREDENTIAL)

    def test_wrong_operation_response_id_is_rejected_after_post(self):
        client = self.make(Connection(Response(envelope({"operation_id": binding_tests.OP_B}))))
        with self.assertRaises(transport.TransportError) as caught: self.reserve(client)
        self.assertEqual(caught.exception.code, "SCOPE_MISMATCH")
        self.assertTrue(caught.exception.may_have_executed)

    def test_http_success_must_agree_with_business_success(self):
        for response in (Response(envelope(code="TOKEN_INVALID")), Response(envelope(), status=503),
                         Response(envelope(code="RESULT_UNKNOWN", success=False), status=200)):
            client = self.make(Connection(response))
            with self.assertRaises(transport.TransportError) as caught: self.reserve(client)
            self.assertEqual(caught.exception.code, "RESULT_UNKNOWN")

    def test_only_explicit_invalid_token_is_invalid_not_db_or_timeout(self):
        for code, status in (("TOKEN_INVALID", 400), ("DB_UNAVAILABLE", 503), ("CONFLICT", 409)):
            client = self.make(Connection(Response(envelope(code=code, success=False), status=status)))
            with self.assertRaises(transport.TransportError) as caught:
                client.request("token/validate", {"token_code": "123456"}, token=CREDENTIAL)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("PRIVATE", str(caught.exception))

    def test_handshake_checks_all_flags_scope_mode_and_schema_with_one_get(self):
        client = self.make(Connection(Response(capability())))
        self.assertEqual(client.handshake(token=CREDENTIAL)["authority"], "center")
        self.assertEqual(len(self.connection.requests), 1)
        for update in ({"storage_type": "onebot"}, {"storage_mode": "local"}, {"schema_version": True},
                       {"game_key_rule": "case_sensitive"}, {"multi_account": False},
                       {"operation_results": False}, {"revision_sync": "true"}, {"authority": "local"}):
            value = capability()
            value["data"].update(update)
            client = self.make(Connection(Response(value)))
            with self.assertRaises(transport.TransportError) as caught: client.handshake(token=CREDENTIAL)
            self.assertEqual(caught.exception.code, "SCHEMA_MISMATCH")
        for mode in ("remote", "local"):
            client = self.make(Connection(Response(capability(mode))))
            self.assertEqual(client.handshake(token=CREDENTIAL, storage_mode=mode)["storage_mode"], mode)

    def test_pending_operation_response_keeps_state_and_original_operation(self):
        data = envelope({"operation_id": OP, "state": "RESERVED"}, code="RESERVED", success=False)
        client = self.make(Connection(Response(data, status=202)))
        result = client.request("operations/" + OP, {}, token=CREDENTIAL)
        self.assertEqual(result["state"], "RESERVED")
        self.assertNotIn(CREDENTIAL, str(result))

    def test_no_legacy_retry_when_v2_route_missing(self):
        client = self.make(Connection(Response(status=404, raw=b"<html>not found</html>")))
        with self.assertRaises(transport.TransportError) as caught: client.handshake(token=CREDENTIAL)
        self.assertEqual(caught.exception.code, "NOT_SUPPORTED")
        self.assertEqual(len(self.connection.requests), 1)

    def test_deadline_and_timeout_arguments_are_bounded(self):
        now = iter((0, 9))
        client = self.make(clock=lambda: next(now))
        with self.assertRaises(transport.TransportError): client.request("capabilities", {}, token=CREDENTIAL)
        self.assertEqual(self.connection.requests, [])
        for timeout in (0, True, float("nan"), 100):
            with self.assertRaises(ValueError): self.make(total_timeout=timeout)

    def test_target_rejects_url_credentials_paths_and_numeric_shorthand(self):
        for host, port in (("https://user:pass@host", 80), ("host/path", 80), ("127.1", 80), ("0x7f000001", 80), ("host", True), ("host", 70000)):
            with self.assertRaises(prior.core.OfficialError): transport.ModTarget(SCOPE, host, port)
        self.assertEqual(transport.ModTarget(SCOPE, "127.0.0.1", 8080).port, 8080)

    def test_operation_terminal_rejection_is_returned_with_original_state(self):
        data = envelope({"operation_id": OP, "state": "ABORTED"}, success=False)
        client = self.make(Connection(Response(data)))
        self.assertEqual(client.request("operations/" + OP, {}, token=CREDENTIAL)["state"], "ABORTED")
        data["data"]["operation_id"] = binding_tests.OP_B
        client = self.make(Connection(Response(data)))
        with self.assertRaises(transport.TransportError) as caught:
            client.request("operations/" + OP, {}, token=CREDENTIAL)
        self.assertEqual(caught.exception.code, "SCOPE_MISMATCH")


class WirePeerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-wire-peer-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "center.sqlite3"
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(binding_tests.SCHEMA)
            connection.commit()
        finally:
            connection.close()
        self.store = binding_tests.bindings_module.OfficialBindings(lambda: sqlite3.connect(self.path), placeholder="?")
        self.service_module = prior.module("official_binding_service")
        self.responses, self.connections, self.secrets_requested = [], [], 0
        def factory(*_):
            if not self.responses: raise AssertionError("unexpected request")
            connection = Connection(self.responses.pop(0))
            self.connections.append(connection)
            return connection
        def token_source():
            self.secrets_requested += 1
            return CREDENTIAL
        target = transport.ModTarget(SCOPE, "synthetic.example.invalid", 8080)
        self.client = transport.V2ModClient(target, connection_factory=factory)
        self.peer = transport.V2TokenPeer(self.client, token_source, revalidate=lambda: None)
        self.request = dict(operation_id=OP, request_key="a" * 64, actor_kind="qq_member", actor_id=USER,
            user_id=USER, authorization={"scope": "self", "policy_revision": "1"}, policy_revision="1",
            target_server_ids=[SERVER], token_code="123456")

    @contextmanager
    def guard(self, scope, intent):
        self.assertNotIn("token_code", intent)
        yield "1"

    def validate_data(self):
        return envelope(dict(game_id="Player_A", game_key="player_a", token_generation=TOKEN,
                              expires_at="2099-01-01T00:00:00Z"))

    def reserve_data(self):
        return envelope(dict(operation_id=OP, reservation_id=RES, token_generation=TOKEN,
                              game_key="player_a", state="RESERVED"))

    def finalize_data(self):
        return envelope(dict(operation_id=OP, token_generation=TOKEN, token_state="CONSUMED"))

    def handshake(self, reservation_query=False):
        data = capability()
        if reservation_query: data["data"]["token_reservation_query"] = True
        self.responses.append(Response(data))
        self.peer.handshake()

    def reservation_data(self, state="RESERVED", center_available=False):
        receipt = dict(protocol_version=2, scope=SCOPE.wire(), operation_id=OP, state=state,
            token_generation=TOKEN, reservation_id=RES, game_key="player_a", user_id=USER)
        if state == "ABSENT":
            for name in ("token_generation", "reservation_id", "game_key", "user_id"): receipt[name] = None
        return envelope(dict(operation_id=OP, center_available=center_available, state="PREPARED", reservation=receipt),
            code="PREPARED" if center_available else "RESULT_UNKNOWN", success=False)

    def prepare(self):
        return self.store.prepare_bind(operation_id=OP, request_key="a" * 64, environment_id=ENV, group_id=GROUP,
            actor_kind="qq_member", actor_id=USER, user_id=USER, server_id=SERVER, token_generation=TOKEN,
            game_id="Player_A", policy_revision="1", target_server_ids=[SERVER], authorization=self.request["authorization"])

    def test_no_handshake_or_scope_mismatch_prevents_token_source_and_network(self):
        with self.assertRaises(transport.BindingError) as caught:
            self.peer.validate(SCOPE, "123456", SERVER)
        self.assertEqual(caught.exception.code, "NOT_SUPPORTED")
        self.assertEqual(self.secrets_requested, 0)
        self.assertFalse(self.connections)

    def test_full_wire_sequence_connects_storage_and_service_with_canonical_payloads(self):
        self.handshake()
        self.responses.extend(Response(data) for data in (self.validate_data(), self.reserve_data(), self.finalize_data()))
        service = self.service_module.OfficialBindingService(self.store, self.peer, policy_guard=self.guard)
        result = service.bind(SCOPE, self.request)
        self.assertEqual((result["state"], result["code"], result["success"]), ("COMMITTED", "SYNC_PENDING", True))
        self.assertEqual(self.secrets_requested, 4)
        paths = [c.requests[0][0][1].split("?")[0] for c in self.connections]
        self.assertEqual(paths, [transport.PREFIX + name for name in ("capabilities", "token/validate", "token/reserve", "token/finalize")])
        reserved = json.loads(self.connections[2].requests[0][1]["body"])
        self.assertEqual(set(reserved), transport.COMMON | {"operation_id", "user_id", "game_key", "token_generation", "token_code"})
        finalized = json.loads(self.connections[3].requests[0][1]["body"])
        self.assertEqual(set(finalized), transport.COMMON | {"operation_id", "reservation_id", "decision"})
        self.assertNotIn("token_code", str(finalized))
        self.assertNotIn("123456", str(result))
        self.assertTrue(all(c.closed for c in self.connections))

    def test_failed_rehandshake_disables_previously_ready_peer(self):
        self.handshake()
        self.assertTrue(self.peer.ready)
        wrong = capability()
        wrong["data"]["schema_version"] = 99
        self.responses.append(Response(wrong))
        with self.assertRaises(transport.BindingError): self.peer.handshake()
        self.assertFalse(self.peer.ready)


        before = self.secrets_requested
        with self.assertRaises(transport.BindingError): self.peer.validate(SCOPE, "123456", SERVER)
        self.assertEqual(self.secrets_requested, before)

    def test_wrong_reservation_generation_cannot_commit_and_remains_recoverable(self):
        self.handshake()
        wrong = self.reserve_data()
        wrong["data"]["token_generation"] = binding_tests.TOKEN_B
        self.responses.extend([Response(self.validate_data()), Response(wrong)])
        service = self.service_module.OfficialBindingService(self.store, self.peer, policy_guard=self.guard)
        result = service.bind(SCOPE, self.request)
        self.assertEqual((result["code"], result["state"]), ("RESULT_UNKNOWN", "PREPARED"))
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"]["token_reservation"]["state"], "PENDING")

    def test_local_reservation_status_is_not_inferred_from_center_operation(self):
        operation = self.prepare()
        before = self.secrets_requested
        with self.assertRaises(transport.BindingError) as caught: self.peer.status(SCOPE, operation)
        self.assertEqual(caught.exception.code, "NOT_SUPPORTED")
        self.assertEqual(self.secrets_requested, before)
        self.assertFalse(self.connections)

    def test_reserve_requires_pre_dispatch_claim_before_network(self):
        operation = self.prepare()
        self.handshake()
        with self.assertRaises(transport.BindingError) as caught: self.peer.reserve(SCOPE, operation, "123456")
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertEqual(len(self.connections), 1)

    def test_finalize_refuses_client_success_claim_before_center_commit(self):
        operation = self.prepare()
        with self.assertRaises(transport.BindingError) as caught: self.peer.finalize(SCOPE, operation, "COMMITTED")
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertFalse(self.connections)

    def test_validate_checks_name_generation_expiry_and_no_unspecified_extra_data(self):
        self.handshake()
        for change in ({"game_key": "wrong"}, {"game_id": "../bad"}, {"expires_at": "tomorrow"},
                       {"token_generation": "bad"}, {"unexpected": "SYNTHETIC_PRIVATE"}):
            data = self.validate_data()
            data["data"].update(change)
            self.responses.append(Response(data))
            with self.assertRaises(transport.BindingError): self.peer.validate(SCOPE, "123456", SERVER)

    def test_unknown_finalize_keeps_success_and_never_returns_remote_text(self):
        self.handshake()
        self.responses.extend([Response(self.validate_data()), Response(self.reserve_data()),
            Response(read_error=TimeoutError("SYNTHETIC_PRIVATE_HEADER"))])
        service = self.service_module.OfficialBindingService(self.store, self.peer, policy_guard=self.guard)
        result = service.bind(SCOPE, self.request)
        self.assertEqual((result["success"], result["state"], result["code"]), (True, "COMMITTED", "SYNC_PENDING"))
        self.assertNotIn("PRIVATE", str(result))
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"]["token_finalize"]["state"], "PENDING")

    def test_credential_source_failure_cannot_escape_as_raw_exception_text(self):
        def fails(): raise RuntimeError("SYNTHETIC_PRIVATE_CONFIG_DETAIL")
        peer = transport.V2TokenPeer(self.client, fails, revalidate=lambda: None)
        with self.assertRaises(transport.TransportError) as caught: peer.handshake()
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertFalse(self.connections)

    def test_contract_reserve_finalize_examples_without_scope_echo_are_supported(self):
        self.handshake()
        reserve, finalize = self.reserve_data(), self.finalize_data()
        del reserve["data"]["scope"]
        del finalize["data"]["scope"]
        self.responses.extend([Response(self.validate_data()), Response(reserve), Response(finalize)])
        service = self.service_module.OfficialBindingService(self.store, self.peer, policy_guard=self.guard)
        result = service.bind(SCOPE, self.request)
        self.assertTrue(result["success"])
        self.assertEqual(result["operation"]["followup"]["token_finalize"]["state"], "CONSUMED")

    def test_inactive_guard_prevents_even_transient_credential_lookup(self):
        def blocked(): raise transport.BindingError("UNAUTHORIZED", "inactive")
        peer = transport.V2TokenPeer(self.client, prior.forbidden, revalidate=blocked)
        with self.assertRaises(transport.BindingError) as caught: peer.handshake()
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertFalse(self.connections)

    def test_mod_local_reserved_receipt_is_available_while_center_result_is_unknown(self):
        operation = self.prepare()
        self.handshake(reservation_query=True)
        self.responses.append(Response(self.reservation_data(), status=202))
        receipt = self.peer.status(SCOPE, operation)
        self.assertEqual((receipt["state"], receipt["operation_id"], receipt["reservation_id"]), ("RESERVED", OP, RES))
        self.assertNotIn("COMMITTED", str(receipt))
        self.assertEqual(self.connections[-1].requests[0][0][0], "GET")

    def test_reserve_response_lost_then_wire_status_recovers_without_code_or_re_reserve(self):
        self.handshake(reservation_query=True)
        self.responses.extend([Response(self.validate_data()), Response(read_error=TimeoutError("SYNTHETIC"))])
        service = self.service_module.OfficialBindingService(self.store, self.peer, policy_guard=self.guard)
        unknown = service.bind(SCOPE, self.request)
        self.assertEqual((unknown["code"], unknown["state"]), ("RESULT_UNKNOWN", "PREPARED"))
        self.responses.extend([Response(self.reservation_data(), status=202), Response(self.finalize_data())])
        recovered = service.recover(SCOPE, OP)
        self.assertEqual((recovered["success"], recovered["state"]), (True, "COMMITTED"))
        endpoints = [c.requests[0][0][1].split("?")[0] for c in self.connections]
        self.assertEqual(endpoints.count(transport.PREFIX + "token/reserve"), 1)
        self.assertEqual(endpoints.count(transport.PREFIX + "token/validate"), 1)
        self.assertEqual(endpoints.count(transport.PREFIX + "operations/" + OP), 1)

    def test_shared_java_absent_fixture_requires_center_cas_abort_before_invalidation(self):
        fixture_path = prior.ROOT / "tests" / "fixtures" / "official_reservation_v2.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertTrue(fixture["synthetic"])
        operation = self.prepare()
        self.store.claim_reservation(environment_id=ENV, group_id=GROUP, operation_id=OP, token_generation=TOKEN)
        self.handshake(reservation_query=True)
        final = envelope(dict(operation_id=OP, token_generation=TOKEN, token_state="INVALIDATED", reservation_id=RES))
        self.responses.extend([Response(fixture["absent_response"], status=202), Response(final)])
        original_factory = self.client.connection_factory
        def observe(target, timeout):
            if len(self.responses) == 1:
                self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["state"], "ABORTED")
            return original_factory(target, timeout)
        self.client.connection_factory = observe
        service = self.service_module.OfficialBindingService(self.store, self.peer, policy_guard=self.guard)
        result = service.recover(SCOPE, OP)
        self.assertFalse(result["success"])
        self.assertEqual(result["state"], "ABORTED")
        self.assertEqual(result["operation"]["followup"]["token_finalize"]["state"], "INVALIDATED")
        body = json.loads(self.connections[-1].requests[0][1]["body"])
        self.assertEqual(body, dict(SCOPE.wire(), **fixture["absent_finalize"]))

    def test_foreign_generation_owner_or_local_receipt_shape_is_rejected(self):
        operation = self.prepare()
        self.handshake(reservation_query=True)
        for changed in ({"token_generation": binding_tests.TOKEN_B}, {"user_id": "OTHER_USER"},
                        {"operation_id": binding_tests.OP_B}, {"reservation_id": None}, {"state": "COMMITTED"}):
            value = self.reservation_data()
            value["data"]["reservation"].update(changed)
            self.responses.append(Response(value, status=202))
            with self.assertRaises(prior.core.OfficialError): self.peer.status(SCOPE, operation)

    def test_claimed_query_capability_without_local_reservation_data_is_not_proof(self):
        operation = self.prepare()
        self.handshake(reservation_query=True)
        self.responses.append(Response(envelope({"operation_id": OP, "state": "COMMITTED"})))
        with self.assertRaises(transport.TransportError) as caught: self.peer.status(SCOPE, operation)
        self.assertEqual(caught.exception.code, "STORAGE_ERROR")

    def test_absent_receipt_cannot_carry_another_generation_or_owner(self):
        operation = self.prepare()
        self.handshake(reservation_query=True)
        value = self.reservation_data("ABSENT")
        value["data"]["reservation"]["user_id"] = USER
        self.responses.append(Response(value, status=202))
        with self.assertRaises(transport.TransportError): self.peer.status(SCOPE, operation)

    def test_bad_pending_operation_id_is_rejected_before_local_receipt_is_used(self):
        operation = self.prepare()
        self.handshake(reservation_query=True)
        value = self.reservation_data()
        value["data"]["operation_id"] = binding_tests.OP_B
        self.responses.append(Response(value, status=202))
        with self.assertRaises(transport.TransportError) as caught: self.peer.status(SCOPE, operation)
        self.assertEqual(caught.exception.code, "SCOPE_MISMATCH")

    def test_new_capability_is_optional_but_must_be_a_real_boolean(self):
        bad = capability()
        bad["data"]["token_reservation_query"] = "true"
        self.responses.append(Response(bad))
        with self.assertRaises(transport.TransportError): self.peer.handshake()
        self.assertFalse(self.peer.ready)


class MirrorDeliveryTests(unittest.TestCase):
    setUp = WirePeerTests.setUp
    prepare = WirePeerTests.prepare
    handshake = WirePeerTests.handshake
    guard = WirePeerTests.guard

    def commit(self):
        self.prepare()
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP, reservation_id=RES, token_generation=TOKEN)
        return self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP, policy_revision="1")

    def response(self, **changes):
        fixture_path = prior.ROOT / "tests" / "fixtures" / "official_mirror_v2.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertTrue(fixture["synthetic"])
        response = deepcopy(fixture["response"])
        response["data"]["results"][0].update(changes)
        return Response(response)

    def deliver(self):
        return transport.V2MirrorDelivery(self.store, self.peer).deliver(SCOPE, OP)

    def test_java_shared_sync_fixture_acks_mirror_only_and_keeps_token_pending(self):
        operation = self.commit()
        self.handshake()
        self.responses.append(self.response())
        result = self.deliver()
        self.assertEqual((result["success"], result["state"], result["code"]), (True, "COMMITTED", "SYNC_PENDING"))
        self.assertEqual(result["delivery"]["mirror"], "APPLIED")
        self.assertEqual(result["delivery"]["whitelist"], "PENDING")
        sent = json.loads(self.connections[-1].requests[0][1]["body"])
        self.assertEqual(sent, dict(SCOPE.wire(), operation_id=OP, effects=operation["effects"]))
        self.assertNotIn("token_code", str(sent))
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"]["token_finalize"]["state"], "PENDING")

    def test_followup_sync_gets_completed_game_receipts_separately_from_token_consumption(self):
        self.commit()
        self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP, server_id=SERVER,
            token_state="CONSUMED", reservation_id=RES, token_generation=TOKEN)
        self.handshake()
        self.responses.append(self.response(whitelist="APPLIED", online="APPLIED"))
        result = self.deliver()
        self.assertEqual(result["code"], "OK")
        self.assertEqual(self.store.due_operations(ENV), [])

    def test_old_bind_task_superseded_receipt_cannot_change_new_unbind_delivery(self):
        self.commit()
        self.store.prepare_unbind(operation_id=binding_tests.OP_B, request_key="b" * 64,
            environment_id=ENV, group_id=GROUP, actor_kind="qq_member", actor_id=USER,
            target_user_id=USER, kind="unbind_game", targets=[dict(game_key="player_a", expected_revision="1")],
            policy_revision="1", authorization=dict(scope="self", policy_revision="1"), target_server_ids=[SERVER])
        latest = self.store.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=binding_tests.OP_B, policy_revision="1")
        self.handshake()
        self.responses.append(self.response(result="ignored", effective_revision="2", whitelist="SUPERSEDED", online="SUPERSEDED"))
        result = self.deliver()
        self.assertEqual(result["delivery"]["mirror"], "SUPERSEDED")
        self.assertEqual(self.store.get_operation(ENV, binding_tests.OP_B, group_id=GROUP)["followup"], latest["followup"])
        self.responses.append(self.response(whitelist="APPLIED", online="APPLIED"))
        self.assertEqual(self.deliver()["delivery"]["mirror"], "SUPERSEDED")

    def test_changed_revision_in_reply_never_acknowledges_center(self):
        original = self.commit()
        self.handshake()
        self.responses.append(self.response(requested_revision="2"))
        result = self.deliver()
        self.assertEqual(result["followup_code"], "CONFLICT")
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"], original["followup"])

    def test_missing_or_duplicate_ack_set_does_not_partly_update(self):
        original = self.commit()
        self.handshake()
        for results in ([], [{"game_key": "player_a"}, {"game_key": "player_a"}]):
            self.responses.append(Response(envelope(dict(operation_id=OP, results=results))))
            result = self.deliver()
            self.assertEqual(result["followup_code"], "CONFLICT")
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"], original["followup"])

    def test_mod_timeout_preserves_center_success_and_does_not_retry(self):
        original = self.commit()
        self.handshake()
        self.responses.append(Response(read_error=TimeoutError("SYNTHETIC_PRIVATE_TOKEN")))
        result = self.deliver()
        self.assertEqual((result["success"], result["code"], result["followup_code"]), (True, "SYNC_PENDING", "RESULT_UNKNOWN"))
        self.assertNotIn("PRIVATE", str(result))
        self.assertEqual(len(self.connections), 2)
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"], original["followup"])

    def test_uncommitted_and_foreign_target_never_reach_transport(self):
        self.prepare()
        result = self.deliver()
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "SCOPE_MISMATCH")
        self.assertEqual(self.secrets_requested, 0)
        foreign = transport.BindingScope(ENV, GROUP, binding_tests.TOKEN_B)
        result = transport.V2MirrorDelivery(self.store, self.peer).deliver(foreign, OP)
        self.assertFalse(result["success"])
        self.assertEqual(self.connections, [])

    def test_inactive_guard_fails_before_credential_and_before_center_lookup(self):
        self.commit()
        def inactive(): raise transport.BindingError("UNAUTHORIZED", "inactive")
        self.peer.revalidate = inactive
        with patch.object(self.store, "get_operation", side_effect=prior.forbidden): result = self.deliver()
        self.assertEqual(result["code"], "UNAUTHORIZED")
        self.assertFalse(result["success"])
        self.assertEqual(self.secrets_requested, 0)

    def test_mod_ack_success_but_local_ack_commit_unknown_is_still_committed(self):
        self.commit()
        self.handshake()
        self.responses.append(self.response())
        with patch.object(self.store, "acknowledge", side_effect=transport.BindingError("RESULT_UNKNOWN", "SYNTHETIC_PRIVATE")):
            result = self.deliver()
        self.assertEqual((result["success"], result["state"], result["followup_code"]), (True, "COMMITTED", "RESULT_UNKNOWN"))
        self.assertNotIn("PRIVATE", str(result))

    def test_conflicting_receipt_pauses_old_operation_and_later_success_cannot_clear_it(self):
        self.commit()
        self.handshake()
        self.responses.append(self.response(result="conflict", whitelist="CONFLICT", online="CONFLICT"))
        result = self.deliver()
        self.assertEqual(result["delivery"]["mirror"], "CONFLICT")
        self.responses.append(self.response(whitelist="APPLIED", online="APPLIED"))
        self.assertEqual(self.deliver()["delivery"]["mirror"], "CONFLICT")


if __name__ == "__main__":
    unittest.main()
