"""公开实例选择及持久化；不导入框架，不读取机器人配置或凭据。

生产入口显式注入 activation.json 路径；测试注入内存存储或临时目录。
保存只修改下次完整启动的目标，热重载沿用同一 Context 的运行快照。
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
from threading import RLock
from typing import Callable, Iterable, Optional
from uuid import UUID, uuid4


PLATFORM_NAMES = {
    "aiocqhttp": "onebot",
    "qq_official": "qq_official",
    "qq_official_webhook": "qq_official",
}
ACTIVE_KEYS = frozenset({"active_platform", "active_bot_instance"})
READY_PLATFORMS = frozenset({"onebot"})  # 官方业务待 S2/S3 单独放行
MAX_STORE_BYTES = 65536
MAX_ENVIRONMENTS = 100
RUNTIME_KEY = "_mcsight_activation_runtime_v1"
# 每条消息都会经过门禁；实例枚举和文件读取没必要按命令重复。选择保存、
# 生命周期通知和卸载都会立即失效，这里只吸收同一秒内的重复查询。
STATUS_CACHE_SECONDS = 1.0


class SelectionError(ValueError):
    """只包含面向用户的说明，不暴露文件内容/路径或底层异常。"""


class InventoryError(SelectionError):
    """实例枚举临时失败，不锁死后续生命周期重试。"""


def _text(value, limit=128, *, empty=False):
    if (not isinstance(value, str) or len(value) > limit
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or (not empty and not value)):
        raise SelectionError("选择字段必须是无首尾空白和控制字符的有效文本。")
    return value


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise SelectionError("环境标识无效，请重新选择环境。") from None
    return value


def _empty_registry():
    return {"schema_version": 1, "selection": None, "environments": []}


def _validate_registry(data):
    if (not isinstance(data, dict)
            or set(data) != {"schema_version", "selection", "environments"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or not isinstance(data["environments"], list)
            or len(data["environments"]) > MAX_ENVIRONMENTS):
        raise SelectionError("生效机器人记录格式或版本无效，请恢复有效记录后重启。")
    environments = {}
    for entry in data["environments"]:
        if (not isinstance(entry, dict)
                or set(entry) != {"environment_id", "platform", "application_ref"}
                or entry["platform"] not in ("onebot", "qq_official")):
            raise SelectionError("官方环境登记格式无效。")
        key = _uuid(entry["environment_id"])
        _text(entry["application_ref"], empty=entry["platform"] == "onebot")
        if key in environments or (entry["platform"] == "onebot" and entry["application_ref"]):
            raise SelectionError("环境登记重复或归属不匹配。")
        environments[key] = entry
    selection = data["selection"]
    if selection is not None:
        keys = {"platform", "instance_id", "environment_id", "application_ref", "config_revision"}
        if not isinstance(selection, dict) or set(selection) != keys:
            raise SelectionError("生效机器人选择不完整，请恢复有效记录。")
        _text(selection["instance_id"])
        revision = selection["config_revision"]
        if type(revision) is not int or not 1 <= revision <= 9007199254740991:
            raise SelectionError("生效机器人配置修订无效。")
        entry = environments.get(_uuid(selection["environment_id"]))
        if (entry is None or entry["platform"] != selection["platform"]
                or entry["application_ref"] != selection["application_ref"]):
            raise SelectionError("生效机器人与环境归属不一致。")
    elif environments:
        raise SelectionError("环境登记缺少生效机器人选择。")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class SelectionStore:
    """只操作注入的公开选择文件；无路径时为测试用内存存储。"""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self._memory = _empty_registry()
        self._lock = RLock()

    def read(self):
        with self._lock:
            if self.path is None:
                return deepcopy(self._memory)
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(MAX_STORE_BYTES + 1)
                if len(raw) > MAX_STORE_BYTES:
                    raise ValueError("size")
                return _validate_registry(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object))
            except FileNotFoundError:
                return _empty_registry()
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                raise SelectionError("无法读取有效的生效机器人记录；已暂停业务，不覆盖原文件。") from None

    def save(self, data, expected_revision):
        with self._lock:
            current = self.read()
            revision = (current["selection"] or {}).get("config_revision", 0)
            if type(expected_revision) is not int or expected_revision != revision:
                raise SelectionError("选择已被其它页面修改，请刷新后重新保存。")
            data = _validate_registry(deepcopy(data))
            if data["selection"]["config_revision"] != revision + 1:
                raise SelectionError("配置修订必须连续递增。")
            raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            if len(raw) > MAX_STORE_BYTES:
                raise SelectionError("环境登记容量已满，请先安排维护。")
            if self.path is None:
                self._memory = data
                return
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent,
                                                 prefix=".activation-", suffix=".tmp", delete=False) as stream:
                    temporary = stream.name
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                temporary = None
                if os.name != "nt":
                    directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            except OSError:
                raise SelectionError("生效机器人选择保存失败；请刷新状态确认，未切换当前运行实例。") from None
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass


@dataclass(frozen=True)
class PlatformInstance:
    name: str
    instance_id: str
    adapter_status: str = "unknown"


class ActivationController:
    def __init__(self, platform: str, instance_id: str,
                 inventory: Callable[[], Iterable[PlatformInstance]], *, store=None, runtime=None, official_scope=None):
        self.platform = platform
        self.requested_instance = instance_id
        self._inventory = inventory
        self.official_scope = official_scope
        self.store = store if store is not None else SelectionStore()
        self.runtime = runtime if runtime is not None else {}
        self.stopped = False
        self._error = ""
        self._memory_only = bool(self.runtime.get("memory_only", False))
        self._status_cache = None
        self._status_cache_at = 0.0
        self._lock = RLock()
        try:
            registry = self.store.read()
            if "selection" not in self.runtime:
                self.runtime["selection"] = deepcopy(registry["selection"])
            self.selection = deepcopy(self.runtime["selection"])
        except SelectionError as error:
            self._error = str(error)
            self.selection = None
            self.runtime.setdefault("selection", None)

    def _instances(self):
        try:
            raw = list(self._inventory())
            if any(not isinstance(item, PlatformInstance) for item in raw):
                raise ValueError
            instances = [item for item in raw if item.name in PLATFORM_NAMES]
            for item in instances:
                _text(item.instance_id)
            return instances
        except Exception:
            raise InventoryError("暂时无法读取机器人实例列表，本插件已暂停处理命令。") from None

    def initialize_selection(self, *, framework_loaded=False):
        """生命周期显式触发；status/allows 永远不选择实例或写盘。"""
        with self._lock:
            # 生命周期通知可能伴随适配器注册变化，缓存先作废。
            self._invalidate_status()
            if self.stopped or self._error:
                return
            if framework_loaded:
                self.runtime["framework_loaded"] = True
            if self.selection is not None:
                return
            if self.platform != "onebot" or not isinstance(self.requested_instance, str):
                return
            if not self.requested_instance and not self.runtime.get("framework_loaded", False):
                return
            try:
                registry = self.store.read()
                # 页面已保存下次启动目标时，不在当前进程抢先应用。
                if registry["selection"] is not None:
                    return
                instances = self._instances()
                if len({item.instance_id for item in instances}) != len(instances):
                    return
                candidates = [item for item in instances if PLATFORM_NAMES[item.name] == "onebot"
                              and (not self.requested_instance or item.instance_id == self.requested_instance)]
                if len(candidates) != 1:
                    return
                try:
                    self._save({"platform": "onebot", "instance_id": candidates[0].instance_id,
                                "expected_revision": 0}, registry, instances=instances)
                except SelectionError as error:
                    # 只读目录、权限或瞬时 IO 失败不应让已确认的唯一 OneBot
                    # 实例停摆：本次运行用内存选择继续，下一次完整启动重试写盘。
                    # 读取失败或已有他人写入时仍按原规则暂停，避免覆盖未知记录。
                    try:
                        current = self.store.read()
                    except SelectionError:
                        raise error
                    if current["selection"] is not None or current["environments"]:
                        raise error
                    self.selection = {"platform": "onebot", "instance_id": candidates[0].instance_id,
                                      "environment_id": str(uuid4()), "application_ref": "",
                                      "config_revision": 1}
                    self._memory_only = True
                    self.runtime["memory_only"] = True
                else:
                    self.selection = deepcopy(registry["selection"])
                    self._memory_only = False
                    self.runtime.pop("memory_only", None)
                self.runtime["selection"] = deepcopy(self.selection)
            except InventoryError:
                return
            except SelectionError as error:
                self._error = str(error)
                self._invalidate_status()

    def _save(self, request, registry, *, instances=None):
        allowed = {"platform", "instance_id", "application_ref", "environment_id",
                   "expected_revision", "new_environment", "confirm_application"}
        if not isinstance(request, dict) or set(request) - allowed:
            raise SelectionError("生效机器人选择包含无效字段。")
        platform = request.get("platform")
        if platform not in ("onebot", "qq_official"):
            raise SelectionError("生效平台只允许 OneBot 或 QQ 官方。")
        instance_id = _text(request.get("instance_id"))
        instances = self._instances() if instances is None else instances
        if len({item.instance_id for item in instances}) != len(instances):
            raise SelectionError("机器人实例 ID 重复，请先在 AstrBot 中更正。")
        if not any(item.instance_id == instance_id and PLATFORM_NAMES[item.name] == platform for item in instances):
            raise SelectionError("未找到所选平台的具体机器人实例，请刷新实例列表。")
        revision = (registry["selection"] or {}).get("config_revision", 0)
        if type(request.get("expected_revision")) is not int or request["expected_revision"] != revision:
            raise SelectionError("选择已被其它页面修改，请刷新后重新保存。")
        new_environment = request.get("new_environment", False)
        if type(new_environment) is not bool:
            raise SelectionError("新建环境选项必须为布尔值。")
        application_ref = ""
        if platform == "qq_official":
            application_ref = _text(request.get("application_ref"))
            if request.get("confirm_application") is not True:
                raise SelectionError("请确认所选官方实例属于填写的应用；更换应用须使用新环境。")
        environment_id = request.get("environment_id", "")
        _text(environment_id, empty=True)
        if new_environment and environment_id:
            raise SelectionError("新建环境时不能同时指定旧环境。")
        matches = [item for item in registry["environments"]
                   if item["platform"] == platform and item["application_ref"] == application_ref]
        if environment_id:
            if not any(item["environment_id"] == environment_id for item in matches):
                raise SelectionError("所选环境不属于当前平台和应用。")
        elif not new_environment:
            saved = registry["selection"] or {}
            if any(item["environment_id"] == saved.get("environment_id") for item in matches):
                environment_id = saved["environment_id"]
            elif len(matches) == 1:
                environment_id = matches[0]["environment_id"]
            elif len(matches) > 1:
                raise SelectionError("该应用有多个环境，请显式选择要使用的环境。")
        if not environment_id:
            if len(registry["environments"]) >= MAX_ENVIRONMENTS:
                raise SelectionError("环境登记数量已满，请先安排维护。")
            environment_id = str(uuid4())
            registry["environments"].append({"environment_id": environment_id, "platform": platform,
                                              "application_ref": application_ref})
        registry["selection"] = {"platform": platform, "instance_id": instance_id,
                                 "environment_id": environment_id, "application_ref": application_ref,
                                 "config_revision": revision + 1}
        self.store.save(registry, revision)
        self._invalidate_status()

    def save_selection(self, request):
        with self._lock:
            if self.stopped or self._error:
                raise SelectionError(self._error or "插件正在卸载，请重新打开配置页。")
            self._save(request, self.store.read())
            return self.status()

    def _invalidate_status(self):
        with self._lock:
            self._status_cache = None
            self._status_cache_at = 0.0

    def status(self):
        if self.stopped:
            return self._status_uncached()
        with self._lock:
            now = time.monotonic()
            if self._status_cache is not None and now - self._status_cache_at < STATUS_CACHE_SECONDS:
                return deepcopy(self._status_cache)
            result = self._status_uncached()
            self._status_cache = result
            self._status_cache_at = now
            return deepcopy(result)

    def _status_uncached(self):
        result = {"platform": (self.selection or {}).get("platform", self.platform),
                  "requested_instance": self.requested_instance,
                  "selected_instance": (self.selection or {}).get("instance_id", ""),
                  "selection": deepcopy(self.selection), "saved_selection": None,
                  "config_revision": 0, "environments": [], "choices": [],
                  "registered": False, "online": "unknown", "adapter_status": "unknown",
                  "state": "unavailable", "message": "插件尚未初始化或正在卸载，暂不处理业务。",
                  "pending_restart": False, "restart_required": False,
                  "framework_loaded": bool(self.runtime.get("framework_loaded", False))}
        if self.stopped:
            return result
        try:
            if self._error:
                raise SelectionError(self._error)
            registry = self.store.read()
        except SelectionError as error:
            result.update(state="storage_error", message=str(error))
            return result
        saved = registry["selection"]
        memory_only = self._memory_only and saved is None
        if self.selection is not None and saved is None and not memory_only:
            result.update(state="storage_error", message="已使用的选择记录丢失；业务暂停，请恢复有效记录。")
            return result
        pending_restart = saved != self.selection and not memory_only
        result.update(saved_selection=deepcopy(saved), config_revision=(saved or {}).get("config_revision", 0),
                      environments=deepcopy(registry["environments"]), pending_restart=pending_restart,
                      restart_required=pending_restart)
        if self.selection is None and saved is None and (self.platform not in ("onebot", "qq_official")
                                       or not isinstance(self.requested_instance, str)):
            result.update(state="invalid", message="首次选择配置无效，请在配置页统一选择平台与实例。")
            return result
        try:
            instances = self._instances()
        except SelectionError as error:
            result.update(message=str(error))
            return result
        result["choices"] = [{"platform": PLATFORM_NAMES[item.name], "instance_id": item.instance_id,
                              "adapter": item.name, "adapter_status": item.adapter_status} for item in instances]
        if len({item.instance_id for item in instances}) != len(instances):
            result.update(state="ambiguous", message="机器人实例 ID 重复，请先在 AstrBot 中设置不同的实例 ID。")
            return result
        if self.selection is None:
            if saved is not None:
                result.update(state="pending_restart", message="选择已保存，请完整重启 AstrBot 后应用。")
            elif self.platform == "qq_official":
                result.update(state="selection_required", message="QQ 官方必须在配置页显式选择实例并确认应用归属。")
            elif not self.requested_instance and not result["framework_loaded"]:
                result.update(state="loading", message="等待框架加载完成；也可显式保存实例后完整重启。")
            else:
                candidates = [i for i in instances if PLATFORM_NAMES[i.name] == "onebot"
                              and (not self.requested_instance or i.instance_id == self.requested_instance)]
                result.update(state="ambiguous" if len(candidates) > 1 else "missing",
                              message="尚无已保存的运行选择，请显式选择具体实例后完整重启。")
            return result
        selected = next((item for item in instances if item.instance_id == self.selection["instance_id"]
                         and PLATFORM_NAMES[item.name] == self.selection["platform"]), None)
        if selected is None:
            result.update(state="missing", message="选定实例未注册，本插件不会自动改用其它机器人。")
            return result
        result.update(registered=True, adapter=selected.name, adapter_status=selected.adapter_status)
        if selected.adapter_status in ("error", "stopped"):
            result.update(state="offline", message="所选适配器已停止或出错，业务暂停且不自动接管。")
        elif self.selection["platform"] not in READY_PLATFORMS:
            if self.official_scope is not None:
                result.update(state="configuring", message="已选择 QQ 官方；群登记和只读功能按群配置开放，绑定与远程写操作尚未开放。")
            else:
                result.update(state="not_ready", message="当前阶段尚未开放 QQ 官方业务，所选官方实例保持静默。")
        else:
            result.update(state="active", message="本插件仅处理当前运行选择；在线状态尚未确认。")
        if memory_only:
            result["message"] += " 本次选择未能写入磁盘，仅在当前运行中有效；下次完整启动会重试写入。"
        return result

    def allows(self, event):
        current = self.status()
        if current["state"] != "active" or self.selection is None:
            return False
        try:
            return (event.get_platform_name() == current["adapter"]
                    and event.get_platform_id() == self.selection["instance_id"])
        except Exception:
            return False

    def accepts(self, event):
        """实例门禁先于消息改写；官方仅接受原始字段一致的群事件。"""
        if self.allows(event):
            return True
        current = self.status()
        if current["state"] != "configuring" or self.official_scope is None:
            return False
        try:
            return (event.get_platform_name() == current["adapter"]
                    and event.get_platform_id() == self.selection["instance_id"]
                    and self.official_scope(event, self.selection) is not None)
        except Exception:
            return False


_controller: Optional[ActivationController] = None


def configure(context, config=None, *, store_path=None, store=None, official_scope=None):
    global _controller
    platform = "onebot" if config is None else config.get("active_platform", "onebot")
    instance_id = "" if config is None else config.get("active_bot_instance", "")
    runtime = getattr(context, RUNTIME_KEY, None)
    if not isinstance(runtime, dict):
        runtime = {}
        setattr(context, RUNTIME_KEY, runtime)

    def inventory():
        for adapter in tuple(context.platform_manager.get_insts()):
            meta = adapter.meta()
            adapter_status = getattr(adapter, "status", "unknown")
            adapter_status = getattr(adapter_status, "value", adapter_status)
            yield PlatformInstance(meta.name, meta.id, adapter_status if isinstance(adapter_status, str) else "unknown")

    _controller = ActivationController(platform, instance_id, inventory,
                                       store=store if store is not None else SelectionStore(store_path), runtime=runtime,
                                       official_scope=official_scope)
    _controller.initialize_selection()
    return _controller


def refresh(*, framework_loaded=False):
    if _controller is not None:
        _controller.initialize_selection(framework_loaded=framework_loaded)


def status():
    if _controller is None:
        return {"state": "unavailable", "message": "插件尚未初始化或正在卸载，暂不处理业务。",
                "choices": [], "selection": None, "saved_selection": None, "environments": [],
                "config_revision": 0, "pending_restart": False, "registered": False,
                "online": "unknown", "adapter_status": "unknown", "restart_required": False}
    return _controller.status()


def save_selection(request):
    if _controller is None:
        raise SelectionError("插件尚未初始化或正在卸载。")
    return _controller.save_selection(request)


def allows(event):
    return _controller is not None and _controller.allows(event)


def accepts(event):
    return _controller is not None and _controller.accepts(event)


def onebot_business_enabled():
    current = status()
    return current["state"] == "active" and current.get("platform") == "onebot"


def shutdown(controller=None):
    global _controller
    target = controller if controller is not None else _controller
    if target is not None:
        target.stopped = True
    if target is _controller:
        _controller = None
