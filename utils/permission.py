from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from ..config.whitelist_config import WhitelistManager

# 权限等级定义
LEVEL_BLACKLIST = 0
LEVEL_MEMBER = 1
LEVEL_WHITELIST_ADMIN = 2
LEVEL_GROUP_ADMIN = 3
LEVEL_SUPER_ADMIN = 4

# 各命令所需最低权限等级
PERMISSION_REQUIRED = {
    "help": LEVEL_MEMBER,
    "status": LEVEL_MEMBER,
    "list": LEVEL_MEMBER,
    "add": LEVEL_GROUP_ADMIN,
    "remove": LEVEL_GROUP_ADMIN,
    "edit": LEVEL_GROUP_ADMIN,
    "batchadd": LEVEL_GROUP_ADMIN,
    "batchremove": LEVEL_GROUP_ADMIN,
    "move": LEVEL_GROUP_ADMIN,
    "swap": LEVEL_GROUP_ADMIN,
    "whitelist": LEVEL_SUPER_ADMIN,
}


async def get_user_permission_level(event: AstrMessageEvent) -> int:
    wm = WhitelistManager()
    user_id = str(event.get_sender_id())

    # 1. 黑名单检查
    if wm.is_blacklisted(user_id):
        logger.debug(f"用户 {user_id} 在黑名单中")
        return LEVEL_BLACKLIST

    # 2. AstrBot 全局管理员
    if is_astrbot_super_admin(event):
        logger.debug(f"用户 {user_id} 是 AstrBot 全局管理员")
        return LEVEL_SUPER_ADMIN

    # 3. 插件超级管理员
    if wm.is_super_admin(user_id):
        logger.debug(f"用户 {user_id} 是插件超级管理员")
        return LEVEL_SUPER_ADMIN

    # 4. 插件白名单管理员
    if wm.is_whitelist_admin(user_id):
        logger.debug(f"用户 {user_id} 是插件白名单管理员")
        return LEVEL_WHITELIST_ADMIN

    # 5. 群管理员（包括群主）
    if await is_group_admin(event):
        role = await get_group_role(event)
        if role == 'owner' and wm.enable_group_owner:
            logger.debug(f"用户 {user_id} 是群主，且开关已启用")
            return LEVEL_GROUP_ADMIN
        elif role in ('admin', 'owner') and wm.enable_group_admin:
            logger.debug(f"用户 {user_id} 是群管理员，且开关已启用")
            return LEVEL_GROUP_ADMIN

    return LEVEL_MEMBER


async def get_group_role(event: AstrMessageEvent) -> str:
    """异步获取用户在群中的真实角色"""
    # 获取群号（使用 get_group_id() 方法）
    group_id = event.get_group_id()
    if not group_id:
        logger.debug("无法获取群号，返回 member")
        return 'member'

    user_id = int(event.get_sender_id())

    # 如果是 AiocqhttpMessageEvent，使用其 bot.get_group_member_info
    if isinstance(event, AiocqhttpMessageEvent):
        try:
            result = await event.bot.get_group_member_info(
                group_id=group_id,
                user_id=user_id
            )
            if result and isinstance(result, dict):
                role = result.get('role', 'member')
                logger.debug(f"从 AiocqhttpMessageEvent 获取角色: {role}")
                return role
        except Exception as e:
            logger.debug(f"AiocqhttpMessageEvent 获取角色失败: {e}")

    # 通用方法：使用 api.call_action
    try:
        if hasattr(event, 'bot') and hasattr(event.bot, 'api'):
            result = await event.bot.api.call_action(
                'get_group_member_info',
                group_id=group_id,
                user_id=user_id
            )
            if result and isinstance(result, dict):
                role = result.get('role', 'member')
                logger.debug(f"从 call_action 获取角色: {role}")
                return role
    except Exception as e:
        logger.debug(f"call_action 获取角色失败: {e}")

    # 尝试 event.bot.get_group_member_info（如果存在）
    try:
        if hasattr(event.bot, 'get_group_member_info'):
            result = await event.bot.get_group_member_info(
                group_id=group_id,
                user_id=user_id
            )
            if result and isinstance(result, dict):
                role = result.get('role', 'member')
                logger.debug(f"从 bot.get_group_member_info 获取角色: {role}")
                return role
    except Exception as e:
        logger.debug(f"bot.get_group_member_info 获取角色失败: {e}")

    # 回退到 event.is_admin()
    try:
        if event.is_admin():
            logger.debug("回退到 event.is_admin() 返回 admin")
            return 'admin'
    except Exception:
        pass
    logger.debug("无法获取角色，返回 member")
    return 'member'


def is_astrbot_super_admin(event: AstrMessageEvent) -> bool:
    try:
        if hasattr(event, 'get_admin_list') and callable(event.get_admin_list):
            admin_list = event.get_admin_list()
            if admin_list and event.get_sender_id() in admin_list:
                return True
        if hasattr(event, 'get_sender_admin') and callable(event.get_sender_admin):
            return event.get_sender_admin()
        if hasattr(event, 'get_priority') and callable(event.get_priority):
            return event.get_priority() >= 100
    except Exception:
        pass
    return False


async def is_group_admin(event: AstrMessageEvent) -> bool:
    try:
        role = await get_group_role(event)
        if role in ('owner', 'admin'):
            return True
    except Exception:
        pass
    return False


async def check_permission(event: AstrMessageEvent, command: str) -> bool:
    level = await get_user_permission_level(event)
    required = PERMISSION_REQUIRED.get(command, LEVEL_GROUP_ADMIN)
    return level >= required