"""S2 隔离测试：只加载允许的纯源码，临时目录与虚构身份，无真实框架/网络。"""
import ast
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_mcwatch_synthetic_official"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "utils")]
sys.modules[PACKAGE] = package


def module(name):
    assert name in {"official_core", "official_queries", "official_runtime", "official_bindings", "official_center", "official_protocol", "official_binding_service", "official_binding_policy", "official_binding_host", "official_binding_commands", "official_binding_recovery", "official_binding_admin", "official_transport", "official_writes", "platform_activation", "debounce"}
    if PACKAGE + "." + name in sys.modules:
        return sys.modules[PACKAGE + "." + name]
    spec = importlib.util.spec_from_file_location(PACKAGE + "." + name, ROOT / "utils" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


core = module("official_core")
queries = module("official_queries")
runtime_module = module("official_runtime")
activation = module("platform_activation")
ENV = "11111111-1111-4111-8111-111111111111"
OTHER_ENV = "22222222-2222-4222-8222-222222222222"
GROUP = "SYNTHETIC_GROUP_A"
USER = "SYNTHETIC_USER_A"
SELECTION = {"platform": "qq_official", "instance_id": "official-a", "environment_id": ENV,
             "application_ref": "SYNTHETIC_APP", "config_revision": 1}


def forbidden(*args, **kwargs):
    raise AssertionError("must not enter old configuration, credential, data or network paths")


class Event:
    def __init__(self, message="/mc help", *, group=GROUP, user=USER, message_id="SYNTHETIC_MESSAGE_A", instance="official-a", platform="qq_official"):
        self.message_str, self.group, self.user = message, group, user
        self.instance, self.platform = instance, platform
        self.message_obj = SimpleNamespace(message_id=message_id, raw_message={
            "id": message_id, "group_openid": group, "author": {"member_openid": user, "member_nickname": "玩家（Player_A）", "role": "member"}})
        self.is_at_or_wake_command = True
        self.sent = []
        self.bot = None
    def get_platform_name(self): return self.platform
    def get_platform_id(self): return self.instance
    def get_group_id(self): return self.group
    def get_sender_id(self): return self.user
    get_sender_name = forbidden
    stop_event = forbidden
    send = forbidden


def scope(**kwargs):
    return core.event_scope(Event(**kwargs), SELECTION)


def server(name="one", **values):
    result = {"server_id": "", "name": name, "host": "mc.example.invalid", "game_port": 25565,
              "api_port": 8080, "hidden": False, "remark": "合成测试"}
    result.update(values)
    return result


class ScopeTests(unittest.TestCase):
    def test_both_transports_read_raw_fields_without_sender_name_or_member_api(self):
        for platform in ("qq_official", "qq_official_webhook"):
            event = Event(platform=platform)
            if platform.endswith("webhook"):
                event.message_obj.raw_message = SimpleNamespace(raw_data=event.message_obj.raw_message)
            parsed = core.event_scope(event, SELECTION)
            self.assertEqual((parsed.group_id, parsed.user_id, parsed.nickname), (GROUP, USER, "玩家（Player_A）"))

    def test_channel_c2c_unknown_and_mismatched_ids_rejected(self):
        events = []
        for key in ("channel_id", "guild_id", "openid"):
            e = Event(); e.message_obj.raw_message[key] = "SYNTHETIC_OTHER"; events.append(e)
        e = Event(); del e.message_obj.raw_message["group_openid"]; events.append(e)
        e = Event(); e.message_obj.raw_message["author"]["member_openid"] = "SYNTHETIC_OTHER"; events.append(e)
        events += [Event(instance="official-b"), Event(platform="aiocqhttp")]
        for event in events:
            with self.subTest(event=event):
                self.assertIsNone(core.event_scope(event, SELECTION))

    def test_username_and_unknown_role_do_not_grant_nickname_or_permission(self):
        event = Event()
        event.message_obj.raw_message["author"] = {"member_openid": USER, "username": "伪群昵称", "role": 4}
        parsed = core.event_scope(event, SELECTION)
        self.assertEqual(parsed.nickname, "")
        self.assertEqual(parsed.role, "member")


class StoreTests(unittest.TestCase):
    def setUp(self):
        module("debounce").reset()
        self.temp = tempfile.TemporaryDirectory(prefix="mcwatch-official-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.now = 1000000.0
        self.api_token = ""
        self.store = core.OfficialStore(self.temp.name, clock=lambda: self.now,
                                        token_provider=lambda: self.api_token)
        self.scope = scope()

    def approve(self):
        c = self.store.register(self.scope, "合成备注")
        group = self.store.approve(ENV, c["candidate_id"], self.store.list_state(ENV)["revision"], "测试群", "123456789")
        return c, group

    def test_setup_creates_only_expiring_candidate_not_group_or_authority(self):
        candidate = self.store.register(self.scope)
        state = self.store.list_state(ENV)
        self.assertEqual(state["groups"], [])
        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(candidate, self.store.register(self.scope))
        self.now += 86401
        self.assertEqual(self.store.list_state(ENV)["candidates"], [])

    def test_setup_code_is_hashed_single_use_and_creates_disabled_group(self):
        created = self.store.create_setup_code(ENV, "测试群", "123456789")
        self.assertRegex(created["code"], r"^[A-HJ-NP-Z2-9]{6}$")
        path = Path(self.temp.name) / ENV / "setup_codes" / (created["setup_id"] + ".json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn(created["code"], json.dumps(payload))
        self.assertNotIn("code", payload)
        self.assertEqual(self.store.setup_status(ENV, created["setup_id"])["status"], "pending")
        with self.assertRaises(core.OfficialError):
            self.store.claim_setup_code(ENV, "AAAAAA", GROUP)
        group, was_created = self.store.claim_setup_code(ENV, created["code"].lower(), GROUP)
        self.assertTrue(was_created)
        self.assertEqual(group["alias"], "测试群")
        self.assertEqual(group["display_group"], "123456789")
        self.assertFalse(group["enabled"])
        status = self.store.setup_status(ENV, created["setup_id"])
        self.assertEqual(status["status"], "claimed")
        self.assertEqual(status["config_id"], group["config_id"])
        with self.assertRaises(core.OfficialError):
            self.store.claim_setup_code(ENV, created["code"], GROUP)

    def test_setup_code_expiry_cancel_and_existing_group(self):
        expired = self.store.create_setup_code(ENV, "过期群")
        self.now += core.SETUP_TTL + 1
        self.assertEqual(self.store.setup_status(ENV, expired["setup_id"])["status"], "expired")
        with self.assertRaises(core.OfficialError):
            self.store.claim_setup_code(ENV, expired["code"], GROUP)
        cancelled = self.store.create_setup_code(ENV, "取消群")
        self.store.cancel_setup_code(ENV, cancelled["setup_id"])
        self.assertEqual(self.store.setup_status(ENV, cancelled["setup_id"])["status"], "missing")
        first = self.store.create_setup_code(ENV, "正式群")
        group, created = self.store.claim_setup_code(ENV, first["code"], GROUP)
        self.assertTrue(created)
        second = self.store.create_setup_code(ENV, "重复群")
        again, created = self.store.claim_setup_code(ENV, second["code"], GROUP)
        self.assertFalse(created)
        self.assertEqual(again["config_id"], group["config_id"])
        self.assertEqual(again["alias"], "正式群")
        self.assertEqual(self.store.setup_status(ENV, second["setup_id"])["status"], "claimed")

    def test_setup_alias_width_and_display_group_digits(self):
        # 8 个中文和 16 个英文都恰好达到上限；再长一位就应拒绝。
        self.store.create_setup_code(ENV, "中文中文中文中文", "123456789")
        self.store.create_setup_code(ENV, "abcdefghijklmnop")
        with self.assertRaises(core.OfficialError):
            self.store.create_setup_code(ENV, "中文中文中文中文中")
        with self.assertRaises(core.OfficialError):
            self.store.create_setup_code(ENV, "abcdefghijklmnopq")
        with self.assertRaises(core.OfficialError):
            self.store.create_setup_code(ENV, "测试群", "12ab")
        with self.assertRaises(core.OfficialError):
            self.store.create_setup_code(ENV, "测试群", "１２３")

    def test_new_group_inherits_official_policy_defaults(self):
        self.store.policy_defaults = lambda: {
            "enable_group_owner": False, "enable_group_admin": False,
            "show_server_latency": False, "show_server_version": "yes", "unknown": True,
        }
        created = self.store.create_setup_code(ENV, "默认群")
        group, was_created = self.store.claim_setup_code(ENV, created["code"], GROUP)
        self.assertTrue(was_created)
        policy = group["policy"]
        self.assertFalse(policy["enable_group_owner"])
        self.assertFalse(policy["enable_group_admin"])
        self.assertFalse(policy["show_server_latency"])
        # 非布尔值和未知键不能穿透到群策略。
        self.assertFalse(policy["show_server_version"])
        self.assertNotIn("unknown", policy)
        # 已有群的策略不随后续全局默认值变化。
        self.store.policy_defaults = lambda: {"enable_group_owner": True}
        latest = self.store.list_state(ENV)["groups"][0]
        self.assertFalse(latest["policy"]["enable_group_owner"])

    def test_v1_group_policy_is_migrated(self):
        _, group = self.approve()
        path = Path(self.temp.name) / ENV / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["schema_version"] = 1
        del state["groups"][0]["policy"]["use_central_db"]
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        loaded = self.store.group(ENV, group_id=GROUP)
        self.assertEqual(loaded["config_id"], group["config_id"])
        self.assertFalse(loaded["policy"]["use_central_db"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)

    def test_v2_group_without_required_policy_key_is_rejected(self):
        self.approve()
        path = Path(self.temp.name) / ENV / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        del state["groups"][0]["policy"]["use_central_db"]
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(core.OfficialError):
            self.store.group(ENV, group_id=GROUP)

    def test_group_and_members_limits_do_not_grant_privileges(self):
        for i in range(20):
            self.store.register(scope(user=f"SYNTHETIC_USER_{i}"))
        with self.assertRaises(core.OfficialError): self.store.register(scope(user="EXCESS"))
        self.assertEqual(self.store.list_state(ENV)["groups"], [])

    def test_openid_never_becomes_path_and_other_environment_stays_empty(self):
        strange = scope(group="../../SYNTHETIC_GROUP")
        self.store.register(strange)
        paths = [str(p.relative_to(self.temp.name)) for p in Path(self.temp.name).rglob("*.json")]
        self.assertEqual(paths, [str(Path(ENV) / "state.json")])
        self.assertEqual(self.store.list_state(OTHER_ENV)["groups"], [])
        with self.assertRaises(core.OfficialError): self.store.list_state("../outside")

    def test_approval_does_not_enable_or_authorize_and_revision_is_required(self):
        candidate, group = self.approve()
        self.assertFalse(group["enabled"])
        self.assertEqual(group["administrators"], [])
        with self.assertRaises(core.OfficialError):
            self.store.update(ENV, group["config_id"], 0, {"enabled": True})
        updated = self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server()]})
        saved_id = updated["servers"][0]["server_id"]
        updated["servers"][0]["name"] = "renamed"
        next_group = self.store.update(ENV, group["config_id"], 2, {"servers": updated["servers"]})
        self.assertEqual(next_group["servers"][0]["server_id"], saved_id)

    def test_delete_group_requires_matching_revision_and_keeps_other_groups(self):
        _, group = self.approve()
        updated = self.store.update(ENV, group["config_id"], group["revision"], {"servers": [server()]})
        token_path = Path(self.temp.name) / ENV / "console_tokens" / (updated["servers"][0]["server_id"] + ".json")
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text("{}", encoding="utf-8")
        other_candidate = self.store.register(scope(group="SYNTHETIC_GROUP_B", user="SYNTHETIC_USER_B"))
        other = self.store.approve(ENV, other_candidate["candidate_id"],
                                   self.store.list_state(ENV)["revision"], "第二群", "")
        with self.assertRaises(core.OfficialError):
            self.store.delete_group(ENV, group["config_id"], 0)
        removed = self.store.delete_group(ENV, group["config_id"], updated["revision"])
        self.assertEqual(removed["config_id"], group["config_id"])
        state = self.store.list_state(ENV)
        self.assertEqual([g["config_id"] for g in state["groups"]], [other["config_id"]])
        self.assertIsNone(self.store.group(ENV, config_id=group["config_id"]))
        self.assertFalse(token_path.exists())
        self.assertFalse(any(c["group_openid"] == GROUP for c in state["candidates"]))

    def test_console_token_is_per_server_and_cannot_equal_api_token(self):
        _, group = self.approve()
        updated = self.store.update(ENV, group["config_id"], group["revision"], {"servers": [server()]})
        server_id = updated["servers"][0]["server_id"]
        token = "SYNTHETIC_CONSOLE_TOKEN_123"
        with self.assertRaises(core.OfficialError):
            self.store.save_console_token(ENV, group["config_id"], server_id, 0, token)
        with self.assertRaises(core.OfficialError):
            self.store.save_console_token(ENV, group["config_id"], server_id, updated["revision"], "too-short")
        with self.assertRaises(core.OfficialError):
            self.store.save_console_token(ENV, group["config_id"], OTHER_ENV, updated["revision"], token)
        self.store.save_console_token(ENV, group["config_id"], server_id, updated["revision"], token)
        self.assertEqual(self.store.console_token_ids(ENV), [server_id])
        self.api_token = "SYNTHETIC_API_TOKEN_123456"
        self.assertTrue(self.store.token_is_set(ENV))
        with self.assertRaises(core.OfficialError):
            self.store.save_console_token(ENV, group["config_id"], server_id, updated["revision"],
                                          "SYNTHETIC_API_TOKEN_123456")
        self.store.save_console_token(ENV, group["config_id"], server_id, updated["revision"], "")
        self.assertEqual(self.store.console_token_ids(ENV), [])
        self.assertFalse((Path(self.temp.name) / ENV / "console_tokens" / (server_id + ".json")).exists())

    def test_removing_server_deletes_its_console_token(self):
        _, group = self.approve()
        updated = self.store.update(ENV, group["config_id"], group["revision"], {"servers": [server()]})
        server_id = updated["servers"][0]["server_id"]
        self.store.save_console_token(ENV, group["config_id"], server_id, updated["revision"],
                                      "SYNTHETIC_CONSOLE_TOKEN_123")
        self.assertEqual(self.store.console_token_ids(ENV), [server_id])
        self.store.update(ENV, group["config_id"], updated["revision"], {"servers": []})
        self.assertEqual(self.store.console_token_ids(ENV), [])
        self.assertFalse((Path(self.temp.name) / ENV / "console_tokens" / (server_id + ".json")).exists())

    def test_server_remark_source_and_motd_cache_are_per_server_and_cleaned_on_removal(self):
        _, group = self.approve()
        updated = self.store.update(ENV, group["config_id"], group["revision"], {"servers": [server()]})
        server_id = updated["servers"][0]["server_id"]
        self.assertEqual(self.store.server_remark(ENV, server_id),
                         {"remark_src": "manual", "remark_motd": "", "remark_motd_at": 0})
        with self.assertRaises(core.OfficialError):
            self.store.save_server_remark(ENV, group["config_id"], server_id, 0, remark_src="motd")
        with self.assertRaises(core.OfficialError):
            self.store.save_server_remark(ENV, group["config_id"], server_id, updated["revision"],
                                          remark_src="other")
        saved = self.store.save_server_remark(ENV, group["config_id"], server_id, updated["revision"],
                                              remark_src="motd", remark_motd="SYNTHETIC_MOTD")
        self.assertEqual(saved["remark_src"], "motd")
        self.assertEqual(saved["remark_motd"], "SYNTHETIC_MOTD")
        self.assertEqual(saved["remark_motd_at"], int(self.now))
        self.now += 60
        kept = self.store.save_server_remark(ENV, group["config_id"], server_id, updated["revision"],
                                             remark_src="manual")
        self.assertEqual(kept["remark_motd"], "SYNTHETIC_MOTD")
        self.assertEqual(kept["remark_motd_at"], int(self.now - 60))
        path = Path(self.temp.name) / ENV / "remarks" / (server_id + ".json")
        self.assertTrue(path.exists())
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(self.store.server_remark(ENV, server_id)["remark_src"], "manual")
        self.store.update(ENV, group["config_id"], updated["revision"], {"servers": []})
        self.assertFalse(path.exists())

    def test_new_server_cannot_impersonate_existing_external_identity(self):
        _, group = self.approve()
        with self.assertRaises(core.OfficialError):
            self.store.update(ENV, group["config_id"], 1, {"servers": [server(server_id=OTHER_ENV)]})

    def test_authorization_requires_same_group_candidate_and_blacklist_wins(self):
        candidate, group = self.approve()
        other = self.store.register(scope(group="SYNTHETIC_GROUP_B"))
        with self.assertRaises(core.OfficialError):
            self.store.authorize(ENV, group["config_id"], other["candidate_id"], "environment_admin", 1)
        authorized = self.store.authorize(ENV, group["config_id"], candidate["candidate_id"], "group_admin", 1)
        self.assertEqual(core.permission(self.scope, authorized), 3)
        blocked = self.store.authorize(ENV, group["config_id"], candidate["candidate_id"], "blacklist", 2)
        self.assertEqual(core.permission(self.scope, blocked), 0)
        revoked = self.store.revoke(ENV, group["config_id"], USER, 3, "blacklist")
        self.assertEqual(core.permission(self.scope, revoked), 3)
        self.assertNotIn("bindings", self.store.list_state(ENV))

    def test_public_lists_apply_to_official_openids_and_global_blacklist_wins(self):
        _, group = self.approve()
        self.store.access_provider = lambda: {
            "super_admins": f"SYNTHETIC_USER_B,{USER}", "whitelist_admins": [], "blacklist": []}
        self.assertEqual(self.store.access_level(self.scope, group), 4)
        self.store.access_provider = lambda: {
            "super_admins": [USER], "whitelist_admins": [USER], "blacklist": [USER]}
        self.assertEqual(self.store.access_level(self.scope, group), 0)
        self.store.access_provider = lambda: {
            "super_admins": [], "whitelist_admins": [USER], "blacklist": []}
        self.assertEqual(self.store.access_level(self.scope, group), 3)

    def test_official_access_overlay_is_environment_scoped_and_fails_closed(self):
        public = {"super_admins": [], "whitelist_admins": [], "blacklist": []}
        self.store.access_provider = lambda: public
        target = "SYNTHETIC_TARGET_A"
        self.assertTrue(self.store.update_official_access(ENV, "whitelist_admins", target, True))
        self.assertFalse(self.store.update_official_access(ENV, "whitelist_admins", target, True))
        self.assertIn(target, self.store.access_lists(ENV)["whitelist_admins"])
        self.assertNotIn(target, self.store.access_lists(OTHER_ENV)["whitelist_admins"])
        self.assertEqual(public["whitelist_admins"], [])
        path = Path(self.temp.name) / ENV / "access.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)
        self.assertTrue(self.store.update_official_access(ENV, "whitelist_admins", target, False))
        self.assertNotIn(target, self.store.access_lists(ENV)["whitelist_admins"])
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(core.OfficialError):
            self.store.access_lists(ENV)

    def test_environment_administrator_is_environment_wide_and_blacklist_still_wins(self):
        candidate, group = self.approve()
        other_candidate = self.store.register(scope(group="SYNTHETIC_GROUP_B", user="SYNTHETIC_USER_B"))
        other = self.store.approve(ENV, other_candidate["candidate_id"], self.store.list_state(ENV)["revision"], "第二群", "")
        data = self.store.list_state(ENV)
        self.store.authorize(ENV, group["config_id"], candidate["candidate_id"], "environment_admin", data["revision"])
        state = self.store.list_state(ENV)
        self.assertIn(USER, state["environment_administrators"])
        self.assertEqual(core.permission(self.scope, other, state["environment_administrators"]), 4)
        self.store.register(scope(group="SYNTHETIC_GROUP_B", user=USER))
        identity = next(c for c in self.store.list_state(ENV)["candidates"]
                        if c["group_openid"] == "SYNTHETIC_GROUP_B" and c["member_openid"] == USER)
        self.store.authorize(ENV, other["config_id"], identity["candidate_id"], "blacklist", other["revision"])
        state = self.store.list_state(ENV)
        self.assertEqual(core.permission(self.scope, self.store.group(ENV, config_id=other["config_id"]),
                                         state["environment_administrators"]), 0)
        self.store.revoke(ENV, other["config_id"], USER, state["revision"], "environment_admin")
        self.assertNotIn(USER, self.store.list_state(ENV)["environment_administrators"])

    def test_failed_atomic_replace_retains_config_and_revision(self):
        _, group = self.approve()
        before = self.store.list_state(ENV)
        with patch.object(core.os, "replace", side_effect=OSError("synthetic error")):
            with self.assertRaises(core.OfficialError): self.store.update(ENV, group["config_id"], 1, {"enabled": True})
        self.assertEqual(self.store.list_state(ENV), before)
        self.assertEqual(list(Path(self.temp.name).rglob("*.tmp")), [])

    def test_corruption_never_silently_overwrites_existing_config(self):
        self.approve()
        path = Path(self.temp.name) / ENV / "state.json"
        path.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(core.OfficialError): self.store.register(self.scope)
        self.assertEqual(path.read_bytes(), before)

    def test_normal_state_does_not_read_or_store_secret_file(self):
        self.approve()
        self.api_token = "SYNTHETIC_NOT_A_REAL_TOKEN"
        original = core.read_json
        def guarded(path, *args, **kwargs):
            if Path(path).name == "mod_token.json": forbidden()
            return original(path, *args, **kwargs)
        with patch.object(core, "read_json", side_effect=guarded):
            self.assertTrue(self.store.token_is_set(ENV))
            self.assertEqual(self.store.runtime_token(ENV), "SYNTHETIC_NOT_A_REAL_TOKEN")
            self.assertNotIn("mod_api_token", json.dumps(self.store.list_state(ENV)))
        self.assertFalse((Path(self.temp.name) / ENV / "mod_token.json").exists())

    def test_policy_and_server_inputs_are_strict(self):
        _, group = self.approve()
        for changes in ({"administrators": [USER]}, {"enabled": "true"}, {"servers": [server(host="http://bad/")]},
                        {"servers": [server(api_port=True)]}, {"servers": [server(token="SHOULD_REJECT")]}, {"policy": {}}):
            with self.subTest(changes=changes), self.assertRaises(core.OfficialError):
                self.store.update(ENV, group["config_id"], 1, changes)


class PaginationTests(unittest.TestCase):
    def test_scope_revision_and_expiry_prevent_cross_user_leaks(self):
        now = [0]
        cache = core.PageCache(clock=lambda: now[0], max_bytes=10, max_entries=2, ttl=10)
        a = scope(); b = scope(user="SYNTHETIC_USER_B")
        cache.put(a, 1, [b"abc", b"def"])
        self.assertEqual(cache.get(a, 1, 2), (b"def", 2))
        with self.assertRaises(core.OfficialError): cache.get(b, 1, 1)
        with self.assertRaises(core.OfficialError): cache.get(a, 2, 1)
        cache.put(a, 2, [b"123"])
        now[0] = 11
        with self.assertRaises(core.OfficialError): cache.get(a, 2, 1)

    def test_capacity_eviction_is_bounded_and_latest_query_replaces_old(self):
        cache = core.PageCache(max_bytes=8, max_entries=2)
        a, b = scope(), scope(user="USER_B")
        cache.put(a, 1, [b"1111"])
        cache.put(b, 1, [b"22222"])
        with self.assertRaises(core.OfficialError): cache.get(a, 1, 1)
        cache.put(b, 1, [b"new"])
        self.assertEqual(cache.get(b, 1, 1)[0], b"new")
        with self.assertRaises(core.OfficialError): cache.put(a, 1, [b"123456789"])

    def test_text_fallback_pagination_is_bounded_and_preserves_all_rows(self):
        lines = [f"ROW-{i:03d} " + "合成测试" * 120 for i in range(40)]
        pages = queries.render_text_pages("测试标题", lines)
        self.assertTrue(all(len(page) <= 1800 for page in pages))
        for i in range(40):
            self.assertIn(f"ROW-{i:03d}", "\n".join(pages))


class EventReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcwatch-event-synthetic-")
        self.addCleanup(self.temp.cleanup)

    def test_claim_and_request_budget_survive_restart_without_saving_raw_identity(self):
        first = runtime_module.ReplyLedger(self.temp.name)
        record = first.claim(scope())
        record["calls"] += 1
        record["sequence"] += 1
        second = runtime_module.ReplyLedger(self.temp.name)
        self.assertIsNone(second.claim(scope()))
        self.assertIsNotNone(second.claim(scope(message_id="NEW_SYNTHETIC_EVENT")))
        body = (Path(self.temp.name) / ENV / "event_receipts.json").read_text(encoding="utf-8")
        self.assertNotIn(USER, body)
        self.assertNotIn(GROUP, body)
        self.assertNotIn("SYNTHETIC_MESSAGE_A", body)
        self.assertTrue(any(e["calls"] == 1 and e["sequence"] == 1 for e in json.loads(body)["events"].values()))

    def test_failed_durable_claim_cannot_dispatch_or_overwrite_existing_claims(self):
        ledger = runtime_module.ReplyLedger(self.temp.name)
        ledger.claim(scope())
        path = Path(self.temp.name) / ENV / "event_receipts.json"
        before = path.read_bytes()
        with patch.object(core.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(core.OfficialError): ledger.claim(scope(message_id="NEW_SYNTHETIC_EVENT"))
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(ledger.claim(scope()))

    def test_corrupt_receipt_storage_stops_claim_without_replacing_data(self):
        path = Path(self.temp.name) / ENV / "event_receipts.json"
        path.parent.mkdir()
        path.write_bytes(b'{"schema_version":1,"schema_version":2}')
        ledger = runtime_module.ReplyLedger(self.temp.name)
        with self.assertRaises(core.OfficialError): ledger.claim(scope())
        self.assertEqual(path.read_bytes(), b'{"schema_version":1,"schema_version":2}')
        self.assertFalse(ledger.health(ENV)["ready"])


class QueryTests(unittest.IsolatedAsyncioTestCase):
    def group(self, servers): return {"servers": servers, "policy": core.default_policy()}

    async def test_status_falls_back_without_reading_binding_state_and_skips_hidden(self):
        calls = []
        async def mod(s, token, endpoint):
            calls.append((s["name"], endpoint)); raise core.OfficialError("synthetic unavailable")
        async def status(s):
            calls.append((s["name"], "mcstatus")); return {"online_players": 2, "max_players": 20, "players": []}
        service = queries.QueryService(mod=mod, status=status)
        title, lines = await service.query("status", "", self.group([server(), server("hidden", hidden=True)]), "SYNTHETIC")
        self.assertEqual(calls, [("one", "/api/status"), ("one", "mcstatus")])
        self.assertIn("mcstatus", " ".join(lines))

    async def test_rank_uses_game_ticks_and_public_data_only(self):
        async def mod(s, token, endpoint):
            self.assertEqual(endpoint, "/api/stats/all")
            return {"players": [{"name": "Player_A", "stats": {"play_time": 72000}, "last_seen": 1000000}], "truncated": True}
        service = queries.QueryService(mod=mod)
        _, lines = await service.query("rank", "时长 总", self.group([server()]), "SYNTHETIC")
        self.assertIn("1.0 小时", " ".join(lines))
        self.assertIn("截断", " ".join(lines))

    async def test_multiple_rank_servers_require_explicit_target_before_network(self):
        service = queries.QueryService(mod=forbidden)
        with self.assertRaises(core.OfficialError):
            await service.query("rank", "时长", self.group([server(), server("two")]), "")

    async def test_stats_remains_public_and_query_errors_are_not_no_binding(self):
        async def mod(s, token, endpoint):
            self.assertEqual(endpoint, "/api/stats/AnotherPlayer")
            return {"deaths": 4, "mobKills": 10}
        _, lines = await queries.QueryService(mod=mod).query("stats", "AnotherPlayer", self.group([server()]), "SYNTHETIC")
        self.assertIn("死亡次数：4", lines)

    async def test_server_selection_matches_onebot_forms_and_hidden_rule(self):
        calls = []
        async def mod(s, token, endpoint):
            calls.append((s["name"], endpoint))
            if endpoint.startswith("/api/stats/"):
                return {"playTime": 72000}
            if endpoint == "/api/tps":
                return {"tps": 20}
            return {"online_players": 1, "max_players": 20, "players": []}
        service = queries.QueryService(mod=mod)
        group = self.group([server(), server("two"), server("hidden", hidden=True)])

        status = await service.query("status", "-s two", group, "SYNTHETIC")
        self.assertEqual([card["name"] for card in status.data], ["two"])
        self.assertEqual(calls, [("two", "/api/status")])
        calls.clear()
        with self.assertRaises(core.OfficialError):
            await service.query("status", "two", group, "SYNTHETIC")
        with self.assertRaises(core.OfficialError):
            await service.query("status", "-s hidden", group, "SYNTHETIC")
        with self.assertRaises(core.OfficialError):
            await service.query("status", "-s missing", group, "SYNTHETIC")
        self.assertEqual(calls, [])

        tps = await service.query("tps", "two", group, "SYNTHETIC")
        self.assertEqual(calls, [("two", "/api/tps")])
        self.assertEqual(tps.data[0]["name"], "two")
        calls.clear()

        await service.query("stats", "Player_A -s two", group, "SYNTHETIC")
        self.assertEqual(calls, [("two", "/api/stats/Player_A")])
        calls.clear()
        await service.query("stats", "Player_A two", group, "SYNTHETIC")
        self.assertEqual(calls, [("two", "/api/stats/Player_A")])
        calls.clear()
        with self.assertRaises(core.OfficialError):
            await service.query("stats", "Player_A -s one two", group, "SYNTHETIC")
        with self.assertRaises(core.OfficialError):
            await service.query("stats", "Player_A -s hidden", group, "SYNTHETIC")
        self.assertEqual(calls, [])

        with self.assertRaises(core.OfficialError):
            await service.query("tps", "", self.group([server("hidden", hidden=True)]), "SYNTHETIC")

    async def test_status_and_rank_documents_carry_shared_renderer_data(self):
        """官方查询要交出与 OneBot 生成器同构的数据，才能渲染出一样的图片。"""
        async def mod(s, token, endpoint):
            if endpoint == "/api/status":
                return {"online_players": 2, "max_players": 20, "version": "1.20.1",
                        "players": [{"name": "Player_A", "uuid": "uuid-a", "is_premium": True}]}
            if endpoint == "/api/tps":
                return {"tps": 19.5}
            if endpoint == "/api/stats/all":
                return {"players": [{"name": "Player_A", "stats": {"play_time": 72000},
                                     "last_seen": 10**13}]}
            raise AssertionError(endpoint)
        service = queries.QueryService(mod=mod)
        group = self.group([server()])

        status = await service.query("status", "", group, "SYNTHETIC")
        self.assertIsInstance(status, queries.QueryDocument)
        self.assertEqual(status.kind, "status")
        card = status.data[0]
        self.assertEqual(card["name"], "one")
        self.assertEqual(card["online"], 2)
        self.assertEqual(card["max"], 20)
        self.assertEqual(card["remark"], "合成测试")
        self.assertEqual([p["name"] for p in card["players"]], ["Player_A"])
        title, lines = status
        self.assertEqual(title, "服务器在线状态")

        tps = await service.query("tps", "", group, "SYNTHETIC")
        self.assertEqual(tps.kind, "tps")
        self.assertEqual(tps.data[0]["tps"], 19.5)

        rank = await service.query("rank", "时长 总", group, "SYNTHETIC")
        self.assertEqual(rank.kind, "rank")
        self.assertEqual(rank.data["title"], "排行榜")
        self.assertTrue(rank.data["rows"])
        self.assertEqual(rank.data["rows"][0]["name"], "Player_A")


class WriteParseTests(unittest.TestCase):
    """官方写命令与 OneBot 共用同一套主机校验和选服语法。"""
    def setUp(self):
        self.writes = module("official_writes")

    def group(self, servers):
        return {"servers": servers, "policy": core.default_policy(),
                "nickname_ignore": [], "revision": 1}

    def test_add_and_edit_normalize_ports_and_reject_private_hosts(self):
        group = self.group([server()])
        parse = self.writes.OfficialWriteCommands._parse
        with self.assertRaises(core.OfficialError):
            parse("add", "two 127.0.0.1", group, allow_private=False)
        command, _, _, changes = parse("add", "two 127.0.0.1 : 25566 8080", group,
                                       allow_private=True)
        added = changes["servers"][-1]
        self.assertEqual(command, "add")
        self.assertEqual((added["host"], added["game_port"], added["api_port"]),
                         ("127.0.0.1", 25566, 8080))
        _, targets, _, changes = parse("edit", "one host mc.example.invalid : 25570", group,
                                       allow_private=False)
        self.assertEqual(targets[0]["host"], "mc.example.invalid")
        self.assertEqual(targets[0]["game_port"], 25570)
        with self.assertRaises(core.OfficialError):
            parse("batchadd", "two:10.0.0.8", group, allow_private=False)

    def test_broadcast_and_console_accept_onebot_selector_forms(self):
        group = self.group([server(), server("two")])
        parse = self.writes.OfficialWriteCommands._parse
        _, targets, payload, changes = parse("broadcast", "one hello", group)
        self.assertIsNone(changes)
        self.assertEqual((targets[0]["name"], payload), ("one", "hello"))
        _, targets, payload, _ = parse("broadcast", "hello", group)
        self.assertEqual([s["name"] for s in targets], ["one", "two"])
        self.assertEqual(payload, "hello")
        _, targets, payload, _ = parse("console", "[two] say hi", group)
        self.assertEqual((targets[0]["name"], payload), ("two", "say hi"))
        with self.assertRaises(core.OfficialError):
            parse("broadcast", "x" * 41, group)
        with self.assertRaises(core.OfficialError):
            parse("console", "say hi", group)

    def test_hide_show_and_del_match_onebot(self):
        group = self.group([server(), server("two")])
        parse = self.writes.OfficialWriteCommands._parse
        _, targets, _, changes = parse("hide", "two", group)
        self.assertTrue(changes["servers"][1]["hidden"])
        _, targets, _, changes = parse("show", "two", group)
        self.assertFalse(changes["servers"][1]["hidden"])
        _, targets, _, changes = parse("del", "one", group)
        self.assertEqual([s["name"] for s in changes["servers"]], ["two"])

    def test_name_and_remark_limits_match_onebot(self):
        parse = self.writes.OfficialWriteCommands._parse
        for arguments in ("x" * 21 + " mc.example.invalid",
                          "a" * 10 + "中" * 6 + " mc.example.invalid",
                          "[bad] mc.example.invalid"):
            with self.subTest(arguments=arguments), self.assertRaises(core.OfficialError):
                parse("add", arguments, self.group([]))
        with self.assertRaises(core.OfficialError):
            parse("batchadd", "x" * 21 + ":mc.example.invalid", self.group([]))
        with self.assertRaises(core.OfficialError):
            parse("edit", "one name " + "x" * 21, self.group([server()]))
        with self.assertRaises(core.OfficialError):
            parse("edit", "one name has space", self.group([server()]))
        with self.assertRaises(core.OfficialError):
            parse("edit", "one remark " + "字" * 26, self.group([server()]))
        parse("add", "x" * 20 + " mc.example.invalid", self.group([]))
        _, targets, _, _ = parse("edit", "one remark " + "字" * 25, self.group([server()]))
        self.assertEqual(targets[0]["remark"], "字" * 25)

    def test_add_duplicate_and_capacity_errors_match_onebot(self):
        parse = self.writes.OfficialWriteCommands._parse
        with self.assertRaises(core.OfficialError) as caught:
            parse("add", "one mc.example.invalid", self.group([server()]))
        self.assertIn("名称「one」已存在", str(caught.exception))
        full = self.group([server(f"s{i}") for i in range(20)])
        with self.assertRaises(core.OfficialError) as caught:
            parse("add", "extra mc.example.invalid", full)
        self.assertIn("每个群最多绑定 20 个服务器", str(caught.exception))
        with self.assertRaises(core.OfficialError):
            parse("batchadd", "extra:mc.example.invalid", full)
        with self.assertRaises(core.OfficialError) as caught:
            parse("batchadd", "two:mc.example.invalid,two:mc2.example.invalid",
                  self.group([]))
        self.assertIn("名称「two」已存在", str(caught.exception))
        with self.assertRaises(core.OfficialError) as caught:
            parse("edit", "one name two", self.group([server(), server("two")]))
        self.assertIn("名称「two」已存在", str(caught.exception))

    def test_config_success_title_matches_onebot(self):
        title = self.writes.OfficialWriteCommands._config_title
        done = {"state": "SUCCEEDED"}
        self.assertEqual(title("add", "two host", done),
                         "✅ 添加成功：two\n主机：host\n模组 API 端口：未配置（/执行、/广播 不可用）")
        self.assertEqual(title("batchadd", "two:host,three:host2 8080", done),
                         "批量添加：成功 2 个")
        self.assertEqual(title("batchremove", "two,three", done),
                         "批量删除：成功 2 个")
        self.assertEqual(title("del", "two", done), "删除成功：two")
        self.assertEqual(title("hide", "two", done),
                         "✅ 已隐藏「two」，它不会再出现在查询和状态卡片中。")
        self.assertEqual(title("edit", "one host x", done), "修改主机地址成功。")
        self.assertEqual(title("nicknamecheck", "忽略 user X", done),
                         "✅ 已更新昵称检测忽略名单。")
        self.assertEqual(title("hide", "two", {"state": "UNKNOWN"}),
                         "官方服务器配置结果")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    setUp = StoreTests.setUp
    approve = StoreTests.approve
    def make_runtime(self):
        class Activation:
            @staticmethod
            def status(): return {"state": "configuring", "selection": SELECTION, "environments": [{"environment_id": ENV, "platform": "qq_official"}]}
            @staticmethod
            def accepts(event): return core.event_scope(event, SELECTION) is not None
        class Sender:
            def __init__(self, event, scoped, record): self.event, self.record = event, record
            async def text(self, value): self.record["calls"] += 1; self.event.sent.append(("text", value))
            async def image(self, value): self.record["calls"] += 2; self.event.sent.append(("image", value))
            async def markdown(self, content, rows): self.record["calls"] += 1; self.event.sent.append(("markdown", (content, rows)))
        async def query(*args): return "构造标题", ["构造结果"]
        runtime = runtime_module.OfficialRuntime(self.temp.name, Activation(),
                    queries=SimpleNamespace(query=query), renderer=lambda *a: [b"page1", b"page2"], sender=Sender)
        runtime.store = self.store
        return runtime

    async def test_setup_code_creates_group_and_help_only_before_activation(self):
        runtime = self.make_runtime()
        event = Event("/mc setup")
        await runtime.handle(event)
        self.assertIn("生成验证码", event.sent[0][1])
        self.assertEqual(runtime.store.list_state(ENV)["groups"], [])
        bad = Event("/mc setup ABCDEF", message_id="EVENT_SETUP_BAD")
        await runtime.handle(bad)
        self.assertIn("验证码无效或已过期", bad.sent[0][1])
        created = self.store.create_setup_code(ENV, "测试群")
        event = Event(f"/mc setup {created['code'].lower()}", message_id="EVENT_SETUP_OK")
        await runtime.handle(event)
        self.assertIn("已创建本群配置「测试群」", event.sent[0][1])
        groups = runtime.store.list_state(ENV)["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["alias"], "测试群")
        again = Event(f"/mc setup {created['code']}", message_id="EVENT_SETUP_AGAIN")
        await runtime.handle(again)
        self.assertIn("验证码无效或已过期", again.sent[0][1])
        other = Event("/mc status", message_id="EVENT_B")
        runtime.queries = SimpleNamespace(query=forbidden)
        await runtime.handle(other)
        self.assertIn("本群功能已关闭", other.sent[0][1])

    async def test_registered_but_disabled_group_gets_onebot_closed_message(self):
        runtime = self.make_runtime()
        created = self.store.create_setup_code(ENV, "测试群")
        self.store.claim_setup_code(ENV, created["code"], GROUP)
        event = Event("/mc status", message_id="EVENT_DISABLED")
        await runtime.handle(event)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.sent[0][0], "text")
        self.assertIn("本群功能已关闭", event.sent[0][1])
        self.assertNotIn("/mc setup", event.sent[0][1])

    async def test_official_binding_help_does_not_reuse_onebot_binding_steps(self):
        runtime = self.make_runtime()
        event = Event("/mc bindhelp")
        await runtime.handle(event)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.sent[0][0], "text")
        self.assertIn("开放绑定", event.sent[0][1])
        self.assertIn("旧 OneBot", event.sent[0][1])
        self.assertNotIn("请发送 /绑定", event.sent[0][1])

    async def test_help_rendering_failure_still_sends_full_help_text(self):
        runtime = self.make_runtime()
        def fails(*_): raise ImportError("synthetic absent Pillow")
        runtime.renderer = fails
        event = Event("/mc help")
        await runtime.handle(event)
        self.assertEqual(event.sent, [("text", runtime_module.HELP)])

    async def test_unknown_help_image_send_is_not_retried_as_text(self):
        runtime = self.make_runtime()
        runtime.renderer = lambda *_: [b"synthetic_png"]
        calls = []
        async def upload(**kwargs):
            calls.append(kwargs)
            raise TimeoutError("synthetic timeout")
        event = Event("/mc help")
        event.bot = SimpleNamespace(api=SimpleNamespace(post_group_base64file=upload, post_group_message=forbidden))
        runtime.sender = runtime_module.OfficialSender
        await runtime.handle(event)
        self.assertEqual(len(calls), 1)
        self.assertEqual(event.sent, [])

    async def test_help_and_bindhelp_use_official_images_under_default_renderer(self):
        runtime = self.make_runtime()
        runtime.renderer = runtime_module.render_pages
        kinds = []
        def official(kind="help"):
            kinds.append(kind)
            return ("official_" + kind).encode()
        with patch.object(runtime_module, "render_official_help_image", official):
            help_event = Event("/mc help", message_id="EVENT_HELP_IMAGE")
            await runtime.handle(help_event)
            self.assertEqual(help_event.sent, [("image", b"official_help")])
            bind_event = Event("/mc bindhelp", message_id="EVENT_BINDHELP_IMAGE")
            await runtime.handle(bind_event)
            self.assertEqual(bind_event.sent, [("image", b"official_bind")])
        self.assertEqual(kinds, ["help", "bind"])

    async def test_guide_page_one_uses_command_buttons_not_callbacks(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1,
                          {"enabled": True, "servers": [server()]})
        event = Event("/mc guide")
        await runtime.handle(event)
        self.assertEqual(len(event.sent), 1)
        kind, payload = event.sent[0]
        self.assertEqual(kind, "markdown")
        content, rows = payload
        self.assertEqual(content,
                         "# MCSight · 命令指引\n"
                         "> 点击按钮只会填入命令，发送后执行\n\n"
                         "***\n\n"
                         "在线状态、TPS、玩家统计、排行榜、账号查询与帮助入口。"
                         "多服务器时，排行榜需用 -s 指定服务器。")
        self.assertEqual(len(rows), 5)
        labels, data, ids = [], [], set()
        for row in rows:
            self.assertIn("buttons", row)
            self.assertEqual(len(row["buttons"]), 2)
            row_labels, row_data = [], []
            for button in row["buttons"]:
                self.assertEqual(button["action"]["type"], 2)
                self.assertEqual(button["action"]["permission"], {"type": 2})
                self.assertTrue(button["action"]["data"].startswith("/mc "))
                self.assertLessEqual(len(button["render_data"]["label"]), 10)
                self.assertEqual(button["render_data"]["label"],
                                 button["render_data"]["visited_label"])
                self.assertNotIn("enter", button["action"])
                ids.add(button["id"])
                row_labels.append(button["render_data"]["label"])
                row_data.append(button["action"]["data"])
            labels.append(row_labels)
            data.append(row_data)
        self.assertEqual(len(ids), 10)
        self.assertEqual(labels, [["在线状态", "当前 TPS"],
                                  ["玩家统计", "时长排行"],
                                  ["死亡排行", "击杀排行"],
                                  ["钓鱼排行", "账号查询"],
                                  ["查看帮助", "绑定帮助"]])
        self.assertEqual(data, [["/mc status", "/mc tps"],
                                ["/mc stats", "/mc rank 时长"],
                                ["/mc rank 死亡", "/mc rank 击杀生物"],
                                ["/mc rank 钓鱼", "/mc check"],
                                ["/mc help", "/mc bindhelp"]])

    async def test_guide_page_one_never_adds_an_admin_entry_for_managers(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1,
                          {"enabled": True, "servers": [server()]})
        self.store.access_provider = lambda: {
            "super_admins": [], "whitelist_admins": [USER], "blacklist": []}
        event = Event("/mc 命令指引 1", message_id="EVENT_GUIDE_ADMIN")
        await runtime.handle(event)
        kind, (content, rows) = event.sent[0]
        self.assertEqual(kind, "markdown")
        self.assertEqual(len(rows), 5)
        labels = [b["render_data"]["label"] for row in rows for b in row["buttons"]]
        self.assertEqual(labels, ["在线状态", "当前 TPS", "玩家统计", "时长排行",
                                  "死亡排行", "击杀排行", "钓鱼排行", "账号查询",
                                  "查看帮助", "绑定帮助"])
        self.assertNotIn("管理指引", labels)
        self.assertNotIn("管理指引", content)

    async def test_guide_page_two_is_admin_only_with_exact_button_set(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1,
                          {"enabled": True, "servers": [server()]})
        denied = Event("/mc guide 2", message_id="EVENT_GUIDE_DENIED")
        await runtime.handle(denied)
        self.assertEqual(denied.sent[0][0], "text")
        self.assertIn("仅限本群管理员", denied.sent[0][1])
        expected_content = ("# MCSight · 管理指引\n"
                            "> 仅群管理员可用，点击按钮填入命令后发送\n\n"
                            "***\n\n"
                            "服务器维护、批量排序与运行管理。上次在线开关也在这里。")
        expected_labels = [["添加服务器", "编辑服务器", "隐藏服务器"],
                           ["显示服务器", "开启上次在线", "关闭上次在线"],
                           ["批量添加", "移动排序", "交换排序"],
                           ["广播", "查看帮助", "绑定帮助"]]
        for access, event_id in (
                ({"super_admins": [], "whitelist_admins": [USER], "blacklist": []},
                 "EVENT_GUIDE_MANAGER"),
                ({"super_admins": [USER], "whitelist_admins": [], "blacklist": []},
                 "EVENT_GUIDE_SUPER")):
            self.store.access_provider = lambda access=access: access
            module("debounce").reset()
            event = Event("/mc guide 2", message_id=event_id)
            await runtime.handle(event)
            kind, (content, rows) = event.sent[0]
            self.assertEqual(kind, "markdown")
            self.assertEqual(content, expected_content)
            self.assertEqual(len(rows), 4)
            actual = [[b["render_data"]["label"] for b in row["buttons"]] for row in rows]
            self.assertEqual(actual, expected_labels)
            self.assertEqual([[b["action"]["data"] for b in row["buttons"]] for row in rows],
                             [["/mc add", "/mc edit", "/mc hide"],
                              ["/mc show", "/mc lastonline 开", "/mc lastonline 关"],
                              ["/mc batchadd", "/mc move", "/mc swap"],
                              ["/mc broadcast", "/mc help", "/mc bindhelp"]])
            self.assertTrue(all(len(row) == 3 for row in actual))
            self.assertNotIn("控制台令牌", [label for row in actual for label in row])
            self.assertNotIn("返回常用", [label for row in actual for label in row])
            self.assertNotIn("超管", content)

    async def test_guide_rejected_markdown_falls_back_once_to_plain_text(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        group = self.store.update(ENV, group["config_id"], 1,
                                  {"enabled": True, "servers": [server()]})
        self.store.access_provider = lambda: {
            "super_admins": [], "whitelist_admins": [USER], "blacklist": []}
        class RejectedSender(runtime_module.OfficialSender):
            def __init__(self, event, scoped, record):
                self.record, self.revalidate, self.texts = record, None, []
            async def markdown(self, content, rows):
                self.record["calls"] += 1
                raise runtime_module.OfficialApiError("rejected", "synthetic", rejected=True)
            def can_fallback_text(self): return True
            async def text(self, value):
                self.record["calls"] += 1
                self.texts.append(value)
        sender = RejectedSender(None, self.scope, {"calls": 0})
        await runtime._guide_command(self.scope, sender, "", group, [])
        self.assertEqual(len(sender.texts), 1)
        self.assertIn("/mc status", sender.texts[0])
        self.assertIn("在线状态", sender.texts[0])
        self.assertIn("查看帮助 /mc help", sender.texts[0])
        self.assertNotIn("服务器状态", sender.texts[0])
        self.assertNotIn("/mc guide 2", sender.texts[0])

    async def test_unknown_markdown_result_never_posts_duplicate_text(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        group = self.store.update(ENV, group["config_id"], 1,
                                  {"enabled": True, "servers": [server()]})
        class UnknownSender(runtime_module.OfficialSender):
            def __init__(self, event, scoped, record):
                self.record, self.revalidate, self.texts = record, None, []
            async def markdown(self, content, rows):
                self.record["calls"] += 1
                raise runtime_module.OfficialApiError("unknown", "synthetic")
            def can_fallback_text(self): return True
            async def text(self, value): self.texts.append(value)
        sender = UnknownSender(None, self.scope, {"calls": 0})
        with self.assertRaises(runtime_module.OfficialApiError):
            await runtime._guide_command(self.scope, sender, "", group, [])
        self.assertEqual(sender.texts, [])

    async def test_token_command_matches_onebot_and_never_echoes(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        group = self.store.update(ENV, group["config_id"], 1,
                                  {"enabled": True, "servers": [server()]})
        self.store.access_provider = lambda: {
            "super_admins": [USER], "whitelist_admins": [], "blacklist": []}
        server_id = group["servers"][0]["server_id"]
        token = "SYNTHETIC_CONSOLE_TOKEN_123"
        event = Event(f"/mc token one {token}")
        await runtime.handle(event)
        self.assertEqual(event.sent[0][0], "text")
        self.assertIn("已为「one」设置命令 Token", event.sent[0][1])
        self.assertNotIn(token, event.sent[0][1])
        self.assertEqual(self.store.console_token_ids(ENV), [server_id])
        cleared = Event("/mc token one 清除", message_id="EVENT_TOKEN_CLEAR")
        await runtime.handle(cleared)
        self.assertIn("已清除", cleared.sent[0][1])
        self.assertEqual(self.store.console_token_ids(ENV), [])

    async def test_whitelist_list_masks_official_ids_and_server_list_matches_onebot(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        group = self.store.update(ENV, group["config_id"], 1,
                                  {"enabled": True,
                                   "servers": [server(), server("two", hidden=True, api_port=None)]})
        self.store.access_provider = lambda: {
            "super_admins": ["SYNTHETIC_ADMIN_B", USER], "whitelist_admins": [],
            "blacklist": ["SYNTHETIC_BLOCKED"]}
        listing = Event("/mc list")
        await runtime.handle(listing)
        lines = listing.sent[0][1].splitlines()
        self.assertEqual(lines[0], "已添加的服务器：")
        self.assertIn("one -> mc.example.invalid (模组端口: 8080)", lines[1])
        self.assertIn("two -> mc.example.invalid (未装模组) （隐藏）", lines[2])
        whitelist = Event("/mc whitelist list", message_id="EVENT_WHITELIST")
        await runtime.handle(whitelist)
        text = whitelist.sent[0][1]
        for value in ("SYNTHETIC_ADMIN_B", USER, "SYNTHETIC_BLOCKED"):
            self.assertNotIn(value, text)
            self.assertIn(runtime_module._mask_access_value(value), text)
        self.assertIn("已脱敏", text)

    async def test_official_mention_access_lists_are_separate_from_onebot_public_lists(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True})
        public = {"super_admins": [USER], "whitelist_admins": [], "blacklist": []}
        self.store.access_provider = lambda: public
        target = "SYNTHETIC_TARGET_A"

        def mentioned(message, message_id, mention=target, nickname="目标玩家"):
            event = Event(message, message_id=message_id)
            event.message_obj.raw_message["mentions"] = [{
                "member_openid": mention, "member_nickname": nickname,
                "scope": "single", "bot": False, "is_you": False}]
            return event

        added = mentioned("/mc whitelist add @成员", "EVENT_ACCESS_ADD")
        await runtime.handle(added)
        self.assertIn("目标玩家", added.sent[0][1])
        self.assertNotIn(target, added.sent[0][1])
        self.assertEqual(public["whitelist_admins"], [])
        official = self.store.access_lists(ENV)
        self.assertIn(target, official["whitelist_admins"])
        target_scope = core.event_scope(Event(user=target), SELECTION)
        self.assertEqual(self.store.access_level(
            target_scope, self.store.group(ENV, group_id=GROUP)), 3)

        removed = mentioned("/mc whitelist remove @成员", "EVENT_ACCESS_REMOVE")
        await runtime.handle(removed)
        self.assertIn("已从白名单管理员名单移除", removed.sent[0][1])
        self.assertNotIn(target, self.store.access_lists(ENV)["whitelist_admins"])
        self.assertEqual(public["whitelist_admins"], [])

        super_added = mentioned("/mc whitelist super add @成员", "EVENT_ACCESS_SUPER")
        await runtime.handle(super_added)
        self.assertIn("已添加", super_added.sent[0][1])
        self.assertEqual(public["super_admins"], [USER])
        self.assertIn(target, self.store.access_lists(ENV)["super_admins"])
        target_group = self.store.group(ENV, group_id=GROUP)
        self.assertEqual(self.store.access_level(target_scope, target_group), 4)
        super_removed = mentioned("/mc whitelist super remove @成员", "EVENT_ACCESS_SUPER_REMOVE")
        await runtime.handle(super_removed)
        self.assertIn("已从超级管理员名单移除", super_removed.sent[0][1])
        self.assertNotIn(target, self.store.access_lists(ENV)["super_admins"])

        blacklisted = mentioned("/mc blacklist add @成员", "EVENT_ACCESS_BLACKLIST")
        await runtime.handle(blacklisted)
        self.assertIn("已添加", blacklisted.sent[0][1])
        self.assertEqual(public["blacklist"], [])
        self.assertIn(target, self.store.access_lists(ENV)["blacklist"])
        self.assertEqual(self.store.access_level(target_scope, target_group), 0)

        blocked_white = mentioned("/mc whitelist add @成员", "EVENT_ACCESS_BLACKLIST_CONFLICT")
        await runtime.handle(blocked_white)
        self.assertIn("黑名单优先", blocked_white.sent[0][1])
        self.assertNotIn(target, self.store.access_lists(ENV)["whitelist_admins"])

    async def test_official_access_list_rejects_indirect_or_ambiguous_targets(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True})
        self.store.access_provider = lambda: {
            "super_admins": [USER], "whitelist_admins": [], "blacklist": []}

        no_mention = Event("/mc whitelist add 123456", message_id="EVENT_ACCESS_NONE")
        await runtime.handle(no_mention)
        self.assertIn("请 @ 唯一一位成员", no_mention.sent[0][1])
        self.assertEqual(self.store.access_lists(ENV)["whitelist_admins"], frozenset())

        many = Event("/mc whitelist add @成员", message_id="EVENT_ACCESS_MANY")
        many.message_obj.raw_message["mentions"] = [
            {"member_openid": "SYNTHETIC_TARGET_A", "scope": "single"},
            {"member_openid": "SYNTHETIC_TARGET_B", "scope": "single"}]
        await runtime.handle(many)
        self.assertIn("请 @ 唯一一位成员", many.sent[0][1])
        self.assertEqual(self.store.access_lists(ENV)["whitelist_admins"], frozenset())

        self_remove = Event("/mc whitelist super remove @成员", message_id="EVENT_ACCESS_SELF")
        self_remove.message_obj.raw_message["mentions"] = [
            {"member_openid": USER, "scope": "single"}]
        await runtime.handle(self_remove)
        self.assertIn("不能把自己从超级管理员名单移除", self_remove.sent[0][1])

    async def test_official_access_list_requires_super_admin(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True})
        self.store.access_provider = lambda: {
            "super_admins": [], "whitelist_admins": [USER], "blacklist": []}
        event = Event("/mc whitelist add @成员", message_id="EVENT_ACCESS_DENIED")
        event.message_obj.raw_message["mentions"] = [
            {"member_openid": "SYNTHETIC_TARGET_A", "scope": "single"}]
        await runtime.handle(event)
        self.assertIn("仅限超级管理员", event.sent[0][1])
        self.assertNotIn("SYNTHETIC_TARGET_A", self.store.access_lists(ENV)["whitelist_admins"])

    async def test_official_access_list_cannot_remove_onebot_public_entry(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True})
        self.store.access_provider = lambda: {
            "super_admins": [USER], "whitelist_admins": [], "blacklist": ["SYNTHETIC_TARGET_A"]}
        event = Event("/mc blacklist remove @成员", message_id="EVENT_ACCESS_PUBLIC")
        event.message_obj.raw_message["mentions"] = [
            {"member_openid": "SYNTHETIC_TARGET_A", "member_nickname": "目标玩家", "scope": "single"}]
        await runtime.handle(event)
        self.assertIn("来自 WebUI 公共名单", event.sent[0][1])
        self.assertIn("SYNTHETIC_TARGET_A",
                      self.store.access_lists(ENV)["blacklist"])

    async def test_bindcheck_uses_shared_unbound_image_under_default_renderer(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server()]})
        self.store.access_provider = lambda: {
            "super_admins": [USER], "whitelist_admins": [], "blacklist": []}
        roster = {"members": [{"user_id": "MEMBER_ONE", "display": "玩家一", "bot": False},
                              {"user_id": "MEMBER_TWO", "display": "玩家二", "bot": False}],
                  "complete": True}
        def unbound_members(value, active):
            members = [m for m in value["members"] if not m["bot"]]
            return ([{"user_id": m["user_id"], "name": m["display"], "qq": m["user_id"]}
                     for m in members if m["user_id"] not in active], len(members))
        async def read_roster(*args):
            return roster
        runtime.members = SimpleNamespace(
            observe=lambda *args: None, roster=read_roster,
            unbound_members=unbound_members,
            binding_audit=lambda *args: ("官方群绑定检测", ["文本兜底"]))
        runtime.binding_commands = SimpleNamespace(
            active_user_ids=lambda scope, arguments: ("one", frozenset({"MEMBER_ONE"})))
        runtime.renderer = runtime_module.render_pages
        captured = {}
        async def fake_pages(unbound, info):
            captured["unbound"], captured["info"] = unbound, info
            return [b"unbound_png"]
        with patch.object(runtime_module, "render_unbound_pages", fake_pages):
            event = Event("/mc bindcheck", message_id="EVENT_BINDCHECK_IMAGE")
            await runtime.handle(event)
        self.assertEqual(event.sent[0][0], "text")
        self.assertIn("正在比对", event.sent[0][1])
        self.assertEqual(event.sent[1:], [("image", b"unbound_png")])
        self.assertEqual([item["user_id"] for item in captured["unbound"]], ["MEMBER_TWO"])
        self.assertEqual(captured["info"]["group_total"], 2)
        self.assertEqual(captured["info"]["unbound_total"], 1)

    async def test_nonselected_c2c_and_channel_do_not_create_data_or_send(self):
        runtime = self.make_runtime()
        for event in [Event("/mc setup", instance="other"), Event("/mc setup", group="")]:
            await runtime.handle(event)
            self.assertEqual(event.sent, [])
        event = Event("/mc setup"); event.message_obj.raw_message["channel_id"] = "CHANNEL"
        await runtime.handle(event)
        self.assertEqual(event.sent, [])
        self.assertFalse((Path(self.temp.name) / ENV).exists())

    async def test_query_paginates_with_new_event_and_blocks_writes(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server(api_port=None)]})
        event = Event("/mc status")
        await runtime.handle(event)
        self.assertEqual(event.sent[0], ("image", b"page1"))
        self.assertIn("/mc page 2", event.sent[1][1])
        await runtime.handle(event)
        self.assertEqual(len(event.sent), 2)
        next_page = Event("/mc page 2", message_id="EVENT_B")
        await runtime.handle(next_page)
        self.assertEqual(next_page.sent[0], ("image", b"page2"))
        writes = Event("/mc unbind Player_A", message_id="EVENT_C")
        await runtime.handle(writes)
        # 本群未开启中心库，写入走本地路径且该服无 API 端口，必须保持关闭。
        self.assertIn("能力未就绪", writes.sent[0][1])

    async def test_permission_change_during_query_prevents_sending_stale_image(self):
        runtime = self.make_runtime()
        candidate, group = self.approve()
        group = self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server(api_port=None)]})
        async def query(*args):
            runtime.store.authorize(ENV, group["config_id"], candidate["candidate_id"], "blacklist", group["revision"])
            return "标题", ["结果"]
        runtime.queries = SimpleNamespace(query=query)
        event = Event("/mc status")
        await runtime.handle(event)
        self.assertFalse(any(kind == "image" for kind, _ in event.sent))
        self.assertIn("配置或权限已变化", event.sent[0][1])

    async def test_inactive_environment_cannot_run_mc_query(self):
        runtime = self.make_runtime()
        runtime.activation = SimpleNamespace(status=lambda: {"state": "active", "selection": {"platform": "onebot"},
            "environments": [{"environment_id": ENV, "platform": "qq_official"}]})
        with self.assertRaises(core.OfficialError): runtime.environment(ENV, active=True)
        self.assertEqual(runtime.environment(ENV), ENV)

    async def test_sdk_without_base64_upload_gets_sensitive_image_as_text_pages(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server(api_port=None)]})
        class TextOnlySender:
            def __init__(self, event, scope, record): self.event, self.record = event, record
            def supports_images(self): return False
            async def image(self, _payload): forbidden()
            async def text(self, value): self.record["calls"] += 1; self.event.sent.append(("text", value))
        runtime.sender = TextOnlySender
        event = Event("/mc status")
        await runtime.handle(event)
        self.assertTrue(event.sent and event.sent[0][0] == "text")
        self.assertIn("构造标题", event.sent[0][1])

    async def test_render_failure_keeps_text_pages_available_to_new_page_events(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server(api_port=None)]})
        def rendering_fails(*_): raise core.OfficialError("synthetic rendering failure")
        async def many_rows(*_): return "标题", ["合成结果" * 120] * 20
        runtime.renderer = rendering_fails
        runtime.queries = SimpleNamespace(query=many_rows)
        first = Event("/mc status")
        await runtime.handle(first)
        self.assertEqual(first.sent[0][0], "text")
        self.assertIn("/mc page 2", first.sent[1][1])
        second = Event("/mc page 2", message_id="SYNTHETIC_NEXT_EVENT")
        await runtime.handle(second)
        self.assertEqual(second.sent[0][0], "text")
        self.assertIn("第 2/", second.sent[0][1])

    async def test_reload_does_not_resend_same_official_event(self):
        first_runtime = self.make_runtime()
        first = Event("/mc help")
        await first_runtime.handle(first)
        self.assertEqual(len(first.sent), 1)
        first_runtime.close()
        second_runtime = self.make_runtime()
        replay = Event("/mc help")
        await second_runtime.handle(replay)
        self.assertEqual(replay.sent, [])

    async def test_close_cancels_inflight_query_before_publication(self):
        runtime = self.make_runtime()
        _, group = self.approve()
        self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server(api_port=None)]})
        entered = asyncio.Event()
        async def slow(*_):
            entered.set()
            await asyncio.Event().wait()
        runtime.queries = SimpleNamespace(query=slow)
        event = Event("/mc status")
        task = asyncio.create_task(runtime.handle(event))
        await entered.wait()
        runtime.close()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(event.sent, [])


class SenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_then_passive_message_and_hint_use_bounded_calls(self):
        calls = []
        async def upload(**kwargs): calls.append(("upload", kwargs)); return {"file_info": "SYNTHETIC_FILE"}
        async def message(**kwargs): calls.append(("message", kwargs)); return {"id": "SYNTHETIC_REPLY"}
        event = Event(); event.bot = SimpleNamespace(api=SimpleNamespace(post_group_base64file=upload, post_group_message=message))
        scoped = scope()
        record = runtime_module.ReplyLedger().claim(scoped)
        sender = runtime_module.OfficialSender(event, scoped, record)
        await sender.image(b"synthetic_png")
        await sender.text("/mc page 2")
        self.assertEqual(record["calls"], 3)
        self.assertFalse(calls[0][1]["srv_send_msg"])
        for kind, values in calls[1:]:
            self.assertEqual(values["msg_id"], scoped.message_id)
        self.assertEqual([x[1]["msg_seq"] for x in calls[1:]], [1, 2])

    async def test_markdown_with_keyboard_is_one_passive_call_without_text_content(self):
        calls = []
        async def message(**kwargs):
            calls.append(kwargs)
            return {"id": "SYNTHETIC_REPLY"}
        event = Event()
        event.bot = SimpleNamespace(api=SimpleNamespace(post_group_message=message))
        scoped = scope()
        sender = runtime_module.OfficialSender(
            event, scoped, runtime_module.ReplyLedger().claim(scoped))
        rows = runtime_module._guide_rows(1, 1)
        await sender.markdown("# 命令指引", rows)
        self.assertEqual(len(calls), 1)
        payload = calls[0]
        self.assertEqual(payload["msg_type"], 2)
        self.assertEqual(payload["markdown"], {"content": "# 命令指引"})
        self.assertEqual(payload["keyboard"], {"content": {"rows": rows}})
        self.assertNotIn("content", payload)
        self.assertEqual(payload["msg_id"], scoped.message_id)
        self.assertEqual(payload["msg_seq"], 1)
        self.assertEqual(sender.record["calls"], 1)

    async def test_only_explicit_message_rejection_allows_a_text_fallback(self):
        responses = [{"code": 40001}, {"id": "SYNTHETIC_REPLY"}]
        calls = []
        async def message(**kwargs):
            calls.append(kwargs)
            return responses[len(calls) - 1]
        event = Event()
        event.bot = SimpleNamespace(api=SimpleNamespace(post_group_message=message))
        scoped = scope()
        sender = runtime_module.OfficialSender(
            event, scoped, runtime_module.ReplyLedger().claim(scoped))
        with self.assertRaises(runtime_module.OfficialApiError):
            await sender.markdown("# 命令指引", runtime_module._guide_rows(1, 1))
        self.assertTrue(sender.can_fallback_text())
        await sender.text("命令指引文字版")
        self.assertEqual([call["msg_type"] for call in calls], [2, 0])

        async def unknown(**kwargs):
            calls.append(kwargs)
            return None
        event = Event(message_id="SYNTHETIC_UNKNOWN")
        event.bot = SimpleNamespace(api=SimpleNamespace(post_group_message=unknown))
        scoped = scope()
        sender = runtime_module.OfficialSender(
            event, scoped, runtime_module.ReplyLedger().claim(scoped))
        calls.clear()
        with self.assertRaises(runtime_module.OfficialApiError):
            await sender.markdown("# 命令指引", runtime_module._guide_rows(1, 1))
        self.assertFalse(sender.can_fallback_text())

    async def test_unknown_send_result_is_not_retried_or_fallen_back(self):
        calls = []
        async def message(**kwargs): calls.append(kwargs); raise asyncio.TimeoutError
        event = Event(); event.bot = SimpleNamespace(api=SimpleNamespace(post_group_message=message))
        sender = runtime_module.OfficialSender(event, scope(), runtime_module.ReplyLedger().claim(scope()))
        with self.assertRaises(core.OfficialError): await sender.text("hello")
        self.assertEqual(len(calls), 1)

    async def test_url_only_sdk_is_not_sent_an_unsupported_base64_payload(self):
        async def group_message(group_openid, content, msg_type, msg_id, msg_seq):
            return {"id": "SYNTHETIC_REPLY"}
        event = Event()
        event.bot = SimpleNamespace(post_group_message=group_message, post_group_file=forbidden)
        sender = runtime_module.OfficialSender(event, scope(), runtime_module.ReplyLedger().claim(scope()))
        self.assertFalse(sender.supports_images())
        with self.assertRaises(core.OfficialError): await sender.image(b"synthetic_png")
        self.assertEqual(sender.record["calls"], 0)
        await sender.text("可用的只读结果")
        self.assertEqual(sender.record["calls"], 1)

    async def test_revoke_during_media_upload_cannot_publish_result(self):
        calls = []
        allowed = [True]
        async def upload(**kwargs):
            calls.append("upload")
            allowed[0] = False
            return {"file_info": "SYNTHETIC_FILE"}
        async def message(**kwargs): calls.append("publish"); return {"id": "SYNTHETIC_REPLY"}
        event = Event(); event.bot = SimpleNamespace(post_group_base64file=upload, post_group_message=message)
        sender = runtime_module.OfficialSender(event, scope(), runtime_module.ReplyLedger().claim(scope()))
        def revalidate():
            if not allowed[0]: raise core.OfficialError("revoked")
        sender.revalidate = revalidate
        with self.assertRaises(core.OfficialError): await sender.image(b"synthetic_png")
        self.assertEqual(calls, ["upload"])

    async def test_sdk_signature_is_checked_before_dispatch(self):
        async def incomplete(group_openid, content): forbidden()
        event = Event(); event.bot = SimpleNamespace(post_group_message=incomplete)
        sender = runtime_module.OfficialSender(event, scope(), runtime_module.ReplyLedger().claim(scope()))
        with self.assertRaises(core.OfficialError): await sender.text("synthetic result")
        self.assertEqual(sender.record["calls"], 0)


