# features/player_status/tps_image_generator.py

import os
import time
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any, Optional

# ========== 样式常量 ==========
CONTAINER_WIDTH = 1080
CONTAINER_PADDING = 36
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 24

PAGE_TITLE_COLOR = '#5b6abf'
PAGE_TITLE_SIZE = 36
PAGE_TITLE_MARGIN_BOTTOM = 12

CARD_BG = '#FFFFFF'
CARD_RADIUS = 16
CARD_PADDING_SIDE = 32

# ★★★ 上下内边距 ★★★
CARD_PADDING_VERTICAL = 28    # 顶部和底部内边距相同

SERVER_NAME_COLOR = '#444444'
SERVER_NAME_SIZE = 22
SERVER_TPS_SIZE = 22
SERVER_TPS_BG = '#f0f0ee'
SERVER_TPS_BORDER_RADIUS = 20

TPS_GOOD_COLOR = '#5cb85c'
TPS_MEDIUM_COLOR = '#D9A87C'
TPS_BAD_COLOR = '#d9534f'
TPS_UNKNOWN_COLOR = '#999999'

DIVIDER_COLOR = '#e8e8e6'

TIME_TEXT_COLOR = '#aaaaaa'
TIME_TEXT_SIZE = 16
TIME_MARGIN_TOP = 24

# TPS 背景框固定宽度和高度（像素）
TPS_BOX_WIDTH = 160
TPS_BOX_HEIGHT = 36

# 行布局参数
ROW_HEIGHT = 40          # 每行数据的高度（名称、TPS框）
GAP_HEIGHT = 28          # 行与行之间的间隙高度（分割线位于中间）

# ========== 字体加载 ==========
def _load_font(size, bold=False):
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

_font_cache = {}
def get_font(size, bold=False):
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = _load_font(size, bold)
    return _font_cache[key]

# ========== 颜色辅助 ==========
def get_tps_color(tps: Optional[float]) -> str:
    if tps is None:
        return TPS_UNKNOWN_COLOR
    if tps >= 19.0:
        return TPS_GOOD_COLOR
    elif tps >= 15.0:
        return TPS_MEDIUM_COLOR
    else:
        return TPS_BAD_COLOR

def get_tps_label(tps: Optional[float]) -> str:
    if tps is None:
        return "无法获取"
    return f"{tps:.1f} TPS"

# ========== 主绘图函数 ==========
def draw_tps_image(servers: List[Dict[str, Any]]) -> Image.Image:
    now_str = time.strftime("%Y/%m/%d  %H:%M:%S")

    row_count = len(servers)
    # 总内容高度 = 每行数据高度之和 + 行间间隙之和
    total_content_height = row_count * ROW_HEIGHT + (row_count - 1) * GAP_HEIGHT

    # 卡片内容高度 = 上下内边距 + 总内容高度
    card_content_height = CARD_PADDING_VERTICAL * 2 + total_content_height

    total_height = (
        CONTAINER_PADDING
        + PAGE_TITLE_SIZE
        + PAGE_TITLE_MARGIN_BOTTOM
        + card_content_height
        + TIME_MARGIN_TOP
        + TIME_TEXT_SIZE
        + CONTAINER_PADDING
    )

    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [(0, 0), (CONTAINER_WIDTH, total_height)],
        radius=CONTAINER_RADIUS,
        fill=CONTAINER_BG
    )

    y = CONTAINER_PADDING

    # ---- 标题 ----
    title_text = "服务器 TPS 状态"
    title_font = get_font(PAGE_TITLE_SIZE, bold=True)
    title_w = draw.textbbox((0, 0), title_text, font=title_font)[2]
    title_x = (CONTAINER_WIDTH - title_w) // 2
    draw.text((title_x, y), title_text, fill=PAGE_TITLE_COLOR, font=title_font)
    y += PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM

    # ---- 卡片背景 ----
    card_x0 = CONTAINER_PADDING
    card_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
    card_y0 = y
    card_y1 = y + card_content_height
    draw.rounded_rectangle(
        (card_x0, card_y0, card_x1, card_y1),
        radius=CARD_RADIUS,
        fill=CARD_BG
    )

    # ---- 绘制每一行 ----
    name_font = get_font(SERVER_NAME_SIZE, bold=False)
    tps_font = get_font(SERVER_TPS_SIZE, bold=True)

    y_pos = card_y0 + CARD_PADDING_VERTICAL

    for idx, srv in enumerate(servers):
        name = srv.get("name", "未知")
        tps = srv.get("tps")

        # 名称垂直居中于 ROW_HEIGHT 内
        name_bbox = draw.textbbox((0, 0), name, font=name_font)
        name_height = name_bbox[3] - name_bbox[1]
        name_y = y_pos + (ROW_HEIGHT - name_height) // 2
        draw.text(
            (card_x0 + CARD_PADDING_SIDE, name_y),
            name,
            fill=SERVER_NAME_COLOR,
            font=name_font
        )

        # ---- TPS 值（固定背景框） ----
        tps_text = get_tps_label(tps)
        tps_color = get_tps_color(tps)

        box_x = card_x1 - CARD_PADDING_SIDE - TPS_BOX_WIDTH
        box_y = y_pos + (ROW_HEIGHT - TPS_BOX_HEIGHT) // 2

        draw.rounded_rectangle(
            (box_x, box_y, box_x + TPS_BOX_WIDTH, box_y + TPS_BOX_HEIGHT),
            radius=SERVER_TPS_BORDER_RADIUS,
            fill=SERVER_TPS_BG
        )

        tps_bbox = draw.textbbox((0, 0), tps_text, font=tps_font)
        tps_w = tps_bbox[2] - tps_bbox[0]
        tps_h = tps_bbox[3] - tps_bbox[1]
        text_x = box_x + (TPS_BOX_WIDTH - tps_w) // 2
        text_y = box_y + (TPS_BOX_HEIGHT - tps_h) // 2 - 1

        draw.text(
            (text_x, text_y),
            tps_text,
            fill=tps_color,
            font=tps_font
        )

        # ---- 分割线（仅在行与行之间） ----
        if idx < len(servers) - 1:
            # 间隙起始于 y_pos + ROW_HEIGHT
            gap_y = y_pos + ROW_HEIGHT
            line_y = gap_y + GAP_HEIGHT // 2
            draw.line(
                [(card_x0 + CARD_PADDING_SIDE, line_y), (card_x1 - CARD_PADDING_SIDE, line_y)],
                fill=DIVIDER_COLOR,
                width=1
            )

        # 移动到下一行（加上数据高度和间隙）
        y_pos += ROW_HEIGHT + GAP_HEIGHT

    # ---- 底部时间 ----
    time_font = get_font(TIME_TEXT_SIZE, bold=False)
    time_w = draw.textbbox((0, 0), now_str, font=time_font)[2]
    time_x = (CONTAINER_WIDTH - time_w) // 2
    time_y = card_y1 + TIME_MARGIN_TOP
    draw.text((time_x, time_y), now_str, fill=TIME_TEXT_COLOR, font=time_font)

    return img