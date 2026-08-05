from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .commands.mc_handler import handle_mc_command
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

    async def terminate(self):
        logger.info("MCWatcher 插件已卸载")