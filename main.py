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

    # ---------- 新增：绑定命令中文别名 ----------
    @filter.command("绑定", aliases=["bind"])
    async def bind_command(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        if raw_msg.startswith('/'):
            raw_msg = raw_msg[1:]
        if raw_msg.startswith("绑定"):
            rest = raw_msg[len("绑定"):].strip()
        elif raw_msg.startswith("bind"):
            rest = raw_msg[len("bind"):].strip()
        else:
            rest = raw_msg
        if rest:
            event.message_str = f"/mc bind {rest}"
        else:
            event.message_str = "/mc bind"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 新增：解绑命令中文别名 ----------
    @filter.command("解绑", aliases=["unbind"])
    async def unbind_command(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        if raw_msg.startswith('/'):
            raw_msg = raw_msg[1:]
        if raw_msg.startswith("解绑"):
            rest = raw_msg[len("解绑"):].strip()
        elif raw_msg.startswith("unbind"):
            rest = raw_msg[len("unbind"):].strip()
        else:
            rest = raw_msg
        if rest:
            event.message_str = f"/mc unbind {rest}"
        else:
            event.message_str = "/mc unbind"
        async for result in handle_mc_command(event):
            yield result

    # ---------- 新增：查询绑定状态中文别名 ----------
    @filter.command("查绑定", aliases=["绑定状态", "checkbind"])
    async def checkbind_command(self, event: AstrMessageEvent):
        raw_msg = event.message_str.strip()
        if raw_msg.startswith('/'):
            raw_msg = raw_msg[1:]
        # 支持多个前缀
        prefixes = ["查绑定", "绑定状态", "checkbind"]
        rest = raw_msg
        for p in prefixes:
            if raw_msg.startswith(p):
                rest = raw_msg[len(p):].strip()
                break
        if rest:
            event.message_str = f"/mc check {rest}"
        else:
            event.message_str = "/mc check"
        async for result in handle_mc_command(event):
            yield result

    async def terminate(self):
        logger.info("MCWatcher 插件已卸载")