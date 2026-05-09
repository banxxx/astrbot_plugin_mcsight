import asyncio
import os
import time
import aiohttp
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any
from ...utils.avatar_cache import download_avatar
from ...config.whitelist_config import WhitelistManager

# ========== 样式常量（与 HTML 保持一致）==========
CONTAINER_WIDTH = 760
CONTAINER_PADDING = 24
CONTAINER_BOTTOM_PADDING = 16
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 16

PAGE_TITLE_COLOR = '#5b6abf'
PAGE_TITLE_SIZE = 32
PAGE_TITLE_MARGIN_BOTTOM = 8

MAIN_TITLE_COLOR = '#555555'
MAIN_TITLE_SIZE = 18
MAIN_TITLE_MARGIN_BOTTOM = 20

CARD_BG = '#FFFFFF'
CARD_RADIUS = 12
CARD_PADDING_TOP = 16
CARD_PADDING_SIDE = 18
CARD_PADDING_BOTTOM = 24
CARD_MARGIN_BOTTOM = 18

STATUS_DOT_SIZE = 10
STATUS_DOT_ONLINE = '#5cb85c'
STATUS_DOT_OFFLINE = '#d9534f'

SERVER_NAME_COLOR = '#444444'
SERVER_NAME_SIZE = 18
SERVER_COUNT_COLOR = '#777777'
SERVER_COUNT_SIZE = 14

# 新增：版本和延迟样式
SERVER_VERSION_COLOR = '#888888'
SERVER_VERSION_SIZE = 13
LATENCY_GOOD_COLOR = '#7BA87F'      # ≤100ms 莫奈灰绿
LATENCY_MEDIUM_COLOR = '#D9A87C'    # 100~500ms 莫奈暖灰橙
LATENCY_BAD_COLOR = '#C47D7D'       # >500ms 莫奈灰红
LATENCY_SIZE = 13

DIVIDER_COLOR = '#e0e0e0'
DIVIDER_MARGIN_TOP = 10
DIVIDER_MARGIN_BOTTOM = 24

SEAT_WIDTH = 160
AVATAR_SIZE = 36
AVATAR_RADIUS = 6
GAP_H = 16
GAP_V = 10

TEXT_NAME_COLOR = '#555555'
TEXT_NAME_SIZE = 13

EMPTY_TEXT_COLOR = '#999999'
EMPTY_TEXT_SIZE = 14

TIME_TEXT_COLOR = '#aaaaaa'
TIME_TEXT_SIZE = 12
TIME_MARGIN_TOP = 16

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
FONT_TIME = _load_font(TIME_TEXT_SIZE)

def make_rounded(img: Image.Image, radius: int) -> Image.Image:
    """将 RGBA 图片切割为圆角"""
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)
    img = img.copy()
    img.putalpha(mask)
    return img

def get_latency_color(ms: float) -> str:
    """根据延迟值返回莫奈色系颜色"""
    if ms <= 100:
        return LATENCY_GOOD_COLOR
    elif ms <= 500:
        return LATENCY_MEDIUM_COLOR
    else:
        return LATENCY_BAD_COLOR

