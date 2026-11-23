"""MOTD 颜色解析（motd_runs / clean_motd / remark_runs_of）的单元回归。

隔离测试不装 AstrBot：用内存桩补 server_config 需要的 sub_dir / normalize_rule，
其余（group_policy 等）按真实文件加载。
"""
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_mcwatch_synthetic_motd"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

utils_pkg = ModuleType(PACKAGE + ".utils")
utils_pkg.__path__ = [str(ROOT / "utils")]
sys.modules[PACKAGE + ".utils"] = utils_pkg
plugin_paths = ModuleType(PACKAGE + ".utils.plugin_paths")
plugin_paths.sub_dir = lambda name: str(ROOT / "data" / name)
sys.modules[PACKAGE + ".utils.plugin_paths"] = plugin_paths
bind_rule = ModuleType(PACKAGE + ".utils.bind_rule")
bind_rule.normalize_rule = lambda x: x
sys.modules[PACKAGE + ".utils.bind_rule"] = bind_rule

from _mcwatch_synthetic_motd.config import server_config as sc


class MotdRunsTest(unittest.TestCase):
    def plain(self, raw):
        return sc.motd_runs(raw)[0]

    def runs(self, raw):
        return sc.motd_runs(raw)[1]

    def test_legacy_color_codes(self):
        plain, runs = sc.motd_runs("§cHello §aWorld")
        self.assertEqual(plain, "Hello World")
        self.assertEqual(runs, [("Hello ", "#d64545"), ("World", "#3aa23f")])

    def test_reset_returns_to_ambient(self):
        plain, runs = sc.motd_runs("§cRed §rPlain")
        self.assertEqual(plain, "Red Plain")
        self.assertEqual(runs, [("Red ", "#d64545"), ("Plain", None)])

    def test_formatting_codes_are_noops(self):
        plain, runs = sc.motd_runs("§lBold §kObf §cRed")
        self.assertEqual(plain, "Bold Obf Red")
        self.assertEqual(runs, [("Bold Obf ", None), ("Red", "#d64545")])

    def test_plain_text_no_color(self):
        plain, runs = sc.motd_runs("plain text")
        self.assertEqual(plain, "plain text")
        self.assertEqual(runs, [("plain text", None)])

    def test_json_color_names(self):
        raw = '{"text":"A","color":"red","extra":[{"text":"B","color":"gold"}]}'
        plain, runs = sc.motd_runs(raw)
        self.assertEqual(plain, "AB")
        self.assertEqual(runs, [("A", "#d64545"), ("B", "#a97a0a")])

    def test_json_embedded_legacy_codes(self):
        plain, runs = sc.motd_runs('{"text":"§cHi §aYo"}')
        self.assertEqual(plain, "Hi Yo")
        self.assertEqual(runs, [("Hi ", "#d64545"), ("Yo", "#3aa23f")])

    def test_json_empty_object_is_empty(self):
        self.assertEqual(sc.motd_runs('{"color":"red"}'), ("", []))

    def test_bracket_plain_not_json(self):
        plain, runs = sc.motd_runs("[VIP] Welcome")
        self.assertEqual(plain, "[VIP] Welcome")
        self.assertEqual(runs, [("[VIP] Welcome", None)])

    def test_literal_newline_becomes_space(self):
        plain, _ = sc.motd_runs("§cHello §aWorld\\n§e!")
        self.assertEqual(plain, "Hello World !")

    def test_plain_matches_clean_motd(self):
        for raw in [
            "§cHello §aWorld",
            '{"text":"A","color":"red","extra":[{"text":"B","color":"gold"}]}',
            "[VIP] Welcome",
            "§lBold §cRed §rDone",
            "plain",
            "",
            None,
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(sc.motd_runs(raw)[0], sc.clean_motd(raw))

    def test_truncation_matches_clean_motd(self):
        long_raw = "§c" + "很长的中文备注内容" * 20 + "§f"
        self.assertEqual(sc.motd_runs(long_raw)[0], sc.clean_motd(long_raw))
        # 截断后的 runs 拼接应与 plain 一致
        plain, runs = sc.motd_runs(long_raw)
        self.assertEqual("".join(t for t, _ in runs), plain)

    def test_hex_color_is_out_of_scope_but_text_survives(self):
        plain, _ = sc.motd_runs("§x§F§F§0§0§0§0hex")
        self.assertEqual(plain, "hex")


class RemarkRunsOfTest(unittest.TestCase):
    def test_returns_runs_only_when_motd_source_active(self):
        entry = {"remark_src": "motd", "remark_motd": "Hi",
                 "remark_motd_runs": [["Hi", "#d64545"]]}
        self.assertEqual(sc.remark_runs_of(entry), [["Hi", "#d64545"]])

    def test_returns_none_for_manual_source(self):
        entry = {"remark": "手填", "remark_motd_runs": [["Hi", "#d64545"]]}
        self.assertIsNone(sc.remark_runs_of(entry))

    def test_returns_none_when_cache_empty(self):
        entry = {"remark_src": "motd", "remark_motd": ""}
        self.assertIsNone(sc.remark_runs_of(entry))


class SetRemarkMotdRunsTest(unittest.TestCase):
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

    def test_stores_and_clears_runs(self):
        cm = sc.ConfigManager(group_id=123456)
        self.assertTrue(cm.add_server("srv", "1.2.3.4"))
        self.assertTrue(cm.set_remark_motd("srv", "Hi", 1000, runs=[["Hi", "#d64545"]]))
        data = cm.get_all_servers()
        self.assertEqual(data[0]["remark_motd_runs"], [["Hi", "#d64545"]])
        # 无颜色时清掉 runs 键
        self.assertTrue(cm.set_remark_motd("srv", "Yo", 1001, runs=[]))
        self.assertNotIn("remark_motd_runs", cm.get_all_servers()[0])


if __name__ == "__main__":
    unittest.main()
