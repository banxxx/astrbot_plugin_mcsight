import asyncio
import os
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .commands.mc_handler import handle_mc_command
from .commands.common import attach_db_keepalive_context, start_db_keepalive, stop_db_keepalive
from .config.server_config import migrate_legacy_group_configs
from .config.whitelist_config import WhitelistManager
from .utils import mod_http, platform_activation, stats_sampler, official_runtime
from .utils.official_core import event_scope
from .web.api import register_view_api
from .utils.plugin_paths import data_dir


def _lifecycle_hook(name):
    """旧框架缺少可选钩子时仍能加载；不据此假定平台已全部注册。"""
    registrar = getattr(filter, name, None)
    return registrar() if callable(registrar) else (lambda handler: handler)


def _has_loaded_hook():
    return callable(getattr(filter, "on_astrbot_loaded", None))


def _global_mod_token(config) -> str:
    """官方与 OneBot 共用全局 mod_api_token；每次运行时读取，面板改完立即生效。"""
    getter = getattr(config, "get", None)
    return str(getter("mod_api_token", "") or "") if callable(getter) else ""


def _config_text(getter, key: str, default: str = "") -> str:
    value = getter(key, default)
    return value.strip() if isinstance(value, str) else default


def _config_list(getter, key: str) -> list:
    """权限名单统一成字符串列表；兼容手写配置里的逗号串。"""
    value = getter(key, [])
    if isinstance(value, str):
        value = re.split(r"[,，\s]+", value)
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _global_access_lists(config) -> dict:
    """公共超级管理员/白名单管理员/黑名单；官方用 openid，OneBot 用 QQ 号精确匹配。"""
    getter = getattr(config, "get", None)
    if not callable(getter):
        return {}
    return {key: _config_list(getter, key)
            for key in ("super_admins", "whitelist_admins", "blacklist")}


