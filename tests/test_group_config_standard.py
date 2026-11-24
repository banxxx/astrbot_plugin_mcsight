"""OneBot 群配置 SQLite 存储、迁移与备份回归。

运行期只读 ``onebot/config.sqlite3``；旧 ``onebot/<群号>/state.json`` 和
``group_configs/servers_<群号>.json`` 只在首次打开时一次性迁移到 ``legacy/``。
"""
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_mcwatch_synthetic_group"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

# 隔离测试不装 AstrBot：只给 server_config 需要的 sub_dir 一个内存桩。
utils_pkg = ModuleType(PACKAGE + ".utils")
utils_pkg.__path__ = [str(ROOT / "utils")]
sys.modules[PACKAGE + ".utils"] = utils_pkg
plugin_paths = ModuleType(PACKAGE + ".utils.plugin_paths")
plugin_paths.sub_dir = lambda name: str(ROOT / "data" / name)
sys.modules[PACKAGE + ".utils.plugin_paths"] = plugin_paths

from _mcwatch_synthetic_group.config import onebot_store
from _mcwatch_synthetic_group.config import server_config as sc
from _mcwatch_synthetic_group.utils.group_policy import POLICY_DEFAULTS, POLICY_KEYS


class GroupConfigStandardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

        def sub_dir(name):
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            return str(path)

        self.patch = patch.object(sc, "sub_dir", side_effect=sub_dir)
        self.patch.start()
        onebot_store.close_store()

    def tearDown(self):
        onebot_store.close_store()
        self.patch.stop()
        self.temp.cleanup()

    def snapshot(self, gid=123456):
        return sc.ConfigManager(group_id=gid).snapshot()

    def test_group_is_stored_in_sqlite_without_json_file(self):
        cm = sc.ConfigManager(group_id=123456)
        data = self.snapshot()
        self.assertEqual(set(data), {
            "scope_kind", "scope_id", "enabled", "alias", "servers",
            "policy", "nickname_ignore",
        })
        self.assertEqual(data["scope_kind"], "group")
        self.assertEqual(data["scope_id"], "123456")
        self.assertTrue(data["enabled"])
        self.assertEqual(data["alias"], "")
        self.assertEqual(data["servers"], [])
        self.assertEqual(set(data["policy"]), POLICY_KEYS)
        self.assertEqual(data["policy"], POLICY_DEFAULTS)
        self.assertEqual(cm.get_policy(), POLICY_DEFAULTS)
        self.assertTrue((self.root / "onebot" / "config.sqlite3").is_file())
        self.assertFalse((self.root / "onebot" / "123456" / "state.json").exists())

    def test_alias_is_optional_and_normalized(self):
        cm = sc.ConfigManager(group_id=123456)
        self.assertEqual(cm.get_alias(), "")
        self.assertTrue(cm.set_alias(" 生存服玩家群 ")[0])
        self.assertEqual(cm.get_alias(), "生存服玩家群")
        self.assertEqual(self.snapshot()["alias"], "生存服玩家群")
        self.assertFalse(cm.set_alias("中文" * 9)[0])
        self.assertTrue(cm.set_alias("")[0])
        self.assertEqual(cm.get_alias(), "")
        self.assertEqual(self.snapshot()["alias"], "")

    def test_flags_are_written_under_policy(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.set_flag("show_last_online", True)
        cm.set_flag("use_central_db", True)
        cm.set_group_enabled(False)
        data = self.snapshot()
        self.assertFalse(data["enabled"])
        self.assertTrue(data["policy"]["show_last_online"])
        self.assertTrue(data["policy"]["use_central_db"])
        self.assertNotIn("last_online_enabled", data)
        self.assertNotIn("use_central_db", data)
        self.assertTrue(cm.get_flag("show_last_online"))
        self.assertFalse(cm.is_group_enabled())

    def test_legacy_flat_file_is_migrated_once(self):
        old = Path(sc.legacy_group_config_path(123456))
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(json.dumps({
            "servers": [{"name": "主服", "host": "mc.example.com:25565", "api_port": 25566}],
            "last_online_enabled": True,
            "show_server_latency": False,
            "use_central_db": True,
            "nickname_check_ignore_ids": ["10001"],
        }, ensure_ascii=False), encoding="utf-8")

        cm = sc.ConfigManager(group_id=123456)
        self.assertEqual([s["name"] for s in cm.get_all_servers()], ["主服"])
        self.assertEqual(cm.get_policy()["show_last_online"], True)
        self.assertEqual(cm.get_policy()["show_server_latency"], False)
        self.assertEqual(cm.get_policy()["use_central_db"], True)
        self.assertEqual(cm.get_nickname_ignore(), ["10001"])
        self.assertFalse((self.root / "onebot" / "123456" / "state.json").exists())
        archived = self.root / "onebot" / "legacy" / "group_configs" / "servers_123456.json"
        self.assertTrue(archived.exists(), "旧文件应移入 legacy/ 留档")
        self.assertFalse(old.exists())

        cm.set_flag("show_server_version", True)
        self.assertTrue(self.snapshot()["policy"]["show_server_version"])

    def test_legacy_new_layout_is_imported_and_archived(self):
        path = Path(sc.group_config_path(123456))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 1,
            "platform": "onebot",
            "group_id": "123456",
            "enabled": True,
            "alias": "旧群",
            "servers": [{"name": "主服", "host": "mc.example.com", "api_port": 25566}],
            "policy": {"show_last_online": True},
            "nickname_ignore": ["10001"],
        }, ensure_ascii=False), encoding="utf-8")

        cm = sc.ConfigManager(group_id=123456)
        self.assertEqual(cm.get_alias(), "旧群")
        self.assertEqual(cm.get_policy()["show_last_online"], True)
        self.assertTrue((self.root / "onebot" / "legacy" / "groups" / "123456"
                         / "state.json").exists())
        self.assertFalse(path.exists())

    def test_private_scope_state_file_migrates_as_private(self):
        session = "friend-123456"
        path = self.root / "onebot" / session / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "platform": "onebot",
            "group_id": session,
            "servers": [{"name": "私聊服", "host": "mc.example.com", "api_port": 25566}],
        }, ensure_ascii=False), encoding="utf-8")

        cm = sc.ConfigManager(session_id=session)
        data = cm.snapshot()
        self.assertEqual(data["scope_kind"], "private")
        self.assertEqual(data["scope_id"], session)
        self.assertEqual([s["name"] for s in data["servers"]], ["私聊服"])
        # 私聊作用域不出现在群列表里，也不会被当成数字群号。
        self.assertNotIn(session, sc.group_ids())
        # 采样沿用旧行为：私聊作用域里启用的服务器也参与端点汇总。
        self.assertIn(("私聊服", "mc.example.com", 25566),
                      sc.all_enabled_server_endpoints())

    def test_migrate_scan_and_delete(self):
        old = Path(sc.legacy_group_config_path(123456))
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(json.dumps({"servers": [{"name": "主服", "host": "mc.example.com"}]}),
                       encoding="utf-8")
        self.assertEqual(sc.migrate_legacy_group_configs(), (1, 0))
        self.assertTrue(sc.has_group_config(123456))
        self.assertTrue(sc.delete_group_config(123456))
        self.assertFalse(sc.has_group_config(123456))

    def test_nickname_ignore_is_deduplicated_and_trimmed(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.set_nickname_ignore([" 10001 ", "10002", "10001", ""])
        self.assertEqual(cm.get_nickname_ignore(), ["10001", "10002"])
        self.assertEqual(self.snapshot()["nickname_ignore"], ["10001", "10002"])

    def test_read_group_flag_uses_policy_only(self):
        self.assertEqual(sc.read_group_flag(123456, "show_server_latency"), True)
        cm = sc.ConfigManager(group_id=123456)
        cm.set_flag("show_server_latency", True)
        self.assertTrue(sc.read_group_flag(123456, "show_server_latency"))
        self.assertFalse(sc.read_group_flag(123456, "show_server_version"))

    def test_missing_db_with_legacy_archive_fails_closed(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.set_alias("主群")
        onebot_store.close_store()
        # 模拟运行库丢失，但 legacy/ 留档仍在。
        legacy = self.root / "onebot" / "legacy" / "groups" / "123456"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "state.json").write_text(json.dumps({"servers": []}), encoding="utf-8")
        for path in (self.root / "onebot").glob("config.sqlite3*"):
            path.unlink()
        with self.assertRaises(onebot_store.OneBotError):
            sc.ensure_store_ready()

    def _drop_db_with_archive(self):
        onebot_store.close_store()
        legacy = self.root / "onebot" / "legacy" / "groups" / "123456"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "state.json").write_text(json.dumps({"servers": []}), encoding="utf-8")
        for path in (self.root / "onebot").glob("config.sqlite3*"):
            path.unlink()

    def test_import_rebuilds_missing_db_despite_archive(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.add_server("主服", "mc.example.com", 25566)
        backup = sc.ensure_store_ready().export_state()
        self._drop_db_with_archive()
        with self.assertRaises(onebot_store.OneBotError):
            sc.ensure_store_ready()
        summary = sc.import_store_state(backup, 0)
        self.assertEqual(summary["groups"], 1)
        self.assertEqual(summary["servers"], 1)
        self.assertEqual([s["name"] for s in sc.ConfigManager(group_id=123456).get_all_servers()],
                         ["主服"])

    def test_failed_import_removes_new_empty_db(self):
        self._drop_db_with_archive()
        with self.assertRaises(onebot_store.OneBotError):
            sc.import_store_state({"format": "bad"}, 0)
        self.assertFalse((self.root / "onebot" / "config.sqlite3").exists())
        with self.assertRaises(onebot_store.OneBotError):
            sc.ensure_store_ready()

    def test_export_excludes_token_and_import_preserves_it(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.add_server("主服", "mc.example.com", 25566)
        cm.edit_server_console_token("主服", "BearerLikeToken")
        store = sc.ensure_store_ready()
        backup = store.export_state()
        self.assertNotIn("BearerLikeToken", json.dumps(backup))
        self.assertNotIn("console_token", json.dumps(backup["groups"]))
        summary = store.import_state(backup, backup["revision"],
                                     validator=sc.validate_backup_document)
        self.assertEqual(summary["groups"], 1)
        self.assertEqual(summary["servers"], 1)
        token = cm.get_all_servers()[0].get("console_token")
        self.assertEqual(token, "BearerLikeToken")

    def test_import_rejects_backup_with_token(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.add_server("主服", "mc.example.com")
        store = sc.ensure_store_ready()
        backup = store.export_state()
        backup["groups"][0]["servers"][0]["console_token"] = "leak"
        with self.assertRaises(onebot_store.OneBotError):
            store.import_state(backup, backup["revision"],
                               validator=sc.validate_backup_document)

    def test_concurrent_writes_do_not_lose_servers(self):
        import threading
        cm = sc.ConfigManager(group_id=123456)
        errors = []

        def worker(name):
            try:
                sc.ConfigManager(group_id=123456).add_server(name, "host.example")
            except Exception as exc:  # pragma: no cover - 失败时给出可读原因
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"服{i}",)) for i in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        names = {s["name"] for s in cm.get_all_servers()}
        self.assertEqual(names, {f"服{i}" for i in range(10)})


if __name__ == "__main__":
    unittest.main()
