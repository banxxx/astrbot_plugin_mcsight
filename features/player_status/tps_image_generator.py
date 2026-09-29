"""服务器 TPS 图片生成器（/tps、/mc tps）

复用 help_image 引擎：延迟 ops 布局 + 2x 超采样渲染。
行结构：状态圆点 + 服务器名 + TPS/20 占比条 + 语义色数字胶囊。
"""

import time
from typing import List, Dict, Any, Optional

from PIL import Image

from ..help_image.image_generator import (
    WIDTH, PAD,
    new_ops, add_text, add_rect, add_ellipse,
    text_w, build_image,
)

# ========== 布局常量（设计单位） ==========
TITLE_TEXT = "服务器 TPS 状态"
TITLE_SIZE = 36
TITLE_GAP = 24

CARD_PAD_X = 32
CARD_PAD_V = 28
CARD_RADIUS = 16
CARD_BG = '#FFFFFF'

ROW_H = 56
DIV_GAP = 7
DIV_H = 2
DIVIDER = '#efefec'

DOT_SIZE = 14
NAME_SIZE = 22
NAME_COLOR = '#444444'
NAME_GAP = 14

METER_W = 220
METER_H = 8
METER_BG = '#f0f0ee'
METER_GAP = 20

CHIP_H = 38
CHIP_MIN_W = 132
CHIP_SIZE = 18
CHIP_PAD_X = 16

TIME_SIZE = 16
TIME_COLOR = '#aaaaaa'
TIME_GAP = 24

# (文字色, 底色, 描边)
TPS_GOOD = ('#3f9d44', '#edf7ed', '#d8ecd8')
TPS_MID = ('#b97f45', '#fbf3ea', '#f0dfc8')
TPS_BAD = ('#c74440', '#fbecec', '#f2d4d3')
TPS_UNK = ('#999999', '#f2f2f0', '#e6e6e3')

DOT_GOOD = '#5cb85c'
DOT_MID = '#D9A87C'
DOT_BAD = '#d9534f'
DOT_UNK = '#bbbbbb'


def _tps_level(tps: Optional[float]):
    if tps is None:
        return None
    if tps >= 19.0:
        return 'good'
    if tps >= 15.0:
        return 'mid'
    return 'bad'


def _tps_chip_colors(tps):
    lvl = _tps_level(tps)
    return {'good': TPS_GOOD, 'mid': TPS_MID, 'bad': TPS_BAD,
            None: TPS_UNK}[lvl]


def _tps_dot_color(tps):
    lvl = _tps_level(tps)
    return {'good': DOT_GOOD, 'mid': DOT_MID, 'bad': DOT_BAD,
            None: DOT_UNK}[lvl]


def _tps_label(tps):
    return "无法获取" if tps is None else f"{tps:.1f} TPS"


def _truncate(draw, name, size, max_w):
    if not name:
        return ""
    if text_w(draw, name, size) <= max_w:
        return name
    ew = text_w(draw, '…', size)
    acc = ''
    for ch in name:
        if text_w(draw, acc + ch, size) + ew > max_w:
            break
        acc += ch
    return acc + '…'


# ========== 单行 ==========
def _draw_row(draw, ops, px0, px1, row, top):
    tps = row.get('tps')
    fg, bg, bd = _tps_chip_colors(tps)

    # 胶囊（右对齐）
    label = _tps_label(tps)
    lw = text_w(draw, label, CHIP_SIZE)
    chip_w = max(CHIP_MIN_W, lw + CHIP_PAD_X * 2)
    chip_x = px1 - chip_w
    chip_y = top + (ROW_H - CHIP_H) // 2
    add_rect(ops, 'bg1', chip_x, chip_y, chip_w, CHIP_H, CHIP_H // 2, bg, bd)
    add_text(ops, chip_x + (chip_w - lw) / 2, top + (ROW_H - CHIP_SIZE) / 2,
             label, CHIP_SIZE, fg)

    # 占比条
    meter_x = chip_x - METER_GAP - METER_W
    meter_y = top + (ROW_H - METER_H) // 2
    add_rect(ops, 'bg1', meter_x, meter_y, METER_W, METER_H, METER_H // 2,
             METER_BG)
    if tps is not None:
        ratio = max(0.0, min(1.0, tps / 20.0))
        fill_w = max(METER_H, METER_W * ratio)
        add_rect(ops, 'bg1', meter_x, meter_y, fill_w, METER_H, METER_H // 2,
                 fg)

    # 状态圆点
    add_ellipse(ops, 'bg1', px0, top + (ROW_H - DOT_SIZE) // 2,
                DOT_SIZE, _tps_dot_color(tps))

    # 名称
    name_x = px0 + DOT_SIZE + NAME_GAP
    name = _truncate(draw, row.get('name', '未知'), NAME_SIZE,
                     meter_x - METER_GAP - name_x)
    add_text(ops, name_x, top + (ROW_H - NAME_SIZE) / 2, name,
             NAME_SIZE, NAME_COLOR)


# ========== 整体布局 ==========
def _build_tps(draw, ops, doc, y):
    tw = text_w(draw, TITLE_TEXT, TITLE_SIZE)
    add_text(ops, (WIDTH - tw) / 2, y, TITLE_TEXT, TITLE_SIZE,
             '#5b6abf')
    y += TITLE_SIZE + TITLE_GAP

    x0, x1 = PAD, WIDTH - PAD
    px0, px1 = x0 + CARD_PAD_X, x1 - CARD_PAD_X
    inner_w = px1 - px0

    rows = doc['rows']
    content_h = (len(rows) * ROW_H
                 + max(0, len(rows) - 1) * (DIV_GAP * 2 + DIV_H))
    card_h = CARD_PAD_V * 2 + content_h
    add_rect(ops, 'bg0', x0, y, x1 - x0, card_h, CARD_RADIUS, CARD_BG)

    yy = y + CARD_PAD_V
    for idx, row in enumerate(rows):
        _draw_row(draw, ops, px0, px1, row, yy)
        yy += ROW_H
        if idx < len(rows) - 1:
            yy += DIV_GAP
            add_rect(ops, 'bg1', px0, yy, inner_w, DIV_H, 1, DIVIDER)
            yy += DIV_GAP + DIV_H

    y = y + card_h + TIME_GAP
    nw = text_w(draw, doc['now_str'], TIME_SIZE)
    add_text(ops, (WIDTH - nw) / 2, y, doc['now_str'], TIME_SIZE, TIME_COLOR)
    return y + TIME_SIZE


def draw_tps_image(servers: List[Dict[str, Any]]) -> Image.Image:
    doc = {
        'rows': [{'name': s.get('name', '未知'), 'tps': s.get('tps')}
                 for s in servers],
        'now_str': time.strftime("%Y/%m/%d  %H:%M:%S"),
    }
    return build_image(_build_tps, doc)
