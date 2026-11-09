"""排行榜图片生成器（/排行、/mc rank）

复用 help_image 的延迟 ops 引擎 + 2x 超采样；专有 op：'paste'（头像）、'vdash'（双列分隔虚线）。
一张图只放一台服务器；≤20 条单列，21–50 条双列（列优先）。
"""

import asyncio
from typing import Any, Dict

import aiohttp
from PIL import Image, ImageDraw

from ...utils.avatar_cache import download_avatar, get_default_avatar
from ...utils.text_renderer import draw_text_with_emoji, measure_text_with_emoji
from ..help_image.image_generator import (
    WIDTH, PAD, SCALE, ACCENT, DIM, TEXT, MUTED, DASH,
    add_text, add_rect, add_ellipse, add_dash, add_shadow, text_w, _font, build_image,
)

# ========== 版式常量（设计单位） ==========
TITLE_SIZE = 40
TITLE_GAP = 14
SUB_SIZE = 22
SUB_COLOR = '#555555'
CARD_TOP_GAP = 20
CARD_RADIUS = 16
CARD_PAD_X = 28
CARD_PAD_TOP = 22
CARD_PAD_BOTTOM = 24
CARD_BG = '#FFFFFF'
HEAD_H = 34
HEAD_NAME_SIZE = 24
CHIP_SIZE = 15
CHIP_H = 26
CHIP_PAD_X = 14
CHIP_FG, CHIP_BG, CHIP_BD = '#8a7bc0', '#f4f2fb', '#e6e0f5'
DASH_GAP = 16

SELF_H = 52
SELF_RADIUS = 12
SELF_BG = '#f4f2fb'
SELF_LABEL_SIZE = 15
SELF_NAME_SIZE = 24
SELF_VAL_SIZE = 22

COL_HEAD_SIZE = 14
COL_HEAD_H = 26
COL_GAP = 12
TWO_DIV_GAP = 28
AVATAR = 44
AVATAR_COMPACT = 40
AVATAR_R = 8
RANK_W = 48
RANK_W_COMPACT = 44
RANK_D = 34
RANK_D_COMPACT = 30
RANK_SIZE = 17
RANK_DIM_SIZE = 24
NAME_SIZE = 24
NAME_COMPACT = 22
VALUE_SIZE = 24
VALUE_COMPACT = 22
ROW_SINGLE_H = 56
ROW_COMPACT_H = 50

ERR_H = 84
EMPTY_H = 96
HINT_SIZE = 15
HINT_COLOR = '#a5aabb'
TIME_SIZE = 16
TIME_COLOR = '#aaaaaa'

AVATAR_CONCURRENCY = 10


def _text_w(draw, text: str, size: int) -> float:
    """服务器名/副标题可能含 emoji，宽度要按混排口径量。"""
    return measure_text_with_emoji(draw, text, _font(size), emoji_scale=0.95)


def _add_text_mixed(ops, x, y, text, size, color):
    ops['fg'].append(('emoji', x, y, text, size, color))


def _render_emoji(op, img):
    _, x, y, text, size, color = op
    draw = ImageDraw.Draw(img)
    draw_text_with_emoji(img, draw, (x * SCALE, y * SCALE), text,
                         _font(size * SCALE), color, emoji_scale=0.95)


def _truncate_mixed(draw, text: str, size: int, max_w: float) -> str:
    if _text_w(draw, text, size) <= max_w:
        return text
    text = text.rstrip()
    while text and _text_w(draw, text + '…', size) > max_w:
        text = text[:-1]
    return (text + '…') if text else '…'


def _truncate(draw, text: str, size: int, max_w: float) -> str:
    if text_w(draw, text, size) <= max_w:
        return text
    text = text.rstrip()
    while text and text_w(draw, text + '…', size) > max_w:
        text = text[:-1]
    return (text + '…') if text else '…'


