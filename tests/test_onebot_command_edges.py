"""OneBot 入口边界回归：帮助豁免、/mc say all、/解绑 all。

只从源码抽取单个函数并注入替身，不导入 AstrBot、插件包或真实配置。
"""

import ast
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"commands/mc_handler.py", "commands/services.py", "commands/bind.py"}


def isolated_function(relative_path, name, **namespace):
    assert relative_path in ALLOWED
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8-sig"))
    node = copy.deepcopy(next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    ))
    node.decorator_list = []
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        node,
    ], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, f"<isolated {name}>", "exec"), namespace)
    return namespace[name]


class Event:
    def __init__(self, message="/mc help", group="100000001", user="42"):
        self.message_str = message
        self.group = group
        self.user = user
        self.session_id = "synthetic-session"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user

    def plain_result(self, text):
        return ("text", text)


async def collect(iterator):
    return [item async for item in iterator]


class DisabledGroupHelpTests(unittest.IsolatedAsyncioTestCase):
    """与官方一致：本群功能关闭仍允许查看帮助，其它命令被拦。"""

    def dispatcher(self, enabled):
        calls = []
        async def help_handler(event):
            calls.append("help")
            yield "HELP"
        async def bindhelp_handler(event):
            calls.append("bindhelp")
            yield "BINDHELP"
        async def other_handler(*args):
            calls.append("other")
            yield "OTHER"

        handler_names = (
            "handle_whitelist", "handle_help", "handle_bindhelp", "handle_add",
            "handle_remove", "handle_hidden", "handle_edit", "handle_batchadd",
            "handle_batchremove", "handle_list", "handle_move", "handle_swap",
            "handle_lastonline", "handle_say", "handle_tps", "handle_status",
            "handle_stats", "handle_exec", "handle_token", "handle_bind",
            "handle_unbind", "handle_check", "handle_checknick",
            "handle_nicknamecheck_switch", "handle_bindcheck", "handle_rank",
        )
        namespace = {name: other_handler for name in handler_names}
        namespace.update(handle_help=help_handler, handle_bindhelp=bindhelp_handler)

        class Config:
            def __init__(self, **kwargs):
                pass
            def is_group_enabled(self):
                return enabled

        class Whitelist:
            def is_blacklisted(self, user_id):
                return False
            def is_super_admin(self, user_id):
                return False

        namespace.update(
            platform_activation=SimpleNamespace(accepts=lambda event: True),
            official_runtime=SimpleNamespace(handle=other_handler),
            ConfigManager=Config,
            WhitelistManager=Whitelist,
            is_astrbot_super_admin=lambda event: True,
            check_permission=other_handler,
            debounce=SimpleNamespace(
                gate=lambda *args: (True, 0),
                should_notify=lambda *args: False,
                window_of=lambda command: 1,
            ),
            ADMIN_CMDS=frozenset(),
            PERMISSION_REQUIRED={},
            LEVEL_SUPER_ADMIN=4,
            logger=SimpleNamespace(info=lambda *args: None),
        )
        return isolated_function("commands/mc_handler.py", "handle_mc_command",
                                 **namespace), calls

    async def test_disabled_group_keeps_help_and_bindhelp_only(self):
        dispatcher, calls = self.dispatcher(enabled=False)
        self.assertEqual(await collect(dispatcher(Event("/mc help"))), ["HELP"])
        self.assertEqual(await collect(dispatcher(Event("/mc bindhelp"))), ["BINDHELP"])
        blocked = await collect(dispatcher(Event("/mc status")))
        self.assertEqual(len(blocked), 1)
        self.assertIn("本群功能已关闭", blocked[0][1])
        self.assertEqual(calls, ["help", "bindhelp"])

    async def test_enabled_group_dispatches_normally(self):
        dispatcher, calls = self.dispatcher(enabled=True)
        self.assertEqual(await collect(dispatcher(Event("/mc status"))), ["OTHER"])
        self.assertEqual(calls, ["other"])


class SayAllTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_and_bare_server_selectors(self):
        sent = []
        class Whitelist:
            mod_api_token = "synthetic-token"
            def has_mod_api(self, server):
                return server.get("api_port") is not None
            def get_server_port(self, server):
                return server["api_port"]
        async def broadcast(host, port, token, message):
            sent.append((host, port, message))
            return True

        handler = isolated_function(
            "commands/services.py", "handle_say",
            WhitelistManager=Whitelist,
            send_broadcast_via_mod=broadcast,
            asyncio=asyncio,
            logger=SimpleNamespace(error=lambda *args: None),
        )
        servers = [
            {"name": "one", "host": "one.invalid", "api_port": 8080},
            {"name": "two", "host": "two.invalid", "api_port": 8081},
            {"name": "plain", "host": "plain.invalid", "api_port": None},
        ]
        config = SimpleNamespace(get_all_servers=lambda: servers)

        result = await collect(handler(Event(), config, ["say", "all", "hello"]))
        self.assertIn("所有 2 个服务器", result[0][1])
        self.assertEqual([item[2] for item in sent], ["hello", "hello"])
        sent.clear()

        result = await collect(handler(Event(), config,
                                       ["say", "-s", "all", "hi"]))
        self.assertIn("所有 2 个服务器", result[0][1])
        self.assertEqual([item[2] for item in sent], ["hi", "hi"])
        sent.clear()

        result = await collect(handler(Event(), config,
                                       ["say", "one", "targeted"]))
        self.assertIn("服务器「one」", result[0][1])
        self.assertEqual(sent, [("one.invalid", 8080, "targeted")])


class UnbindAllTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_and_missing_target_unbind_self(self):
        calls = []
        async def by_qq(event, config, wm, use_central, qq, server):
            calls.append(("qq", qq, server))
            yield "UNBOUND"
        async def by_game(event, config, wm, use_central, game_id, server):
            calls.append(("game", game_id, server))
            yield "GAME"

        class Whitelist:
            mod_api_token = "synthetic-token"
            def has_mod_api(self, server):
                return True

        handler = isolated_function(
            "commands/bind.py", "handle_unbind",
            WhitelistManager=Whitelist,
            _do_unbind_by_qq=by_qq,
            _do_unbind_by_game_id=by_game,
            check_permission=lambda *args: (_ for _ in ()).throw(
                AssertionError("本人解绑不应检查管理员权限")),
        )
        config = SimpleNamespace(
            get_flag=lambda name: False,
            get_all_servers=lambda: [{"name": "one", "host": "one.invalid",
                                      "api_port": 8080}],
        )

        self.assertEqual(await collect(handler(Event(user="42"), config,
                                               ["unbind", "all"])), ["UNBOUND"])
        self.assertEqual(calls[-1], ("qq", "42", None))
        self.assertEqual(await collect(handler(Event(user="42"), config,
                                               ["unbind"])), ["UNBOUND"])
        self.assertEqual(calls[-1], ("qq", "42", None))
        self.assertEqual(await collect(handler(Event(user="42"), config,
                                               ["unbind", "all", "-s", "one"])),
                         ["UNBOUND"])
        self.assertEqual(calls[-1], ("qq", "42", "one"))


if __name__ == "__main__":
    unittest.main()
