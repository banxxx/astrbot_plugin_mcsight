"""Scoped history and delivery tests: constructed HTTP objects and new temporary data only."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import test_official_readonly as prior
from test_official_readonly import (ENV, OTHER_ENV, GROUP, USER, SELECTION, Event,
    core, module, queries, runtime_module, scope, server, forbidden)

# The imported modules contain no file/network initialization; their source was
# reviewed as part of this change. No AstrBot or live plugin package is imported.
stats_module = sys.modules[core.__package__ + ".official_stats"]
delivery = sys.modules[core.__package__ + ".official_delivery"]
SERVER = "33333333-3333-4333-8333-333333333333"
SERVER_B = "44444444-4444-4444-8444-444444444444"


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="official-history-synthetic-")
        self.addCleanup(self.directory.cleanup)
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp()
        self.history = stats_module.OfficialStats(self.directory.name, clock=lambda: self.now)

    def save(self, **kwargs):
        values = dict(environment=ENV, group=GROUP, server=SERVER, player="Player_A", data={"playTime": 720000})
        values.update(kwargs)
        return self.history.record(**values)

    def test_same_day_overwrites_but_never_becomes_its_own_baseline(self):
        self.assertEqual(self.save(), [])
        self.assertEqual(self.save(data={"playTime": 800000}), [])
        self.now += 86400
        self.assertEqual(self.save()[0], ("2026-11-01", {"playTime": 800000.0}))

    def test_environment_group_and_stable_server_id_are_independent(self):
        self.save()
        self.now += 86400
        self.assertEqual(self.save(environment=OTHER_ENV), [])
        self.assertEqual(self.save(group="SYNTHETIC_GROUP_B"), [])
        self.assertEqual(self.save(server=SERVER_B), [])
        self.assertEqual(len(self.save()), 1)

    def test_game_name_case_is_normalized_and_server_name_is_not_a_history_key(self):
        self.save()
        self.now += 86400
        self.assertEqual(len(self.save(player="PLAYER_A")), 1)
        self.assertEqual(self.history.roster(ENV), [(GROUP, SERVER, "player_a")])

    def test_only_numeric_public_stats_are_saved(self):
        self.save(data={"playTime": 720000, "nickname": "SYNTHETIC_PRIVATE_TEXT", "deaths": 4,
                        "online": True, "nan": float("nan"), "huge": float("inf"), "negative": -1})
        self.now += 86400
        snapshot = self.save()[0][1]
        self.assertEqual(snapshot, {"playTime": 720000.0, "deaths": 4.0})

    def test_retention_and_no_legacy_source_scanning(self):
        self.save()
        self.now += 31 * 86400
        self.assertEqual(self.save(), [])
        files = [str(p.relative_to(self.directory.name)) for p in Path(self.directory.name).rglob("*.sqlite3")]
        self.assertEqual(files, [str(Path(ENV) / "stats.sqlite3")])

    def test_existing_foreign_or_corrupt_database_is_not_recreated(self):
        target = Path(self.directory.name) / ENV / "stats.sqlite3"
        target.parent.mkdir()
        target.write_bytes(b"synthetic invalid database")
        with self.assertRaises(core.OfficialError): self.save()
        self.assertEqual(target.read_bytes(), b"synthetic invalid database")

    def test_environment_meta_mismatch_does_not_overwrite(self):
        self.save()
        original = Path(self.directory.name) / ENV / "stats.sqlite3"
        other = Path(self.directory.name) / OTHER_ENV / "stats.sqlite3"
        other.parent.mkdir()
        other.write_bytes(original.read_bytes())
        before = other.read_bytes()
        with self.assertRaises(core.OfficialError): self.save(environment=OTHER_ENV)
        self.assertEqual(other.read_bytes(), before)

    def test_invalid_scope_is_rejected_before_any_file_creation(self):
        for values in ({"environment": "../escape"}, {"server": "name-is-not-id"},
                       {"player": "../player"}, {"group": ""}):
            with self.subTest(values=values), self.assertRaises(core.OfficialError): self.save(**values)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])


class FullStatisticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_six_categories_and_missing_zero_values_are_public(self):
        async def mod(*_): return {"playTime": 72000, "deaths": 4, "mobKills": 10}
        result = await queries.QueryService(mod=mod).query("stats", "AnotherPlayer", {"servers": [server()], "policy": core.default_policy()}, "SYNTHETIC")
        self.assertIsInstance(result, queries.StatisticsQuery)
        for category in ("探索", "战斗", "工艺", "仓储", "田园", "休闲"):
            self.assertIn(f"【{category}】", result.lines)
        self.assertIn("死亡次数：4", result.lines)
        self.assertIn("使用工作台：0", result.lines)
        self.assertEqual(result.player_name, "AnotherPlayer")

    async def test_query_uses_only_the_official_history_scope(self):
        with tempfile.TemporaryDirectory(prefix="official-query-history-") as directory:
            now = [datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()]
            history = stats_module.OfficialStats(directory, clock=lambda: now[0])
            async def mod(*_): return {"playTime": 720000, "deaths": 4}
            configured = server(server_id=SERVER)
            group = {"servers": [configured], "policy": core.default_policy(),
                     "environment_id": ENV, "group_openid": GROUP}
            service = queries.QueryService(mod=mod, history=history, clock=lambda: now[0])
            first = await service.query("stats", "Player_A", group, "SYNTHETIC")
            self.assertEqual(first.snapshots, [])
            now[0] += 7 * 86400
            result = await service.query("stats", "Player_A", group, "SYNTHETIC")
            self.assertEqual(result.snapshots[0][0], "2026-11-01")
            self.assertEqual(result.today.isoformat(), "2026-11-08")


class FakeResponse:
    def __init__(self, result, *, status=200, chunks=None):
        self.status, self.result, self.chunks = status, result, chunks
        self.content = self
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def iter_chunked(self, _size):
        for chunk in self.chunks or [json.dumps(self.result).encode("utf-8")]:
            yield chunk


def fake_http(response=None, error=None, on_auth=None):
    calls = []
    async def check_session(self):
        if on_auth is not None: on_auth()
    def request(**kwargs):
        calls.append(kwargs)
        if error: raise error
        return response or FakeResponse({"id": "SYNTHETIC_REPLY"})
    cls = type("BotHttp", (), {"__module__": "botpy.http", "check_session": check_session})
    http = cls()
    http._session = SimpleNamespace(request=request)
    http._headers = {"Authorization": "SYNTHETIC_NOT_A_REAL_ACCESS_TOKEN"}
    http.timeout, http.is_sandbox = 20, False
    http.request = forbidden
    return http, calls


class FixedTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.aiohttp = ModuleType("aiohttp")
        self.aiohttp.ClientTimeout = lambda **kwargs: kwargs
        self.patcher = patch.dict(sys.modules, {"aiohttp": self.aiohttp})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def test_fixed_botpy_cannot_claim_file_data_from_url_only_public_interface(self):
        http, calls = fake_http(FakeResponse({"file_info": "SYNTHETIC_FILE"}))
        event = Event()
        event.bot = SimpleNamespace(api=SimpleNamespace(_http=http))
        transport = delivery.transport_for(event)
        self.assertIsNotNone(transport)
        self.assertFalse(transport.supports_images)
        with self.assertRaises(core.OfficialError):
            await transport.call("post_group_base64file", group_openid=GROUP,
                                 file_type=1, file_data="SYNTHETIC_BASE64", srv_send_msg=False)
        self.assertEqual(calls, [])
        sender = runtime_module.OfficialSender(event, scope(), runtime_module.ReplyLedger().claim(scope()))
        self.assertFalse(sender.supports_images())

    async def test_connection_reset_does_not_replay_a_message(self):
        http, calls = fake_http(error=ConnectionResetError("SYNTHETIC_PRIVATE_ERROR"))
        with self.assertRaises(core.OfficialError) as error:
            await delivery.BotpyGroupTransport(http).call("post_group_message", group_openid=GROUP, msg_id="SYNTHETIC_EVENT", msg_seq=1, content="hello")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("SYNTHETIC_PRIVATE_ERROR", str(error.exception))

    async def test_auth_wait_revalidates_permission_before_actual_request(self):
        allowed = [True]
        http, calls = fake_http(on_auth=lambda: allowed.__setitem__(0, False))
        def revalidate():
            if not allowed[0]: raise core.OfficialError("revoked")
        with self.assertRaises(core.OfficialError):
            await delivery.BotpyGroupTransport(http).call("post_group_message", group_openid=GROUP, revalidate=revalidate)
        self.assertEqual(calls, [])

    async def test_redirect_and_oversized_response_fail_closed(self):
        for response in (FakeResponse({}, status=302), FakeResponse({}, chunks=[b"x" * (delivery.MAX_REPLY_BYTES + 1)])):
            http, calls = fake_http(response)
            with self.assertRaises(core.OfficialError): await delivery.BotpyGroupTransport(http).call("post_group_message", group_openid=GROUP)
            self.assertEqual(len(calls), 1)

    async def test_unknown_transport_does_not_assume_framework_private_layout(self):
        event = Event()
        event.bot = SimpleNamespace(api=SimpleNamespace(_http=SimpleNamespace(check_session=forbidden)))
        self.assertIsNone(delivery.transport_for(event))

    async def test_verified_astrbot_event_uses_file_data_directly_without_sdk_helper(self):
        async def upload(self, image_base64, file_type, **kwargs): forbidden()
        event_class = type("QQOfficialMessageEvent", (Event,), {
            "__module__": "astrbot.core.platform.sources.qqofficial.qqofficial_message_event",
            "upload_group_and_c2c_image": upload})
        event = event_class()
        http, calls = fake_http(FakeResponse({"file_info": "SYNTHETIC_FILE"}))
        event.bot = SimpleNamespace(api=SimpleNamespace(_http=http))
        transport = delivery.transport_for(event)
        self.assertTrue(transport.supports_images)
        result = await transport.call("post_group_base64file", group_openid=GROUP,
            file_data="SYNTHETIC_BASE64", file_type=1, srv_send_msg=False)
        self.assertEqual(result["file_info"], "SYNTHETIC_FILE")
        self.assertEqual(len(calls), 1)
        # 群身份固定在 URL 路径里，请求体只带文件字段。
        self.assertEqual(calls[0]["json"], dict(file_data="SYNTHETIC_BASE64", file_type=1, srv_send_msg=False))
        self.assertIn(GROUP, calls[0]["url"])
        self.assertTrue(calls[0]["url"].endswith("/files"))
        self.assertFalse(calls[0]["allow_redirects"])

    async def test_webhook_inherited_event_can_use_verified_file_schema(self):
        async def upload(self, image_base64, file_type, **kwargs): forbidden()
        base = type("QQOfficialMessageEvent", (Event,), {
            "__module__": "astrbot.core.platform.sources.qqofficial.qqofficial_message_event",
            "upload_group_and_c2c_image": upload})
        webhook = type("QQOfficialWebhookMessageEvent", (base,), {
            "__module__": "astrbot.core.platform.sources.qqofficial_webhook.qo_webhook_event"})
        event = webhook(platform="qq_official_webhook")
        http, _calls = fake_http()
        event.bot = SimpleNamespace(api=SimpleNamespace(_http=http))
        self.assertTrue(delivery.transport_for(event).supports_images)


class OfficialSamplerTests(unittest.IsolatedAsyncioTestCase):
    setUp = prior.StoreTests.setUp
    approve = prior.StoreTests.approve

    def runtime(self):
        activation = SimpleNamespace(status=lambda: {"state": "configuring", "selection": SELECTION,
            "environments": [{"environment_id": ENV, "platform": "qq_official"}]})
        runtime = runtime_module.OfficialRuntime(self.temp.name, activation)
        runtime.store = self.store
        return runtime

    async def test_inactive_environment_does_not_read_roster_credentials_or_network(self):
        runtime = self.runtime()
        runtime.activation = SimpleNamespace(status=lambda: {"state": "active", "selection": {"platform": "onebot"}})
        runtime.stats = SimpleNamespace(roster=forbidden)
        runtime.store.runtime_token = forbidden
        await runtime.sample_once()

    async def test_hidden_and_disabled_servers_are_skipped_before_token_read(self):
        runtime = self.runtime()
        _, group = self.approve()
        group = self.store.update(ENV, group["config_id"], 1, {"servers": [server(hidden=True)]})
        runtime.stats = SimpleNamespace(roster=lambda _: [(GROUP, group["servers"][0]["server_id"], "player_a")])
        runtime.store.runtime_token = forbidden
        runtime.queries.mod = forbidden
        await runtime.sample_once()
        self.assertEqual(runtime.last_sample["skipped"], 1)

    async def test_active_sampler_uses_stable_server_and_own_history(self):
        runtime = self.runtime()
        _, group = self.approve()
        group = self.store.update(ENV, group["config_id"], 1, {"enabled": True, "servers": [server()]})
        sid = group["servers"][0]["server_id"]
        calls = []
        runtime.stats = SimpleNamespace(roster=lambda _: [(GROUP, sid, "player_a")], record=lambda *args: calls.append(args))
        runtime.store.runtime_token = lambda _: "SYNTHETIC_NOT_A_REAL_TOKEN"
        async def mod(s, token, endpoint):
            self.assertEqual(s["server_id"], sid)
            self.assertEqual(endpoint, "/api/stats/player_a")
            return {"playTime": 72000}
        runtime.queries.mod = mod
        await runtime.sample_once()
        self.assertEqual(runtime.last_sample["sampled"], 1)
        self.assertEqual(calls[0][:4], (ENV, GROUP, sid, "player_a"))


if __name__ == "__main__":
    unittest.main()
