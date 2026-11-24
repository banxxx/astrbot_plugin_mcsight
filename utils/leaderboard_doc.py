"""排行榜绘制文档构建（纯计算，不依赖 AstrBot / 配置 / 网络）。

commands/rank.py（OneBot）与 utils/official_queries.py（QQ 官方）共用同一套
构建逻辑，保证两边渲染出完全一致的排行榜图片；文字分页仍各自处理。
"""
import time

TYPE_KEY = {
    "时长": "play_time",
    "死亡": "deaths",
    "击杀生物": "mob_kills",
    "钓鱼": "fish_caught",
}
WINDOW_MS = {"周": 7 * 86_400_000, "月": 30 * 86_400_000}


def format_value(key: str, value: int) -> str:
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


def hint_for(type_label: str) -> str:
    return "其他榜：" + " / ".join(t for t in TYPE_KEY if t != type_label)


def now_str() -> str:
    return time.strftime("%Y/%m/%d  %H:%M:%S")


def doc_shell(server: str, type_label: str, window: str) -> dict:
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
        "hint": hint_for(type_label),
        "time": now_str(),
    }


def error_doc(server: str, type_label: str, window: str, message: str) -> dict:
    doc = doc_shell(server, type_label, window)
    doc["chip"] = "不可用"
    doc["error"] = message
    return doc


def build_doc(server: str, type_label: str, window: str, n: int,
              players: list, self_names: list) -> dict:
    """按 /api/stats/all 的 players 原始列表构建排行榜文档。"""
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
                "value": f"#{best['rank']} · {format_value(key, best['value'])}",
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
        row["value"] = format_value(key, row["value"])
        row["is_self"] = bool(
            self_doc and self_doc.get("ranked")
            and row["name"].lower() == self_doc["name"].lower())

    doc = doc_shell(server, type_label, window)
    doc["rows"] = rows
    doc["self"] = self_doc
    doc["compact"] = len(rows) > 20
    doc["empty"] = not rows
    doc["chip"] = f"{window}榜 · Top {len(rows)}" if rows else "暂无数据"
    return doc
