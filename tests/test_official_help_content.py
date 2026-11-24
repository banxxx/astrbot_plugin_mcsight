"""官方帮助图内容回归：只解析源码字符串，不导入 Pillow / AstrBot。"""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELP = ROOT / "features" / "help_image" / "official_help_generator.py"
BIND_HELP = ROOT / "features" / "help_image" / "bind_help_generator.py"


def source_strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return "\n".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


class OfficialHelpContentTests(unittest.TestCase):
    def test_help_lists_official_commands_and_identity_limit(self):
        text = source_strings(HELP)
        for expected in ("/mc setup ", "/mc operation ", "/mc command ",
                         "/mc page ", "/mc guide ", "命令指引", "不显示数字 QQ 号",
                         "/mc whitelist ", "super add|remove", "/mc blacklist ",
                         "不回显官方标识"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_bind_help_does_not_reuse_onebot_binding_identity(self):
        text = source_strings(BIND_HELP)
        for expected in ("旧 OneBot", "@成员", "不显示数字 QQ 号",
                         "不会自动重发"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
