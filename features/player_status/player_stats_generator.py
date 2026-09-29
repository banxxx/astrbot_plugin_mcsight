"""玩家数据统计图片生成器（/查询、/mc stats）

复用 help_image 引擎：延迟 ops 布局 + 2x 超采样渲染；
matplotlib 雷达图/环形图按 2x 像素输出后经 'paste' 专有 op 贴入。
"""

import time
from io import BytesIO
from typing import Dict, Any, Optional

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Patch
from PIL import Image, ImageDraw

from ...utils.avatar_cache import _smart_resize
from ...utils.text_renderer import draw_text_with_emoji, measure_text_with_emoji
from ..help_image.image_generator import (
    WIDTH, PAD, SCALE,
    add_text, add_rect, add_ellipse,
    text_w, _font, build_image,
)

# ========== 布局常量（设计单位） ==========
ACCENT = '#5b6abf'
TITLE_TEXT = "玩家数据统计"
TITLE_SIZE = 36
TITLE_GAP = 20

CARD_BG = '#FFFFFF'
CARD_RADIUS = 16
CARD_GAP = 20

# 玩家卡
P_CARD_PAD_V = 20
P_CARD_PAD_X = 28
AVATAR = 72
AVATAR_R = 12
DOT_D = 18
NAME_SIZE = 28
NAME_COLOR = '#333333'
UUID_SIZE = 14
UUID_COLOR = '#aaaaaa'
CHIP_SIZE = 15
CHIP_H = 26
CHIP_PAD_X = 12
CHIP_FG, CHIP_BG, CHIP_BD = '#666666', '#f4f5fa', '#e7e9f3'
SUM_GAP = 10
SRV_FG, SRV_BG, SRV_BD = '#8a7bc0', '#f4f2fb', '#e6e0f5'
SRV_SIZE = 14
SRV_H = 24
DOT_ON = '#5cb85c'
DOT_OFF = '#d9534f'

# 图表卡
CH_GAP = 24
CH_PAD_TOP = 14
CH_PAD_X = 12
CH_PAD_BOTTOM = 10
CH_TITLE_SIZE = 15
CH_TITLE_GAP = 6
CH_H = 230

# 明细卡
D_PAD = 16
D_COLS = 3
D_GAP = 12
CAT_BG = '#f6f6f5'
CAT_R = 12
CAT_PAD_V = 12
CAT_PAD_X = 14
CAT_H_SIZE = 15
CAT_H_H = 20
CAT_H_GAP = 10
BADGE_FG, BADGE_BG = '#a5aabb', '#efeff6'
BADGE_SIZE = 12
BADGE_H = 18
ITEM_COLS = 2
ITEM_GAP_X = 10
ITEM_GAP_Y = 8
LBL_SIZE = 13
LBL_COLOR = '#888888'
VAL_SIZE = 17
VAL_COLOR = '#333333'
LBL_LH = 18
VAL_LH = 23
NOTE_SIZE = 15
NOTE_COLOR = '#a5aabb'

TIME_SIZE = 16
TIME_COLOR = '#aaaaaa'
TIME_GAP = 24

CATEGORY_COLORS = ['#5b6abf', '#7BA87F', '#D9A87C', '#C47D7D', '#6F8B9F',
                   '#B8A9C9']

