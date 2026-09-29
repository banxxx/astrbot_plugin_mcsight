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
from ..utils.permission import check_permission, is_astrbot_super_admin
from ..utils import debounce

# 导入拆分的处理函数
from ..commands import (
    handle_whitelist, handle_help, handle_bindhelp,
    handle_add, handle_remove, handle_edit, handle_batchadd, handle_batchremove,
    handle_list, handle_move, handle_swap, handle_lastonline,
    handle_say, handle_tps, handle_status, handle_stats,
    handle_bind, handle_unbind, handle_check,
    handle_checknick, handle_nicknamecheck_switch
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

    # 防抖：中文别名、正则别名和裸 /mc 都汇聚到这里，sub_cmd 已是归一后的真命令，
    # 在这一个入口生效即覆盖所有命令。超管不受限，方便连测。
    wm = WhitelistManager()
    user_id = str(event.get_sender_id())
    if not (is_astrbot_super_admin(event) or wm.is_super_admin(user_id)):
        scope = str(group_id or event.session_id)
        allowed, remaining = debounce.gate(scope, user_id, sub_cmd)
        if not allowed:
            # 只在每个窗口第一次被拒时回话：否则刷屏党能拿我们的回复刷我们的屏
            if debounce.should_notify(scope, user_id, sub_cmd):
                logger.info(f"[debounce] 拒绝 {scope}/{user_id} 的 {sub_cmd}，剩余 {remaining} 秒")
                yield event.plain_result(f"该命令 {debounce.window_of(sub_cmd):.0f} 秒内只能"
                                         f"使用一次，请 {remaining} 秒后再试。")
            return

    # 权限检查（管理命令）
    admin_cmds = {"add", "remove", "edit", "batchadd", "batchremove", "move", "swap", "lastonline", "checknick", "nicknamecheck", "say"}
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
    elif sub_cmd == "checknick":
        async for result in handle_checknick(event, config, parts):
            yield result
    elif sub_cmd == "nicknamecheck":
        async for result in handle_nicknamecheck_switch(event, config, parts):
            yield result
    else:
        yield event.plain_result(f"未知子命令: {sub_cmd}，使用 /mc help 查看帮助。")