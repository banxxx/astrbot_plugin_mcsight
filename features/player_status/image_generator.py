import asyncio
import os
import time
import re
import aiohttp
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any
from ...utils.avatar_cache import download_avatar, get_default_avatar
from ...config.whitelist_config import WhitelistManager
from ...utils.text_renderer import draw_text_with_emoji

# ========== 样式常量（高清优化版）==========
CONTAINER_WIDTH = 1080
CONTAINER_PADDING = 36
CONTAINER_BOTTOM_PADDING = 52
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 24
BIG_DOT_SIZE = 48

PAGE_TITLE_COLOR = '#5b6abf'
PAGE_TITLE_SIZE = 40
PAGE_TITLE_MARGIN_BOTTOM = 12

MAIN_TITLE_COLOR = '#555555'
MAIN_TITLE_SIZE = 24
MAIN_TITLE_MARGIN_BOTTOM = 28

CARD_BG = '#FFFFFF'
CARD_RADIUS = 16
CARD_PADDING_TOP = 24
CARD_PADDING_SIDE = 28
CARD_PADDING_BOTTOM = 32
CARD_MARGIN_BOTTOM = 24

STATUS_DOT_SIZE = 16
STATUS_DOT_ONLINE = '#5cb85c'
STATUS_DOT_OFFLINE = '#d9534f'

SERVER_NAME_COLOR = '#444444'
SERVER_NAME_SIZE = 24
SERVER_COUNT_COLOR = '#777777'
SERVER_COUNT_SIZE = 18

SERVER_VERSION_COLOR = '#888888'
SERVER_VERSION_SIZE = 18
LATENCY_GOOD_COLOR = '#7BA87F'
LATENCY_MEDIUM_COLOR = '#D9A87C'
LATENCY_BAD_COLOR = '#C47D7D'
LATENCY_SIZE = 18

DIVIDER_COLOR = '#e0e0e0'
DIVIDER_MARGIN_TOP = 14
DIVIDER_MARGIN_BOTTOM = 28

SEAT_WIDTH = 220
AVATAR_SIZE = 48
AVATAR_RADIUS = 8
GAP_H = 20
GAP_V = 16

TEXT_NAME_COLOR = '#555555'
TEXT_NAME_SIZE = 16

EMPTY_TEXT_COLOR = '#999999'
EMPTY_TEXT_SIZE = 18
PLAYER_LIST_HIDDEN_COLOR = '#aaaaaa'
PLAYER_LIST_HIDDEN_SIZE = 18

TIME_TEXT_COLOR = '#aaaaaa'
TIME_TEXT_SIZE = 16
TIME_MARGIN_TOP = 32

# ★ 新增：并发下载头像的最大并发数（避免打爆第三方 API）
AVATAR_DOWNLOAD_CONCURRENCY = 10

# 圆点缓存：{(size, color): Image}
_dot_cache = {}


# 常用 Emoji Unicode 范围（覆盖常见 Emoji）
EMOJI_PATTERN = re.compile(
    r'^[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE00-\uFE0F]+$'
)

# ========== 字体加载 ==========
def _load_font(size):
    base = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(base, '..', '..', 'resources', 'fonts', 'msyh.ttf'),
        "msyh.ttc", "PingFang.ttc", "wqy-microhei.ttc"
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except:
            continue
    return ImageFont.load_default()

FONT_PAGE_TITLE = _load_font(PAGE_TITLE_SIZE)
FONT_MAIN_TITLE = _load_font(MAIN_TITLE_SIZE)
FONT_SERVER_NAME = _load_font(SERVER_NAME_SIZE)
FONT_SERVER_COUNT = _load_font(SERVER_COUNT_SIZE)
FONT_SERVER_VERSION = _load_font(SERVER_VERSION_SIZE)
FONT_LATENCY = _load_font(LATENCY_SIZE)
FONT_NAME = _load_font(TEXT_NAME_SIZE)
FONT_EMPTY = _load_font(EMPTY_TEXT_SIZE)
FONT_PLAYER_HIDDEN = _load_font(PLAYER_LIST_HIDDEN_SIZE)
FONT_TIME = _load_font(TIME_TEXT_SIZE)

def make_rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)
    img = img.copy()
    img.putalpha(mask)
    return img

def get_dot(size: int, color: str) -> Image.Image:
    """获取指定大小和颜色的圆点图像（先绘制大圆再缩放，保证平滑）"""
    key = (size, color)
    if key in _dot_cache:
        return _dot_cache[key]
    big_size = max(size * 2, 48)
    big_img = Image.new('RGBA', (big_size, big_size), (0, 0, 0, 0))
    big_draw = ImageDraw.Draw(big_img)
    big_draw.ellipse([(0, 0), (big_size, big_size)], fill=color)
    resized = big_img.resize((size, size), Image.LANCZOS)
    _dot_cache[key] = resized
    return resized