# ========== 分类定义（与HTML保持一致） ==========
CATEGORIES = {
    '探索': {
        'items': [
            ('步行距离', 'walkDistance', 'm'),
            ('飞行距离', 'flyDistance', 'm'),
            ('游泳距离', 'swimDistance', 'm'),
            ('划船距离', 'boatDistance', 'm'),
            ('攀爬距离', 'climbDistance', 'm'),
            ('疾跑距离', 'sprintDistance', 'm'),
            ('水下行走', 'underwaterWalkDistance', 'm'),
            ('矿车距离', 'minecartDistance', 'm'),
            ('鞘翅滑行', 'elytraDistance', 'm'),
            ('骑猪距离', 'pigDistance', 'm'),
            ('骑马距离', 'horseDistance', 'm'),
            ('骑炽足兽', 'striderDistance', 'm')
        ]
    },
    '建筑': {
        'items': [
            ('破坏方块', 'blocksMined', ''),
            ('放置方块', 'blocksPlaced', ''),
            ('盆栽种植', 'flowerPotted', ''),
            ('使用工作台', 'craftingTableInteractions', '')
        ]
    },
    '工业': {
        'items': [
            ('使用漏斗', 'hopperInspected', ''),
            ('使用投掷器', 'dropperInspected', ''),
            ('使用发射器', 'dispenserInspected', ''),
            ('使用铁砧', 'anvilInteractions', ''),
            ('使用熔炉', 'furnaceInteractions', ''),
            ('使用高炉', 'blastFurnaceInteractions', ''),
            ('使用酿造台', 'brewingStandInteractions', ''),
            ('使用切石机', 'stonecutterInteractions', ''),
            ('物品合成', 'itemsCrafted', '')
        ]
    },
    '战斗': {
        'items': [
            ('造成伤害', 'damageDealt', ''),
            ('承受伤害', 'damageTaken', ''),
            ('吸收伤害', 'damageAbsorbed', '')
        ]
    },
    '社交': {
        'items': [
            ('床上入眠', 'sleepInBed', ''),
            ('与村民交谈', 'talkToVillager', '')
        ]
    },
    '养老': {
        'items': [
            ('播放唱片', 'jukeboxPlayed', ''),
            ('繁殖动物', 'animalsBred', ''),
            ('播放音符盒', 'noteBlockPlayed', ''),
            ('音符盒调音', 'noteBlockTuned', ''),
            ('鸣钟', 'bellRing', '')
        ]
    }
}


def format_value(val, unit):
    """格式化数值，超过1000显示为K"""
    if val is None:
        val = 0
    num = float(val)
    if num >= 1000:
        display = f"{num/1000:.1f}K"
    elif isinstance(num, float) and num % 1 != 0:
        display = f"{num:.2f}"
    else:
        display = str(int(num))
    return f"{display} {unit}" if unit else display


def calc_category_scores(data):
    """计算每个分类的得分（分类占比归一化）"""
    category_totals = {}
    grand_total = 0
    for cat_name, cat_info in CATEGORIES.items():
        total = 0
        for _, key, _ in cat_info['items']:
            v = data.get(key, 0)
            if isinstance(v, (int, float)):
                total += v
        category_totals[cat_name] = total
        grand_total += total
    if grand_total == 0:
        return {cat: 0 for cat in CATEGORIES}
    return {cat: category_totals[cat] / grand_total for cat in CATEGORIES}


# ========== matplotlib 图表（保存时 dpi×SCALE 得到 2x 像素） ==========
def _setup_matplotlib_font():
    try:
        plt.rcParams['font.sans-serif'] = [
            'Microsoft YaHei', 'SimHei', 'PingFang SC',
            'WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'DejaVu Sans'
        ]
        plt.rcParams['axes.unicode_minus'] = False
    except Exception:
        pass


def _fig_to_image(fig):
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100 * SCALE, facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert('RGBA')


def create_radar_chart(scores, category_names, w, h):
    """生成雷达图（w/h 为设计像素），返回 2x 像素 PIL Image"""
    _setup_matplotlib_font()
    labels = category_names
    values = [scores[cat] for cat in labels]
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    values += values[:1]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(w / 100, h / 100), dpi=100,
                           subplot_kw=dict(polar=True))
    ax.fill(angles, values, color=ACCENT, alpha=0.22)
    ax.plot(angles, values, color=ACCENT, linewidth=1.6)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9.5, color='#555555')
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.grid(color='#e6e6e3', linestyle='-', linewidth=0.5)
    ax.spines['polar'].set_visible(False)
    for r in np.linspace(0, 1, 6)[1:]:
        ax.add_patch(Circle((0, 0), r, transform=ax.transData._b,
                            fill=False, edgecolor='#efefec'))
    fig.tight_layout(pad=0.4)
    return _fig_to_image(fig)