async def draw_multi_server_image(servers_data: List[Dict[str, Any]]) -> Image.Image:
    """
    servers_data: 由 checker 返回的查询结果列表，每个元素包含：
        name, host, online, max, players, version, latency, error
    """
    # 生成时间字符串
    now_str = time.strftime("%Y/%m/%d  %H:%M:%S")

    # 获取显示开关
    wm = WhitelistManager()
    show_version = wm.show_server_version
    show_latency = wm.show_server_latency

    # 内容区宽度
    content_width = CONTAINER_WIDTH - 2 * CONTAINER_PADDING
    inner_width = content_width - 2 * CARD_PADDING_SIDE
    # 每行可放的座位数（与原逻辑一致）
    cols = max(1, (inner_width + GAP_H) // (SEAT_WIDTH + GAP_H))
    used_cols = 0

    card_infos = []
    async with aiohttp.ClientSession() as session:
        for srv in servers_data:
            name = srv["name"]
            players = srv.get("players", [])
            online_str = str(srv.get("online", "?"))
            max_str = str(srv.get("max", "?"))
            version = srv.get("version", "未知")
            latency = srv.get("latency", 0.0)
            error = srv.get("error")

            if players:
                num_players = len(players)
                rows = (num_players + cols - 1) // cols
                actual_cols = min(cols, num_players)
            else:
                rows = 1
                actual_cols = 0

            used_cols = max(used_cols, actual_cols)

            grid_w = actual_cols * SEAT_WIDTH + (actual_cols - 1) * GAP_H if actual_cols > 0 else 0
            grid_h = rows * (AVATAR_SIZE + TEXT_NAME_SIZE + GAP_V) if players else (EMPTY_TEXT_SIZE + 10)

            # 标题行高度
            title_h = max(FONT_SERVER_NAME.size, FONT_SERVER_COUNT.size)
            if show_version:
                title_h = max(title_h, FONT_SERVER_VERSION.size)
            if show_latency:
                title_h = max(title_h, FONT_LATENCY.size)

            card_h = (CARD_PADDING_TOP +
                      title_h + 8 +
                      DIVIDER_MARGIN_TOP + 1 + DIVIDER_MARGIN_BOTTOM +
                      grid_h +
                      CARD_PADDING_BOTTOM)

            # 预下载头像
            if players:
                tasks = [download_avatar(session, p, AVATAR_SIZE) for p in players]
                avatar_imgs = await asyncio.gather(*tasks)
            else:
                avatar_imgs = []

            card_infos.append({
                "name": name,
                "online_str": online_str,
                "max_str": max_str,
                "players": players,
                "avatars": avatar_imgs,
                "rows": rows if players else 0,
                "grid_w": grid_w,
                "grid_h": grid_h,
                "card_h": card_h,
                "error": error,
                "actual_cols": actual_cols,
                "version": version,
                "latency": latency,
            })

    # 计算画布总高度
    total_height = CONTAINER_PADDING
    total_height += PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM
    total_height += MAIN_TITLE_SIZE + MAIN_TITLE_MARGIN_BOTTOM
    for card in card_infos:
        total_height += card["card_h"] + CARD_MARGIN_BOTTOM
    total_height -= CARD_MARGIN_BOTTOM
    total_height += TIME_TEXT_SIZE + TIME_MARGIN_TOP + CONTAINER_BOTTOM_PADDING

    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)

    # 绘制圆角容器背景
    draw.rounded_rectangle(
        [(0, 0), (CONTAINER_WIDTH, total_height)],
        radius=CONTAINER_RADIUS, fill=CONTAINER_BG
    )

    y = CONTAINER_PADDING

    # 1. 页面大标题
    title_text = "在线玩家列表"
    title_bbox = draw.textbbox((0, 0), title_text, font=FONT_PAGE_TITLE)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (CONTAINER_WIDTH - title_w) // 2
    draw.text((title_x, y), title_text, fill=PAGE_TITLE_COLOR, font=FONT_PAGE_TITLE)
    y += PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM

    # 2. 总在线人数
    total_online = sum(s["online"] for s in servers_data if not s.get("error"))
    main_text = f"总在线: {total_online} 人"
    main_bbox = draw.textbbox((0, 0), main_text, font=FONT_MAIN_TITLE)
    main_w = main_bbox[2] - main_bbox[0]
    main_x = (CONTAINER_WIDTH - main_w) // 2
    draw.text((main_x, y), main_text, fill=MAIN_TITLE_COLOR, font=FONT_MAIN_TITLE)
    y += MAIN_TITLE_SIZE + MAIN_TITLE_MARGIN_BOTTOM

    # 3. 逐个绘制服务器卡片
    for card in card_infos:
        card_x0 = CONTAINER_PADDING
        card_y0 = y
        card_x1 = CONTAINER_PADDING + content_width
        card_y1 = y + card["card_h"]

        # 卡片白色圆角背景
        draw.rounded_rectangle(
            [card_x0, card_y0, card_x1, card_y1],
            radius=CARD_RADIUS, fill=CARD_BG
        )

        # ---- 标题行 ----
        # 左边：状态圆点 + 服务器名 [+ 版本]
        dot_x = card_x0 + CARD_PADDING_SIDE
        dot_center_y = card_y0 + CARD_PADDING_TOP + FONT_SERVER_NAME.size // 2
        dot_y = dot_center_y - STATUS_DOT_SIZE // 2
        is_online = not card["error"]
        dot_color = STATUS_DOT_ONLINE if is_online else STATUS_DOT_OFFLINE
        draw.ellipse(
            [dot_x, dot_y, dot_x + STATUS_DOT_SIZE, dot_y + STATUS_DOT_SIZE],
            fill=dot_color
        )
        name_x = dot_x + STATUS_DOT_SIZE + 8
        name_y = card_y0 + CARD_PADDING_TOP
        draw.text((name_x, name_y), card["name"], fill=SERVER_NAME_COLOR, font=FONT_SERVER_NAME)

        # 服务器名称右侧的版本号（如果开关开启）
        next_x = name_x + draw.textbbox((0, 0), card["name"], font=FONT_SERVER_NAME)[2] + 4
        if show_version:
            version_text = card["version"]
            draw.text((next_x, name_y + 2), version_text, fill=SERVER_VERSION_COLOR, font=FONT_SERVER_VERSION)
            next_x += draw.textbbox((0, 0), version_text, font=FONT_SERVER_VERSION)[2] + 8

        # 右边区域：在线人数 [间隔] 延迟
        # 先计算右侧组件的总宽度（以便右对齐）
        right_elements = []
        count_text = f"{card['online_str']}/{card['max_str']} 人在线"
        count_w = draw.textbbox((0, 0), count_text, font=FONT_SERVER_COUNT)[2]
        right_elements.append((count_text, FONT_SERVER_COUNT, SERVER_COUNT_COLOR, count_w))

        latency_text = ""
        latency_w = 0
        if show_latency:
            latency = card["latency"]
            latency_text = f"{latency:.0f}ms" if latency else "?ms"
            latency_w = draw.textbbox((0, 0), latency_text, font=FONT_LATENCY)[2]
            # 颜色稍后决定（根据值变化）
            right_elements.append((latency_text, FONT_LATENCY, get_latency_color(latency), latency_w))

        total_right_w = 0
        for i, (txt, font, color, w) in enumerate(right_elements):
            if i > 0:
                total_right_w += 10  # 间距
            total_right_w += w

        # 从右边界开始向左绘制
        cursor_x = card_x1 - CARD_PADDING_SIDE - total_right_w
        right_y = name_y  # 对齐基线
        for txt, font, color, w in right_elements:
            draw.text((cursor_x, right_y), txt, fill=color, font=font)
            cursor_x += w + 10

        # ---- 分割线 ----
        line_y = name_y + FONT_SERVER_NAME.size + DIVIDER_MARGIN_TOP
        draw.line(
            [(card_x0 + CARD_PADDING_SIDE, line_y), (card_x1 - CARD_PADDING_SIDE, line_y)],
            fill=DIVIDER_COLOR, width=1
        )

        # ---- 玩家网格或无人在线 ----
        grid_start_y = line_y + DIVIDER_MARGIN_BOTTOM
        if card["players"]:
            for i, player in enumerate(card["players"]):
                row = i // cols
                col = i % cols
                seat_x = card_x0 + CARD_PADDING_SIDE + col * (SEAT_WIDTH + GAP_H)
                seat_y = grid_start_y + row * (AVATAR_SIZE + TEXT_NAME_SIZE + GAP_V)

                rounded = make_rounded(card["avatars"][i], AVATAR_RADIUS)
                img.paste(rounded, (int(seat_x), int(seat_y)), rounded)

                name_text_x = seat_x + AVATAR_SIZE + 6
                name_text_y = seat_y + (AVATAR_SIZE - TEXT_NAME_SIZE) // 2
                draw.text((int(name_text_x), int(name_text_y)), player, fill=TEXT_NAME_COLOR, font=FONT_NAME)
        else:
            empty_str = "当前无人在线" if is_online else f"查询失败: {card['error']}"
            empty_bbox = draw.textbbox((0, 0), empty_str, font=FONT_EMPTY)
            empty_w = empty_bbox[2] - empty_bbox[0]
            empty_x = card_x0 + (card_x1 - card_x0 - empty_w) // 2
            draw.text((empty_x, grid_start_y), empty_str, fill=EMPTY_TEXT_COLOR, font=FONT_EMPTY)

        y += card["card_h"] + CARD_MARGIN_BOTTOM

    # 4. 底部生成时间
    time_bbox = draw.textbbox((0, 0), now_str, font=FONT_TIME)
    time_w = time_bbox[2] - time_bbox[0]
    time_x = (CONTAINER_WIDTH - time_w) // 2
    draw.text((time_x, y + TIME_MARGIN_TOP), now_str, fill=TIME_TEXT_COLOR, font=FONT_TIME)

    return img