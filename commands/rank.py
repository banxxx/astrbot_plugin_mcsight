"""排行榜命令（/排行、/mc rank）

四个榜：时长 / 死亡 / 击杀生物 / 钓鱼；窗口：总 / 周 / 月。
周/月 = 最近 7/30 天有上线记录的人，按累计值排序（不是窗口内增量）。
数据来自模组 /api/stats/all；多服时必须 -s 指定，单台可查服自动用。
"""

import asyncio
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image as AstrImage

from ..config.server_config import ConfigManager, is_hidden
from ..config.whitelist_config import WhitelistManager
from ..utils.leaderboard_doc import build_doc, error_doc
from ..features.leaderboard.image_generator import draw_leaderboard_image
from ..utils import mod_http
from ..utils.temp_image import make_temp_png, remove_quietly

TYPE_ALIAS = {
    "时长": "时长",
    "死亡": "死亡",
    "击杀生物": "击杀生物",
    "击杀": "击杀生物",
    "钓鱼": "钓鱼",
}
DEFAULT_N = 10
MAX_N = 50
BULK_TIMEOUT = 10.0

USAGE = ("用法：/排行 [类型] [窗口] -s <服务器名> [-n N]\n"
         "类型：时长 / 死亡 / 击杀生物 / 钓鱼；窗口：总 / 周 / 月")

_S_VALUE = re.compile(r"(?:^|\s)-s\s+(?:\[([^\]]*)\]|【([^】]*)】|(\S+))")
_N_VALUE = re.compile(r"(?:^|\s)-n\s+(\S+)")


def parse_rank_args(raw: str):
    """解析参数，返回 (type, window, server_name, n, error)。"""
    raw = (raw or "").strip()

    server_name = None
    match = _S_VALUE.search(raw)
    if match:
        server_name = next(g for g in match.groups() if g is not None).strip()
        raw = (raw[:match.start()] + " " + raw[match.end():]).strip()
        if not server_name:
            return None, None, None, None, "服务器名不能为空。\n" + USAGE
        if ((server_name.startswith("[") and not server_name.endswith("]"))
                or (server_name.startswith("【") and not server_name.endswith("】"))):
            return None, None, None, None, "服务器名的括号没有闭合。\n" + USAGE
    elif re.search(r"(?:^|\s)-s(?:\s|$)", raw):
        return None, None, None, None, USAGE

    n = DEFAULT_N
    match = _N_VALUE.search(raw)
    if match:
        try:
            n = int(match.group(1))
        except ValueError:
            return None, None, None, None, f"数量「{match.group(1)}」不是数字。\n" + USAGE
        if n < 1:
            return None, None, None, None, USAGE
        n = min(n, MAX_N)
        raw = (raw[:match.start()] + " " + raw[match.end():]).strip()
    elif re.search(r"(?:^|\s)-n(?:\s|$)", raw):
        return None, None, None, None, USAGE

    type_label = None
    window = None
    for token in raw.split():
        if token in TYPE_ALIAS:
            if type_label:
                return None, None, None, None, "榜单类型重复。\n" + USAGE
            type_label = TYPE_ALIAS[token]
        elif token in ("总", "周", "月"):
            if window:
                return None, None, None, None, "窗口重复。\n" + USAGE
            window = token
        else:
            return None, None, None, None, f"未知的榜单参数「{token}」。\n" + USAGE

    return type_label or "时长", window or "总", server_name, n, None


def _resolve_target(servers: list, wm: WhitelistManager, server_name):
    """按“可查服务器 = 未隐藏 + 已配置模组 API”规则选服，返回 (target, error)。"""
    eligible = [s for s in servers if not is_hidden(s) and wm.has_mod_api(s)]
    if server_name is None:
        if not eligible:
            return None, "本群没有开启模组 API 的服务器，无法查询排行榜。"
        if len(eligible) == 1:
            return eligible[0], None
        return None, ("本群有多台可查询的服务器，请用 -s 指定：\n"
                      "/排行 时长 周 -s <服务器名>")

    target = next((s for s in servers if s["name"] == server_name), None)
    if target is None:
        return None, f"未找到名为「{server_name}」的服务器。"
    if is_hidden(target):
        return None, f"服务器「{server_name}」不参与排行榜查询。"
    if not wm.has_mod_api(target):
        return None, f"服务器「{server_name}」未开启模组 API，无法查询排行榜。"
    return target, None


