import re
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger
from ..utils.permission import check_permission
from ..utils.group_members import fetch_group_members, get_bot_qq

# 与 bind.py 中一致的昵称规范：必须包含一对中英文括号，且括号内有内容
NICKNAME_PATTERN = re.compile(r'[（(](.*?)[）)]')

# 单次回复最多列出多少位不符合的成员（防止刷屏）
MAX_LIST_COUNT = 30


def _check_nickname(name: str) -> bool:
    """返回 True 表示不符合；False 表示符合"""
    if not name or not name.strip():
        return True
    name = name.strip()
    match = NICKNAME_PATTERN.search(name)
    if not match:
        return True
    game_id = match.group(1).strip()
    if not game_id:
        return True
    return False


# ============================================================
# 昵称检测开关（按群独立）
# ============================================================
async def handle_nicknamecheck_switch(event: AstrMessageEvent, config, parts: list):
    """
    处理 /检测昵称 开关 命令
    用法：
      /mc nicknamecheck                查看当前状态
      /mc nicknamecheck 开             开启
      /mc nicknamecheck 关             关闭
      /mc nicknamecheck 忽略 <QQ>      加入忽略列表
      /mc nicknamecheck 取消忽略 <QQ>  从忽略列表移除
      /mc nicknamecheck 忽略列表       查看忽略列表
    """
    if not await check_permission(event, "checknick"):
        yield event.plain_result("权限不足：该命令需要群管理员或插件管理员权限。")
        return

    action = None
    if len(parts) >= 2:
        action = parts[1].strip().lower()

    if action in ("开", "on", "true", "1", "开启"):
        config.set_nickname_check_enabled(True)
        yield event.plain_result("✅ 已开启「昵称检测」功能，本群可使用 /检测昵称。")
    elif action in ("关", "off", "false", "0", "关闭"):
        config.set_nickname_check_enabled(False)
        yield event.plain_result("✅ 已关闭「昵称检测」功能。")
    # ---------- 忽略列表管理 ----------
    elif action in ("忽略", "ignore") and len(parts) >= 3:
        qq = parts[2].strip()
        if not qq.isdigit():
            yield event.plain_result("❌ QQ 号必须是纯数字。")
            return
        ids = config.get_nickname_check_ignore_ids()
        if qq in ids:
            yield event.plain_result(f"⚠️ QQ「{qq}」已在忽略列表中。")
            return
        ids.append(qq)
        config.set_nickname_check_ignore_ids(ids)
        yield event.plain_result(f"✅ 已将 QQ「{qq}」加入忽略列表。")
    elif action in ("取消忽略", "unignore") and len(parts) >= 3:
        qq = parts[2].strip()
        ids = config.get_nickname_check_ignore_ids()
        if qq not in ids:
            yield event.plain_result(f"⚠️ QQ「{qq}」不在忽略列表中。")
            return
        ids.remove(qq)
        config.set_nickname_check_ignore_ids(ids)
        yield event.plain_result(f"✅ 已将 QQ「{qq}」从忽略列表移除。")
    elif action in ("忽略列表", "ignorelist"):
        ids = config.get_nickname_check_ignore_ids()
        if not ids:
            yield event.plain_result("当前忽略列表为空。")
        else:
            yield event.plain_result("当前忽略的 QQ 列表：\n" + "\n".join(f"• {q}" for q in ids))
    elif action is None or action in ("查询", "status", "state"):
        enabled = config.is_nickname_check_enabled()
        state = "已开启" if enabled else "已关闭"
        ignore_ids = config.get_nickname_check_ignore_ids()
        ignore_tip = f"\n忽略列表：{', '.join(ignore_ids)}" if ignore_ids else "\n忽略列表：无"
        yield event.plain_result(
            f"当前本群的「昵称检测」功能：{state}{ignore_tip}\n"
            f"用法：\n"
            f"  /检测昵称 开 / 关\n"
            f"  /检测昵称 忽略 <QQ号>\n"
            f"  /检测昵称 取消忽略 <QQ号>\n"
            f"  /检测昵称 忽略列表"
        )
    else:
        yield event.plain_result(
            "用法：\n"
            "/检测昵称                    查看状态\n"
            "/检测昵称 开 / 关             开启 / 关闭\n"
            "/检测昵称 忽略 <QQ号>         加入忽略列表\n"
            "/检测昵称 取消忽略 <QQ号>     从忽略列表移除\n"
            "/检测昵称 忽略列表            查看忽略列表"
        )


# ============================================================
# 昵称检测
# ============================================================
async def handle_checknick(event: AstrMessageEvent, config, parts: list):
    """处理 /mc checknick —— 检测群成员昵称是否符合规范"""
    if not await check_permission(event, "checknick"):
        yield event.plain_result("权限不足：该命令需要群管理员或插件管理员权限。")
        return

    group_id = event.get_group_id()
    if not group_id:
        yield event.plain_result("❌ 请在群聊中使用此命令。")
        return

    # 检查本群是否开启该功能
    if not config.is_nickname_check_enabled():
        yield event.plain_result(
            "❌ 本群的「昵称检测」功能未开启。\n"
            "请管理员使用 /检测昵称 开 启用。"
        )
        return

    yield event.plain_result("🔍 正在检测群成员昵称，成员较多时可能需要几秒，请稍候...")

    members = await fetch_group_members(event, group_id)
    if members is None:
        yield event.plain_result(
            "❌ 获取群成员列表失败。\n"
            "请确认机器人是否为群管理员，或协议端是否支持该接口。"
        )
        return

    # ---- 先读取手动忽略列表（廉价操作，优先判断） ----
    manual_ignore = set(config.get_nickname_check_ignore_ids())
    if manual_ignore:
        logger.info(f"昵称检测：手动忽略列表 = {manual_ignore}")

    # ---- 再获取机器人自身 QQ（可能涉及 API 调用，较慢） ----
    bot_qqs = await get_bot_qq(event)
    logger.info(f"昵称检测：机器人自身 QQ = {bot_qqs or '未获取到'}")

    total = len(members)
    bad_members = []
    excluded_count = 0

    for m in members:
        if not isinstance(m, dict):
            continue

        user_id = str(m.get("user_id", "")).strip()

        # 1) 优先检查手动忽略列表
        if user_id in manual_ignore:
            excluded_count += 1
            continue

        # 2) 其次排除机器人自身
        if bot_qqs and user_id in bot_qqs:
            excluded_count += 1
            continue

        card = (m.get("card") or "").strip()
        nick = (m.get("nickname") or "").strip()
        display = card or nick

        if _check_nickname(display):
            bad_members.append({
                "user_id": user_id,
                "name": display,
            })

    total -= excluded_count

    if not bad_members:
        yield event.plain_result(
            f"✅ 检测完成！共 {total} 位群成员，全部符合昵称规范。"
        )
        return

    lines = [
        "📋 昵称检测结果",
        f"群成员总数：{total}",
        f"不符合规范：{len(bad_members)} 人",
        ""
    ]

    show_count = min(len(bad_members), MAX_LIST_COUNT)
    for m in bad_members[:show_count]:
        lines.append(f"• {m['name']}（{m['user_id']}）")

    if len(bad_members) > MAX_LIST_COUNT:
        lines.append("")
        lines.append(f"... 还有 {len(bad_members) - MAX_LIST_COUNT} 人未显示")

    lines.append("")
    lines.append("请提醒以上成员修改群昵称为「玩家（游戏ID）」格式。")

    yield event.plain_result("\n".join(lines))