def isolated_function(path, name, **namespace):
    assert path in {"commands/mc_handler.py", "web/api.py"}
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    node = copy.deepcopy(next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name))
    node.decorator_list = []
    module_ast = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    ast.fix_missing_locations(module_ast)
    exec(compile(module_ast, "<isolated official dispatch>", "exec"), namespace)
    return namespace[name]


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_dispatch_never_constructs_old_configuration(self):
        calls = []
        async def handle(event): calls.append(event)
        dispatcher = isolated_function("commands/mc_handler.py", "handle_mc_command",
            platform_activation=SimpleNamespace(accepts=lambda event: True),
            official_runtime=SimpleNamespace(handle=handle), ConfigManager=forbidden, WhitelistManager=forbidden)
        event = Event()
        self.assertEqual([x async for x in dispatcher(event)], [])
        self.assertEqual(calls, [event])

    async def test_selected_official_summary_does_not_scan_old_groups(self):
        handler = isolated_function("web/api.py", "view_summary",
            platform_activation=SimpleNamespace(status=lambda: {"selection": SELECTION}),
            _ok=lambda data: data, MAX_SERVERS_PER_GROUP=20, _plugin_version=lambda: "synthetic", PLUGIN_NAME="synthetic",
            WhitelistManager=forbidden, ConfigManager=forbidden, _group_ids=forbidden)
        self.assertEqual((await handler())["groups"], [])

    async def test_onebot_preview_summary_scans_groups_while_official_selected(self):
        class Request:
            query = {"platform": "onebot"}
        class Config:
            def __init__(self, group_id): self.group_id = group_id
            def get_all_servers(self): return []
            def get_alias(self): return ""
        class Whitelist:
            def has_mod_api(self, server): return False
        handler = isolated_function("web/api.py", "view_summary",
            platform_activation=SimpleNamespace(status=lambda: {"selection": SELECTION}),
            web_request=Request(), _ok=lambda data: data, MAX_SERVERS_PER_GROUP=20,
            _plugin_version=lambda: "synthetic", PLUGIN_NAME="synthetic",
            WhitelistManager=Whitelist, ConfigManager=Config, _group_ids=lambda: ["100000001"])
        self.assertEqual([g["gid"] for g in (await handler())["groups"]], ["100000001"])

    async def test_activation_accepts_only_selected_raw_official_group_but_old_business_stays_closed(self):
        store = activation.SelectionStore()
        controller = activation.ActivationController("qq_official", "official-a", lambda: [activation.PlatformInstance("qq_official", "official-a")], store=store, official_scope=core.event_scope)
        controller.save_selection({"platform": "qq_official", "instance_id": "official-a", "application_ref": "SYNTHETIC", "expected_revision": 0, "confirm_application": True})
        restarted = activation.ActivationController("onebot", "", controller._inventory, store=store, official_scope=core.event_scope)
        self.assertTrue(restarted.accepts(Event()))
        self.assertFalse(restarted.allows(Event()))
        self.assertFalse(restarted.accepts(Event(group="")))
        self.assertFalse(restarted.accepts(Event(instance="other")))

    async def test_all_official_command_aliases_dispatch_before_legacy_configuration(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8-sig"))
        source = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MCWatcher")
        methods = [copy.deepcopy(n) for n in source.body if isinstance(n, ast.AsyncFunctionDef)
                   and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                           and d.func.attr in ("command", "regex") for d in n.decorator_list)]
        for method in methods: method.decorator_list = []
        helper = copy.deepcopy(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_rest_after_command"))
        cls = ast.ClassDef(name="Aliases", bases=[], keywords=[], body=methods, decorator_list=[])
        if hasattr(ast.ClassDef, "_fields") and "type_params" in ast.ClassDef._fields: cls.type_params = []
        data = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), helper, cls], type_ignores=[])
        ast.fix_missing_locations(data)
        calls = []
        async def dispatch(event):
            calls.append(event.message_str)
            if False: yield None
        namespace = {"platform_activation": SimpleNamespace(accepts=lambda event: core.event_scope(event, SELECTION) is not None),
                     "ConfigManager": forbidden, "WhitelistManager": forbidden, "handle_mc_command": dispatch}
        exec(compile(data, "<isolated aliases>", "exec"), namespace)
        handler = namespace["Aliases"]()
        names = {"mc": "/mc help", "online": "/在线 -s one", "query": "/查询 Player_A",
                 "rank": "/排行 时长", "broadcast": "/广播 one hello", "exec_command": "/执行 -s one say hello",
                 "tps_command": "/tps", "bind_command": "/绑定 123456", "unbind_command": "/解绑 Player_A",
                 "checkbind_command": "/查绑定", "bindhelp_command": "/绑定帮助", "help_command": "/帮助",
                 "lastonline_command": "/上次在线", "checknick_command": "/检测昵称", "bindcheck_command": "/绑定检测"}
        self.assertEqual(set(names), {m.name for m in methods})
        for name, command in names.items():
            event = Event(command)
            self.assertEqual([x async for x in getattr(handler, name)(event)], [])
        self.assertEqual(len(calls), len(names))
        self.assertIn("/mc status -s one", calls)


if __name__ == "__main__":
    unittest.main()