def _make_rounded(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert('RGBA')
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def _paste(op, img):
    _, x, y, ava = op
    img.paste(ava, (int(x * SCALE), int(y * SCALE)), ava)


def _vdash(op, img):
    _, x, y0, y1 = op
    draw = ImageDraw.Draw(img)
    xx = int(x * SCALE)
    yy = int(y0 * SCALE)
    end = int(y1 * SCALE)
    dash = int(4 * SCALE)
    step = int(8 * SCALE)
    while yy < end:
        draw.line([(xx, yy), (xx, min(yy + dash, end))], fill=DASH,
                  width=max(1, int(SCALE / 2)))
        yy += step


def _card_height(doc: Dict[str, Any]) -> int:
    h = CARD_PAD_TOP + HEAD_H + 1 + DASH_GAP
    if doc.get('error'):
        return h + ERR_H + CARD_PAD_BOTTOM
    if doc.get('empty'):
        return h + EMPTY_H + CARD_PAD_BOTTOM
    if doc.get('self'):
        h += SELF_H + 1 + DASH_GAP
    rows = doc.get('rows') or []
    compact = doc.get('compact')
    row_h = ROW_COMPACT_H if compact else ROW_SINGLE_H
    per_col = (len(rows) + 1) // 2 if compact else len(rows)
    return h + COL_HEAD_H + per_col * row_h + CARD_PAD_BOTTOM


def _draw_header(draw, ops, x, y, w, label, compact):
    rank_w = RANK_W_COMPACT if compact else RANK_W
    rw = text_w(draw, "排名", COL_HEAD_SIZE)
    add_text(ops, x + (rank_w - rw) / 2, y, "排名", COL_HEAD_SIZE, MUTED)
    add_text(ops, x + rank_w + COL_GAP, y, "玩家", COL_HEAD_SIZE, MUTED)
    vw = text_w(draw, label, COL_HEAD_SIZE)
    add_text(ops, x + w - vw, y, label, COL_HEAD_SIZE, MUTED)


def _draw_row(draw, ops, row, x, y, w, row_h, compact):
    highlight = bool(row.get('is_self'))
    if highlight:
        add_rect(ops, 'bg1', x, y, w, row_h, 10, SELF_BG)

    rank_w = RANK_W_COMPACT if compact else RANK_W
    badge_d = RANK_D_COMPACT if compact else RANK_D
    avatar = AVATAR_COMPACT if compact else AVATAR
    name_size = NAME_COMPACT if compact else NAME_SIZE
    val_size = VALUE_COMPACT if compact else VALUE_SIZE
    cy = y + row_h / 2

    rank = row['rank']
    if rank <= 3:
        add_ellipse(ops, 'bg1', x + (rank_w - badge_d) / 2,
                    cy - badge_d / 2, badge_d, ACCENT)
        rt = str(rank)
        rw = text_w(draw, rt, RANK_SIZE)
        add_text(ops, x + (rank_w - rw) / 2, cy - RANK_SIZE / 2 - 1,
                 rt, RANK_SIZE, '#FFFFFF')
    else:
        rt = str(rank)
        size = NAME_COMPACT if compact else RANK_DIM_SIZE
        rw = text_w(draw, rt, size)
        add_text(ops, x + (rank_w - rw) / 2, cy - size / 2 - 1,
                 rt, size, DIM)

    ax = x + rank_w + COL_GAP
    ops['fg'].append(('paste', ax, cy - avatar / 2, row['avatar']))
    vw = text_w(draw, row['value'], val_size)
    vx = x + w - vw
    nx = ax + avatar + COL_GAP
    name = _truncate(draw, row['name'], name_size, max(24, vx - nx - COL_GAP))
    color = ACCENT if highlight else TEXT
    add_text(ops, nx, cy - name_size / 2 - 1, name, name_size, color)
    add_text(ops, vx, cy - val_size / 2 - 1, row['value'], val_size, color)


def _draw_self(draw, ops, self_doc, x, y, w):
    add_rect(ops, 'bg1', x, y, w, SELF_H, SELF_RADIUS, SELF_BG)
    label = "你的名次"
    add_text(ops, x + 16, y + (SELF_H - SELF_LABEL_SIZE) / 2 + 1,
             label, SELF_LABEL_SIZE, CHIP_FG)
    ax = x + 16 + text_w(draw, label, SELF_LABEL_SIZE) + 14
    ops['fg'].append(('paste', ax, y + (SELF_H - 40) / 2, self_doc['avatar']))
    nx = ax + 40 + 12
    vw = text_w(draw, self_doc['value'], SELF_VAL_SIZE)
    vx = x + w - 16 - vw
    name = _truncate(draw, self_doc['name'], SELF_NAME_SIZE,
                     max(24, vx - nx - 12))
    add_text(ops, nx, y + (SELF_H - SELF_NAME_SIZE) / 2 - 1,
             name, SELF_NAME_SIZE, TEXT)
    color = ACCENT if self_doc.get('ranked') else MUTED
    add_text(ops, vx, y + (SELF_H - SELF_VAL_SIZE) / 2 - 1,
             self_doc['value'], SELF_VAL_SIZE, color)


def _draw_card(draw, ops, doc, y):
    x = PAD
    w = WIDTH - 2 * PAD
    total = _card_height(doc)
    px0 = x + CARD_PAD_X
    px1 = x + w - CARD_PAD_X
    inner_w = px1 - px0

    add_shadow(ops, x, y, w, total, CARD_RADIUS)
    add_rect(ops, 'bg0', x, y, w, total, CARD_RADIUS, CARD_BG)

    hy = y + CARD_PAD_TOP
    chip = doc['chip']
    cw = text_w(draw, chip, CHIP_SIZE) + CHIP_PAD_X * 2
    server = _truncate_mixed(draw, doc['server'], HEAD_NAME_SIZE,
                             max(80, (px1 - cw - 16) - px0))
    _add_text_mixed(ops, px0, hy + 2, server, HEAD_NAME_SIZE, TEXT)
    add_rect(ops, 'bg1', px1 - cw, hy, cw, CHIP_H, CHIP_H // 2, CHIP_BG, CHIP_BD)
    add_text(ops, px1 - cw + CHIP_PAD_X, hy + 4, chip, CHIP_SIZE, CHIP_FG)

    cy = hy + HEAD_H
    add_dash(ops, px0, px1, cy)
    cy += 1 + DASH_GAP

    if doc.get('error'):
        msg = _truncate(draw, doc['error'], 18, inner_w)
        mw = text_w(draw, msg, 18)
        add_text(ops, px0 + (inner_w - mw) / 2, cy + 26, msg, 18, MUTED)
        return total

    if doc.get('empty'):
        line1 = "该服务器暂无统计数据"
        line2 = "玩家产生统计后自动入榜"
        w1 = text_w(draw, line1, 18)
        w2 = text_w(draw, line2, 14)
        add_text(ops, px0 + (inner_w - w1) / 2, cy + 22, line1, 18, MUTED)
        add_text(ops, px0 + (inner_w - w2) / 2, cy + 52, line2, 14, MUTED)
        return total

    if doc.get('self'):
        _draw_self(draw, ops, doc['self'], px0, cy, inner_w)
        cy += SELF_H
        add_dash(ops, px0, px1, cy)
        cy += 1 + DASH_GAP

    rows = doc['rows']
    compact = doc.get('compact')
    row_h = ROW_COMPACT_H if compact else ROW_SINGLE_H
    label = doc['type_label']

    if compact:
        col_w = (inner_w - TWO_DIV_GAP) / 2
        per_col = (len(rows) + 1) // 2
        for col in (0, 1):
            cx = px0 + col * (col_w + TWO_DIV_GAP)
            _draw_header(draw, ops, cx, cy, col_w, label, True)
            for pos in range(per_col):
                idx = col * per_col + pos
                if idx >= len(rows):
                    break
                _draw_row(draw, ops, rows[idx], cx,
                          cy + COL_HEAD_H + pos * row_h, col_w, row_h, True)
        ops['fg'].append(('vdash', px0 + col_w + TWO_DIV_GAP / 2,
                          cy, cy + COL_HEAD_H + per_col * row_h))
    else:
        _draw_header(draw, ops, px0, cy, inner_w, label, False)
        for idx, row in enumerate(rows):
            _draw_row(draw, ops, row, px0,
                      cy + COL_HEAD_H + idx * row_h, inner_w, row_h, False)
    return total


def _build(draw, ops, doc, y):
    tw = text_w(draw, doc['title'], TITLE_SIZE)
    add_text(ops, (WIDTH - tw) / 2, y, doc['title'], TITLE_SIZE, ACCENT)
    y += TITLE_SIZE + TITLE_GAP

    subtitle = _truncate_mixed(draw, doc['subtitle'], SUB_SIZE, WIDTH - 2 * PAD)
    sw = _text_w(draw, subtitle, SUB_SIZE)
    _add_text_mixed(ops, (WIDTH - sw) / 2, y, subtitle, SUB_SIZE, SUB_COLOR)
    y += SUB_SIZE + CARD_TOP_GAP

    y += _draw_card(draw, ops, doc, y)
    y += 18

    hw = text_w(draw, doc['hint'], HINT_SIZE)
    add_text(ops, (WIDTH - hw) / 2, y, doc['hint'], HINT_SIZE, HINT_COLOR)
    y += HINT_SIZE + 14

    tw = text_w(draw, doc['time'], TIME_SIZE)
    add_text(ops, (WIDTH - tw) / 2, y, doc['time'], TIME_SIZE, TIME_COLOR)
    return y + TIME_SIZE


async def _load_avatars(doc) -> None:
    need = []
    for row in doc.get('rows') or []:
        need.append((row.get('name'), row.get('uuid')))
    self_doc = doc.get('self')
    if self_doc:
        need.append((self_doc.get('name'), self_doc.get('uuid')))
    need = [item for item in dict.fromkeys(need) if item[0]]
    if not need:
        return

    px = int(AVATAR * SCALE)
    async with aiohttp.ClientSession() as session:
        sem = asyncio.Semaphore(AVATAR_CONCURRENCY)

        async def _get(name, uuid):
            async with sem:
                try:
                    return await download_avatar(session, name, px,
                                                 is_premium=None, uuid=uuid)
                except Exception:
                    return get_default_avatar(px)

        images = await asyncio.gather(*[_get(n, u) for n, u in need])

    cache = {}
    for (name, uuid), img in zip(need, images):
        cache[(name, uuid)] = _make_rounded(img, AVATAR_R * SCALE)

    compact = doc.get('compact')
    size_px = int((AVATAR_COMPACT if compact else AVATAR) * SCALE)
    for row in doc.get('rows') or []:
        img = cache.get((row.get('name'), row.get('uuid')))
        if img is None:
            img = _make_rounded(get_default_avatar(px), AVATAR_R * SCALE)
        if img.size[0] != size_px:
            img = img.resize((size_px, size_px), Image.NEAREST)
        row['avatar'] = img

    if self_doc:
        img = cache.get((self_doc.get('name'), self_doc.get('uuid')))
        if img is None:
            img = _make_rounded(get_default_avatar(px), AVATAR_R * SCALE)
        self_px = int(40 * SCALE)
        if img.size[0] != self_px:
            img = img.resize((self_px, self_px), Image.NEAREST)
        self_doc['avatar'] = img


async def draw_leaderboard_image(doc: Dict[str, Any]) -> Image.Image:
    await _load_avatars(doc)
    return await asyncio.to_thread(
        build_image, _build, doc,
        extra_handlers={'paste': _paste, 'vdash': _vdash, 'emoji': _render_emoji})
