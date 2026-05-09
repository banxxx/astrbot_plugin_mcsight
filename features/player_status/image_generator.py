import asyncio
import os
import aiohttp
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any
from ...utils.avatar_cache import download_avatar

# ========== 样式常量 ==========
BG_COLOR = '#F9F9F8'
CARD_COLOR = '#FFFFFF'
TITLE_COLOR = '#444444'
COUNT_COLOR = '#777777'
DIVIDER_COLOR = '#E0E0E0'
TEXT_COLOR = '#555555'
EMPTY_COLOR = '#999999'

AVATAR_SIZE = 36
AVATAR_RADIUS = 6
SEAT_WIDTH = 160          # 每个座位宽度（头像+名字）
COLS = 4                  # 每行玩家数
PADDING = 24
GAP_H = 16
GAP_V = 10
CARD_PADDING = 18

FONT_TITLE_SIZE = 18
FONT_COUNT_SIZE = 14
FONT_NAME_SIZE = 13
FONT_EMPTY_SIZE = 14

# ========== 字体加载 ==========
def _load_font(size):
    # 优先使用资源目录下的字体，其次系统字体
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

FONT_TITLE = _load_font(FONT_TITLE_SIZE)
FONT_COUNT = _load_font(FONT_COUNT_SIZE)
FONT_NAME = _load_font(FONT_NAME_SIZE)
FONT_EMPTY = _load_font(FONT_EMPTY_SIZE)

def make_rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new('L', img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)
    img = img.copy()
    img.putalpha(mask)
    return img

async def draw_multi_server_image(servers_data: List[Dict[str, Any]]) -> Image.Image:
    # ---- 计算尺寸并预下载头像 ----
    max_card_w = 0
    cards = []

    async with aiohttp.ClientSession() as session:
        for srv in servers_data:
            name = srv["name"]
            players = srv.get("players", [])
            online_str = str(srv.get("online", "?"))
            max_str = str(srv.get("max", "?"))

            if players:
                rows = (len(players) + COLS - 1) // COLS
            else:
                rows = 1
            grid_w = COLS * SEAT_WIDTH + (COLS - 1) * GAP_H
            grid_h = rows * (AVATAR_SIZE + FONT_NAME_SIZE + GAP_V) if players else 30

            card_w = grid_w + 2 * CARD_PADDING
            card_h = 60 + 1 + 14 + grid_h + 16
            if card_w > max_card_w:
                max_card_w = card_w

            # 并行下载头像
            if players:
                tasks = [download_avatar(session, p) for p in players]
                avatars = await asyncio.gather(*tasks)
            else:
                avatars = []

            cards.append({
                "name": name, "online_str": online_str, "max_str": max_str,
                "players": players, "avatars": avatars,
                "grid_w": grid_w, "grid_h": grid_h, "rows": rows if players else 0,
                "card_w": card_w, "card_h": card_h,
                "error": srv.get("error")
            })

    # ---- 创建画布 ----
    total_w = PADDING * 2 + max_card_w
    total_h = PADDING
    for c in cards:
        total_h += c["card_h"] + 18
    total_h += PADDING - 18

    img = Image.new('RGB', (total_w, total_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING
    for card in cards:
        x0, y0 = PADDING, y
        x1, y1 = PADDING + max_card_w, y + card["card_h"]
        draw.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=CARD_COLOR)

        # 标题行
        draw.text((x0 + CARD_PADDING, y0 + 16), card["name"], fill=TITLE_COLOR, font=FONT_TITLE)
        count_text = f"{card['online_str']}/{card['max_str']} 人在线"
        count_w = draw.textbbox((0,0), count_text, font=FONT_COUNT)[2]
        draw.text((x1 - CARD_PADDING - count_w, y0 + 20), count_text, fill=COUNT_COLOR, font=FONT_COUNT)

        # 分割线
        line_y = y0 + 52
        draw.line([(x0 + CARD_PADDING, line_y), (x1 - CARD_PADDING, line_y)], fill=DIVIDER_COLOR)

        # 玩家网格
        grid_y = line_y + 14
        if card["players"]:
            for i, player in enumerate(card["players"]):
                row, col = divmod(i, COLS)
                cx = x0 + CARD_PADDING + col * (SEAT_WIDTH + GAP_H)
                cy = grid_y + row * (AVATAR_SIZE + FONT_NAME_SIZE + GAP_V)
                avatar = make_rounded(card["avatars"][i], AVATAR_RADIUS)
                img.paste(avatar, (cx, cy), avatar)
                name_x = cx + AVATAR_SIZE + 6
                name_bbox = draw.textbbox((0,0), player, font=FONT_NAME)
                name_y = cy + (AVATAR_SIZE - (name_bbox[3] - name_bbox[1])) // 2
                draw.text((name_x, name_y), player, fill=TEXT_COLOR, font=FONT_NAME)
        else:
            txt = "当前无人在线" if not card.get("error") else f"查询失败: {card['error']}"
            draw.text((x0 + CARD_PADDING, grid_y), txt, fill=EMPTY_COLOR, font=FONT_EMPTY)

        y += card["card_h"] + 18

    return img