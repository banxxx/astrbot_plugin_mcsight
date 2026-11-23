"""OneBot 群文件与共享群策略的格式回归。

群规则统一放在 policy 下，字段与 QQ 官方群对象一致。旧版
group_configs/servers_<群号>.json 只在首次读取时一次性迁到
onebot/<群号>/state.json；这里用临时目录验证落盘结构、迁移和读取口径。
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

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def read(self, gid=123456):
        return json.loads(Path(sc.group_config_path(gid)).read_text(encoding="utf-8"))

    def test_new_file_has_standard_shape(self):
        cm = sc.ConfigManager(group_id=123456)
        data = self.read()
        self.assertEqual(set(data), {
            "schema_version", "platform", "group_id", "enabled", "alias", "servers",
            "policy", "nickname_ignore",
        })
        self.assertEqual(data["schema_version"], sc.GROUP_FILE_SCHEMA_VERSION)
        self.assertEqual(data["platform"], "onebot")
        self.assertEqual(data["group_id"], "123456")
        self.assertTrue(data["enabled"])
        self.assertEqual(data["alias"], "")
        self.assertEqual(data["servers"], [])
        self.assertEqual(set(data["policy"]), POLICY_KEYS)
        self.assertEqual(data["policy"], POLICY_DEFAULTS)
        self.assertEqual(cm.get_policy(), POLICY_DEFAULTS)

    def test_alias_is_optional_and_normalized(self):
        cm = sc.ConfigManager(group_id=123456)
        self.assertEqual(cm.get_alias(), "")
        self.assertTrue(cm.set_alias(" 生存服玩家群 ")[0])
        self.assertEqual(cm.get_alias(), "生存服玩家群")
        self.assertEqual(self.read()["alias"], "生存服玩家群")
        # 超长别名被拒绝；空串清除别名
        self.assertFalse(cm.set_alias("中文" * 9)[0])
        self.assertTrue(cm.set_alias("")[0])
        self.assertEqual(cm.get_alias(), "")
        self.assertEqual(self.read()["alias"], "")

    def test_flags_are_written_under_policy(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.set_flag("show_last_online", True)
        cm.set_flag("use_central_db", True)
        cm.set_group_enabled(False)
        data = self.read()
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
        self.assertTrue(Path(sc.group_config_path(123456)).exists())
        self.assertTrue(old.exists(), "旧文件保留给用户核对")

        cm.set_flag("show_server_version", True)
        data = self.read()
        self.assertEqual(data["servers"][0]["name"], "主服")
        self.assertEqual(data["policy"]["show_server_version"], True)
        for legacy in ("last_online_enabled", "use_central_db", "nickname_check_ignore_ids"):
            self.assertNotIn(legacy, data)

    def test_migrate_scan_and_delete_remove_both_layouts(self):
        old = Path(sc.legacy_group_config_path(123456))
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(json.dumps({"servers": [{"name": "主服", "host": "mc.example.com"}]}),
                       encoding="utf-8")
        self.assertEqual(sc.migrate_legacy_group_configs(), (1, 0))
        self.assertTrue(sc.has_group_config(123456))
        self.assertTrue(sc.delete_group_config(123456))
        self.assertFalse(sc.has_group_config(123456))
        self.assertFalse(old.exists())

    def test_nickname_ignore_is_deduplicated_and_trimmed(self):
        cm = sc.ConfigManager(group_id=123456)
        cm.set_nickname_ignore([" 10001 ", "10002", "10001", ""])
        self.assertEqual(cm.get_nickname_ignore(), ["10001", "10002"])
        self.assertEqual(self.read()["nickname_ignore"], ["10001", "10002"])

    def test_read_group_flag_uses_policy_only(self):
        self.assertEqual(sc.read_group_flag(123456, "show_server_latency"), True)
        path = Path(sc.group_config_path(123456))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "servers": [],
            "show_server_latency": False,
            "policy": {"show_server_latency": True},
        }), encoding="utf-8")
        self.assertTrue(sc.read_group_flag(123456, "show_server_latency"))
        self.assertFalse(sc.read_group_flag(123456, "show_server_version"))


if __name__ == "__main__":
    unittest.main()
