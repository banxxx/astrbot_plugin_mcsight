from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from ..config.whitelist_config import WhitelistManager

# 权限等级定义
LEVEL_BLACKLIST = 0
LEVEL_MEMBER = 1
# 配置里的「白名单管理员」承诺与群主/群管理员同等权限（schema 的 hint 也这么写），
# 所以和 LEVEL_GROUP_ADMIN 同级；曾经是 2，比群管理员低一级，配了也过不了管理命令
LEVEL_GROUP_ADMIN = 3
LEVEL_WHITELIST_ADMIN = LEVEL_GROUP_ADMIN
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
    # 按服配置命令 Token：等同交出该服控制台权限的钥匙，只给超管
    "token": LEVEL_SUPER_ADMIN,
    "say": LEVEL_GROUP_ADMIN,
    # 执行控制台命令：等同服务器控制台权限，群管理员/群主可用
    "exec": LEVEL_GROUP_ADMIN,
    "tps": LEVEL_MEMBER,
    "bind": LEVEL_MEMBER,          # 允许普通玩家绑定自己
    "unbind": LEVEL_GROUP_ADMIN,   # 解绑需要管理员权限
    "check": LEVEL_MEMBER,         # 公开查询
    "lastonline": LEVEL_GROUP_ADMIN,
    "checknick": LEVEL_GROUP_ADMIN, # 检查昵称需要管理员权限
    "nicknamecheck": LEVEL_GROUP_ADMIN, # 昵称检测开关需要管理员权限
    "bindcheck": LEVEL_GROUP_ADMIN,  # 群内未绑定成员检测：会列出他人 QQ，仅管理员
}

# 分发入口统一拦闸的子命令（commands/mc_handler.py 用这个集合决定要不要查权限）
ADMIN_CMDS = {"add", "remove", "edit", "batchadd", "batchremove", "move", "swap",
              "lastonline", "checknick", "nicknamecheck", "bindcheck",
              "say", "exec", "token"}

# 不在入口拦、但在各自 handler 里自查的：/mc whitelist 只有一条列表命令，超管专用
SELF_CHECKED_CMDS = {"whitelist"}

# 帮助页与 /帮助 图据此打权限标识：入口没拦闸的（如 /查询 /解绑自己）不该出现「管理员」
GATED_CMDS = ADMIN_CMDS | SELF_CHECKED_CMDS


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

    # 5. 群主 / 群管理员：两个开关各管各的角色。
    # 旧写法里群主走的是 `role in ('admin','owner') and enable_group_admin` 那条，
    # 于是「允许群主」关掉后，只要「允许管理员」还开着，群主照样能过——那个开关等于没生效。
    role = await get_group_role(event)
    if role == 'owner' and wm.enable_group_owner:
        logger.debug(f"用户 {user_id} 是群主，且开关已启用")
        return LEVEL_GROUP_ADMIN
    if role == 'admin' and wm.enable_group_admin:
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

    # 群成员接口要数字 user_id；qq_official 等平台的 ID 可能是 openid，
    # 直接 int() 会在这里抛 ValueError，把整条命令打成异常
    try:
        user_id = int(event.get_sender_id())
    except (TypeError, ValueError):
        logger.debug(f"发送者 ID 不是数字（{event.get_sender_id()!r}），无法按群角色判定")
        return 'member'

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

    # 注意：event.is_admin() 表示的是 AstrBot 全局管理员（role="admin"），
    # 不是群角色；全局管理员在 get_user_permission_level() 里已经按超级管理员处理，
    # 这里不能拿它冒充群管理员。
    logger.debug("无法获取角色，返回 member")
    return 'member'


def is_astrbot_super_admin(event: AstrMessageEvent) -> bool:
    """是否是 AstrBot 全局管理员（config 里的 admins_id）。

    4.29 的唤醒阶段会把 admins_id 命中的发送者标成 role="admin"，官方判断入口就是
    event.is_admin()；旧写法查的 get_admin_list / get_sender_admin / get_priority
    在 4.29 里根本不存在，于是全局管理员一直不被认，管理命令照样提示权限不足。
    """
    try:
        if event.is_admin():
            return True
    except Exception:
        pass

    # 兼容其它版本/适配器提供的接口；名单里可能混着 int，统一按字符串比
    try:
        getter = getattr(event, 'get_admin_list', None)
        if callable(getter):
            admin_list = getter()
            if admin_list and str(event.get_sender_id()) in {str(a) for a in admin_list}:
                return True
        getter = getattr(event, 'get_sender_admin', None)
        if callable(getter) and getter():
            return True
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