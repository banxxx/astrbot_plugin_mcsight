from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .commands.mc_handler import handle_mc_command
from .features.player_status.controller import run_player_status

@register("astrbot_plugin_mcwatcher")
class MCWatcher(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("mc")
    async def mc(self, event: AstrMessageEvent):
        """所有 /mc xxx 指令统一入口，委托给 commands 模块"""
        async for result in handle_mc_command(event):
            yield result

    @filter.command("在线", aliases=["online"])
    async def player_status_shortcut(self, event: AstrMessageEvent):
        async for result in run_player_status(event):
            yield result

    async def terminate(self):
        logger.info("MCWatcher 插件已卸载")