async def _resolve_self_names(event: AstrMessageEvent, wm: WhitelistManager,
                              config: ConfigManager, target: dict, token: str) -> list:
    """查询者在本群绑定的游戏名；未绑定/查询失败返回空列表。"""
    qq = str(event.get_sender_id())

    if config.get_flag("use_central_db"):
        group_id = event.get_group_id()
        if not group_id:
            return []
        try:
            from .common import execute_db_query
            rows = await execute_db_query(
                "SELECT game_id FROM bindings WHERE group_id = %s AND qq = %s",
                (group_id, qq), fetch_all=True)
            return [str(r["game_id"]) for r in rows if r.get("game_id")]
        except Exception as e:
            logger.warning(f"[rank] 查询绑定失败: {e}")
            return []

    ok, payload = await mod_http.request(
        target["host"], wm.get_server_port(target), "/api/check",
        method="GET", data={"qq": qq}, token=token, timeout=3.0)
    if not ok or not isinstance(payload, dict):
        return []
    game_id = (payload.get("data") or {}).get("gameId")
    return [str(game_id)] if game_id else []


def _bulk_error_text(payload) -> str:
    """把批量接口失败翻译成给用户看的文案。

    旧模组没有 /api/stats/all：路由不存在时是裸 404；也可能被旧 StatsHandler
    当成玩家名 "all" 返回业务 404。两种都按“模组过旧”处理。
    """
    if mod_http.category_of(payload) == mod_http.CAT_NOT_FOUND:
        return "该服务器模组版本过旧，暂不支持排行榜"
    if getattr(payload, "status", 0) == 404:
        return "该服务器模组版本过旧，暂不支持排行榜"
    return str(payload) or "该服务器暂不可用"


async def handle_rank(event: AstrMessageEvent, config: ConfigManager,
                      raw_args: str):
    type_label, window, server_name, n, error = parse_rank_args(raw_args)
    if error:
        yield event.plain_result(error)
        return

    wm = WhitelistManager()
    servers = config.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    target, error = _resolve_target(servers, wm, server_name)
    if error:
        yield event.plain_result(error)
        return

    token = wm.mod_api_token
    if not token:
        yield event.plain_result("❌ 模组 API Token 未配置，请联系管理员设置。")
        return

    ok, payload = await mod_http.request(
        target["host"], wm.get_server_port(target), "/api/stats/all",
        method="GET", token=token, timeout=BULK_TIMEOUT)

    if not ok or not isinstance(payload, dict):
        doc = error_doc(target["name"], type_label, window,
                        _bulk_error_text(payload))
    else:
        data = payload.get("data") or {}
        players = data.get("players")
        if not isinstance(players, list):
            doc = error_doc(target["name"], type_label, window,
                            "模组返回了无法解析的响应")
        else:
            if data.get("truncated"):
                logger.warning(f"[rank] {target['name']} 玩家数超过上限，结果已截断")
            self_names = await _resolve_self_names(event, wm, config, target, token)
            doc = build_doc(target["name"], type_label, window, n,
                            players, self_names)

    path = make_temp_png("mc_rank_")
    try:
        img = await draw_leaderboard_image(doc)
        await asyncio.to_thread(img.save, path)
        await event.send(MessageChain([AstrImage(file=path)]))
    except Exception as e:
        logger.error(f"[rank] 生成排行榜图片失败: {e}")
        yield event.plain_result(f"生成图片失败：{e}")
    finally:
        await asyncio.to_thread(remove_quietly, path)
