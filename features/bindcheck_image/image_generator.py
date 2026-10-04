"""群内未绑定成员名单图（/绑定检测）

每页 100 人、两列 50 行；页头是基本信息 + 页码胶囊，页脚是本页人数。
群昵称按列宽截断加省略号，QQ 号永不牺牲——管理员要拿它去搜人。
"""
import asyncio
from typing import List, Dict, Any

from PIL import Image, ImageDraw

from ...utils.text_renderer import draw_text_with_emoji, measure_text_with_emoji
from ..help_image.image_generator import (
    WIDTH, PAD, SCALE, BG, ACCENT, TEXT, DESC, MUTED, DASH, CARD_BG,
    PILL_FG, PILL_BG, PILL_BD, GREEN,
    new_ops, add_text, add_rect, add_dash, text_w, _font, render_ops,
)

# ========== 布局常量（设计单位，渲染时统一 ×SCALE） ==========
PER_PAGE = 100
COLS = 2
ROWS_PER_COL = 50
COL_GAP = 12
COL_W = (WIDTH - PAD * 2 - COL_GAP * (COLS - 1)) // COLS   # 498

ROW_H = 44
ROW_GAP = 8
ROW_RADIUS = 12
ROW_PAD_X = 14
IDX_W = 30
SEG_GAP = 10

HEAD_H = 150
HEAD_PAD_X = 26
HEAD_PAD_TOP = 22
TITLE_SIZE = 30
PILL_SIZE = 16
META_V_SIZE = 22
META_K_SIZE = 15
NOTE_SIZE = 15
NICK_SIZE = 19
QQ_SIZE = 17
IDX_SIZE = 15

EMOJI_SCALE = 0.95
ELLIPSIS = "…"

# 页头里三组「数字 + 标签」
META_LABEL_COLOR = MUTED
META_SEP_COLOR = DASH


def _fit_nickname(draw, name: str, max_w: float) -> str:
    """按可用宽度截断昵称：整字/整 emoji 为单位裁，末尾加省略号。

    截不断时也返回原串（max_w 为负等极端情况），调用方靠它兜底。
    """
    if not name:
        return ""
    font = _font(NICK_SIZE * SCALE)
    if measure_text_with_emoji(draw, name, font, EMOJI_SCALE) <= max_w:
        return name

    # 以「显示单元」为切割粒度：emoji 是一整块，普通字符逐个
    from ...utils.text_renderer import split_emoji_runs
    units = []
    for is_emoji, seg in split_emoji_runs(name):
        if is_emoji:
            units.append(seg)
        else:
            units.extend(list(seg))

    while units:
        units.pop()
        candidate = "".join(units) + ELLIPSIS
        if measure_text_with_emoji(draw, candidate, font, EMOJI_SCALE) <= max_w:
            return candidate
    return ELLIPSIS


def _add_head(ops, draw, page: int, pages: int, info: Dict[str, Any]):
    x, y = PAD, PAD
    w = WIDTH - PAD * 2
    add_rect(ops, 'bg0', x, y, w, HEAD_H, 16, CARD_BG, DASH)

    tx, ty = x + HEAD_PAD_X, y + HEAD_PAD_TOP
    add_text(ops, tx, ty, "本群未绑定成员", TITLE_SIZE, ACCENT)

    pill = f"第 {page + 1} / {pages} 页"
    pill_w = text_w(draw, pill, PILL_SIZE) + 28
    pill_x = x + w - HEAD_PAD_X - pill_w
    add_rect(ops, 'bg1', pill_x, ty + 2, pill_w, 30, 15, PILL_BG, PILL_BD)
    add_text(ops, pill_x + 14, ty + 9, pill, PILL_SIZE, PILL_FG)

    my = ty + 30 + 14
    cx = tx
    total = info["group_total"]
    unbound = info["unbound_total"]
    bound = max(total - unbound, 0)
    for idx, (value, label) in enumerate(
            ((str(total), "群成员"), (str(unbound), "未绑定"), (str(bound), "已绑定"))):
        add_text(ops, cx, my, value, META_V_SIZE, TEXT)
        cx += text_w(draw, value, META_V_SIZE) + 6
        add_text(ops, cx, my + 5, label, META_K_SIZE, META_LABEL_COLOR)
        cx += text_w(draw, label, META_K_SIZE) + 10
        if idx < 2:
            add_text(ops, cx, my + 5, "/", META_K_SIZE, META_SEP_COLOR)
            cx += text_w(draw, "/", META_K_SIZE) + 10

    if total > 0:
        pct = f"未绑定占比 {unbound * 100.0 / total:.1f}%"
        add_text(ops, cx + 8, my + 5, pct, META_K_SIZE, GREEN)

    ny = my + 40
    add_dash(ops, tx, x + w - HEAD_PAD_X, ny)
    add_text(ops, tx, ny + 10, "未绑定成员登录游戏后发送「/绑定 游戏内 6 位码」即可",
             NOTE_SIZE, DESC)
    stamp = info.get("stamp") or ""
    if stamp:
        add_text(ops, x + w - HEAD_PAD_X - text_w(draw, stamp, NOTE_SIZE),
                 ny + 10, stamp, NOTE_SIZE, META_LABEL_COLOR)


