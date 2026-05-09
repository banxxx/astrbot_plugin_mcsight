from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .commands.mc_handler import handle_mc_command
from .features.player_status.controller import run_player_status
from .config.whitelist_config import WhitelistManager

@register("astrbot_plugin_mcwatcher", "poso", "Minecraft 多服务器状态监控插件", "v1.0.0")
class MCWatcher(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 将 AstrBot 上下文注入白名单管理器，使其可以读取/写入插件配置
        WhitelistManager().set_context(context)

    @filter.command("mc")
    async def mc(self, event: AstrMessageEvent):
        async for result in handle_mc_command(event):
            yield result

    @filter.command("在线", aliases=["online"])
    async def player_status_shortcut(self, event: AstrMessageEvent):
        from .utils.permission import check_permission
        if not check_permission(event, "status"):
            yield event.plain_result("权限不足。")
            return
        from .config.server_config import ConfigManager
        config = ConfigManager(event.session_id)
        async for result in run_player_status(event, config):
            yield result

    async def terminate(self):
        logger.info("MCWatcher 插件已卸载")