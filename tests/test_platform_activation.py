"""S1 的隔离回归测试，只使用标准库、构造实例和构造消息。

仅用文件加载器导入不含文件/网络初始化的 platform_activation.py；其它待测入口
从允许的源码文件提取 AST 定义，移除框架装饰器后注入替身。不会导入 AstrBot、
项目包、数据库、配置管理器或统计存储，也不会加载真实配置/群记录/凭据。
由主代理检查后运行：python -B -m unittest discover -s tests -v
"""
import ast
import asyncio
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch


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
    "commands/common.py",
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
    activation.refresh(framework_loaded=True)


def ready_controller(platform, instance_id, inventory):
    """旧回归显式模拟框架已加载；官方测试先保存，再模拟完整启动。"""
    controller = activation.ActivationController(platform, instance_id, inventory)
    controller.initialize_selection(framework_loaded=True)
    if platform == "qq_official" and instance_id:
        controller.save_selection({
            "platform": platform, "instance_id": instance_id,
            "expected_revision": 0,
        })
        controller = activation.ActivationController(platform, instance_id, inventory, store=controller.store)
    return controller


class SelectionTests(unittest.TestCase):
    def tearDown(self):
        activation.shutdown()

    def test_unique_onebot_selected_with_official_present(self):
        entries = [onebot(), official()]
        controller = ready_controller("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["state"], "active")
        self.assertTrue(controller.allows(SyntheticEvent()))
        self.assertFalse(controller.allows(SyntheticEvent("qq_official", "official-a")))
        self.assertFalse(controller.allows(SyntheticEvent("aiocqhttp", "wrong-id")))

    def test_multiple_onebot_instances_need_explicit_selection(self):
        entries = [onebot(), onebot("onebot-b"), official()]
        controller = ready_controller("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["state"], "ambiguous")
        self.assertFalse(controller.allows(SyntheticEvent()))
        self.assertEqual(controller.status()["selected_instance"], "")

    def test_explicit_selection_accepts_only_exact_instance(self):
        entries = [onebot(), onebot("onebot-b"), official()]
        controller = ready_controller(
            "onebot", "onebot-b", lambda: entries
        )
        self.assertTrue(controller.allows(SyntheticEvent("aiocqhttp", "onebot-b")))
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_missing_explicit_selection_does_not_fall_back(self):
        controller = ready_controller(
            "onebot", "missing", lambda: [onebot()]
        )
        self.assertEqual(controller.status()["state"], "missing")
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_removed_selected_instance_does_not_fail_over(self):
        entries = [onebot()]
        controller = ready_controller("onebot", "", lambda: entries)
        self.assertTrue(controller.allows(SyntheticEvent()))
        entries[:] = [onebot("replacement")]
        controller._invalidate_status()
        self.assertEqual(controller.status()["state"], "missing")
        self.assertEqual(controller.status()["selected_instance"], "onebot-a")
        self.assertFalse(controller.allows(SyntheticEvent("aiocqhttp", "replacement")))
        entries[:] = [onebot()]
        controller._invalidate_status()
        self.assertTrue(controller.allows(SyntheticEvent()))

    def test_new_candidate_does_not_replace_latched_selection(self):
        entries = [onebot()]
        controller = ready_controller("onebot", "", lambda: entries)
        self.assertTrue(controller.allows(SyntheticEvent()))
        entries.append(onebot("onebot-b"))
        controller._invalidate_status()
        self.assertTrue(controller.allows(SyntheticEvent()))
        self.assertFalse(controller.allows(SyntheticEvent("aiocqhttp", "onebot-b")))

    def test_initial_empty_inventory_can_become_ready(self):
        entries = []
        controller = ready_controller("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["state"], "missing")
        entries.extend([onebot(), official()])
        controller.initialize_selection()
        self.assertTrue(controller.allows(SyntheticEvent()))

    def test_unknown_platform_is_not_a_supported_candidate(self):
        entries = [activation.PlatformInstance("webchat", "webchat"), onebot()]
        controller = ready_controller("onebot", "", lambda: entries)
        self.assertEqual(controller.status()["choices"], [
            {"platform": "onebot", "instance_id": "onebot-a",
             "adapter": "aiocqhttp", "adapter_status": "unknown"}
        ])
        self.assertFalse(controller.allows(SyntheticEvent("webchat", "webchat")))

    def test_duplicate_instance_ids_fail_closed(self):
        controller = ready_controller(
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
                controller = ready_controller("onebot", "", inventory)
                self.assertEqual(controller.status()["state"], "unavailable")
                self.assertFalse(controller.allows(SyntheticEvent()))

    def test_invalid_platform_does_not_enumerate_adapters(self):
        controller = ready_controller("unsupported", "", forbidden)
        self.assertEqual(controller.status()["state"], "invalid")
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_both_official_transports_remain_closed_during_s1(self):
        self.assertNotIn("qq_official", activation.READY_PLATFORMS)
        for adapter in ("qq_official", "qq_official_webhook"):
            with self.subTest(adapter=adapter):
                controller = ready_controller(
                    "qq_official", "official-a",
                    lambda adapter=adapter: [official(adapter=adapter)],
                )
                self.assertEqual(controller.status()["state"], "not_ready")
                self.assertFalse(
                    controller.allows(SyntheticEvent(adapter, "official-a"))
                )

    def test_bad_event_metadata_is_rejected(self):
        controller = ready_controller(
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
        activation.refresh(framework_loaded=True)
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
        if isinstance(node, ast.AsyncFunctionDef) and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"command", "regex"} for d in node.decorator_list
        )
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
            ("broadcast", "/广播 survival hello", "/mc say survival hello"),
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
        official_only = {"guide_common_command", "guide_admin_command"}
        self.assertEqual(set(names) - official_only, {item[0] for item in cases})
        # 指引按钮是官方 Markdown 能力：这两个入口在 OneBot 下直接返回、不改写消息。
        for name in official_only:
            with self.subTest(entry=name):
                event = SyntheticEvent(message="/命令指引")
                self.assertEqual(
                    [item async for item in getattr(watcher, name)(event)], []
                )
                self.assertEqual(event.message_writes, [])
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


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mcwatch-synthetic-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "activation.json"
        self.entries = [onebot(), onebot("onebot-b"), official(), official("official-b")]
        self.store = activation.SelectionStore(self.path)

    def controller(self, *, runtime=None, platform="onebot", instance_id="onebot-a"):
        return activation.ActivationController(platform, instance_id, lambda: self.entries,
                                               store=self.store, runtime=runtime)

    def start(self):
        controller = self.controller()
        controller.initialize_selection()
        self.assertTrue(controller.allows(SyntheticEvent()))
        return controller

    def save_onebot(self, controller, instance_id="onebot-b", revision=1):
        return controller.save_selection({"platform": "onebot", "instance_id": instance_id,
                                          "expected_revision": revision})

    def save_official(self, controller, **extra):
        request = {"platform": "qq_official", "instance_id": "official-a",
                   "expected_revision": 1}
        request.update(extra)
        return controller.save_selection(request)

    def test_status_is_read_only_and_does_not_select_unique_candidate(self):
        self.entries[:] = [onebot()]
        controller = self.controller(instance_id="")
        self.assertEqual(controller.status()["state"], "loading")
        self.assertFalse(controller.allows(SyntheticEvent()))
        self.assertFalse(self.path.exists())
        controller.initialize_selection()
        self.assertFalse(self.path.exists())
        controller.initialize_selection(framework_loaded=True)
        self.assertTrue(self.path.exists())
        before = self.path.read_bytes()
        controller.status()
        controller.status()
        self.assertEqual(self.path.read_bytes(), before)

    def test_startup_waits_for_all_adapters_instead_of_latching_first(self):
        self.entries[:] = [onebot()]
        controller = self.controller(instance_id="")
        controller.initialize_selection()
        self.entries.append(onebot("onebot-b"))
        controller.initialize_selection(framework_loaded=True)
        self.assertEqual(controller.status()["state"], "ambiguous")
        self.assertFalse(self.path.exists())

    def test_saved_selection_survives_process_restart_and_legacy_changes(self):
        controller = self.start()
        before = controller.status()["selection"]
        self.entries.append(onebot("onebot-c"))
        restarted = self.controller(instance_id="onebot-c", platform="qq_official")
        self.assertEqual(restarted.status()["selection"], before)
        self.assertTrue(restarted.allows(SyntheticEvent()))
        self.assertFalse(restarted.allows(SyntheticEvent(instance_id="onebot-c")))

    def test_pending_target_applies_only_on_fresh_runtime(self):
        runtime = {}
        controller = self.controller(runtime=runtime)
        controller.initialize_selection()
        status = self.save_onebot(controller)
        self.assertEqual(status["selected_instance"], "onebot-a")
        self.assertTrue(status["pending_restart"])
        controller.stopped = True
        reloaded = self.controller(runtime=runtime)
        self.assertTrue(reloaded.allows(SyntheticEvent()))
        self.assertFalse(reloaded.allows(SyntheticEvent(instance_id="onebot-b")))
        restarted = self.controller()
        self.assertFalse(restarted.status()["pending_restart"])
        self.assertTrue(restarted.allows(SyntheticEvent(instance_id="onebot-b")))

    def test_initial_explicit_ui_save_is_not_applied_by_later_lifecycle_hook(self):
        controller = self.controller(instance_id="")
        self.save_onebot(controller, revision=0)
        controller.initialize_selection(framework_loaded=True)
        self.assertEqual(controller.status()["state"], "pending_restart")
        self.assertFalse(controller.allows(SyntheticEvent(instance_id="onebot-b")))

    def test_missing_selected_instance_after_restart_does_not_choose_another(self):
        self.start()
        self.entries[:] = [onebot("onebot-b")]
        restarted = self.controller()
        restarted.initialize_selection(framework_loaded=True)
        self.assertEqual(restarted.status()["state"], "missing")
        self.assertFalse(restarted.allows(SyntheticEvent(instance_id="onebot-b")))
        self.assertEqual(self.store.read()["selection"]["instance_id"], "onebot-a")

    def test_registered_does_not_assert_online_and_stopped_adapter_blocks(self):
        controller = self.start()
        self.assertTrue(controller.status()["registered"])
        self.assertEqual(controller.status()["online"], "unknown")
        self.entries[0] = activation.PlatformInstance("aiocqhttp", "onebot-a", "stopped")
        controller._invalidate_status()
        self.assertEqual(controller.status()["state"], "offline")
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_failed_replace_preserves_previous_selection_and_removes_own_temp(self):
        controller = self.start()
        before = self.path.read_bytes()
        with patch.object(activation.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(activation.SelectionError):
                self.save_onebot(controller)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".activation-*.tmp")), [])
        self.assertFalse(controller.status()["pending_restart"])

    def test_failure_before_fsync_does_not_publish_selection(self):
        controller = self.start()
        before = self.path.read_bytes()
        with patch.object(activation.os, "fsync", side_effect=OSError("synthetic failure")):
            with self.assertRaises(activation.SelectionError):
                self.save_onebot(controller)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_record_cannot_be_overwritten_or_auto_initialized(self):
        for raw in (b'{broken', b'{"schema_version":1,"schema_version":1}', b'[]'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                controller = self.controller()
                controller.initialize_selection(framework_loaded=True)
                self.assertEqual(controller.status()["state"], "storage_error")
                with self.assertRaises(activation.SelectionError):
                    self.save_onebot(controller, revision=0)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_deleted_record_pauses_current_runtime(self):
        controller = self.start()
        self.path.unlink()
        controller._invalidate_status()
        self.assertEqual(controller.status()["state"], "storage_error")
        self.assertFalse(controller.allows(SyntheticEvent()))

    def test_save_failure_keeps_confirmed_unique_onebot_in_memory(self):
        self.entries[:] = [onebot()]
        runtime = {}

        class FailingStore(activation.SelectionStore):
            def save(self, data, expected_revision):
                raise activation.SelectionError("synthetic save failure")

        store = FailingStore(self.path)
        controller = activation.ActivationController(
            "onebot", "", lambda: self.entries, store=store, runtime=runtime)
        controller.initialize_selection(framework_loaded=True)
        status = controller.status()
        self.assertEqual(status["state"], "active")
        self.assertTrue(controller.allows(SyntheticEvent()))
        self.assertFalse(status["pending_restart"])
        self.assertIn("未能写入磁盘", status["message"])
        self.assertFalse(self.path.exists())

        # 热重载沿用同一运行快照：不能因为文件仍不可写就暂停业务。
        controller.stopped = True
        reloaded = activation.ActivationController(
            "onebot", "", lambda: self.entries, store=store, runtime=runtime)
        self.assertTrue(reloaded.allows(SyntheticEvent()))

    def test_status_cache_avoids_repeat_inventory_and_invalidates_on_save(self):
        self.entries[:] = [onebot(), onebot("onebot-b")]
        calls = []

        def inventory():
            calls.append(1)
            return self.entries

        controller = activation.ActivationController(
            "onebot", "onebot-a", inventory, store=self.store)
        controller.initialize_selection(framework_loaded=True)
        calls.clear()
        self.assertEqual(controller.status()["state"], "active")
        self.assertEqual(controller.status()["state"], "active")
        self.assertEqual(len(calls), 1)
        saved = controller.save_selection({
            "platform": "onebot", "instance_id": "onebot-b", "expected_revision": 1})
        self.assertTrue(saved["pending_restart"])
        self.assertEqual(controller.status()["selected_instance"], "onebot-a")
        self.assertGreaterEqual(len(calls), 2)
        controller.stopped = True
        self.assertEqual(controller.status()["state"], "unavailable")

    def test_repaired_file_does_not_activate_on_hot_reload_after_failed_start(self):
        runtime = {}
        self.path.write_bytes(b"{broken")
        broken = self.controller(runtime=runtime)
        self.assertEqual(broken.status()["state"], "storage_error")
        synthetic_store = activation.SelectionStore()
        prepared = activation.ActivationController("onebot", "onebot-a", lambda: self.entries,
                                                   store=synthetic_store)
        prepared.initialize_selection()
        self.path.write_text(activation.json.dumps(synthetic_store.read()), encoding="utf-8")
        reloaded = self.controller(runtime=runtime)
        self.assertEqual(reloaded.status()["state"], "pending_restart")
        self.assertFalse(reloaded.allows(SyntheticEvent()))
        self.assertTrue(self.controller().allows(SyntheticEvent()))

    def test_two_writers_with_same_revision_cannot_both_succeed(self):
        controller = self.start()
        def save(instance_id):
            try:
                self.save_onebot(controller, instance_id=instance_id)
                return True
            except activation.SelectionError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ["onebot-a", "onebot-b"]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.store.read()["selection"]["config_revision"], 2)

    def test_oversized_or_invalid_revision_does_not_touch_file(self):
        controller = self.start()
        before = self.path.read_bytes()
        for revision in (True, -1, "1", None, 0):
            with self.subTest(revision=revision), self.assertRaises(activation.SelectionError):
                self.save_onebot(controller, revision=revision)
        self.assertEqual(before, self.path.read_bytes())
        self.path.write_bytes(b" " * (activation.MAX_STORE_BYTES + 1))
        self.assertEqual(self.controller().status()["state"], "storage_error")

    def test_official_never_auto_selects_even_when_it_is_unique(self):
        self.entries[:] = [official()]
        controller = self.controller(platform="qq_official", instance_id="")
        controller.initialize_selection(framework_loaded=True)
        self.assertEqual(controller.status()["state"], "selection_required")
        self.assertFalse(self.path.exists())

    def test_official_save_does_not_require_appid_and_remains_closed(self):
        controller = self.start()
        saved = self.save_official(controller)["saved_selection"]
        self.assertEqual(saved["application_ref"], "")
        self.assertTrue(controller.allows(SyntheticEvent()))
        restarted = self.controller()
        self.assertEqual(restarted.status()["state"], "not_ready")
        self.assertFalse(restarted.allows(SyntheticEvent("qq_official", "official-a")))

    def test_environment_is_stable_across_saves_and_reset_is_explicit(self):
        controller = self.start()
        saved = self.save_official(controller)["saved_selection"]
        changed = self.save_official(controller, expected_revision=2, instance_id="official-b")
        self.assertEqual(changed["saved_selection"]["environment_id"], saved["environment_id"])
        reset = self.save_official(controller, expected_revision=3, new_environment=True)
        self.assertNotEqual(reset["saved_selection"]["environment_id"], saved["environment_id"])

    def test_legacy_appid_is_migrated_to_empty_without_losing_environment(self):
        controller = self.start()
        registry = self.store.read()
        env_id = "33333333-3333-4333-8333-333333333333"
        registry["environments"].append({"environment_id": env_id, "platform": "qq_official",
                                         "application_ref": "LEGACY_APP"})
        registry["selection"] = {"platform": "qq_official", "instance_id": "official-a",
                                 "environment_id": env_id, "application_ref": "LEGACY_APP",
                                 "config_revision": 2}
        self.store.save(registry, 1)
        reloaded = self.controller(platform="qq_official", instance_id="official-a")
        saved = reloaded.save_selection({"platform": "qq_official", "instance_id": "official-a",
                                         "expected_revision": 2})["saved_selection"]
        self.assertEqual(saved["environment_id"], env_id)
        self.assertEqual(saved["application_ref"], "")
        self.assertEqual(self.store.read()["environments"][-1]["application_ref"], "")

    def test_failed_inventory_can_recover_at_next_lifecycle_notification(self):
        available = False
        def inventory():
            if not available:
                raise RuntimeError("synthetic inventory unavailable")
            return [onebot()]
        controller = activation.ActivationController("onebot", "", inventory, store=self.store)
        controller.initialize_selection(framework_loaded=True)
        self.assertEqual(controller.status()["state"], "unavailable")
        available = True
        controller.initialize_selection()
        self.assertTrue(controller.allows(SyntheticEvent()))

    def test_context_hot_reload_keeps_runtime_and_stale_shutdown_cannot_stop_new_controller(self):
        context = fake_context(self.entries)
        first = activation.configure(context, PublicSelectionOnly(active_bot_instance="onebot-a"), store=self.store)
        self.save_onebot(first)
        second = activation.configure(context, PublicSelectionOnly(active_bot_instance="onebot-b"), store=self.store)
        activation.shutdown(first)
        self.assertTrue(activation.allows(SyntheticEvent()))
        self.assertEqual(second.status()["selected_instance"], "onebot-a")
        activation.shutdown(second)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        activation.shutdown()

    async def test_framework_hook_waits_for_complete_inventory_and_starts_services(self):
        entries = [onebot()]
        activation.configure(fake_context(entries), PublicSelectionOnly())
        source = source_tree("main.py")
        original = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "MCWatcher")
        methods = [copy.deepcopy(n) for n in original.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name in {"initialize", "_start_services", "_wait_for_implicit_selection",
                                  "on_framework_loaded", "on_platform_loaded"}]
        for method in methods:
            method.decorator_list = []
        cls = ast.ClassDef(name="Lifecycle", bases=[], keywords=[], body=methods, decorator_list=[])
        calls = []
        lifecycle = compile_definitions([cls], {
            "platform_activation": activation, "start_db_keepalive": lambda: calls.append("keepalive"),
            "stats_sampler": SimpleNamespace(start=lambda: calls.append("sampler")),
            "official_runtime": SimpleNamespace(start_recovery=lambda: calls.append("official_recovery")),
            "asyncio": asyncio, "_has_loaded_hook": lambda: True,
        })["Lifecycle"]()
        lifecycle._activation = activation._controller
        lifecycle._startup_task = None
        await lifecycle.initialize()
        await lifecycle.on_platform_loaded()
        self.assertNotIn("keepalive", calls)
        self.assertEqual(activation.status()["state"], "loading")
        await lifecycle.on_framework_loaded()
        self.assertTrue(activation.allows(SyntheticEvent()))
        self.assertIn("keepalive", calls)

    def _lifecycle_class(self, *, loaded_hook, calls):
        source = source_tree("main.py")
        original = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "MCWatcher")
        methods = [copy.deepcopy(n) for n in original.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name in {"initialize", "_start_services", "_wait_for_implicit_selection"}]
        for method in methods:
            method.decorator_list = []
        cls = ast.ClassDef(name="Lifecycle", bases=[], keywords=[], body=methods, decorator_list=[])
        return compile_definitions([cls], {
            "platform_activation": activation,
            "start_db_keepalive": lambda: calls.append("keepalive"),
            "stats_sampler": SimpleNamespace(start=lambda: calls.append("sampler")),
            "official_runtime": SimpleNamespace(start_recovery=lambda: calls.append("official_recovery")),
            "asyncio": asyncio, "_has_loaded_hook": lambda: loaded_hook,
        })["Lifecycle"]()

    async def test_runtime_install_selects_unique_onebot_after_framework_hook(self):
        from unittest.mock import AsyncMock
        activation.configure(fake_context([onebot()]), PublicSelectionOnly())
        calls = []
        lifecycle = self._lifecycle_class(loaded_hook=True, calls=calls)
        lifecycle._activation = activation._controller
        lifecycle._startup_task = None
        await lifecycle.initialize()
        self.assertIsNotNone(lifecycle._startup_task)
        with patch.object(asyncio, "sleep", new=AsyncMock()):
            await lifecycle._startup_task
        self.assertEqual(activation.status()["state"], "active")
        self.assertEqual(calls[-3:], ["keepalive", "sampler", "official_recovery"])

    async def test_old_framework_without_loaded_hook_waits_then_selects(self):
        entries = []
        activation.configure(fake_context(entries), PublicSelectionOnly())
        calls = []
        lifecycle = self._lifecycle_class(loaded_hook=False, calls=calls)
        lifecycle._activation = activation._controller
        lifecycle._startup_task = None
        await lifecycle.initialize()

        async def appear(delay):
            if not entries:
                entries.append(onebot())

        with patch.object(asyncio, "sleep", side_effect=appear):
            await lifecycle._startup_task
        self.assertEqual(activation.status()["state"], "active")
        self.assertIn("keepalive", calls)

    async def test_keepalive_restarts_when_previous_thread_ignored_stop(self):
        import threading
        configure_onebot()
        previous = SimpleNamespace(is_alive=lambda: True)
        stopped = threading.Event()
        stopped.set()
        state = {"thread": previous, "stop": stopped}
        handler = isolated_function(
            "commands/common.py", "start_db_keepalive",
            platform_activation=activation,
            WhitelistManager=lambda: SimpleNamespace(
                db_host="synthetic", db_user="synthetic", db_password="synthetic"),
            _keepalive=state, threading=threading,
            _keepalive_loop=lambda stop_event: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None),
        )
        handler()
        replacement = state["thread"]
        self.assertIsNot(replacement, previous)
        replacement.join(2.0)
        self.assertFalse(replacement.is_alive())

    async def test_legacy_half_update_is_rejected_before_config_write(self):
        async def body():
            return {"key": "active_platform", "value": "qq_official"}, ""
        handler = isolated_function("web/api.py", "view_config_update", _body=body,
                                    _plugin_config=ForbiddenObject(), _err=lambda s: s,
                                    platform_activation=activation)
        self.assertIn("统一保存", await handler())

    async def test_activation_endpoint_does_not_touch_general_config(self):
        activation.configure(fake_context([onebot()]), PublicSelectionOnly(active_bot_instance="onebot-a"))
        async def body():
            return {"platform": "onebot", "instance_id": "onebot-a", "expected_revision": 1}, ""
        handler = isolated_function("web/api.py", "view_activation_update", _body=body,
                                    _plugin_config=ForbiddenObject(), _err=lambda s: {"error": s},
                                    _ok=lambda data: data, platform_activation=activation)
        result = await handler()
        self.assertTrue(result["pending_restart"])
        self.assertEqual(result["saved_selection"]["config_revision"], 2)

    async def test_pending_selection_disallows_misleading_plugin_reload(self):
        activation.configure(fake_context([onebot(), onebot("onebot-b")]), PublicSelectionOnly(active_bot_instance="onebot-a"))
        activation.save_selection({"platform": "onebot", "instance_id": "onebot-b", "expected_revision": 1})
        handler = isolated_function("web/api.py", "view_plugin_reload", platform_activation=activation,
                                    _plugin_manager=forbidden, _err=lambda s: s)
        self.assertIn("完整重启", await handler())

    async def test_old_keepalive_blocks_new_worker_before_reading_credentials(self):
        configure_onebot()
        handler = isolated_function("commands/common.py", "start_db_keepalive",
                                    platform_activation=activation, WhitelistManager=forbidden,
                                    _keepalive={"thread": SimpleNamespace(is_alive=lambda: True)})
        self.assertIsNone(handler())

    async def test_inactive_keepalive_does_not_read_credentials(self):
        activation.shutdown()
        handler = isolated_function("commands/common.py", "start_db_keepalive",
                                    platform_activation=activation, WhitelistManager=forbidden)
        self.assertIsNone(handler())

    async def test_optional_lifecycle_hook_does_not_break_old_framework_import(self):
        decorator = isolated_function("main.py", "_lifecycle_hook", filter=SimpleNamespace())
        async def handler():
            return "synthetic"
        self.assertIs(decorator("on_platform_loaded")(handler), handler)

    async def test_explicit_instance_waits_without_selecting_another(self):
        import asyncio
        entries = [onebot("onebot-b")]
        current = activation.configure(fake_context(entries), PublicSelectionOnly(active_bot_instance="onebot-a"))
        tree = source_tree("main.py")
        original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MCWatcher")
        method = next(copy.deepcopy(n) for n in original.body
                      if isinstance(n, ast.AsyncFunctionDef) and n.name == "_wait_for_explicit_instance")
        namespace = compile_definitions([method], {"platform_activation": activation, "asyncio": asyncio})
        calls = []
        worker = SimpleNamespace(_activation=current, _start_services=lambda: calls.append("started"))
        async def appear(delay):
            self.assertFalse(current.allows(SyntheticEvent(instance_id="onebot-b")))
            entries.append(onebot())
        with patch.object(asyncio, "sleep", side_effect=appear):
            await namespace["_wait_for_explicit_instance"](worker)
        self.assertEqual(calls, ["started"])
        self.assertEqual(current.status()["selected_instance"], "onebot-a")


if __name__ == "__main__":
    unittest.main()