def _db_ca_path() -> str:
    """与 OneBot 连接池使用同一份 CA；只返回实际存在的路径。"""
    plugin_dir = os.path.dirname(__file__)
    for path in (os.path.join(plugin_dir, "isrgrootx1.pem"), "isrgrootx1.pem",
                 "/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.isfile(path):
            return path
    return ""


def _official_center_defaults(config):
    """官方中心的全局默认连接；环境级 center_settings.json 仍可完整覆盖。

    是否启用中心库由官方群「本群规则」逐群决定；这里只在公共连接参数齐全时
    提供 hybrid 默认值，避免把为 OneBot 填写的半套连接当成官方中心已就绪。
    密码只在本进程内传给连接层。
    """
    getter = getattr(config, "get", None)
    if not callable(getter):
        return None
    try:
        port = int(getter("db_port", 4000))
    except (TypeError, ValueError):
        port = 0
    host = _config_text(getter, "db_host", "")
    user = _config_text(getter, "db_user", "")
    password = _config_text(getter, "db_password", "")
    database = _config_text(getter, "db_name", "")
    ssl_ca = _db_ca_path()
    if not (host and user and password and database and 1 <= port <= 65535 and ssl_ca):
        return None
    connection = {"host": host, "port": port, "user": user,
                  "password": password, "database": database, "ssl_ca": ssl_ca}
    return {"enabled": True, "storage_mode": "hybrid", "connection": connection}


def _rest_after_command(event: AstrMessageEvent) -> str:
    """取用户输入里「指令名/别名」之后的文本。

    AstrBot 的「指令管理」可以给指令改名或加别名，内核只按新名字匹配，
    但不会改写 event.message_str；这里不能按注册时的指令名长度切字符串。
    """
    raw = event.message_str.strip()
    if raw.startswith("/"):
        raw = raw[1:].lstrip()
    parts = raw.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@register("astrbot_plugin_mcsight", "poso", "Minecraft 多服务器状态监控插件", "v2.1.1")
class MCWatcher(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self._activation = platform_activation.configure(
            context, config, store_path=os.path.join(data_dir(), "activation.json"), official_scope=event_scope)
        self._official = official_runtime.configure(
            os.path.join(data_dir(), "qq_official"), platform_activation, owner=context,
            mod_token=lambda: _global_mod_token(config),
            center_defaults=lambda: _official_center_defaults(config),
            access_lists=lambda: _global_access_lists(config))
        self._startup_task = None
        attach_db_keepalive_context(context)
        legacy_platform = (self._activation.selection or {}).get("platform", self._activation.platform)
        if legacy_platform == "onebot":
            WhitelistManager().set_context(context)
            # 仅 OneBot 注入旧名单与连接配置；官方使用独立环境存储。
            WhitelistManager().set_config(config)
        # 旧版 OneBot 群文件一次性迁到 onebot/<群号>/state.json；旧文件保留不删。
        try:
            migrated, failed = migrate_legacy_group_configs()
            if migrated or failed:
                logger.info(f"[群配置] 旧目录迁移：成功 {migrated}，失败 {failed}")
        except Exception as e:
            logger.error(f"[群配置] 旧目录迁移异常: {e}")
        # config 是 data/config/<插件名>_config.json 的内存对象，配置页写它就等于面板改配置
        register_view_api(context, config)

    async def initialize(self):
        # 4.29.1 此时平台可能尚未加载；只有明确 ID 或同进程快照可以先恢复。
        platform_activation.refresh()
        self._start_services()
        selected = self._activation.selection or {}
        explicit_onebot = (
            selected.get("platform") == "onebot"
            or (self._activation.platform == "onebot"
                and isinstance(self._activation.requested_instance, str)
                and bool(self._activation.requested_instance)))
        if (explicit_onebot and not platform_activation.onebot_business_enabled()
                and (self._startup_task is None or self._startup_task.done())):
            self._startup_task = asyncio.create_task(self._wait_for_explicit_instance())
        elif (self._activation.platform == "onebot"
              and isinstance(self._activation.requested_instance, str)
              and not self._activation.requested_instance
              and self._activation.selection is None
              and not platform_activation.onebot_business_enabled()
              and (self._startup_task is None or self._startup_task.done())):
            self._startup_task = asyncio.create_task(self._wait_for_implicit_selection())

    async def _wait_for_explicit_instance(self):
        # 仅等待明确 ID；兼容缺少平台加载钩子的旧框架，不猜唯一候选。
        for _ in range(60):
            if self._activation.stopped:
                return
            self._activation.initialize_selection()
            if platform_activation.onebot_business_enabled():
                self._start_services()
                return
            await asyncio.sleep(1)

    async def _wait_for_implicit_selection(self):
        """运行期安装/无完成钩子的旧框架下，为唯一 OneBot 候选补一次选择。

        正常启动时平台尚未注册，必须交给 on_astrbot_loaded，不能在逐个平台
        加载期间抢先固定候选。若 initialize() 时实例已注册，说明框架已在
        运行（插件被动态安装），短暂稳定后按“加载完成”处理；旧框架没有
        完成钩子时，等待平台注册后再做同样的处理。
        """
        if platform_activation.status().get("choices"):
            await asyncio.sleep(3)
        elif _has_loaded_hook():
            return
        else:
            for _ in range(60):
                if self._activation.stopped or platform_activation.onebot_business_enabled():
                    return
                # 轮询期间显式作废状态缓存，及时看到新注册的适配器。
                self._activation.initialize_selection()
                if platform_activation.status().get("choices"):
                    await asyncio.sleep(3)
                    break
                await asyncio.sleep(1)
            else:
                return
        if self._activation.stopped or platform_activation.onebot_business_enabled():
            return
        platform_activation.refresh(framework_loaded=True)
        self._start_services()

    def _start_services(self):
        if platform_activation.onebot_business_enabled():
            start_db_keepalive()
        stats_sampler.start()
        official_runtime.start_recovery()

    @_lifecycle_hook("on_astrbot_loaded")
    async def on_framework_loaded(self):
        platform_activation.refresh(framework_loaded=True)
        self._start_services()

    @_lifecycle_hook("on_platform_loaded")
    async def on_platform_loaded(self):
        # 逐个平台加载不能解锁“唯一候选”初始化。
        platform_activation.refresh()
        self._start_services()

    @filter.command("mc")
    async def mc(self, event: AstrMessageEvent):
        """插件总入口：/mc help | status | list | add | remove | edit | batchadd | batchremove | move | swap | token | say | exec | tps | stats | rank | bind | unbind | check | whitelist | lastonline | checknick | nicknamecheck | bindcheck（子命令说明见 /帮助）"""
        if not platform_activation.accepts(event):
            return
        async for result in handle_mc_command(event):
            yield result

    # ---------- 现有别名 ----------
    @filter.command("在线", alias={"online"})
    async def online(self, event: AstrMessageEvent):
        """服务器状态卡片：在线玩家、版本、延迟与上次在线；「-s 服务器名」可只看单台，隐藏的服务器不出卡。等同于 /mc status"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        event.message_str = f"/mc status {rest}".rstrip() if rest else "/mc status"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("查询")
    async def query(self, event: AstrMessageEvent):
        """查询玩家统计：「/查询 <玩家名> [-s 服务器名]」；数据来自各服模组的统计接口。等同于 /mc stats"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        if rest:
            event.message_str = f"/mc stats {rest}"
        else:
            event.message_str = "/mc stats"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("排行")
    async def rank(self, event: AstrMessageEvent):
        """排行榜：「/排行 [类型] [窗口] -s <服务器名> [-n N]」，类型 时长/死亡/击杀生物/钓鱼，窗口 总/周/月。等同于 /mc rank"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        if rest:
            event.message_str = f"/mc rank {rest}"
        else:
            event.message_str = "/mc rank"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("广播")
    async def broadcast(self, event: AstrMessageEvent):
        """向服务器广播消息：「/广播 [服务器名] <消息>」，省略服务器名时发往本群所有已装模组的服务器。等同于 /mc say"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        event.message_str = f"/mc say {rest}".rstrip() if rest else "/mc say"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 控制台命令 ----------
    @filter.command("执行", alias={"exec"})
    async def exec_command(self, event: AstrMessageEvent):
        """在指定服务器执行控制台命令：「/执行 -s <服务器名> <命令>」（或「/执行 [服务器名] <命令>」），需先为该服配置命令 Token；服务器名必须紧跟命令前，省略即拒绝。等同于 /mc exec"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        if rest:
            event.message_str = f"/mc exec {rest}"
        else:
            event.message_str = "/mc exec"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("tps")
    async def tps_command(self, event: AstrMessageEvent):
        """查看服务器 TPS：「/tps [-s 服务器名]」，只查询已配置模组 API 端口的服务器。等同于 /mc tps"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        if rest:
            event.message_str = f"/mc tps {rest}"
        else:
            event.message_str = "/mc tps"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 绑定命令中文别名 ----------
    # 注意：AstrBot 在 waking_check 阶段命中 wake_prefix 后会把前缀从 message_str 中剥离，
    # 因此这里的 "/" 必须保持可选；唤醒与否由 is_at_or_wake_command 判定（同 CommandFilter）。
    @filter.regex(r"^/?\s*(绑定|bind)\s*\d{0,6}(?:\s+-s\s+\S{1,20})?$")
    async def bind_command(self, event: AstrMessageEvent):
        """绑定游戏账号：「/绑定 <6位绑定码> [-s 服务器名]」。等同于 /mc bind"""
        if not platform_activation.accepts(event):
            return
        import re
        if not event.is_at_or_wake_command:
            return
        raw_msg = event.message_str.strip()
        m = re.match(r"^/?\s*(绑定|bind)\s*(\d{0,6})(\s+-s\s+\S{1,20})?$", raw_msg)
        if not m:
            return
        token = m.group(2).strip()
        selected = (m.group(3) or "").strip()
        if token:
            event.message_str = f"/mc bind {token} {selected}".rstrip()
        else:
            event.message_str = "/mc bind"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 解绑命令中文别名 ----------
    @filter.regex(r"^/?\s*(解绑|unbind)(.*)$")
    async def unbind_command(self, event: AstrMessageEvent):
        """解绑游戏账号：「/解绑 [游戏ID]」解绑自己；管理员可用「/解绑 ID <游戏ID>」或「/解绑 QQ <QQ号>」解绑他人。等同于 /mc unbind"""
        if not platform_activation.accepts(event):
            return
        import re
        if not event.is_at_or_wake_command:
            return
        raw_msg = event.message_str.strip()
        m = re.match(r"^/?\s*(解绑|unbind)(.*)$", raw_msg)
        if not m:
            return
        rest = m.group(2).strip()
        if rest:
            event.message_str = f"/mc unbind {rest}"
        else:
            event.message_str = "/mc unbind"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 查询绑定状态中文别名 ----------
    @filter.regex(r"^/?\s*(查绑定|绑定状态|checkbind)(.*)$")
    async def checkbind_command(self, event: AstrMessageEvent):
        """查询绑定关系：「/查绑定 [游戏ID]」「/查绑定 QQ <QQ号>」或「/查绑定 ID <游戏ID>」；不带参数时按群昵称「玩家（游戏ID）」解析。等同于 /mc check"""
        if not platform_activation.accepts(event):
            return
        import re
        if not event.is_at_or_wake_command:
            return
        raw_msg = event.message_str.strip()
        m = re.match(r"^/?\s*(查绑定|绑定状态|checkbind)(.*)$", raw_msg)
        if not m:
            return
        rest = m.group(2).strip()
        if rest:
            event.message_str = f"/mc check {rest}"
        else:
            event.message_str = "/mc check"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 绑定帮助命令 ----------
    @filter.command("绑定帮助", alias={"bindhelp"})
    async def bindhelp_command(self, event: AstrMessageEvent):
        """显示绑定流程帮助图片：绑定码怎么拿、绑定/解绑/查绑定的完整用法与常见问题。等同于 /mc bindhelp"""
        if not platform_activation.accepts(event):
            return
        event.message_str = "/mc bindhelp"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 帮助命令 ----------
    @filter.command("帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示通用命令帮助图片：服务器查询、绑定、管理员命令与所需权限。等同于 /mc help"""
        if not platform_activation.accepts(event):
            return
        event.message_str = "/mc help"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 上次在线显示开关 ----------
    @filter.command("上次在线")
    async def lastonline_command(self, event: AstrMessageEvent):
        """查看或设置「/在线」卡片是否显示玩家最后活动时间：「/上次在线 [开|关]」。等同于 /mc lastonline"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        if rest:
            event.message_str = f"/mc lastonline {rest}"
        else:
            event.message_str = "/mc lastonline"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 检测昵称 ----------
    @filter.command("检测昵称")
    async def checknick_command(self, event: AstrMessageEvent):
        """检测群昵称是否符合「玩家（游戏ID）」格式：「/检测昵称」列出不符合的成员；「/检测昵称 开|关|忽略 <QQ>|取消忽略 <QQ>|忽略列表」管理检测开关与忽略名单（需管理员）。等同于 /mc checknick 或 /mc nicknamecheck"""
        if not platform_activation.accepts(event):
            return
        rest = _rest_after_command(event)
        if rest:
            # 带参数 → 转给开关命令
            event.message_str = f"/mc nicknamecheck {rest}"
        else:
            # 无参数 → 执行检测
            event.message_str = "/mc checknick"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 群内未绑定成员检测 ----------
    @filter.command("绑定检测", alias={"bindcheck"})
    async def bindcheck_command(self, event: AstrMessageEvent):
        """列出本群中尚未绑定任何游戏账号的成员（仅管理员），便于提醒玩家绑定。等同于 /mc bindcheck"""
        if not platform_activation.accepts(event):
            return
        rest=_rest_after_command(event)
        event.message_str = f"/mc bindcheck {rest}".rstrip() if event.get_platform_name() in ("qq_official","qq_official_webhook") else "/mc bindcheck"
        async for result in handle_mc_command(event):
            yield result

    async def terminate(self):
        self._official.close()
        platform_activation.shutdown(self._activation)
        if self._startup_task is not None:
            self._startup_task.cancel()
            try:
                await self._startup_task
            except asyncio.CancelledError:
                pass
        await stats_sampler.stop()
        stopped = await asyncio.to_thread(stop_db_keepalive, True)
        if not stopped:
            logger.warning("[MCSight] 旧数据库保活仍在结束；新实例不会启动重叠保活。")
        await mod_http.close_session()
        logger.info("MCWatcher 插件已卸载")
