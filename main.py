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

    @filter.command("在线", aliases=["online"])
    async def online(self, event: AstrMessageEvent):
        # 将消息转换为 "/mc status"
        event.message_str = "/mc status"
        async for result in handle_mc_command(event):
            yield result

    @filter.command("查询")
    async def query(self, event: AstrMessageEvent):
        # 提取 "查询" 后面的参数
        raw_msg = event.message_str.strip()
        if raw_msg.startswith("/"):
            raw_msg = raw_msg[1:]  # 去掉可能的前缀
        # 注意：此时消息可能是 "查询 POSOO" 或 "查询 POSOO 土豆"
        # 转换为 "/mc stats ..."
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
        # 提取 "广播" 后面的内容
        rest = raw_msg[len("广播"):].strip()
        if not rest:
            event.message_str = "/mc say"
            async for result in handle_mc_command(event):
                yield result
            return
        # 获取当前群组配置的服务器列表，用于检查第一个词是否为服务器名
        group_id = event.get_group_id()
        if group_id:
            config = ConfigManager(group_id=group_id)
        else:
            config = ConfigManager(session_id=event.session_id)
        servers = config.get_all_servers()
        server_names = [s["name"] for s in servers]
        parts = rest.split()
        # 如果第一个词是服务器名
        if parts and parts[0] in server_names:
            server_name = parts[0]
            message = " ".join(parts[1:])
            if not message:
                # 只有服务器名没有消息，提示
                event.message_str = "/mc say"
                async for result in handle_mc_command(event):
                    yield result
                return
            event.message_str = f"/mc say -s {server_name} {message}"
        else:
            # 全部服务器
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

    async def terminate(self):
        logger.info("MCWatcher 插件已卸载")