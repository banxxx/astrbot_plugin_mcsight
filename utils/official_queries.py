"""官方只读请求。仅接收独立群配置与显式令牌，不读取旧配置或绑定库。"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import time

from .official_core import OfficialError, PLAYER_RE
from .stats_schema import CATEGORIES


READ_PATH = re.compile(r"^/api/(?:status|tps|stats/all|stats/[A-Za-z0-9_]{1,16})$")
MAX_RESPONSE = 4 * 1024 * 1024


@dataclass
class StatisticsQuery:
    title: str
    lines: list
    data: dict
    snapshots: list
    player_name: str
    server_name: str
    today: object

    def __iter__(self):
        yield self.title
        yield self.lines


def clean(value, limit=160):
    value = str(value if value is not None else "")
    return "".join(c for c in value if ord(c) >= 32 and ord(c) != 127)[:limit]


def number(value, default=0):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError, OverflowError):
        return default


async def read_mod(server, token, endpoint):
    """一次 GET，无重定向、无重试、无响应/主机/凭据日志。"""
    if not READ_PATH.fullmatch(endpoint) or server.get("api_port") is None:
        raise OfficialError("该服务器未配置所需只读模组接口。")
    if not token:
        raise OfficialError("该官方环境尚未配置模组令牌。")
    import aiohttp
    host = server["host"]
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host):
        raise OfficialError("服务器地址无效。")
    url = f"http://{host}:{server['api_port']}{endpoint}"
    try:
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=8, connect=2)) as session:
            async with session.get(url, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False) as response:
                if response.status in (401, 403):
                    raise OfficialError("模组未授权：请检查该官方环境的令牌。")
                if response.status == 404:
                    raise OfficialError("模组接口或玩家记录不存在；请核对模组版本及玩家名。")
                if response.status == 429:
                    raise OfficialError("模组暂时限流，请稍后重试。")
                if response.status != 200:
                    raise OfficialError("模组暂时无法提供数据。")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise OfficialError("模组结果过大，请缩小查询范围。")
                    chunks.append(chunk)
                import json
                payload = json.loads(b"".join(chunks))
                if (not isinstance(payload, dict) or payload.get("success") is not True
                        or not isinstance(payload.get("data"), dict)):
                    raise OfficialError("模组响应格式无效或拒绝本次查询。")
                return payload["data"]
    except OfficialError:
        raise
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError, UnicodeError):
        raise OfficialError("模组未响应或响应异常，请稍后重试。") from None


async def read_mcstatus(server):
    from mcstatus import JavaServer
    try:
        target = await JavaServer.async_lookup(f"{server['host']}:{server['game_port']}", timeout=4)
        result = await target.async_status()
        return {"online_players": result.players.online, "max_players": result.players.max,
                "version": result.version.name, "latency": result.latency,
                "players": [{"name": p.name} for p in (result.players.sample or [])], "sample_only": True}
    except Exception:
        raise OfficialError("服务器状态查询暂不可用。") from None


def arguments(raw):
    parts = raw.split()
    target = None
    if "-s" in parts:
        pos = parts.index("-s")
        if pos + 1 >= len(parts) or parts.count("-s") != 1:
            raise OfficialError("请使用 -s <服务器名> 指定一台服务器。")
        target = parts[pos + 1]
        parts = parts[:pos] + parts[pos + 2:]
    return parts, target


class QueryService:
    def __init__(self, *, mod=read_mod, status=read_mcstatus, clock=time.time, history=None):
        self.mod, self.status, self.clock = mod, status, clock
        self.history = history

    async def query(self, command, raw, group, token):
        parts, name = arguments(raw)
        targets = [s for s in group["servers"] if not s["hidden"] and (name is None or s["name"] == name)]
        if not targets:
            raise OfficialError("没有可查询的服务器，请在官方群配置中添加或取消隐藏。")
        if command in ("stats", "rank"):
            targets = [s for s in targets if s["api_port"] is not None]
            if not targets:
                raise OfficialError("该查询需要已配置模组 API 的服务器。")
        if command == "rank" and len(targets) != 1:
            raise OfficialError("有多台可查服务器，请用 -s <服务器名> 指定排行目标。")
        if command in ("status", "tps"):
            if parts:
                raise OfficialError("用法：/mc status [-s 服务器名] 或 /mc tps [-s 服务器名]")
            lines = []
            semaphore = asyncio.Semaphore(4)
            async def one(server):
                async with semaphore:
                    return await self._server(command, server, token, group["policy"])
            for result in await asyncio.gather(*(one(server) for server in targets)):
                lines.extend(result)
            return "服务器在线状态" if command == "status" else "服务器 TPS", lines
        if command == "stats":
            if len(parts) != 1 or not PLAYER_RE.fullmatch(parts[0]):
                raise OfficialError("用法：/mc stats <1–16 位游戏名> [-s 服务器名]")
            errors = []
            for server in targets:
                try:
                    data = await self.mod(server, token, "/api/stats/" + parts[0])
                except OfficialError as error:
                    errors.append(f"{server['name']}：{error}")
                    continue
                if not isinstance(data, dict) or len(data) > 256:
                    raise OfficialError("模组统计字段格式无效。")
                numeric_keys = {"playTime", "blocksMined", "blocksPlaced", "jumps"}
                numeric_keys.update(item[1] for category in CATEGORIES.values() for item in category["items"])
                sanitized = {key: max(0, number(data.get(key))) for key in numeric_keys}
                sanitized["online"] = data.get("online") is True
                sanitized["playTimeFormatted"] = clean(data.get("playTimeFormatted") or f"{sanitized['playTime'] / 72000:.1f} 小时", 64)
                rows = [f"服务器：{server['name']}", f"玩家：{parts[0]}", "生涯累计 · 公开统计"]
                rows += ["游玩：" + sanitized["playTimeFormatted"]]
                snapshots = []
                if self.history is not None:
                    try:
                        snapshots = await asyncio.to_thread(self.history.record, group["environment_id"],
                            group["group_openid"], server["server_id"], parts[0], sanitized)
                        rows.append(f"官方独立历史：{len(snapshots)} 天，按 UTC 日历记录。")
                    except OfficialError as error:
                        rows.append(str(error))
                else:
                    rows.append("尚未积累本环境历史，当前展示生涯累计。")
                for name, category in CATEGORIES.items():
                    rows.append(f"【{name}】")
                    for label, key, unit, _reference, _kind in category["items"]:
                        value = sanitized[key]
                        displayed = str(int(value)) if float(value).is_integer() else f"{value:.2f}"
                        rows.append(f"{label}：{displayed}{(' ' + unit) if unit else ''}")
                return StatisticsQuery("玩家数据统计", rows, sanitized, snapshots, parts[0], server["name"],
                                       datetime.fromtimestamp(self.clock(), timezone.utc).date())
            raise OfficialError("查询未完成：" + "；".join(errors)[:600])
        if command == "rank":
            return await self._rank(parts,targets[0],token,group.get("self_games"),group.get("self_games_note"))
        raise OfficialError("尚未开放该只读命令。")

    async def _server(self, command, server, token, policy):
        try:
            if command == "tps":
                if server["api_port"] is None:
                    return [f"{server['name']} · 未配置模组 API"]
                data = await self.mod(server, token, "/api/tps")
                tps = number(data.get("tps"), -1)
                return [f"{server['name']} · TPS {tps:.2f}" if tps >= 0 else f"{server['name']} · TPS 暂不可用"]
            source = "模组"
            try:
                if server["api_port"] is None:
                    raise OfficialError("未配置模组")
                data = await self.mod(server, token, "/api/status")
            except OfficialError:
                data = await self.status(server)
                source = "mcstatus（玩家名单为采样）"
            rows = [f"{server['name']} · {int(number(data.get('online_players')))}/{int(number(data.get('max_players')))} 人 · {source}"]
            if policy["show_server_version"]:
                rows.append("版本：" + clean(data.get("version", "未知"), 60))
            if policy["show_server_latency"] and "latency" in data:
                rows.append(f"延迟：{number(data['latency']):.0f} ms")
            players = data.get("players", [])
            names = [clean(p.get("name"), 32) for p in players[:100] if isinstance(p, dict)] if isinstance(players, list) else []
            if names:
                rows.append("玩家：" + "、".join(names))
            if policy["show_last_online"]:
                timestamp=number(data.get("last_activity_time"))
                player=data.get("last_activity_player")
                if timestamp>0 and isinstance(player,str) and PLAYER_RE.fullmatch(player):
                    elapsed=max(0,int(self.clock()-timestamp/1000))
                    shown=(f"{elapsed//86400}天前" if elapsed>=86400 else f"{elapsed//3600}小时前" if elapsed>=3600
                        else f"{elapsed//60}分钟前" if elapsed>=60 else "刚刚")
                    rows.append(f"最后活动：{player} · {shown}")
                else:
                    rows.append("最后活动：当前数据源没有可靠记录")
            if server["remark"]:
                rows.append("备注：" + clean(server["remark"], 80))
            if data.get("storage_type")=="qq_official" and data.get("binding_ready") is False:
                rows.append("绑定运行条件暂未齐备；在线和统计查询独立可用。")
            return rows + [""]
        except OfficialError as error:
            return [f"{server['name']} · {error}", ""]

    async def _rank(self,parts,server,token,self_games=None,self_games_note=None):
        limit = 10
        if "-n" in parts:
            pos = parts.index("-n")
            try:
                limit = int(parts[pos + 1])
            except (ValueError, IndexError):
                raise OfficialError("排行数量需要 1–50 的整数。") from None
            parts = parts[:pos] + parts[pos + 2:]
        if not 1 <= limit <= 50:
            raise OfficialError("排行数量需要 1–50 的整数。")
        types = {"时长": "play_time", "死亡": "deaths", "击杀生物": "mob_kills", "击杀": "mob_kills", "钓鱼": "fish_caught"}
        kind, window = "时长", "总"
        seen_type = seen_window = False
        for part in parts:
            if part in types and not seen_type:
                kind, seen_type = part, True
            elif part in ("总", "周", "月") and not seen_window:
                window, seen_window = part, True
            else:
                raise OfficialError("用法：/mc rank [时长|死亡|击杀生物|钓鱼] [总|周|月] -s <服务器名> [-n 1–50]")
        data = await self.mod(server, token, "/api/stats/all")
        players = data.get("players")
        if not isinstance(players, list):
            raise OfficialError("模组排行数据格式无效。")
        cutoff = self.clock() * 1000 - (7 if window == "周" else 30) * 86400000 if window != "总" else None
        rows = []
        for player in players[:10000]:
            if not isinstance(player, dict) or not PLAYER_RE.fullmatch(str(player.get("name", ""))):
                continue
            stats = player.get("stats")
            if not isinstance(stats, dict) or (cutoff is not None and number(player.get("last_seen")) < cutoff):
                continue
            value = int(number(stats.get(types[kind])))
            if value > 0:
                rows.append((value, player["name"]))
        rows.sort(key=lambda row: (-row[0], row[1].lower()))
        lines = [f"服务器：{server['name']}", "周/月榜按近期活跃筛选，数值仍为生涯累计。" if window != "总" else "生涯累计 · 公开排行"]
        rank, previous = 0, None
        personal=[]
        for i, (value, name) in enumerate(rows, 1):
            if value != previous:
                rank, previous = i, value
            shown = f"{value / 20 / 3600:.1f} 小时" if types[kind] == "play_time" else str(value)
            mine=self_games is not None and name.lower() in self_games
            if i<=limit: lines.append(f"{rank:02d}  {name}   {shown}"+("  [本人]" if mine else ""))
            if mine: personal.append(f"{name} · 第 {rank} 名 · {shown}")
        if not rows:
            lines.append("当前窗口暂无可展示记录。")
        if data.get("truncated") or len(players) > 10000:
            lines.append("注意：模组结果已截断，本页不代表完整排行。")
        if self_games is not None:
            lines.extend(["本人在本权威范围内的有效账号："]+personal if personal else ["本人有效账号在此排行窗口内无记录。" if self_games else "本人在当前权威范围内尚无有效绑定。"])
        elif self_games_note:
            lines.append(self_games_note)
        return f"{kind} · {window}榜", lines


def render_text_pages(title, lines):
    """Fallback for official SDKs that only accept remotely hosted image URLs."""
    header = clean(title, 100)
    pages, current = [], [header]
    size = len(header)
    for line in lines:
        text = clean(line, 1800)
        chunks = [text[i:i + 800] for i in range(0, len(text), 800)] or [""]
        for chunk in chunks:
            if size + len(chunk) + 1 > 1550 and len(current) > 1:
                pages.append("\n".join(current))
                current, size = [header], len(header)
            current.append(chunk)
            size += len(chunk) + 1
    if current:
        pages.append("\n".join(current))
    total = max(len(pages), 1)
    return [f"{page}\n第 {index}/{total} 页" for index, page in enumerate(pages, 1)]


def render_pages(title, lines):
    """只使用仓库字体，图片不含平台身份，不请求头像或读取旧统计快照。"""
    from io import BytesIO
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont
    font_file = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "msyh.ttf"
    font = ImageFont.truetype(str(font_file), 24)
    heading = ImageFont.truetype(str(font_file), 34)
    rows = []
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for line in lines:
        remaining = clean(line, 4096)
        current = ""
        for char in remaining:
            if measure.textlength(current + char, font=font) > 780:
                rows.append(current)
                current = char
            else:
                current += char
        rows.append(current)
    rows = rows or ["暂无结果"]
    if len(rows) > 1000:
        raise OfficialError("结果过长，请缩小查询范围。")
    count = (len(rows) + 21) // 22
    pages = []
    for start in range(0, len(rows), 22):
        subset = rows[start:start + 22]
        image = Image.new("RGB", (880, 184 + len(subset) * 40), "#F3F5F8")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 880, 8), fill="#25765B")
        draw.text((42, 30), clean(title, 40), font=heading, fill="#202B3B")
        draw.text((44, 83), "MCSight · QQ 官方群查询", font=font, fill="#68788A")
        for i, line in enumerate(subset):
            draw.text((44, 130 + i * 40), line, font=font, fill="#27364A")
        draw.text((44, image.height - 42), f"第 {start // 22 + 1}/{count} 页", font=font, fill="#68788A")
        stream = BytesIO()
        image.save(stream, format="PNG", compress_level=6)
        payload = stream.getvalue()
        if len(payload) > 4 * 1024 * 1024:
            raise OfficialError("图片体积超过上限，请缩小查询范围。")
        pages.append(payload)
    return pages


def render_statistics_pages(document):
    """Reuse the established six-category charts without the legacy avatar/cache imports."""
    from io import BytesIO
    from ..features.player_status.player_stats_generator import draw_player_stats_image
    image = draw_player_stats_image(document.data, document.player_name, server_name=document.server_name,
        is_online=document.data.get("online", False), snapshots=document.snapshots, today=document.today)
    # Keep the chart intact: arbitrary height slicing can split the footer and
    # leave a nearly empty final page. Text pagination remains the size fallback.
    stream = BytesIO()
    image.save(stream, format="PNG", compress_level=6)
    payload = stream.getvalue()
    if len(payload) > 3 * 1024 * 1024:
        raise OfficialError("统计图片编码体积过大，请改用分页文字。")
    return [payload]
