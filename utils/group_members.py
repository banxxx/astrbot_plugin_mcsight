"""群成员名单读取（OneBot 侧）。

只服务于 aiocqhttp 这类支持 get_group_member_list 的协议端；QQ 官方频道没有群成员
列表接口，迁移过去后这两个函数会一律返回失败，调用方必须走「拿不到名单」的文案分支。
"""
from astrbot.api import logger


async def fetch_group_members(event, group_id):
    """返回群成员 dict 列表（含 user_id/card/nickname/role）；拿不到返回 None"""
    try:
        if hasattr(event, 'bot') and hasattr(event.bot, 'api'):
            result = await event.bot.api.call_action(
                'get_group_member_list',
                group_id=int(group_id)
            )
            if isinstance(result, list):
                return result
    except Exception as e:
        logger.debug(f"call_action 获取群成员列表失败: {e}")

    try:
        if hasattr(event.bot, 'get_group_member_list'):
            result = await event.bot.get_group_member_list(group_id=int(group_id))
            if isinstance(result, list):
                return result
    except Exception as e:
        logger.debug(f"bot.get_group_member_list 失败: {e}")

    return None


async def get_bot_qq(event) -> set:
    """
    返回机器人自身的 QQ 集合。
    按优先级逐个尝试，拿到有效值即停止。
    """
    candidates = set()

    def _try_add(val) -> bool:
        if val is None:
            return False
        s = str(val).strip()
        if not s or s.lower() in ("none", "null", "0"):
            return False
        candidates.add(s)
        return True

    # 1. event.bot.self_id
    try:
        if _try_add(getattr(event.bot, "self_id", None)):
            return candidates
    except Exception:
        pass

    # 2. event.bot.qq
    try:
        if _try_add(getattr(event.bot, "qq", None)):
            return candidates
    except Exception:
        pass

    # 3. event.get_self_id()
    try:
        if hasattr(event, "get_self_id"):
            if _try_add(event.get_self_id()):
                return candidates
    except Exception:
        pass

    # 4. API: get_login_info
    try:
        if hasattr(event.bot, "api"):
            info = await event.bot.api.call_action("get_login_info")
            if isinstance(info, dict):
                if _try_add(info.get("user_id")):
                    return candidates
    except Exception as e:
        logger.debug(f"get_login_info 获取机器人 QQ 失败: {e}")

    # 5. event.bot 上可能的其他字段
    for attr in ("uin", "bot_id", "account"):
        try:
            if _try_add(getattr(event.bot, attr, None)):
                return candidates
        except Exception:
            pass

    return candidates
