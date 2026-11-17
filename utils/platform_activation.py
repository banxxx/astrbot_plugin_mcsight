"""本插件唯一生效实例的选择器。

只读取注入配置中的两个公开选项以及适配器 meta()，不自行加载配置文件。
选择在一次插件生命周期内固定；保存配置后需重载，不做热切换或故障接管。
"""
from dataclasses import dataclass
from typing import Callable, Iterable, Optional


PLATFORM_NAMES = {
    "aiocqhttp": "onebot",
    "qq_official": "qq_official",
    "qq_official_webhook": "qq_official",
}
ACTIVE_KEYS = frozenset({"active_platform", "active_bot_instance"})

# 官方群配置、身份与新绑定表将在后续阶段接入。
# 未完成前不能让它进入仍使用旧 bindings 表的业务 handler。
READY_PLATFORMS = frozenset({"onebot"})


@dataclass(frozen=True)
class PlatformInstance:
    name: str
    instance_id: str


class ActivationController:
    def __init__(
        self,
        platform: str,
        instance_id: str,
        inventory: Callable[[], Iterable[PlatformInstance]],
    ):
        self.platform = platform
        self.requested_instance = instance_id
        self._inventory = inventory
        self._selected: Optional[PlatformInstance] = None

    def status(self) -> dict:
        result = {
            "platform": self.platform,
            "requested_instance": self.requested_instance,
            "selected_instance": (
                self._selected.instance_id if self._selected is not None else ""
            ),
            "choices": [],
            "state": "invalid",
            "message": "生效平台配置无效，请选择 onebot 或 qq_official。",
            "restart_required": True,
        }
        if self.platform not in ("onebot", "qq_official"):
            return result
        try:
            instances = list(self._inventory())
            if any(not isinstance(item, PlatformInstance) for item in instances):
                raise ValueError("invalid platform inventory")
            supported = [item for item in instances if item.name in PLATFORM_NAMES]
            if any(
                not isinstance(item.instance_id, str) or not item.instance_id
                for item in supported
            ):
                raise ValueError("invalid supported instance ID")
        except Exception:
            result.update(
                state="unavailable",
                message="暂时无法读取机器人实例列表，本插件已暂停处理命令。",
            )
            return result

        result["choices"] = [
            {"platform": PLATFORM_NAMES[item.name], "instance_id": item.instance_id}
            for item in supported
        ]
        ids = [item.instance_id for item in supported]
        if len(ids) != len(set(ids)):
            result.update(
                state="ambiguous",
                message="机器人实例 ID 重复，请先在 AstrBot 中设置不同的实例 ID。",
            )
            return result

        candidates = [
            item for item in supported if PLATFORM_NAMES[item.name] == self.platform
        ]
        if self._selected is None:
            if self.requested_instance:
                candidates = [
                    item for item in candidates
                    if item.instance_id == self.requested_instance
                ]
            if not candidates:
                result.update(
                    state="missing",
                    message="未找到选定的机器人实例，本插件不会自动改用其它机器人。",
                )
                return result
            if len(candidates) != 1:
                result.update(
                    state="ambiguous",
                    message="存在多个候选机器人，请填写唯一的生效机器人实例 ID 后重载。",
                )
                return result
            self._selected = candidates[0]
            result["selected_instance"] = self._selected.instance_id
        elif self._selected not in candidates:
            result.update(
                state="missing",
                message="选定的机器人实例已移除，本插件不会自动启用其它实例。",
            )
            return result

        if self.platform not in READY_PLATFORMS:
            result.update(
                state="not_ready",
                message="当前阶段尚未开放 QQ 官方业务，请继续使用 OneBot。",
            )
            return result
        result.update(state="active", message="本插件仅处理当前选中的机器人实例。")
        return result

    def allows(self, event) -> bool:
        if self.status()["state"] != "active" or self._selected is None:
            return False
        try:
            return (
                event.get_platform_name() == self._selected.name
                and event.get_platform_id() == self._selected.instance_id
            )
        except Exception:
            return False


_controller: Optional[ActivationController] = None


def configure(context, config=None) -> None:
    """快照公开选择项；此函数不会读取名单、密钥或真实群数据。"""
    global _controller
    platform = "onebot" if config is None else config.get("active_platform", "onebot")
    instance_id = "" if config is None else config.get("active_bot_instance", "")
    platform = platform.strip() if isinstance(platform, str) else ""
    instance_id = instance_id.strip() if isinstance(instance_id, str) else None

    def inventory():
        manager = context.platform_manager
        for adapter in tuple(manager.get_insts()):
            meta = adapter.meta()
            yield PlatformInstance(meta.name, meta.id)

    # 非字符串实例配置不能被当作空值，再意外选择另一个机器人。
    if instance_id is None:
        platform, instance_id = "", ""
    _controller = ActivationController(platform, instance_id, inventory)


def status() -> dict:
    if _controller is None:
        return {
            "state": "unavailable",
            "message": "插件尚未初始化或正在卸载，暂不处理业务。",
            "choices": [],
        }
    return _controller.status()


def allows(event) -> bool:
    return _controller is not None and _controller.allows(event)


def onebot_business_enabled() -> bool:
    current = status()
    return current["state"] == "active" and current["platform"] == "onebot"


def shutdown() -> None:
    global _controller
    _controller = None