def create_donut_chart(scores, donut_cats, all_labels, w, h):
    """生成环形图（饼图仅非零分类，图例含全部分类+百分比），2x 像素"""
    _setup_matplotlib_font()
    values = [scores[c] for c in donut_cats]
    color_map = {c: CATEGORY_COLORS[i % len(CATEGORY_COLORS)]
                 for i, c in enumerate(all_labels)}
    colors = [color_map[c] for c in donut_cats]

    fig, ax = plt.subplots(figsize=(w / 100, h / 100), dpi=100)
    ax.pie(values, labels=None, colors=colors, startangle=90,
           autopct='', wedgeprops=dict(width=0.4, edgecolor='white'))
    patches = [Patch(color=color_map[c],
                     label=f"{c}  {scores[c] * 100:.0f}%") for c in all_labels]
    ax.legend(handles=patches, loc='center left', bbox_to_anchor=(0.92, 0.5),
              fontsize=9.5, ncol=1, frameon=False, handletextpad=0.5)
    fig.subplots_adjust(left=0.0, right=0.55, top=0.98, bottom=0.02)
    return _fig_to_image(fig)


# ========== 组件 ==========
def _make_rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0], img.size[1]],
                                           radius=radius, fill=255)
    img = img.copy()
    img.putalpha(mask)
    return img


