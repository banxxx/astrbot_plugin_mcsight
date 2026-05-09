import logging
from astrbot.api.event import AstrMessageEvent
from ..config.whitelist_config import WhitelistManager

logger = logging.getLogger("astrbot_plugin_mcwatcher")

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


def get_user_permission_level(event: AstrMessageEvent) -> int:
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

    # 5. 群主/管理员（受开关控制）
    if is_group_admin(event):
        role = get_group_role(event)
        if role == 'owner' and wm.enable_group_owner:
            logger.debug(f"用户 {user_id} 是群主，且开关已启用")
            return LEVEL_GROUP_ADMIN
        elif role == 'admin' and wm.enable_group_admin:
            logger.debug(f"用户 {user_id} 是群管理员，且开关已启用")
            return LEVEL_GROUP_ADMIN

    return LEVEL_MEMBER


def get_group_role(event: AstrMessageEvent) -> str:
    """获取用户在群中的角色：owner / admin / member / unknown"""
    try:
        if hasattr(event, 'is_admin') and callable(event.is_admin):
            # 注意：is_admin 通常返回管理员角色，但不能区分群主
            return 'admin' if event.is_admin() else 'member'
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'sender'):
            sender = event.message_obj.sender
            if hasattr(sender, 'role'):
                return sender.role  # 'owner', 'admin', 'member'
    except Exception:
        pass
    return 'unknown'


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


def is_group_admin(event: AstrMessageEvent) -> bool:
    try:
        if hasattr(event, 'is_admin') and callable(event.is_admin):
            return event.is_admin()
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'sender'):
            sender = event.message_obj.sender
            if hasattr(sender, 'role'):
                return sender.role in ('admin', 'owner')
        if hasattr(event, 'get_role') and callable(event.get_role):
            return event.get_role() in ('admin', 'owner')
    except Exception:
        pass
    return False


def check_permission(event: AstrMessageEvent, command: str) -> bool:
    level = get_user_permission_level(event)
    required = PERMISSION_REQUIRED.get(command, LEVEL_GROUP_ADMIN)
    return level >= required