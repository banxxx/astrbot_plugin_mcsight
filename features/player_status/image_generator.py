# features/player_status/image_generator.py

import asyncio
import os
import time
import aiohttp
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any
from ...utils.avatar_cache import download_avatar
from ...config.whitelist_config import WhitelistManager
from ...utils.avatar_cache import download_avatar

# ========== 样式常量（高清优化版）==========
CONTAINER_WIDTH = 1080          # 提升画布宽度，增加清晰度
CONTAINER_PADDING = 36          # 外框内边距适当加大
CONTAINER_BOTTOM_PADDING = 52   # 日期到底边多留白
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 24           # 圆角也略微加大

PAGE_TITLE_COLOR = '#5b6abf'
PAGE_TITLE_SIZE = 40            # 标题字号放大
PAGE_TITLE_MARGIN_BOTTOM = 12

MAIN_TITLE_COLOR = '#555555'
MAIN_TITLE_SIZE = 24            # 副标题也加大
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
SERVER_VERSION_SIZE = 16
LATENCY_GOOD_COLOR = '#7BA87F'
LATENCY_MEDIUM_COLOR = '#D9A87C'
LATENCY_BAD_COLOR = '#C47D7D'
LATENCY_SIZE = 16

DIVIDER_COLOR = '#e0e0e0'
DIVIDER_MARGIN_TOP = 14
DIVIDER_MARGIN_BOTTOM = 28

SEAT_WIDTH = 220               # 每个座位宽度增大，容纳更大头像
AVATAR_SIZE = 48               # 头像尺寸增大
AVATAR_RADIUS = 8
GAP_H = 20                     # 列间距
GAP_V = 14                     # 行间距

TEXT_NAME_COLOR = '#555555'
TEXT_NAME_SIZE = 16

EMPTY_TEXT_COLOR = '#999999'
EMPTY_TEXT_SIZE = 18
# 新增：隐藏列表时的文字样式
PLAYER_LIST_HIDDEN_COLOR = '#aaaaaa'
PLAYER_LIST_HIDDEN_SIZE = 18

TIME_TEXT_COLOR = '#aaaaaa'
TIME_TEXT_SIZE = 16
TIME_MARGIN_TOP = 32

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

def get_latency_color(ms: float) -> str:
    if ms <= 100:
        return LATENCY_GOOD_COLOR
    elif ms <= 500:
        return LATENCY_MEDIUM_COLOR
    else:
        return LATENCY_BAD_COLOR