def _add_chip(ops, draw, x, y, text, fg, bg, bd, size=CHIP_SIZE,
              h=CHIP_H, pad=CHIP_PAD_X):
    w = measure_text_with_emoji(draw, text, _font(size)) + pad * 2
    add_rect(ops, 'bg1', x, y, w, h, h // 2, bg, bd)
    ops['fg'].append(('emoji', x + pad, y + (h - size) / 2 - 1, text,
                      size, fg))
    return w


def _cat_card_h(items):
    rows = (len(items) + ITEM_COLS - 1) // ITEM_COLS
    inner = rows * (LBL_LH + VAL_LH) + (rows - 1) * ITEM_GAP_Y
    return CAT_PAD_V + CAT_H_H + CAT_H_GAP + inner + CAT_PAD_V


# ========== 玩家卡 ==========
def _draw_player_card(draw, ops, doc, x0, x1, y):
    text_block = NAME_SIZE + 8 + CHIP_H
    card_h = max(AVATAR, text_block) + P_CARD_PAD_V * 2
    add_rect(ops, 'bg0', x0, y, x1 - x0, card_h, CARD_RADIUS, CARD_BG)

    ax = x0 + P_CARD_PAD_X
    ay = y + (card_h - AVATAR) / 2
    ops['bg1'].append(('paste', ax, ay, doc['avatar']))
    dot_color = DOT_ON if doc['is_online'] else DOT_OFF
    add_ellipse(ops, 'bg1', ax + AVATAR - DOT_D + 5,
                ay + AVATAR - DOT_D + 5, DOT_D, '#ffffff')
    add_ellipse(ops, 'bg1', ax + AVATAR - DOT_D + 8,
                ay + AVATAR - DOT_D + 8, DOT_D - 6, dot_color)

    info_x = ax + AVATAR + 24
    block_top = y + (card_h - text_block) / 2

    ops['fg'].append(('emoji', info_x, block_top, doc['player_name'],
                      NAME_SIZE, NAME_COLOR))
    name_w = measure_text_with_emoji(draw, doc['player_name'],
                                     _font(NAME_SIZE))
    if doc['uuid_suffix']:
        add_text(ops, info_x + name_w + 10, block_top + 10,
                 f"#{doc['uuid_suffix']}", UUID_SIZE, UUID_COLOR)

    # 摘要胶囊（放不下换行，首行避开右上角服务器标签）
    right_limit = x1 - P_CARD_PAD_X
    if doc['srv_w']:
        right_limit -= doc['srv_w'] + 16
    cy = block_top + NAME_SIZE + 8
    cx = info_x
    for s in doc['summary']:
        w = measure_text_with_emoji(draw, s, _font(CHIP_SIZE)) + CHIP_PAD_X * 2
        if cx + w > right_limit and cx > info_x:
            cy += CHIP_H + 6
            cx = info_x
        cx += _add_chip(ops, draw, cx, cy, s, CHIP_FG, CHIP_BG, CHIP_BD)
        cx += SUM_GAP

    if doc['server_label'] and doc['srv_w']:
        sx = x1 - P_CARD_PAD_X - doc['srv_w']
        sy = y + P_CARD_PAD_V
        add_rect(ops, 'bg1', sx, sy, doc['srv_w'], SRV_H, SRV_H // 2,
                 SRV_BG, SRV_BD)
        add_text(ops, sx + 12, sy + (SRV_H - SRV_SIZE) / 2 - 1,
                 doc['server_label'], SRV_SIZE, SRV_FG)
    return card_h


# ========== 图表卡 ==========
def _draw_charts(draw, ops, doc, x0, x1, y):
    cw = (x1 - x0 - CH_GAP) / 2
    card_h = CH_PAD_TOP + CH_TITLE_SIZE + CH_TITLE_GAP + CH_H + CH_PAD_BOTTOM
    for i, (title, chart) in enumerate(
            [("能力分布 · 雷达图", doc['radar']),
             ("分类占比 · 环形图", doc['donut'])]):
        cx0 = x0 + i * (cw + CH_GAP)
        add_rect(ops, 'bg0', cx0, y, cw, card_h, CARD_RADIUS, CARD_BG)
        tw = text_w(draw, title, CH_TITLE_SIZE)
        add_text(ops, cx0 + (cw - tw) / 2, y + CH_PAD_TOP, title,
                 CH_TITLE_SIZE, '#444444')
        pw, ph = chart.size[0] / SCALE, chart.size[1] / SCALE
        ox = max(cx0 + CH_PAD_X, cx0 + (cw - pw) / 2)
        oy = y + CH_PAD_TOP + CH_TITLE_SIZE + CH_TITLE_GAP + (CH_H - ph) / 2
        ops['bg1'].append(('paste', ox, oy, chart))
    return card_h


# ========== 明细卡 ==========
def _draw_detail(draw, ops, doc, x0, x1, y):
    inner_w = x1 - x0 - D_PAD * 2
    cats = doc['display_categories']
    if not cats:
        card_h = D_PAD * 2 + 30
        add_rect(ops, 'bg0', x0, y, x1 - x0, card_h, CARD_RADIUS, CARD_BG)
        t = "暂无详细数据"
        tw = text_w(draw, t, NOTE_SIZE)
        add_text(ops, x0 + (x1 - x0 - tw) / 2, y + card_h / 2 - NOTE_SIZE / 2,
                 t, NOTE_SIZE, NOTE_COLOR)
        return card_h

    col_w = (inner_w - (D_COLS - 1) * D_GAP) / D_COLS
    groups = [cats[i:i + D_COLS] for i in range(0, len(cats), D_COLS)]
    row_hs = [max(_cat_card_h(it) for _, it in g) for g in groups]
    grid_h = sum(row_hs) + (len(groups) - 1) * D_GAP
    card_h = D_PAD * 2 + grid_h
    add_rect(ops, 'bg0', x0, y, x1 - x0, card_h, CARD_RADIUS, CARD_BG)

    gy = y + D_PAD
    for grp, rh in zip(groups, row_hs):
        for ci, (cat, items) in enumerate(grp):
            cx0 = x0 + D_PAD + ci * (col_w + D_GAP)
            add_rect(ops, 'bg1', cx0, gy, col_w, rh, CAT_R, CAT_BG)
            tx = cx0 + CAT_PAD_X
            add_text(ops, tx, gy + CAT_PAD_V, cat, CAT_H_SIZE, ACCENT)
            badge = str(len(items))
            bw = text_w(draw, badge, BADGE_SIZE) + 16
            bx = cx0 + col_w - CAT_PAD_X - bw
            add_rect(ops, 'bg1', bx, gy + CAT_PAD_V - 1, bw, BADGE_H,
                     BADGE_H // 2, BADGE_BG)
            add_text(ops, bx + 8, gy + CAT_PAD_V + 2, badge, BADGE_SIZE,
                     BADGE_FG)

            iy = gy + CAT_PAD_V + CAT_H_H + CAT_H_GAP
            col_iw = (col_w - CAT_PAD_X * 2 - (ITEM_COLS - 1) * ITEM_GAP_X) \
                / ITEM_COLS
            for ii, (label, _key, unit, val) in enumerate(items):
                r, c = divmod(ii, ITEM_COLS)
                ix = tx + c * (col_iw + ITEM_GAP_X)
                yy = iy + r * (LBL_LH + VAL_LH + ITEM_GAP_Y)
                add_text(ops, ix, yy, label, LBL_SIZE, LBL_COLOR)
                add_text(ops, ix, yy + LBL_LH,
                         format_value(val, unit), VAL_SIZE, VAL_COLOR)
        gy += rh + D_GAP
    return card_h


# ========== 总布局 ==========
def _build_stats(draw, ops, doc, y):
    tw = text_w(draw, TITLE_TEXT, TITLE_SIZE)
    add_text(ops, (WIDTH - tw) / 2, y, TITLE_TEXT, TITLE_SIZE, ACCENT)
    y += TITLE_SIZE + TITLE_GAP

    x0, x1 = PAD, WIDTH - PAD
    y += _draw_player_card(draw, ops, doc, x0, x1, y) + CARD_GAP
    y += _draw_charts(draw, ops, doc, x0, x1, y) + CARD_GAP
    y += _draw_detail(draw, ops, doc, x0, x1, y) + TIME_GAP

    now = doc['now_str']
    nw = text_w(draw, now, TIME_SIZE)
    add_text(ops, (WIDTH - nw) / 2, y, now, TIME_SIZE, TIME_COLOR)
    return y + TIME_SIZE


# ========== 入口 ==========
async def draw_player_stats_image(
    stats_data: Dict[str, Any],
    player_name: str,
    server_name: str = None,
    is_online: bool = True,
    avatar_img: Optional[Image.Image] = None,
    show_server_label: bool = True
) -> Image.Image:
    """生成玩家统计图片（雷达图+环形图+分类明细，2x 超采样）"""
    scores = calc_category_scores(stats_data)
    category_names = list(CATEGORIES.keys())

    display_categories = []
    for cat in category_names:
        items = []
        for label, key, unit in CATEGORIES[cat]['items']:
            val = stats_data.get(key, 0)
            if val:
                items.append((label, key, unit, val))
        if items:
            display_categories.append((cat, items))

    # 头像（2x 像素，NEAREST 放大保持像素风）
    ava_px = int(AVATAR * SCALE)
    if avatar_img is None:
        placeholder = Image.new('RGBA', (ava_px, ava_px), (176, 176, 176, 255))
        ImageDraw.Draw(placeholder).rectangle(
            (int(ava_px * 0.25), int(ava_px * 0.65),
             int(ava_px * 0.75), int(ava_px * 0.95)),
            fill=(136, 136, 136, 255))
        avatar = _make_rounded(placeholder, AVATAR_R * SCALE)
    else:
        avatar = _make_rounded(_smart_resize(avatar_img, ava_px),
                               AVATAR_R * SCALE)

    chart_w = int((WIDTH - PAD * 2 - CH_GAP) / 2) - 2 * CH_PAD_X
    radar = create_radar_chart(scores, category_names, chart_w, CH_H)
    donut_cats = [c for c in category_names if scores[c] > 0]
    if donut_cats:
        donut = create_donut_chart(scores, donut_cats, category_names,
                                   chart_w, CH_H)
    else:
        donut = Image.new('RGBA', radar.size, '#ffffff')

    server_label = ""
    if server_name and show_server_label:
        server_label = f"服务器: {server_name}"
    srv_w = 0
    if server_label:
        scratch = ImageDraw.Draw(Image.new('RGB', (8, 8)))
        srv_w = text_w(scratch, server_label, SRV_SIZE) + 24

    doc = {
        'player_name': player_name,
        'uuid_suffix': stats_data.get('uuidSuffix', ''),
        'summary': [
            f"🗡️ 击杀 {stats_data.get('mobKills', 0)}",
            f"💀 死亡 {stats_data.get('deaths', 0)}",
            f"⏱️ 在线 {stats_data.get('playTimeFormatted', '0分钟')}",
        ],
        'server_label': server_label,
        'srv_w': srv_w,
        'is_online': is_online,
        'avatar': avatar,
        'radar': radar,
        'donut': donut,
        'display_categories': display_categories,
        'now_str': time.strftime("%Y/%m/%d  %H:%M:%S"),
    }

    def _paste(op, img):
        _, x, y, pic = op
        img.paste(pic, (int(x * SCALE), int(y * SCALE)), pic)

    def _render_emoji(op, img):
        _, x, y, text, size, color = op
        d = ImageDraw.Draw(img)
        draw_text_with_emoji(img, d, (x * SCALE, y * SCALE), text,
                             _font(size * SCALE), color, emoji_scale=0.95)

    return build_image(_build_stats, doc,
                       extra_handlers={'paste': _paste,
                                       'emoji': _render_emoji})