def get_latency_color(ms: float) -> str:
    if ms <= 100:
        return LATENCY_GOOD_COLOR
    elif ms <= 500:
        return LATENCY_MEDIUM_COLOR
    else:
        return LATENCY_BAD_COLOR

def format_last_online(ts_ms: int) -> str:
    """
    将时间戳（毫秒）转换为人类可读的“上次在线”描述。
    """
    if not ts_ms or ts_ms <= 0:
        return "无记录"
    try:
        now_ms = time.time() * 1000
        diff_sec = (now_ms - ts_ms) / 1000.0
    except Exception:
        return "无记录"

    if diff_sec < 0:
        return "刚刚"

    if diff_sec < 60:
        return "刚刚"
    elif diff_sec < 3600:
        return f"{int(diff_sec / 60)}分钟"
    elif diff_sec < 86400:
        hours = int(diff_sec / 3600)
        minutes = int((diff_sec % 3600) / 60)
        if minutes > 0:
            return f"{hours}小时{minutes}分钟"
        return f"{hours}小时"
    elif diff_sec < 365 * 86400:
        days = int(diff_sec / 86400)
        hours = int((diff_sec % 86400) / 3600)
        if hours > 0:
            return f"{days}天{hours}小时"
        return f"{days}天"
    else:
        years = int(diff_sec / (365 * 86400))
        days = int((diff_sec % (365 * 86400)) / 86400)
        if days > 0:
            return f"{years}年{days}天"
        return f"{years}年"


