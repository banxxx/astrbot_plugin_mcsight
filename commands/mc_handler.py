import asyncio
import aiohttp
import json
import re
from urllib.parse import urlparse
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from astrbot.api import logger
from ..config.server_config import ConfigManager
from ..config.whitelist_config import WhitelistManager
from ..utils.permission import check_permission

# 导入拆分的处理函数
from ..commands import (
    handle_whitelist, handle_help, handle_bindhelp,
    handle_add, handle_remove, handle_edit, handle_batchadd, handle_batchremove,
    handle_list, handle_move, handle_swap, handle_lastonline,
    handle_say, handle_tps, handle_status, handle_stats,
    handle_bind, handle_unbind, handle_check
)

async def handle_mc_command(event: AstrMessageEvent):
    group_id = event.get_group_id()
    if group_id:
        config = ConfigManager(group_id=group_id)
    else:
        config = ConfigManager(session_id=event.session_id)

    msg = event.message_str.strip()
    if msg.startswith('/'):
        msg = msg[1:]
    if msg.lower().startswith('mc'):
        msg = msg[2:].strip()

    if not msg:
        yield event.plain_result("请提供子命令。使用 /mc help 查看帮助。")
        return

    parts = msg.split()
    sub_cmd = parts[0].lower()

    # 权限检查（管理命令）
    admin_cmds = {"add", "remove", "edit", "batchadd", "batchremove", "move", "swap", "lastonline"}
    if sub_cmd in admin_cmds:
        if not await check_permission(event, sub_cmd):
            yield event.plain_result("权限不足：该操作需要群管理员或插件管理员权限。")
            return

    # 路由到各个处理函数
    if sub_cmd == "whitelist":
        async for result in handle_whitelist(event, parts):
            yield result
    elif sub_cmd == "help":
        async for result in handle_help(event):
            yield result
    elif sub_cmd == "bindhelp":
        async for result in handle_bindhelp(event):
            yield result
    elif sub_cmd == "add":
        async for result in handle_add(event, config, parts):
            yield result
    elif sub_cmd == "remove":
        async for result in handle_remove(event, config, parts):
            yield result
    elif sub_cmd == "edit":
        async for result in handle_edit(event, config, parts):
            yield result
    elif sub_cmd == "batchadd":
        async for result in handle_batchadd(event, config, parts):
            yield result
    elif sub_cmd == "batchremove":
        async for result in handle_batchremove(event, config, parts):
            yield result
    elif sub_cmd == "list":
        async for result in handle_list(event, config):
            yield result
    elif sub_cmd == "move":
        async for result in handle_move(event, config, parts):
            yield result
    elif sub_cmd == "swap":
        async for result in handle_swap(event, config, parts):
            yield result
    elif sub_cmd == "lastonline":
        async for result in handle_lastonline(event, config, parts):
            yield result
    elif sub_cmd == "say":
        async for result in handle_say(event, config, parts):
            yield result
    elif sub_cmd == "tps":
        async for result in handle_tps(event, config, parts):
            yield result
    elif sub_cmd == "status":
        async for result in handle_status(event, config):
            yield result
    elif sub_cmd == "stats":
        async for result in handle_stats(event, config, parts):
            yield result
    elif sub_cmd == "bind":
        async for result in handle_bind(event, config, parts):
            yield result
    elif sub_cmd == "unbind":
        async for result in handle_unbind(event, config, parts):
            yield result
    elif sub_cmd == "check":
        async for result in handle_check(event, config, parts):
            yield result
    else:
        yield event.plain_result(f"未知子命令: {sub_cmd}，使用 /mc help 查看帮助。")