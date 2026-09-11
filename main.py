from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .commands.mc_handler import handle_mc_command
from .config.server_config import ConfigManager
from .config.whitelist_config import WhitelistManager

@register("astrbot_plugin_mcsight", "poso", "Minecraft 多服务器状态监控插件", "v1.0.0")
class MCWatcher(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        WhitelistManager().set_context(context)

    @filter.command("mc")
    async def mc(self, event: AstrMessageEvent):
        async for result in handle_mc_command(event):
            yield result

    # ---------- 现有别名 ----------
    @filter.command("在线", aliases=["online"])
    async def online(self, event: AstrMessageEvent):
        event.message_str = "/mc status"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("查询")
    async def query(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        if raw_msg.startswith("/"):
            raw_msg = raw_msg[1:]
        rest = raw_msg[len("查询"):].strip()
        if rest:
            event.message_str = f"/mc stats {rest}"
        else:
            event.message_str = "/mc stats"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("广播")
    async def broadcast(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        if raw_msg.startswith('/'):
            raw_msg = raw_msg[1:]
        rest = raw_msg[len("广播"):].strip()
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

    @filter.command("tps")
    async def tps_command(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        if raw_msg.startswith('/'):
            raw_msg = raw_msg[1:]
        rest = raw_msg[len("tps"):].strip()
        if rest:
            event.message_str = f"/mc tps {rest}"
        else:
            event.message_str = "/mc tps"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 绑定命令中文别名 ----------
    @filter.regex(r"^/?\s*(绑定|bind)\s*\d{0,6}$")
    async def bind_command(self, event: AstrMessageEvent):
        import re
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
        import re
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
        import re
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
    @filter.command("绑定帮助", aliases=["bindhelp"])
    async def bindhelp_command(self, event: AstrMessageEvent):
        """显示绑定相关命令的帮助图片"""
        event.message_str = "/mc bindhelp"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 上次在线显示开关 ----------
    @filter.command("上次在线")
    async def lastonline_command(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        if raw_msg.startswith('/'):
            raw_msg = raw_msg[1:]
        rest = raw_msg[len("上次在线"):].strip()
        if rest:
            event.message_str = f"/mc lastonline {rest}"
        else:
            event.message_str = "/mc lastonline"
        async for result in handle_mc_command(event):
            yield result

    async def terminate(self):
        logger.info("MCWatcher 插件已卸载")