"""群内未绑定成员检测（/绑定检测 → /mc bindcheck）。

拿群成员名单（OneBot）与中心库 bindings 表的 QQ 集合做差，出图列出谁还没绑定任何游戏账号。
一个 QQ 可以有多条绑定记录，这里只问「这个 QQ 出现过没有」，与条数无关。

发图走 event.send 而不是 yield：AstrBot 会把 handler 产出的整条链攒到最后才发，而图片是在
发送那一刻才读盘转 base64 的——用 yield 就没法控制「发完再删临时文件」的顺序。
"""
import asyncio
import os
import tempfile
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image as AstrImage
from astrbot.api.message_components import Plain

from ..config.whitelist_config import WhitelistManager
from ..features.bindcheck_image.image_generator import draw_unbound_pages
from ..utils.group_members import fetch_group_members, get_bot_qq
from .common import execute_db_query

# PNG 编码档位：9 比 6 贵 3 倍、只小 1%（同 /在线 的结论）
PNG_COMPRESS_LEVEL = 6

# 逐页直发之间留个间隔，别把群消息频率打满
PAGE_SEND_GAP = 0.6


def _save_pages(images):
    """同步落盘（编码已是 CPU 活，整体在 to_thread 里跑）。文件名带页序，避免上一条还没发完就被覆盖"""
    stamp = int(time.time() * 1000)
    paths = []
    for idx, img in enumerate(images):
        path = os.path.join(tempfile.gettempdir(), f"mc_bindcheck_{stamp}_{idx + 1}.png")
        img.save(path, compress_level=PNG_COMPRESS_LEVEL)
        paths.append(path)
    return paths


def _cleanup(paths):
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            # 发图失败时留着文件反而方便排查，不升级为错误
            pass


async def handle_bindcheck(event: AstrMessageEvent, config, parts: list):
    """出图列出本群中未绑定任何游戏账号的成员"""
    group_id = event.get_group_id()
    if not group_id:
        yield event.plain_result("❌ 请在群聊中使用此命令。")
        return

    wm = WhitelistManager()
    if not wm.use_central_db:
        yield event.plain_result(
            "❌ 本插件未启用中心数据库，没有可比对的绑定记录。\n"
            "请在配置中开启 use_central_db 后重试。"
        )
        return

    # 进度提示直发：yield 出去的话要等整个流程跑完才发，就起不到「先回话」的作用了
    try:
        await event.send(MessageChain([Plain(
            "🔍 正在比对群成员与绑定记录，成员较多时可能需要几秒，请稍候..."
        )]))
    except Exception as e:
        logger.warning(f"绑定检测：进度提示发送失败 {type(e).__name__}: {e}")

    # 先读绑定表（跨境一次往返），再取群成员名单：库失败时不用白等一次协议端调用
    try:
        rows = await execute_db_query(
            "SELECT DISTINCT qq FROM bindings WHERE group_id = %s",
            (str(group_id),), fetch_all=True
        )
    except Exception as e:
        logger.error(f"绑定检测：读取绑定记录失败 group_id={group_id} {type(e).__name__}: {e}")
        yield event.plain_result("❌ 读取绑定记录失败（数据库暂时不可达），请稍后再试。")
        return

    bound_qqs = {str(r.get("qq", "")).strip() for r in (rows or [])}
    bound_qqs.discard("")

    members = await fetch_group_members(event, group_id)
    if members is None:
        yield event.plain_result(
            "❌ 获取群成员列表失败。\n"
            "请确认机器人是否为群管理员，或协议端是否支持该接口。"
        )
        return

    bot_qqs = await get_bot_qq(event)

    unbound = []
    member_total = 0
    for m in members:
        if not isinstance(m, dict):
            continue
        user_id = str(m.get("user_id", "")).strip()
        if not user_id:
            continue
        if bot_qqs and user_id in bot_qqs:
            continue
        member_total += 1
        if user_id in bound_qqs:
            continue
        display = (m.get("card") or "").strip() or (m.get("nickname") or "").strip()
        unbound.append({"user_id": user_id, "name": display or "（无昵称）",
                        "qq": user_id})

    if not unbound:
        yield event.plain_result(
            f"✅ 检测完成！本群 {member_total} 位成员均已绑定游戏账号。"
        )
        return

    info = {
        "group_total": member_total,
        "unbound_total": len(unbound),
        "stamp": time.strftime("检测于 %H:%M:%S"),
    }

    try:
        images = await draw_unbound_pages(unbound, info)
        paths = await asyncio.to_thread(_save_pages, images)
    except Exception as e:
        logger.error(f"绑定检测：名单图生成失败 {type(e).__name__}: {e}")
        yield event.plain_result("❌ 名单图生成失败，请稍后再试或联系管理员查看日志。")
        return

    pages = len(paths)
    sent = 0
    try:
        for idx, path in enumerate(paths):
            if idx:
                await asyncio.sleep(PAGE_SEND_GAP)
            try:
                await event.send(MessageChain([AstrImage(file=path)]))
            except Exception as e:
                logger.error(
                    f"绑定检测：第 {idx + 1}/{pages} 页发送失败 {type(e).__name__}: {e}"
                )
                yield event.plain_result(
                    f"❌ 第 {idx + 1} / {pages} 页发送失败，前面 {sent} 页已发出。"
                )
                return
            sent += 1
    finally:
        # event.send 是 await 到协议端受理为止，图片字节在那一刻已读走，此刻删除才安全
        await asyncio.to_thread(_cleanup, paths)

    logger.info(f"绑定检测：群 {group_id} 未绑定 {len(unbound)} 人，已分 {pages} 张图发出")
