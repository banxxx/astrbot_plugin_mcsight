from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .commands.mc_handler import handle_mc_command
from .commands.common import start_db_keepalive, stop_db_keepalive
from .config.server_config import ConfigManager
from .config.whitelist_config import WhitelistManager
from .utils import mod_http, stats_sampler
from .web.api import register_view_api


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


@register("astrbot_plugin_mcsight", "poso", "Minecraft 多服务器状态监控插件", "v1.2.7")
class MCWatcher(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        WhitelistManager().set_context(context)
        # 内核按插件目录名加载 data/config/<目录名>_config.json，把这份配置对象也交给
        # WhitelistManager：文件路径对不上时仍能读到真实配置（超管名单、模组 Token 等）
        WhitelistManager().set_config(config)
        start_db_keepalive()
        # config 是 data/config/<插件名>_config.json 的内存对象，配置页写它就等于面板改配置
        register_view_api(context, config)

    async def initialize(self):
        # 内核在插件实例化后 await 本方法，此时事件循环已在跑，采样任务要起在这里
        stats_sampler.start()

    @filter.command("mc")
    async def mc(self, event: AstrMessageEvent):
        """插件总入口：/mc help | status | list | add | remove | edit | batchadd | batchremove | move | swap | token | say | exec | tps | stats | rank | bind | unbind | check | whitelist | lastonline | checknick | nicknamecheck | bindcheck（子命令说明见 /帮助）"""
        async for result in handle_mc_command(event):
            yield result

    # ---------- 现有别名 ----------
    @filter.command("在线", alias={"online"})
    async def online(self, event: AstrMessageEvent):
        """服务器状态卡片：在线玩家、版本、延迟与上次在线；「-s 服务器名」可只看单台，隐藏的服务器不出卡。等同于 /mc status"""
        event.message_str = "/mc status"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("查询")
    async def query(self, event: AstrMessageEvent):
        """查询玩家统计：「/查询 <玩家名> [-s 服务器名]」；数据来自各服模组的统计接口。等同于 /mc stats"""
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
        rest = _rest_after_command(event)
        if not rest:
            event.message_str = "/mc say"
            async for result in handle_mc_command(event):
                yield result
            return
        group_id = event.get_group_id()
        if group_id:
            config = ConfigManager(group_id=group_id)
        else:
            config = ConfigManager(session_id=event.session_id)
        servers = config.get_all_servers()
        server_names = [s["name"] for s in servers]
        parts = rest.split()
        if parts and parts[0] in server_names:
            server_name = parts[0]
            message = " ".join(parts[1:])
            if not message:
                event.message_str = "/mc say"
                async for result in handle_mc_command(event):
                    yield result
                return
            event.message_str = f"/mc say -s {server_name} {message}"
        else:
            event.message_str = f"/mc say {rest}"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 控制台命令 ----------
    @filter.command("执行", alias={"exec"})
    async def exec_command(self, event: AstrMessageEvent):
        """在指定服务器执行控制台命令：「/执行 -s <服务器名> <命令>」（或「/执行 [服务器名] <命令>」），需先为该服配置命令 Token；服务器名必须紧跟命令前，省略即拒绝。等同于 /mc exec"""
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
    @filter.regex(r"^/?\s*(绑定|bind)\s*\d{0,6}$")
    async def bind_command(self, event: AstrMessageEvent):
        """把当前 QQ 与游戏账号绑定：「/绑定 <6位绑定码>」，绑定码显示在游戏内 Title/ActionBar；只发「绑定」可看说明。等同于 /mc bind"""
        import re
        if not event.is_at_or_wake_command:
            return
        raw_msg = event.message_str.strip()
        m = re.match(r"^/?\s*(绑定|bind)\s*(\d{0,6})$", raw_msg)
        if not m:
            return
        token = m.group(2).strip()
        if token:
            event.message_str = f"/mc bind {token}"
        else:
            event.message_str = "/mc bind"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 解绑命令中文别名 ----------
    @filter.regex(r"^/?\s*(解绑|unbind)(.*)$")
    async def unbind_command(self, event: AstrMessageEvent):
        """解绑游戏账号：「/解绑 [游戏ID]」解绑自己；管理员可用「/解绑 ID <游戏ID>」或「/解绑 QQ <QQ号>」解绑他人。等同于 /mc unbind"""
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
        event.message_str = "/mc bindhelp"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 帮助命令 ----------
    @filter.command("帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示通用命令帮助图片：服务器查询、绑定、管理员命令与所需权限。等同于 /mc help"""
        event.message_str = "/mc help"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 上次在线显示开关 ----------
    @filter.command("上次在线")
    async def lastonline_command(self, event: AstrMessageEvent):
        """查看或设置「/在线」卡片是否显示玩家最后活动时间：「/上次在线 [开|关]」。等同于 /mc lastonline"""
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
        event.message_str = "/mc bindcheck"
        async for result in handle_mc_command(event):
            yield result

    async def terminate(self):
        await stats_sampler.stop()
        stop_db_keepalive()
        await mod_http.close_session()
        logger.info("MCWatcher 插件已卸载")