def _build_page(draw, ops, rows: List[Dict[str, str]], page: int, pages: int,
                info: Dict[str, Any]) -> float:
    """收集一页的绘制指令，返回内容总高（设计单位）"""
    _add_head(ops, draw, page, pages, info)

    grid_y = PAD + HEAD_H + 16
    per = ROWS_PER_COL
    for idx, item in enumerate(rows):
        col, row = divmod(idx, per)
        x = PAD + col * (COL_W + COL_GAP)
        y = grid_y + row * (ROW_H + ROW_GAP)
        add_rect(ops, 'bg0', x, y, COL_W, ROW_H, ROW_RADIUS, CARD_BG, DASH)

        # 序号：全局连续编号，右对齐，方便数人头
        num = str(page * PER_PAGE + idx + 1)
        cursor = x + ROW_PAD_X + IDX_W - text_w(draw, num, IDX_SIZE)
        add_text(ops, cursor, y + 13, num, IDX_SIZE, META_LABEL_COLOR)
        cursor += IDX_W + SEG_GAP

        nick = item["name"]
        qq = item["qq"]
        qq_w = text_w(draw, qq, QQ_SIZE)
        nick_max = x + COL_W - ROW_PAD_X - qq_w - SEG_GAP - cursor
        shown = _fit_nickname(draw, nick, nick_max)
        # 昵称走 emoji op：群名片里带 emoji 时按位图贴，宽度度量与绘制同一套
        ops['fg'].append(('emoji', cursor, y + 10, shown, NICK_SIZE, TEXT))
        add_text(ops, x + COL_W - ROW_PAD_X - qq_w, y + 12, qq, QQ_SIZE,
                 META_LABEL_COLOR)

    filled = min(per, len(rows))
    total_h = grid_y + filled * ROW_H + (filled - 1) * ROW_GAP

    if page == pages - 1:
        note_y = total_h + 16
        add_text(ops, PAD, note_y,
                 f"本页 {len(rows)} 人 · 全量 {info['unbound_total']} 人已列完",
                 NOTE_SIZE, META_LABEL_COLOR)
        total_h = note_y + 26
    else:
        note_y = total_h + 16
        add_text(ops, PAD, note_y, f"本页 {len(rows)} 人 · 共 {pages} 页 · 接下页",
                 NOTE_SIZE, META_LABEL_COLOR)
        total_h = note_y + 26

    return total_h


def _render_page(rows, page, pages, info):
    scratch = Image.new('RGB', (8, 8), BG)
    draw = ImageDraw.Draw(scratch)
    ops = new_ops()
    total_h = _build_page(draw, ops, rows, page, pages, info)

    img = Image.new('RGB', (WIDTH * SCALE, (int(total_h) + PAD) * SCALE), BG)
    full_draw = ImageDraw.Draw(img)

    def _emoji(op, image):
        _, x, y, txt, size, color = op
        draw_text_with_emoji(image, full_draw, (x * SCALE, y * SCALE), txt,
                             _font(size * SCALE), color, emoji_scale=EMOJI_SCALE)

    render_ops(full_draw, ops, img=img, extra_handlers={'emoji': _emoji})
    return img


def render_pages_sync(unbound: List[Dict[str, str]], info: Dict[str, Any]) -> List[Image.Image]:
    """同步渲染全部页（纯 CPU，调用方要挪出事件循环）"""
    pages = max((len(unbound) + PER_PAGE - 1) // PER_PAGE, 1)
    return [_render_page(unbound[p * PER_PAGE:(p + 1) * PER_PAGE], p, pages, info)
            for p in range(pages)]


async def draw_unbound_pages(unbound: List[Dict[str, str]], info: Dict[str, Any]):
    """渲染 + PNG 编码都在工作线程里做，不占事件循环（同 /在线 的处理）"""
    return await asyncio.to_thread(render_pages_sync, unbound, info)
