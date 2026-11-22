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
    assert name in {"official_core", "official_queries", "official_runtime", "official_bindings", "official_center", "official_protocol", "official_binding_service", "official_binding_policy", "official_binding_host", "official_binding_commands", "official_binding_recovery", "official_binding_admin", "official_transport", "platform_activation"}
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
        self.temp = tempfile.TemporaryDirectory(prefix="mcwatch-official-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.now = 1000000.0
        self.store = core.OfficialStore(self.temp.name, clock=lambda: self.now)
        self.scope = scope()

    def approve(self):
        c = self.store.register(self.scope, "合成备注")
        group = self.store.approve(ENV, c["candidate_id"], self.store.list_state(ENV)["revision"], "测试群", "展示值")
        return c, group

    def test_setup_creates_only_expiring_candidate_not_group_or_authority(self):
        candidate = self.store.register(self.scope)
        state = self.store.list_state(ENV)
        self.assertEqual(state["groups"], [])
        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(candidate, self.store.register(self.scope))
        self.now += 86401
        self.assertEqual(self.store.list_state(ENV)["candidates"], [])

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

    def test_normal_state_does_not_read_secret_file(self):
        self.approve()
        self.store.save_token(ENV, "SYNTHETIC_NOT_A_REAL_TOKEN")
        original = core.read_json
        def guarded(path, *args, **kwargs):
            if Path(path).name == "mod_token.json": forbidden()
            return original(path, *args, **kwargs)
        with patch.object(core, "read_json", side_effect=guarded):
            self.assertTrue(self.store.token_is_set(ENV))
            self.assertNotIn("mod_api_token", json.dumps(self.store.list_state(ENV)))

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
        async def query(*args): return "构造标题", ["构造结果"]
        runtime = runtime_module.OfficialRuntime(self.temp.name, Activation(),
                    queries=SimpleNamespace(query=query), renderer=lambda *a: [b"page1", b"page2"], sender=Sender)
        runtime.store = self.store
        return runtime

    async def test_setup_registration_and_help_only_before_activation(self):
        runtime = self.make_runtime()
        event = Event("/mc setup 测试")
        await runtime.handle(event)
        self.assertIn("已登记", event.sent[0][1])
        self.assertEqual(runtime.store.list_state(ENV)["groups"], [])
        other = Event("/mc status", message_id="EVENT_B")
        runtime.queries = SimpleNamespace(query=forbidden)
        await runtime.handle(other)
        self.assertIn("尚未启用", other.sent[0][1])

    async def test_official_binding_help_does_not_reuse_onebot_binding_steps(self):
        runtime = self.make_runtime()
        event = Event("/mc bindhelp")
        await runtime.handle(event)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.sent[0][0], "text")
        self.assertIn("尚未开放", event.sent[0][1])
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
        self.assertIn("尚未", writes.sent[0][1])

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