async def draw_multi_server_image(servers_data: List[Dict[str, Any]], show_last_online: bool = False) -> Image.Image:
    now_str = time.strftime("%Y/%m/%d  %H:%M:%S")
    wm = WhitelistManager()
    show_version = wm.show_server_version
    show_latency = wm.show_server_latency

    content_width = CONTAINER_WIDTH - 2 * CONTAINER_PADDING
    inner_width = content_width - 2 * CARD_PADDING_SIDE
    cols = max(1, (inner_width + GAP_H) // (SEAT_WIDTH + GAP_H))

    card_infos = []
    async with aiohttp.ClientSession() as session:
        # ---- 第一步：构建 card_infos（无需网络请求） ----
        for srv in servers_data:
            version = srv.get("version", "未知")
            latency = srv.get("latency", 0.0)
            name = srv["name"]

            players = srv.get("players", [])
            online = srv.get("online", 0)
            max_players = srv.get("max", 1)
            online_str = str(online)
            max_str = str(max_players)
            error = srv.get("error")
            last_activity_time = srv.get("last_activity_time", 0)

            has_players = len(players) > 0
            if has_players:
                rows = (len(players) + cols - 1) // cols
                grid_h = rows * (AVATAR_SIZE + TEXT_NAME_SIZE + GAP_V)
            else:
                rows = 0
                grid_h = PLAYER_LIST_HIDDEN_SIZE + 20

            card_w = inner_width
            card_h = (CARD_PADDING_TOP + FONT_SERVER_NAME.size + 8 +
                      DIVIDER_MARGIN_TOP + 1 + DIVIDER_MARGIN_BOTTOM +
                      grid_h + CARD_PADDING_BOTTOM)

            card_infos.append({
                "name": name,
                "online_str": online_str,
                "max_str": max_str,
                "players": players,
                "online": online,
                "has_players": has_players,
                "grid_h": grid_h,
                "card_h": card_h,
                "error": error,
                "version": version,
                "latency": latency,
                "last_activity_time": last_activity_time,
            })

        # ============================================================
        # ★ 新增：预并发下载所有玩家头像（去重 + 限流并发）
        # ============================================================
        avatar_map = {}   # key: (player_name, player_uuid, AVATAR_SIZE) -> Image
        download_requests = []  # [(key, name, uuid, is_premium), ...]
        seen_keys = set()

        for card in card_infos:
            if not card["has_players"]:
                continue
            for player in card["players"]:
                if isinstance(player, dict):
                    pname = player.get("name")
                    puuid = player.get("uuid")
                    pprem = player.get("is_premium")
                else:
                    pname = player
                    puuid = None
                    pprem = None

                if not pname:
                    continue

                key = (pname, puuid, AVATAR_SIZE)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                download_requests.append((key, pname, puuid, pprem))

        if download_requests:
            # 用 Semaphore 限制并发数，避免打爆第三方头像 API
            sem = asyncio.Semaphore(AVATAR_DOWNLOAD_CONCURRENCY)

            async def _limited_download(name, uuid_, prem):
                async with sem:
                    try:
                        return await download_avatar(session, name, AVATAR_SIZE, prem, uuid=uuid_)
                    except Exception as e:
                        from astrbot.api import logger as _logger
                        _logger.error(f"头像并发下载异常 {name}: {e}")
                        return get_default_avatar(AVATAR_SIZE)

            # 一次性并发发起所有下载任务
            coros = [_limited_download(n, u, p) for _, n, u, p in download_requests]
            results = await asyncio.gather(*coros, return_exceptions=False)

            for (key, _, _, _), img in zip(download_requests, results):
                avatar_map[key] = img

            from astrbot.api import logger as _logger
            _logger.info(f"✅ 并发下载完成：{len(download_requests)} 个头像（去重后），并发上限 {AVATAR_DOWNLOAD_CONCURRENCY}")

        # ---- 第二步：计算总高度并创建画布 ----
        total_height = CONTAINER_PADDING + PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM
        total_height += MAIN_TITLE_SIZE + MAIN_TITLE_MARGIN_BOTTOM
        for c in card_infos:
            total_height += c["card_h"] + CARD_MARGIN_BOTTOM
        total_height -= CARD_MARGIN_BOTTOM
        total_height += TIME_TEXT_SIZE + TIME_MARGIN_TOP + CONTAINER_BOTTOM_PADDING

        img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
        draw = ImageDraw.Draw(img)

        draw.rounded_rectangle(
            [(0, 0), (CONTAINER_WIDTH, total_height)],
            radius=CONTAINER_RADIUS, fill=CONTAINER_BG
        )

        y = CONTAINER_PADDING

        # 标题
        title_text = "在线玩家列表"
        title_bbox = draw.textbbox((0, 0), title_text, font=FONT_PAGE_TITLE)
        title_w = title_bbox[2] - title_bbox[0]
        title_x = (CONTAINER_WIDTH - title_w) // 2
        draw.text((title_x, y), title_text, fill=PAGE_TITLE_COLOR, font=FONT_PAGE_TITLE)
        y += PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM

        # 总在线人数
        total_online = sum(s["online"] for s in servers_data if not s.get("error"))
        main_text = f"总在线: {total_online} 人"
        main_bbox = draw.textbbox((0, 0), main_text, font=FONT_MAIN_TITLE)
        main_w = main_bbox[2] - main_bbox[0]
        main_x = (CONTAINER_WIDTH - main_w) // 2
        draw.text((main_x, y), main_text, fill=MAIN_TITLE_COLOR, font=FONT_MAIN_TITLE)
        y += MAIN_TITLE_SIZE + MAIN_TITLE_MARGIN_BOTTOM

        # ---- 第三步：遍历卡片绘制（头像从 avatar_map 取，不再串行 await） ----
        for card in card_infos:
            card_x0 = CONTAINER_PADDING
            card_y0 = y
            card_x1 = CONTAINER_PADDING + content_width
            card_y1 = y + card["card_h"]

            draw.rounded_rectangle(
                [card_x0, card_y0, card_x1, card_y1],
                radius=CARD_RADIUS, fill=CARD_BG
            )

            # 状态圆点 + 服务器名
            dot_x = card_x0 + CARD_PADDING_SIDE
            dot_center_y = card_y0 + CARD_PADDING_TOP + FONT_SERVER_NAME.size // 2
            dot_y = dot_center_y - STATUS_DOT_SIZE // 2
            is_online = not card["error"]
            dot_color = STATUS_DOT_ONLINE if is_online else STATUS_DOT_OFFLINE
            dot_img = get_dot(STATUS_DOT_SIZE, dot_color)
            img.paste(dot_img, (dot_x, dot_y), dot_img)

            name_x = dot_x + STATUS_DOT_SIZE + 12
            name_y = card_y0 + CARD_PADDING_TOP

            # 统一用混合渲染函数处理服务器名（可能含 emoji）
            draw_text_with_emoji(
                img, draw, (name_x, name_y),
                card["name"],
                FONT_SERVER_NAME, SERVER_NAME_COLOR,
                emoji_scale=0.95
            )

            # 右侧信息区：上次在线（可选）→ 版本（可选）→ 延迟（可选）→ 在线人数
            right_items = []
            if show_last_online and card.get("last_activity_time", 0) > 0:
                last_online_text = f"上次在线 {format_last_online(card['last_activity_time'])}前"
                lo_w = draw.textbbox((0, 0), last_online_text, font=FONT_SERVER_VERSION)[2]
                right_items.append((last_online_text, FONT_SERVER_VERSION, SERVER_VERSION_COLOR, lo_w))
            if show_version:
                ver_text = card["version"]
                ver_w = draw.textbbox((0, 0), ver_text, font=FONT_SERVER_VERSION)[2]
                right_items.append((ver_text, FONT_SERVER_VERSION, SERVER_VERSION_COLOR, ver_w))
            if show_latency:
                lat_val = card.get("latency")
                if lat_val is None:
                    lat_text = "?ms"
                else:
                    lat_text = f"{lat_val:.0f}ms"
                lat_w = draw.textbbox((0, 0), lat_text, font=FONT_LATENCY)[2]
                right_items.append((lat_text, FONT_LATENCY, get_latency_color(lat_val), lat_w))
            cnt_text = f"{card['online_str']}/{card['max_str']} 人在线"
            cnt_w = draw.textbbox((0, 0), cnt_text, font=FONT_SERVER_COUNT)[2]
            right_items.append((cnt_text, FONT_SERVER_COUNT, SERVER_COUNT_COLOR, cnt_w))

            total_right_w = sum(w + 14 for _, _, _, w in right_items) - 14
            cursor_x = card_x1 - CARD_PADDING_SIDE - total_right_w
            for txt, font, color, w in right_items:
                draw.text((cursor_x, name_y), txt, fill=color, font=font)
                cursor_x += w + 14

            # 分割线
            line_y = name_y + FONT_SERVER_NAME.size + DIVIDER_MARGIN_TOP
            draw.line(
                [(card_x0 + CARD_PADDING_SIDE, line_y), (card_x1 - CARD_PADDING_SIDE, line_y)],
                fill=DIVIDER_COLOR, width=2
            )

            # 玩家网格区域
            grid_start_y = line_y + DIVIDER_MARGIN_BOTTOM
            if card["has_players"]:
                for i, player in enumerate(card["players"]):
                    if isinstance(player, dict):
                        player_name = player.get("name")
                        player_uuid = player.get("uuid")
                        is_premium = player.get("is_premium")
                    else:
                        player_name = player
                        player_uuid = None
                        is_premium = None

                    row = i // cols
                    col = i % cols
                    seat_x = card_x0 + CARD_PADDING_SIDE + col * (SEAT_WIDTH + GAP_H)
                    seat_y = grid_start_y + row * (AVATAR_SIZE + TEXT_NAME_SIZE + GAP_V)

                    # ★ 直接从 avatar_map 取（已并发下载好）
                    key = (player_name, player_uuid, AVATAR_SIZE)
                    avatar = avatar_map.get(key)
                    if avatar is None:
                        avatar = get_default_avatar(AVATAR_SIZE)

                    rounded = make_rounded(avatar, AVATAR_RADIUS)
                    img.paste(rounded, (int(seat_x), int(seat_y)), rounded)

                    name_tx = seat_x + AVATAR_SIZE + 10
                    name_ty = seat_y + (AVATAR_SIZE - TEXT_NAME_SIZE) // 2
                    draw.text((int(name_tx), int(name_ty)), player_name, fill=TEXT_NAME_COLOR, font=FONT_NAME)
            else:
                if card["online"] > 0:
                    hide_text = f"{card['online']} 人在线（玩家列表不可见）"
                    bbox = draw.textbbox((0, 0), hide_text, font=FONT_PLAYER_HIDDEN)
                    hw = bbox[2] - bbox[0]
                    draw.text((card_x0 + (content_width - hw) // 2, grid_start_y),
                              hide_text, fill=PLAYER_LIST_HIDDEN_COLOR, font=FONT_PLAYER_HIDDEN)
                else:
                    if is_online:
                        empty_str = "当前无人在线"
                    else:
                        err = card['error'] or ''
                        offline_keywords = ['积极拒绝', 'Connection refused', 'timeout', '超时', '连接超时', '连接失败']
                        if any(kw in err for kw in offline_keywords):
                            empty_str = "服务器不在线"
                        else:
                            empty_str = f"查询失败: {err}"

                    bbox = draw.textbbox((0, 0), empty_str, font=FONT_EMPTY)
                    ew = bbox[2] - bbox[0]
                    draw.text((card_x0 + (content_width - ew) // 2, grid_start_y),
                              empty_str, fill=EMPTY_TEXT_COLOR, font=FONT_EMPTY)

            y += card["card_h"] + CARD_MARGIN_BOTTOM

        # 底部时间
        time_bbox = draw.textbbox((0, 0), now_str, font=FONT_TIME)
        time_w = time_bbox[2] - time_bbox[0]
        time_x = (CONTAINER_WIDTH - time_w) // 2
        draw.text((time_x, y + TIME_MARGIN_TOP), now_str, fill=TIME_TEXT_COLOR, font=FONT_TIME)

        return img