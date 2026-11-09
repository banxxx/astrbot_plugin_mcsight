"""排行榜命令（/排行、/mc rank）

四个榜：时长 / 死亡 / 击杀生物 / 钓鱼；窗口：总 / 周 / 月。
周/月 = 最近 7/30 天有上线记录的人，按累计值排序（不是窗口内增量）。
数据来自模组 /api/stats/all；多服时必须 -s 指定，单台可查服自动用。
"""

import asyncio
import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image as AstrImage

from ..config.server_config import ConfigManager, is_hidden
from ..config.whitelist_config import WhitelistManager
from ..features.leaderboard.image_generator import draw_leaderboard_image
from ..utils import mod_http
from ..utils.temp_image import make_temp_png, remove_quietly

TYPE_KEY = {
    "时长": "play_time",
    "死亡": "deaths",
    "击杀生物": "mob_kills",
    "钓鱼": "fish_caught",
}
TYPE_ALIAS = {
    "时长": "时长",
    "死亡": "死亡",
    "击杀生物": "击杀生物",
    "击杀": "击杀生物",
    "钓鱼": "钓鱼",
}
WINDOW_MS = {"周": 7 * 86_400_000, "月": 30 * 86_400_000}
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
                              target: dict, token: str) -> list:
    """查询者在本群绑定的游戏名；未绑定/查询失败返回空列表。"""
    qq = str(event.get_sender_id())

    if wm.use_central_db:
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


def _format_value(key: str, value: int) -> str:
    if key != "play_time":
        return f"{value} 次"
    minutes = value // 20 // 60
    if minutes <= 0:
        return "不到1分钟"
    days, hours, mins = minutes // 1440, (minutes % 1440) // 60, minutes % 60
    if days > 0:
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if hours > 0:
        return f"{hours}小时{mins}分" if mins else f"{hours}小时"
    return f"{mins}分钟"


def _hint_for(type_label: str) -> str:
    return "其他榜：" + " / ".join(t for t in TYPE_KEY if t != type_label)


def _now_str() -> str:
    return time.strftime("%Y/%m/%d  %H:%M:%S")


def _doc_shell(server: str, type_label: str, window: str) -> dict:
    subtitle = f"{server} · {type_label} · {window}榜"
    if window in ("周", "月"):
        days = 7 if window == "周" else 30
        subtitle += f"（近 {days} 天活跃）"
    return {
        "title": "排行榜",
        "subtitle": subtitle,
        "server": server,
        "chip": f"{window}榜",
        "type_label": type_label,
        "rows": [],
        "self": None,
        "compact": False,
        "empty": False,
        "error": None,
        "hint": _hint_for(type_label),
        "time": _now_str(),
    }


def _error_doc(server: str, type_label: str, window: str, message: str) -> dict:
    doc = _doc_shell(server, type_label, window)
    doc["chip"] = "不可用"
    doc["error"] = message
    return doc


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


def _build_doc(server: str, type_label: str, window: str, n: int,
               players: list, self_names: list) -> dict:
    key = TYPE_KEY[type_label]
    cutoff = (time.time() * 1000 - WINDOW_MS[window]) if window in WINDOW_MS else None

    candidates = []
    for player in players:
        if not isinstance(player, dict):
            continue
        name = str(player.get("name") or "").strip()
        if not name:
            continue
        stats = player.get("stats") or {}
        try:
            value = int(stats.get(key, 0))
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            continue
        if cutoff is not None:
            try:
                seen = int(player.get("last_seen") or 0)
            except (TypeError, ValueError):
                seen = 0
            if seen < cutoff:
                continue
        candidates.append({"name": name, "uuid": player.get("uuid"),
                           "value": value})

    candidates.sort(key=lambda c: (-c["value"], c["name"].lower()))
    prev = None
    rank = 0
    for idx, item in enumerate(candidates):
        if item["value"] != prev:
            rank = idx + 1
            prev = item["value"]
        item["rank"] = rank

    rows = [dict(item) for item in candidates[:n]]

    self_doc = None
    if self_names:
        by_name = {c["name"].lower(): c for c in candidates}
        best = None
        for name in self_names:
            hit = by_name.get(str(name).lower())
            if hit and (best is None or hit["rank"] < best["rank"]):
                best = hit
        if best:
            self_doc = {
                "name": best["name"],
                "uuid": best["uuid"],
                "value": f"#{best['rank']} · {_format_value(key, best['value'])}",
                "ranked": True,
            }
        else:
            by_player = {str(p.get("name") or "").lower(): p for p in players
                         if isinstance(p, dict)}
            display = next((by_player[str(nm).lower()] for nm in self_names
                            if str(nm).lower() in by_player), None)
            self_doc = {
                "name": str(display.get("name")) if display else str(self_names[0]),
                "uuid": display.get("uuid") if display else None,
                "value": "未上榜",
                "ranked": False,
            }

    for row in rows:
        row["value"] = _format_value(key, row["value"])
        row["is_self"] = bool(
            self_doc and self_doc.get("ranked")
            and row["name"].lower() == self_doc["name"].lower())

    doc = _doc_shell(server, type_label, window)
    doc["rows"] = rows
    doc["self"] = self_doc
    doc["compact"] = len(rows) > 20
    doc["empty"] = not rows
    doc["chip"] = f"{window}榜 · Top {len(rows)}" if rows else "暂无数据"
    return doc


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
        doc = _error_doc(target["name"], type_label, window,
                         _bulk_error_text(payload))
    else:
        data = payload.get("data") or {}
        players = data.get("players")
        if not isinstance(players, list):
            doc = _error_doc(target["name"], type_label, window,
                             "模组返回了无法解析的响应")
        else:
            if data.get("truncated"):
                logger.warning(f"[rank] {target['name']} 玩家数超过上限，结果已截断")
            self_names = await _resolve_self_names(event, wm, target, token)
            doc = _build_doc(target["name"], type_label, window, n,
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
