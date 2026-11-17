"""S1 的隔离回归测试，只使用标准库、构造实例和构造消息。

仅用文件加载器导入不含文件/网络初始化的 platform_activation.py；其它待测入口
从允许的源码文件提取 AST 定义，移除框架装饰器后注入替身。不会导入 AstrBot、
项目包、数据库、配置管理器或统计存储，也不会加载真实配置/群记录/凭据。
由主代理检查后运行：python -B -m unittest discover -s tests -v
"""
import ast
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "_mcwatch_activation_isolated_test"
spec = importlib.util.spec_from_file_location(
    MODULE_NAME, ROOT / "utils" / "platform_activation.py"
)
activation = importlib.util.module_from_spec(spec)
# dataclass 需要在执行模块定义时能够通过 __module__ 找到该模块。
sys.modules[MODULE_NAME] = activation
spec.loader.exec_module(activation)

ALLOWED_AST_FILES = {
    "main.py",
    "commands/mc_handler.py",
    "utils/stats_sampler.py",
    "web/api.py",
}


def source_tree(relative_path):
    if relative_path not in ALLOWED_AST_FILES:
        raise AssertionError("测试不允许读取该源码文件")
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8-sig"))


def compile_definitions(nodes, namespace):
    tree = ast.Module(body=copy.deepcopy(nodes), type_ignores=[])
    # 延迟注解求值，避免为了 AstrMessageEvent 等注解导入框架。
    tree.body.insert(
        0, ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")],
            level=0,
        )
    )
    ast.fix_missing_locations(tree)
    exec(compile(tree, "<isolated-source-definitions>", "exec"), namespace)
    return namespace