async def draw_multi_server_image(servers_data: List[Dict[str, Any]]) -> Image.Image:
    now_str = time.strftime("%Y/%m/%d  %H:%M:%S")
    wm = WhitelistManager()
    show_version = wm.show_server_version
    show_latency = wm.show_server_latency

    content_width = CONTAINER_WIDTH - 2 * CONTAINER_PADDING
    inner_width = content_width - 2 * CARD_PADDING_SIDE
    cols = max(1, (inner_width + GAP_H) // (SEAT_WIDTH + GAP_H))

    card_infos = []
    async with aiohttp.ClientSession() as session:
        for srv in servers_data:
            name = srv["name"]
            players = srv.get("players", [])
            online = srv.get("online", 0)
            max_players = srv.get("max", 1)
            online_str = str(online)
            max_str = str(max_players)
            version = srv.get("version", "未知")
            latency = srv.get("latency", 0.0)
            error = srv.get("error")

            # 关键修复：若 online > 0 但 players 为空，表示服务器隐藏玩家列表
            has_players = len(players) > 0
            # 计算网格高度时，若无玩家则用空行高度
            if has_players:
                rows = (len(players) + cols - 1) // cols
                grid_h = rows * (AVATAR_SIZE + TEXT_NAME_SIZE + GAP_V)
            else:
                rows = 0
                # 隐藏列表时的提示文本高度
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
            })

    # 计算总高度
    total_height = CONTAINER_PADDING + PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM
    total_height += MAIN_TITLE_SIZE + MAIN_TITLE_MARGIN_BOTTOM
    for c in card_infos:
        total_height += c["card_h"] + CARD_MARGIN_BOTTOM
    total_height -= CARD_MARGIN_BOTTOM
    total_height += TIME_TEXT_SIZE + TIME_MARGIN_TOP + CONTAINER_BOTTOM_PADDING

    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)

    # 容器圆角背景
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

    # 服务器卡片
    for card in card_infos:
        card_x0 = CONTAINER_PADDING
        card_y0 = y
        card_x1 = CONTAINER_PADDING + content_width
        card_y1 = y + card["card_h"]

        draw.rounded_rectangle(
            [card_x0, card_y0, card_x1, card_y1],
            radius=CARD_RADIUS, fill=CARD_BG
        )

        # 状态圆点 + 服务器名 + 版本
        dot_x = card_x0 + CARD_PADDING_SIDE
        dot_center_y = card_y0 + CARD_PADDING_TOP + FONT_SERVER_NAME.size // 2
        dot_y = dot_center_y - STATUS_DOT_SIZE // 2
        is_online = not card["error"]
        dot_color = STATUS_DOT_ONLINE if is_online else STATUS_DOT_OFFLINE
        draw.ellipse(
            [dot_x, dot_y, dot_x + STATUS_DOT_SIZE, dot_y + STATUS_DOT_SIZE],
            fill=dot_color
        )
        name_x = dot_x + STATUS_DOT_SIZE + 12
        name_y = card_y0 + CARD_PADDING_TOP
        draw.text((name_x, name_y), card["name"], fill=SERVER_NAME_COLOR, font=FONT_SERVER_NAME)

        next_x = name_x + draw.textbbox((0,0), card["name"], font=FONT_SERVER_NAME)[2] + 8
        if show_version:
            ver_text = card["version"]
            draw.text((next_x, name_y + 4), ver_text, fill=SERVER_VERSION_COLOR, font=FONT_SERVER_VERSION)
            next_x += draw.textbbox((0,0), ver_text, font=FONT_SERVER_VERSION)[2] + 12

        # 右侧在线人数 + 延迟
        right_items = []
        cnt_text = f"{card['online_str']}/{card['max_str']} 人在线"
        cnt_w = draw.textbbox((0,0), cnt_text, font=FONT_SERVER_COUNT)[2]
        right_items.append((cnt_text, FONT_SERVER_COUNT, SERVER_COUNT_COLOR, cnt_w))
        if show_latency:
            lat_val = card["latency"]
            lat_text = f"{lat_val:.0f}ms" if lat_val else "?ms"
            lat_w = draw.textbbox((0,0), lat_text, font=FONT_LATENCY)[2]
            right_items.append((lat_text, FONT_LATENCY, get_latency_color(lat_val), lat_w))

        total_right_w = sum(w + 14 for _,_,_,w in right_items) - 14
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
                # player 可以是字符串（旧格式）或字典（新格式）
                if isinstance(player, dict):
                    player_name = player.get("name")
                    is_premium = player.get("is_premium")
                else:
                    player_name = player
                    is_premium = None  # 未知，视为 True

                row = i // cols
                col = i % cols
                seat_x = card_x0 + CARD_PADDING_SIDE + col * (SEAT_WIDTH + GAP_H)
                seat_y = grid_start_y + row * (AVATAR_SIZE + TEXT_NAME_SIZE + GAP_V)

                # 下载头像，传入 is_premium
                avatar = await download_avatar(session, player_name, AVATAR_SIZE, is_premium)
                rounded = make_rounded(avatar, AVATAR_RADIUS)
                img.paste(rounded, (int(seat_x), int(seat_y)), rounded)

                name_tx = seat_x + AVATAR_SIZE + 10
                name_ty = seat_y + (AVATAR_SIZE - TEXT_NAME_SIZE) // 2
                draw.text((int(name_tx), int(name_ty)), player_name, fill=TEXT_NAME_COLOR, font=FONT_NAME)
        else:
            # 无玩家列表时区分两种状态
            if card["online"] > 0:
                hide_text = f"{card['online']} 人在线（玩家列表不可见）"
                bbox = draw.textbbox((0,0), hide_text, font=FONT_PLAYER_HIDDEN)
                hw = bbox[2] - bbox[0]
                draw.text((card_x0 + (content_width - hw) // 2, grid_start_y),
                          hide_text, fill=PLAYER_LIST_HIDDEN_COLOR, font=FONT_PLAYER_HIDDEN)
            else:
                empty_str = "当前无人在线" if is_online else f"查询失败: {card['error']}"
                bbox = draw.textbbox((0,0), empty_str, font=FONT_EMPTY)
                ew = bbox[2] - bbox[0]
                draw.text((card_x0 + (content_width - ew) // 2, grid_start_y),
                          empty_str, fill=EMPTY_TEXT_COLOR, font=FONT_EMPTY)

        y += card["card_h"] + CARD_MARGIN_BOTTOM

    # 底部时间
    time_bbox = draw.textbbox((0,0), now_str, font=FONT_TIME)
    time_w = time_bbox[2] - time_bbox[0]
    time_x = (CONTAINER_WIDTH - time_w) // 2
    draw.text((time_x, y + TIME_MARGIN_TOP), now_str, fill=TIME_TEXT_COLOR, font=FONT_TIME)

    return img