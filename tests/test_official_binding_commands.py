"""Synthetic ingress/host integration; only fresh temp files and SQLite are used.

No framework import, user config, network, process, environment or credential
discovery. The only source read is main.py for an extracted alias definition.
"""
import ast
import asyncio
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4, uuid5

import test_official_readonly as prior
import test_official_bindings as warehouse
import test_official_center as center_tests

commands = prior.module("official_binding_commands")
host_module = prior.module("official_binding_host")
core, bindings = prior.core, warehouse.bindings_module
ENV, GROUP, USER = prior.ENV, prior.GROUP, prior.USER
OTHER = "SYNTHETIC_USER_B"


class Peer:
    def __init__(self):
        self.calls, self.saved = [], {}
        self.game, self.generation = "Player_A", warehouse.TOKEN_A
        self.valid, self.capability, self.finalize_unknown = True, True, False
        self.before_validate = lambda: None

    def handshake(self):
        self.calls.append("handshake")
        return {"token_reservation_query": self.capability}

    def validate(self, scope, code, server):
        self.calls.append("validate")
        self.before_validate()
        if isinstance(self.valid, Exception):
            raise self.valid
        if not self.valid:
            raise bindings.BindingError("TOKEN_INVALID", "synthetic invalid")
        return dict(scope=scope.wire(), state="FOUND", game_id=self.game, game_key=self.game.lower(),
                    token_generation=self.generation, expires_at="2099-01-01T00:00:00Z")

    @staticmethod
    def receipt(scope, operation, state):
        return dict(scope=scope.wire(), state=state, operation_id=operation["operation_id"],
            token_generation=operation["token_generation"], reservation_id=str(uuid5(commands.NAMESPACE, operation["operation_id"])),
            game_key=operation["intent"]["game_key"], user_id=operation["target_user_id"])

    def reserve(self, scope, operation, code):
        self.calls.append("reserve")
        self.saved[operation["operation_id"]] = "RESERVED"
        return self.receipt(scope, operation, "RESERVED")

    def finalize(self, scope, operation, decision):
        self.calls.append("finalize")
        if self.finalize_unknown:
            raise bindings.BindingError("RESULT_UNKNOWN", "synthetic finalize unknown")
        state = "CONSUMED" if decision == "COMMITTED" else "INVALIDATED"
        self.saved[operation["operation_id"]] = state
        return self.receipt(scope, operation, state)

    def status(self, scope, operation):
        self.calls.append("status")
        return self.receipt(scope, operation, self.saved.get(operation["operation_id"], "RESERVED"))


class ParseTests(unittest.TestCase):
    def test_unbind_without_target_defaults_to_all_like_onebot(self):
        self.assertEqual(commands.parse("unbind", ""), (["all"], None))
        self.assertEqual(commands.parse("unbind", "-s one"), (["all"], "one"))
        self.assertEqual(commands.parse("unbind", "Player_A -s one"), (["Player_A"], "one"))
        self.assertEqual(commands.parse("unbind", "all"), (["all"], None))


class CommandIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-ingress-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "synthetic-center.sqlite3"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript(warehouse.SCHEMA)
            db.commit()
        self.center = bindings.OfficialBindings(lambda: sqlite3.connect(self.db_path), placeholder="?")
        # 中心库改为按群开关；这些集成测试显式开启，覆盖既有中心路径。
        self.groups = core.OfficialStore(self.root / "groups",
                                         policy_defaults=lambda: {"use_central_db": True})
        self.addCleanup(self.groups.close)
        self.event = prior.scope(message_id="SYNTHETIC_BIND_EVENT_A")
        candidate = self.groups.register(self.event)
        self.group = self.groups.approve(ENV, candidate["candidate_id"], self.groups.list_state(ENV)["revision"],
                                         "Synthetic", "123456789")
        self.update({"enabled": True, "servers": [prior.server()]})
        self.current, self.unchanged, self.opens = dict(prior.SELECTION), True, 0
        self.peers, self.dependencies = {}, []
        self.settings = host_module.CenterSettings(center_tests.settings(), lambda: self.unchanged)
        def center_factory(settings, guard):
            self.dependencies.append("center")
            guard()
            return self.center
        def peer_factory(target, token_source, guard):
            guard()
            self.dependencies.append("peer")
            return self.peers.setdefault(target.scope.server_id, Peer())
        self.host = host_module.OfficialBindingHost(self.groups, lambda: self.current,
            settings_source=self.settings_source, center_factory=center_factory, peer_factory=peer_factory)
        self.commands = commands.OfficialBindingCommands(self.host)

    def settings_source(self, environment):
        self.opens += 1
        self.assertEqual(environment, ENV)
        return self.settings

    def update(self, changes):
        changes = deepcopy(changes)
        for field, role in (("administrators", "group_admin"), ("blacklist", "blacklist")):
            for user in changes.pop(field, []):
                candidate = self.groups.register(replace(self.event, user_id=user))
                self.group = self.groups.authorize(ENV, self.group["config_id"], candidate["candidate_id"],
                                                    role, self.group["revision"])
        if changes:
            self.group = self.groups.update(ENV, self.group["config_id"], self.group["revision"], changes)

    def peer(self, index=0):
        server = self.group["servers"][index]
        return self.peers.setdefault(server["server_id"], Peer())

    def execute(self, command="bind", arguments="123456 -s one", event=None):
        return self.commands.execute(event or self.event, command, arguments)[1]

    def operation(self, event=None, action="bind"):
        _, operation_id = commands.request_identity(event or self.event, action)
        return self.center.get_operation(ENV, operation_id, group_id=GROUP)

    def test_bind_runs_through_real_service_policy_and_center_without_code_in_result(self):
        lines = self.execute()
        operation = self.operation()
        self.assertEqual(operation["state"], "COMMITTED")
        self.assertEqual(operation["followup"]["token_finalize"]["state"], "CONSUMED")
        self.assertIn("镜像 PENDING；白名单 PENDING；在线限制 PENDING", " \n".join(lines))
        self.assertNotIn("123456", str(lines) + json.dumps(operation) + repr(self.settings))
        self.assertEqual(self.peer().calls, ["handshake", "validate", "reserve", "finalize"])

    def test_same_event_is_idempotent_and_new_event_same_generation_reuses_original(self):
        self.execute()
        operation = self.operation()
        self.execute()
        other = replace(self.event, message_id="SYNTHETIC_DUPLICATE_EVENT")
        lines = self.execute(event=other)
        self.assertIn(operation["operation_id"], str(lines))
        self.assertEqual(self.peer().calls.count("reserve"), 1)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bindings_qqofficial_ops").fetchone()[0], 1)

    def test_explicit_server_failure_never_probes_another_server(self):
        self.update({"servers": self.group["servers"] + [prior.server("two")]})
        self.peer().valid = False
        self.execute()
        self.assertEqual(self.peer(1).calls, [])
        self.assertIsNone(self.operation())

    def test_automatic_location_requires_all_candidates_to_be_conclusive(self):
        self.update({"servers": self.group["servers"] + [prior.server("two")]})
        self.peer(1).valid = bindings.BindingError("DB_UNAVAILABLE", "synthetic unavailable")
        lines = self.execute(arguments="123456")
        self.assertIn("-s", str(lines))
        self.assertIsNone(self.operation())
        self.assertNotIn("reserve", self.peer().calls)

    def test_two_server_matches_do_not_create_a_center_operation(self):
        self.update({"servers": self.group["servers"] + [prior.server("two")]})
        lines = self.execute(arguments="123456")
        self.assertIn("多服命中", str(lines))
        self.assertIsNone(self.operation())

    def test_unique_automatic_match_is_revalidated_before_reserving(self):
        self.update({"servers": self.group["servers"] + [prior.server("two")]})
        self.peer(1).valid = False
        self.execute(arguments="123456")
        self.assertEqual(self.operation()["state"], "COMMITTED")
        self.assertEqual(self.peer().calls.count("validate"), 2)
        self.assertNotIn("reserve", self.peer(1).calls)

    def test_more_than_four_auto_candidates_require_explicit_server_without_network(self):
        self.update({"servers": [prior.server(str(i)) for i in range(5)]})
        lines = self.execute(arguments="123456")
        self.assertIn("-s", str(lines))
        self.assertNotIn("peer", self.dependencies)

    def test_incomplete_capabilities_block_all_mutations(self):
        self.peer().capability = False
        self.execute()
        self.assertIsNone(self.operation())
        self.assertEqual(self.peer().calls, ["handshake"])

    def test_missing_card_does_not_fall_back_to_username_or_create_binding(self):
        self.execute(event=replace(self.event, nickname=""))
        self.assertIsNone(self.operation())
        self.assertNotIn("reserve", self.peer().calls)

    def test_permission_or_revision_change_during_validation_prevents_reserve(self):
        self.peer().before_validate = lambda: self.update({"blacklist": [USER]})
        self.execute()
        self.assertIsNone(self.operation())
        self.assertNotIn("reserve", self.peer().calls)

    def test_config_disabled_blacklisted_or_other_instance_never_loads_center_settings(self):
        self.current = dict(prior.SELECTION, instance_id="other")
        self.execute()
        self.current = dict(prior.SELECTION)
        self.update({"enabled": False})
        self.execute()
        self.update({"enabled": True, "blacklist": [USER]})
        self.execute()
        self.assertEqual(self.opens, 0)

    def test_user_check_returns_only_current_users_active_rows(self):
        self.execute()
        self.assertIn("Player_A", str(self.execute("check", "")))
        self.assertNotIn("Player_A", str(self.execute("check", "", replace(self.event, user_id=OTHER))))
        self.execute("unbind", "Player_A", replace(self.event, message_id="SYNTHETIC_UNBIND"))
        self.assertNotIn("Player_A", str(self.execute("check", "")))

    def test_self_single_unbind_uses_current_row_revision_and_no_remote_write(self):
        self.execute()
        before = list(self.peer().calls)
        event = replace(self.event, message_id="SYNTHETIC_UNBIND")
        lines = self.execute("unbind", "Player_A", event)
        operation = self.operation(event, "unbind_game")
        self.assertEqual(operation["state"], "COMMITTED")
        self.assertEqual(operation["intent"]["targets"], [{"game_key": "player_a", "expected_revision": "1"}])
        self.assertIn("COMMITTED", str(lines))
        self.assertEqual(self.peer().calls, before + ["handshake"])

    def test_other_member_cannot_unbind_or_inspect_owners_operation(self):
        self.execute()
        other = replace(self.event, user_id=OTHER, message_id="SYNTHETIC_OTHER")
        lines = self.execute("unbind", "Player_A", other)
        self.assertIn("本人", str(lines))
        lines = self.execute("operation", self.operation()["operation_id"][:8], other)
        self.assertNotIn("COMMITTED", str(lines))
        self.assertNotIn("operation_id", str(lines))

    def test_admin_unbind_by_user_uses_fixed_current_scope_only(self):
        self.execute()
        self.update({"administrators": [OTHER]})
        admin = replace(self.event, user_id=OTHER, message_id="SYNTHETIC_ADMIN")
        self.execute("unbind", "user " + USER, admin)
        operation = self.operation(admin, "unbind_user")
        self.assertEqual(operation["state"], "COMMITTED")
        self.assertEqual(operation["intent"]["authorization"]["scope"], "group")

    def test_repeat_unbind_does_not_expand_to_accounts_added_after_original(self):
        self.execute()
        event = replace(self.event, message_id="SYNTHETIC_UNBIND_ALL")
        self.execute("unbind", "all", event)
        original = self.operation(event, "unbind_user")
        self.peer().game, self.peer().generation = "Player_B", warehouse.TOKEN_B
        newer = replace(self.event, message_id="SYNTHETIC_BIND_B", nickname="玩家(Player_B)")
        self.execute(event=newer)
        self.execute("unbind", "all", event)
        self.assertEqual(self.operation(event, "unbind_user")["effects"], original["effects"])
        self.assertEqual([row["game_key"] for row in self.center.active_snapshot(ENV, GROUP, USER)], ["player_b"])

    def test_prepared_unbind_can_resume_original_fixed_targets(self):
        self.execute()
        event = replace(self.event, message_id="SYNTHETIC_PREPARED_UNBIND")
        with patch.object(self.center, "commit_unbind", side_effect=bindings.BindingError("RESULT_UNKNOWN", "synthetic lost")):
            self.execute("unbind", "all", event)
        operation = self.operation(event, "unbind_user")
        self.assertEqual(operation["state"], "PREPARED")
        lines = self.execute("operation", operation["operation_id"] + " retry", replace(event, message_id="SYNTHETIC_RETRY"))
        self.assertIn("COMMITTED", str(lines))
        self.assertEqual(self.operation(event, "unbind_user")["effects"][0]["revision"], "2")

    def test_prepared_unbind_can_be_cancelled_without_touching_binding_or_mod(self):
        self.execute()
        event = replace(self.event, message_id="SYNTHETIC_CANCEL_UNBIND")
        with patch.object(self.center, "commit_unbind", side_effect=bindings.BindingError("RESULT_UNKNOWN", "synthetic lost")):
            self.execute("unbind", "all", event)
        operation = self.operation(event, "unbind_user")
        before = list(self.peer().calls)
        lines = self.execute("operation", operation["operation_id"] + " cancel", replace(event, message_id="SYNTHETIC_CANCEL"))
        self.assertIn("ABORTED", str(lines))
        self.assertEqual(self.center.active_snapshot(ENV, GROUP, USER)[0]["revision"], "1")
        self.assertEqual(self.peer().calls, before)

    def test_same_event_cannot_be_reused_for_another_unbind_selector(self):
        self.execute()
        event = replace(self.event, message_id="SYNTHETIC_SELECTOR")
        self.execute("unbind", "Player_A", event)
        lines = self.execute("unbind", "Player_B", event)
        self.assertIn("不同解绑目标", str(lines))

    def test_changed_policy_rejects_prepared_unbind_instead_of_expanding_it(self):
        self.execute()
        event = replace(self.event, message_id="SYNTHETIC_POLICY_CHANGE")
        with patch.object(self.center, "commit_unbind", side_effect=bindings.BindingError("RESULT_UNKNOWN", "synthetic lost")):
            self.execute("unbind", "all", event)
        operation = self.operation(event, "unbind_user")
        self.update({"alias": "Changed alias"})
        self.execute("operation", operation["operation_id"] + " retry", replace(event, message_id="SYNTHETIC_POLICY_RETRY"))
        self.assertEqual(self.operation(event, "unbind_user")["state"], "REJECTED")
        self.assertEqual(self.center.active_snapshot(ENV, GROUP, USER)[0]["revision"], "1")

    def test_terminal_retry_finishes_token_without_rebinding_or_claiming_effects_applied(self):
        self.peer().finalize_unknown = True
        self.execute()
        operation = self.operation()
        self.peer().finalize_unknown = False
        lines = self.execute("operation", operation["operation_id"][:8] + " retry")
        self.assertIn("验证码确认：CONSUMED", str(lines))
        self.assertIn("白名单 PENDING", str(lines))
        self.assertEqual(self.peer().calls.count("reserve"), 1)
        self.assertEqual(self.center.active_snapshot(ENV, GROUP, USER)[0]["revision"], "1")

    def test_prefix_ambiguity_is_detected_only_among_authorized_operations(self):
        self.execute()
        original = self.operation()
        # A real second normalized operation with the same visible prefix.
        prefix = original["operation_id"][:8]
        operation_id = prefix + "-0000-4000-8000-000000000000"
        self.center.prepare_unbind(operation_id=operation_id, request_key="e" * 64, environment_id=ENV, group_id=GROUP,
            actor_kind="qq_member", actor_id=OTHER, target_user_id=OTHER, kind="unbind_game",
            targets=[{"game_key": "player_b", "expected_revision": "1"}], policy_revision="2",
            authorization={"scope": "self", "policy_revision": "2"}, target_server_ids=[self.group["servers"][0]["server_id"]])
        self.assertEqual(self.center.resolve_operation(ENV, GROUP, prefix, requester=USER)["operation_id"], original["operation_id"])
        with self.assertRaises(bindings.BindingError):
            self.center.resolve_operation(ENV, GROUP, prefix, requester=USER, administrator=True)

    def test_budget_rejects_seventh_attempt_without_growing_unbounded(self):
        self.peer().valid = False
        for _ in range(6):
            self.execute()
        before = self.opens
        self.assertIn("频繁", str(self.execute()))
        self.assertEqual(self.opens, before)
        self.assertEqual(len(self.commands.budget.entries), 3)

    def test_settings_rotation_stops_existing_session_before_network_or_center_access(self):
        session = self.host.open(self.event)
        self.unchanged = False
        with self.assertRaises(bindings.BindingError):
            session.peer(self.group["servers"][0])
        self.assertEqual(self.peers, {})

    def test_group_center_switch_off_uses_local_without_loading_center_settings(self):
        policy = dict(self.group["policy"], use_central_db=False)
        self.update({"policy": policy})
        before = self.opens
        session = self.host.open(self.event)
        self.assertEqual((session.settings.storage_mode, session.settings.connection), ("local", None))
        self.assertIsInstance(session.central, host_module.UnselectedAuthority)
        self.assertEqual(self.opens, before)

    def test_public_hidden_target_is_not_probed_but_remains_a_fixed_sync_target(self):
        self.update({"servers": self.group["servers"] + [prior.server("hidden", hidden=True)]})
        self.execute(arguments="123456")
        self.assertEqual(len(self.operation()["intent"]["target_server_ids"]), 2)
        self.assertEqual(self.peer(1).calls, [])

    def test_malformed_arguments_are_rejected_before_settings_or_dependencies(self):
        for command, value in (("bind", "123456 -s one -s two"), ("bind", "１２３４５６"),
                               ("unbind", "QQ 123456"), ("operation", "%%%%%%%% retry"),
                               ("check", "Player_A extra")):
            self.execute(command, value)
        self.assertEqual(self.opens, 0)

    def test_unexpected_private_exception_is_not_echoed(self):
        self.host.settings_source = lambda _: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_PRIVATE_MATERIAL"))
        lines = self.execute()
        self.assertNotIn("SYNTHETIC_PRIVATE_MATERIAL", str(lines))
        self.assertNotIn("123456", str(lines))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-settings-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.groups = core.OfficialStore(self.temp.name)
        self.addCleanup(self.groups.close)
        self.host = host_module.OfficialBindingHost(self.groups, lambda: prior.SELECTION)
        self.path = Path(self.temp.name) / ENV / "center_settings.json"
        self.data = dict(schema_version=1, environment_id=ENV, enabled=True, storage_mode="hybrid", connection=center_tests.settings())

    def write(self):
        core.atomic_json(self.path, self.data)

    def test_missing_disabled_wrong_environment_and_unsupported_modes_fail_closed(self):
        with self.assertRaises(bindings.BindingError): self.host._settings(ENV)
        for key, value in (("enabled", False), ("environment_id", prior.OTHER_ENV), ("storage_mode", "unsupported"),
                           ("schema_version", True), ("connection", {})):
            before = deepcopy(self.data)
            self.data[key] = value
            self.write()
            with self.assertRaises(bindings.BindingError): self.host._settings(ENV)
            self.data = before

    def test_valid_explicit_file_is_bound_to_its_metadata_and_never_prints_connection(self):
        self.write()
        settings = self.host._settings(ENV)
        self.assertTrue(settings.unchanged())
        self.assertNotIn("SYNTHETIC_DB_USER", repr(settings))
        self.data["enabled"] = False
        self.write()
        self.assertFalse(settings.unchanged())
        self.data.update(enabled=True,storage_mode="local",connection=None)
        self.write()
        self.assertEqual(self.host._settings(ENV).storage_mode,"local")

    def test_duplicate_json_key_is_not_accepted(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"enabled":true,"enabled":false}', encoding="utf-8")
        with self.assertRaises(bindings.BindingError): self.host._settings(ENV)

    def test_missing_file_uses_global_defaults_and_revalidates(self):
        snapshot = dict(enabled=True, storage_mode="hybrid", connection=center_tests.settings())
        host = host_module.OfficialBindingHost(
            self.groups, lambda: prior.SELECTION, center_defaults=lambda: deepcopy(snapshot))
        settings = host._settings(ENV)
        self.assertEqual((settings.storage_mode, settings.server_modes), ("hybrid", {}))
        self.assertEqual(settings.connection["host"], center_tests.settings()["host"])
        self.assertTrue(settings.unchanged())
        snapshot["enabled"] = False
        self.assertFalse(settings.unchanged())

    def test_global_local_mode_needs_no_connection(self):
        host = host_module.OfficialBindingHost(
            self.groups, lambda: prior.SELECTION,
            center_defaults=lambda: {"enabled": True, "storage_mode": "local", "connection": None})
        settings = host._settings(ENV)
        self.assertEqual((settings.storage_mode, settings.connection), ("local", None))

    def test_invalid_explicit_file_never_falls_back_to_global_defaults(self):
        self.data["storage_mode"] = "unsupported"
        self.write()
        host = host_module.OfficialBindingHost(
            self.groups, lambda: prior.SELECTION,
            center_defaults=lambda: dict(enabled=True, storage_mode="hybrid",
                                         connection=center_tests.settings()))
        with self.assertRaises(bindings.BindingError):
            host._settings(ENV)


class RuntimeIngressTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_runtime_command_calls_worker_and_replayed_event_does_not_repeat(self):
        fixture = prior.RuntimeTests("test_setup_code_creates_group_and_help_only_before_activation")
        fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        self.addCleanup(fixture.store.close)
        runtime = fixture.make_runtime()
        self.addCleanup(runtime._original_store.close)
        _, group = fixture.approve()
        fixture.store.update(ENV, group["config_id"], group["revision"], {"enabled": True, "servers": [prior.server()]})
        calls = []
        def execute(scope, command, arguments):
            calls.append(command)
            return "Synthetic result", ["中心 COMMITTED；白名单 PENDING"]
        runtime.binding_commands = SimpleNamespace(execute=execute)
        event = prior.Event("/mc bind 123456 -s one")
        await runtime.handle(event)
        await runtime.handle(event)
        self.assertEqual(calls, ["bind"])
        self.assertEqual(len(event.sent), 1)
        self.assertIn("PENDING", event.sent[0][1])
        self.assertNotIn("123456", event.sent[0][1])
        self.assertFalse(runtime.state(ENV)["binding_ready"])

    async def test_official_chinese_binding_alias_preserves_explicit_server(self):
        # Extract one method only; never import the framework/plugin main module.
        tree = ast.parse((prior.ROOT / "main.py").read_text(encoding="utf-8-sig"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MCWatcher")
        method = deepcopy(next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "bind_command"))
        pattern = method.decorator_list[0].args[0].value
        self.assertIsNotNone(re.fullmatch(pattern, "/绑定 123456 -s one"))
        method.decorator_list = []
        calls = []
        async def handle(event):
            calls.append(event.message_str)
            if False: yield None
        namespace = {"AstrMessageEvent": prior.Event, "platform_activation": SimpleNamespace(accepts=lambda _: True),
                     "handle_mc_command": handle}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "<synthetic-alias>", "exec"), namespace)
        event = prior.Event("/绑定 123456 -s one")
        _ = [item async for item in namespace["bind_command"](None, event)]
        self.assertEqual(calls, ["/mc bind 123456 -s one"])
        old = prior.Event("/绑定 123456 -s one", platform="aiocqhttp")
        _ = [item async for item in namespace["bind_command"](None, old)]
        # 两个平台都支持显式选服，OneBot 不再丢弃 -s。
        self.assertEqual(calls, ["/mc bind 123456 -s one", "/mc bind 123456 -s one"])