def isolated_function(relative_path, name, **namespace):
    node = next(
        node for node in source_tree(relative_path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    node = copy.deepcopy(node)
    node.decorator_list = []
    return compile_definitions([node], namespace)[name]


def forbidden(*args, **kwargs):
    raise AssertionError("拒绝路径不应进入业务、文件、凭据或发送逻辑")


class ForbiddenObject:
    def __getattr__(self, name):
        forbidden()


class SyntheticEvent:
    def __init__(self, name="aiocqhttp", instance_id="onebot-a", message="/mc help"):
        object.__setattr__(self, "message_writes", [])
        object.__setattr__(self, "message_str", message)
        object.__setattr__(self, "platform_name", name)
        object.__setattr__(self, "instance_id", instance_id)
        object.__setattr__(self, "is_at_or_wake_command", True)

    def __setattr__(self, name, value):
        if name == "message_str":
            self.message_writes.append(value)
        object.__setattr__(self, name, value)

    def get_platform_name(self):
        return self.platform_name

    def get_platform_id(self):
        return self.instance_id

    get_group_id = forbidden
    get_sender_id = forbidden
    send = forbidden
    stop_event = forbidden

    def __getattr__(self, name):
        forbidden()


class PublicSelectionOnly:
    """读取非选择项会立即失败，确保门禁不探查密钥或名单。"""
    def __init__(self, **values):
        self.values = values
        self.reads = []

    def get(self, key, default=None):
        if key not in {"active_platform", "active_bot_instance"}:
            raise AssertionError("选择器读取了非公开选择项")
        self.reads.append(key)
        return self.values.get(key, default)


def fake_context(entries):
    def get_insts():
        return [
            SimpleNamespace(
                meta=lambda entry=entry: SimpleNamespace(
                    name=entry.name, id=entry.instance_id
                )
            )
            for entry in entries
        ]
    return SimpleNamespace(
        platform_manager=SimpleNamespace(get_insts=get_insts)
    )


def onebot(instance_id="onebot-a"):
    return activation.PlatformInstance("aiocqhttp", instance_id)


def official(instance_id="official-a", adapter="qq_official"):
    return activation.PlatformInstance(adapter, instance_id)


def configure_onebot():
    activation.configure(
        fake_context([onebot(), official()]),
        PublicSelectionOnly(active_platform="onebot"),
    )


class SelectionTests(unittest.TestCase):
    def tearDown(self):
        activation.shutdown()

    def test_unique_onebot_selected_with_official_present(self):
        entries = [onebot(), official()]
        controller = activation.ActivationController("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["state"], "active")
        self.assertTrue(controller.allows(SyntheticEvent()))
        self.assertFalse(controller.allows(SyntheticEvent("qq_official", "official-a")))
        self.assertFalse(controller.allows(SyntheticEvent("aiocqhttp", "wrong-id")))

    def test_multiple_onebot_instances_need_explicit_selection(self):
        entries = [onebot(), onebot("onebot-b"), official()]
        controller = activation.ActivationController("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["state"], "ambiguous")
        self.assertFalse(controller.allows(SyntheticEvent()))
        self.assertEqual(controller.status()["selected_instance"], "")

    def test_explicit_selection_accepts_only_exact_instance(self):
        entries = [onebot(), onebot("onebot-b"), official()]
        controller = activation.ActivationController(
            "onebot", "onebot-b", lambda: entries
        )
        self.assertTrue(controller.allows(SyntheticEvent("aiocqhttp", "onebot-b")))
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_missing_explicit_selection_does_not_fall_back(self):
        controller = activation.ActivationController(
            "onebot", "missing", lambda: [onebot()]
        )
        self.assertEqual(controller.status()["state"], "missing")
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_removed_selected_instance_does_not_fail_over(self):
        entries = [onebot()]
        controller = activation.ActivationController("onebot", "", lambda: entries)
        self.assertTrue(controller.allows(SyntheticEvent()))
        entries[:] = [onebot("replacement")]
        self.assertEqual(controller.status()["state"], "missing")
        self.assertEqual(controller.status()["selected_instance"], "onebot-a")
        self.assertFalse(controller.allows(SyntheticEvent("aiocqhttp", "replacement")))
        entries[:] = [onebot()]
        self.assertTrue(controller.allows(SyntheticEvent()))

    def test_new_candidate_does_not_replace_latched_selection(self):
        entries = [onebot()]
        controller = activation.ActivationController("onebot", "", lambda: entries)
        self.assertTrue(controller.allows(SyntheticEvent()))
        entries.append(onebot("onebot-b"))
        self.assertTrue(controller.allows(SyntheticEvent()))
        self.assertFalse(controller.allows(SyntheticEvent("aiocqhttp", "onebot-b")))

    def test_initial_empty_inventory_can_become_ready(self):
        entries = []
        controller = activation.ActivationController("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["state"], "missing")
        entries.extend([onebot(), official()])
        self.assertTrue(controller.allows(SyntheticEvent()))

    def test_unknown_platform_is_not_a_supported_candidate(self):
        entries = [activation.PlatformInstance("webchat", "webchat"), onebot()]
        controller = activation.ActivationController("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["choices"], [
            {"platform": "onebot", "instance_id": "onebot-a"}
        ])
        self.assertFalse(controller.allows(SyntheticEvent("webchat", "webchat")))

    def test_duplicate_instance_ids_fail_closed(self):
        controller = activation.ActivationController(
            "onebot", "duplicate",
            lambda: [onebot("duplicate"), official("duplicate")],
        )
        self.assertEqual(controller.status()["state"], "ambiguous")
        self.assertFalse(controller.allows(SyntheticEvent("aiocqhttp", "duplicate")))

    def test_inventory_errors_and_malformed_metadata_fail_closed(self):
        inventories = [
            lambda: [object()],
            lambda: [activation.PlatformInstance("aiocqhttp", "")],
            forbidden,
        ]
        for inventory in inventories:
            with self.subTest(inventory=inventory):
                controller = activation.ActivationController("onebot", "", inventory)
                self.assertEqual(controller.status()["state"], "unavailable")
                self.assertFalse(controller.allows(SyntheticEvent()))

    def test_invalid_platform_does_not_enumerate_adapters(self):
        controller = activation.ActivationController("unsupported", "", forbidden)
        self.assertEqual(controller.status()["state"], "invalid")
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_both_official_transports_remain_closed_during_s1(self):
        self.assertNotIn("qq_official", activation.READY_PLATFORMS)
        for adapter in ("qq_official", "qq_official_webhook"):
            with self.subTest(adapter=adapter):
                controller = activation.ActivationController(
                    "qq_official", "official-a",
                    lambda adapter=adapter: [official(adapter=adapter)],
                )
                self.assertEqual(controller.status()["state"], "not_ready")
                self.assertFalse(
                    controller.allows(SyntheticEvent(adapter, "official-a"))
                )

    def test_bad_event_metadata_is_rejected(self):
        controller = activation.ActivationController(
            "onebot", "", lambda: [onebot()]
        )
        self.assertFalse(controller.allows(ForbiddenObject()))

    def test_configure_reads_only_public_selection_and_snapshots_it(self):
        config = PublicSelectionOnly(
            active_platform="onebot", active_bot_instance="onebot-a"
        )
        entries = [onebot(), onebot("onebot-b"), official()]
        activation.configure(fake_context(entries), config)
        self.assertEqual(config.reads, ["active_platform", "active_bot_instance"])
        config.values.update(active_platform="qq_official",
                             active_bot_instance="official-a")
        self.assertTrue(activation.allows(SyntheticEvent()))
        self.assertFalse(activation.allows(SyntheticEvent("qq_official", "official-a")))
        self.assertEqual(config.reads, ["active_platform", "active_bot_instance"])

    def test_configure_invalid_instance_value_does_not_auto_select(self):
        activation.configure(
            fake_context([onebot()]),
            PublicSelectionOnly(active_bot_instance=123),
        )
        self.assertEqual(activation.status()["state"], "invalid")
        self.assertFalse(activation.allows(SyntheticEvent()))

    def test_default_configuration_and_shutdown(self):
        activation.configure(fake_context([onebot()]))
        self.assertTrue(activation.onebot_business_enabled())
        activation.shutdown()
        self.assertFalse(activation.onebot_business_enabled())
        self.assertFalse(activation.allows(ForbiddenObject()))
        self.assertEqual(activation.status()["state"], "unavailable")


def isolated_command_class(handler, config_manager):
    tree = source_tree("main.py")
    original = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MCWatcher"
    )
    command_names = [
        node.name for node in original.body
        if isinstance(node, ast.AsyncFunctionDef) and node.decorator_list
    ]
    command_class = copy.deepcopy(original)
    command_class.bases = []
    command_class.keywords = []
    command_class.decorator_list = []
    command_class.body = [
        node for node in command_class.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in command_names
    ]
    for node in command_class.body:
        node.decorator_list = []
    rest = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_rest_after_command"
    )
    namespace = compile_definitions([rest, command_class], {
        "platform_activation": activation,
        "handle_mc_command": handler,
        "ConfigManager": config_manager,
    })
    return namespace["MCWatcher"](), command_names


class EntryIsolationTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        activation.shutdown()

    async def test_every_chat_entry_rejects_before_message_or_business_access(self):
        configure_onebot()
        watcher, names = isolated_command_class(forbidden, forbidden)
        self.assertIn("broadcast", names)
        self.assertIn("bind_command", names)
        self.assertIn("mc", names)
        for name in names:
            with self.subTest(entry=name):
                original = "/广播 survival synthetic-text"
                event = SyntheticEvent("qq_official", "official-a", original)
                self.assertEqual(
                    [item async for item in getattr(watcher, name)(event)], []
                )
                self.assertEqual(event.message_str, original)
                self.assertEqual(event.message_writes, [])

    async def test_selected_onebot_aliases_preserve_dispatch(self):
        configure_onebot()
        dispatched = []

        async def handler(event):
            dispatched.append(event.message_str)
            yield "synthetic-result"

        config = SimpleNamespace(
            get_all_servers=lambda: [{"name": "survival"}]
        )
        watcher, names = isolated_command_class(handler, lambda **kwargs: config)
        cases = [
            ("mc", "/mc help", "/mc help"),
            ("online", "/在线", "/mc status"),
            ("query", "/查询 Player_A", "/mc stats Player_A"),
            ("rank", "/排行 时长", "/mc rank 时长"),
            ("broadcast", "/广播 survival hello", "/mc say -s survival hello"),
            ("exec_command", "/执行 [survival] say hello",
             "/mc exec [survival] say hello"),
            ("tps_command", "/tps -s survival", "/mc tps -s survival"),
            ("bind_command", "/绑定 123456", "/mc bind 123456"),
            ("unbind_command", "/解绑 Player_A", "/mc unbind Player_A"),
            ("checkbind_command", "/查绑定 ID Player_A", "/mc check ID Player_A"),
            ("bindhelp_command", "/绑定帮助", "/mc bindhelp"),
            ("help_command", "/帮助", "/mc help"),
            ("lastonline_command", "/上次在线 开", "/mc lastonline 开"),
            ("checknick_command", "/检测昵称", "/mc checknick"),
            ("bindcheck_command", "/绑定检测", "/mc bindcheck"),
        ]
        self.assertEqual(set(names), {item[0] for item in cases})
        for name, text, expected in cases:
            with self.subTest(entry=name):
                event = SyntheticEvent(message=text)
                # 广播会读取选中事件的群号，但配置管理器本身始终为替身。
                event.get_group_id = lambda: "SYNTHETIC_GROUP"
                self.assertEqual(
                    [item async for item in getattr(watcher, name)(event)],
                    ["synthetic-result"],
                )
                self.assertEqual(dispatched[-1], expected)

    async def test_direct_dispatcher_rejects_before_config_lookup(self):
        configure_onebot()
        handler = isolated_function(
            "commands/mc_handler.py", "handle_mc_command",
            platform_activation=activation,
            ConfigManager=forbidden,
            WhitelistManager=forbidden,
        )
        event = SyntheticEvent("qq_official", "official-a", "/mc bind 123456")
        self.assertEqual([item async for item in handler(event)], [])
        self.assertEqual(event.message_writes, [])

    async def test_disabled_sampler_does_not_read_roster_config_or_credentials(self):
        activation.configure(
            fake_context([official()]),
            PublicSelectionOnly(active_platform="qq_official"),
        )
        sampler = isolated_function(
            "utils/stats_sampler.py", "sample_once",
            platform_activation=activation,
            stats_snapshot=ForbiddenObject(),
            _server_endpoints=forbidden,
            WhitelistManager=forbidden,
            mod_http=ForbiddenObject(),
        )
        self.assertIsNone(await sampler())

    async def test_remote_web_guard_blocks_before_handler(self):
        from functools import wraps
        activation.configure(
            fake_context([official()]),
            PublicSelectionOnly(active_platform="qq_official"),
        )
        wrap_remote = isolated_function(
            "web/api.py", "_active_remote_handler",
            platform_activation=activation,
            wraps=wraps,
            _err=lambda text: {"status": "error", "message": text},
        )
        result = await wrap_remote(forbidden)()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], activation.status()["message"])

    async def test_remote_web_guard_preserves_selected_onebot_handler(self):
        from functools import wraps
        configure_onebot()
        calls = []

        async def handler():
            calls.append("called")
            return {"status": "ok"}

        wrap_remote = isolated_function(
            "web/api.py", "_active_remote_handler",
            platform_activation=activation,
            wraps=wraps,
            _err=forbidden,
        )
        self.assertEqual(await wrap_remote(handler)(), {"status": "ok"})
        self.assertEqual(calls, ["called"])


if __name__ == "__main__":
    unittest.main()
