"""玩家数据统计图片生成器（/查询、/mc stats）

复用 help_image 引擎：延迟 ops 布局 + 2x 超采样渲染；
雷达图/环形图由 Pillow 单独绘制，按 2x 像素输出后经 'paste' 专有 op 贴入。
"""

import math
import time
from typing import Dict, Any, Optional

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
CH_H = 196

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

# ========== 分类定义 ==========
# 每项 = (中文标签, 模组 StatUtils 下发字段, 单位, 累计参考值, 语义类型)
#
# 语义类型决定这个数能进哪张图、以什么口径进：
#   ACT   主动操作次数 —— 能跨类比，但必须先除以游玩小时（次数天然差几个数量级）
#   FLOW  流量型（米 / 伤害点）—— 只能算"每小时流量"，绝不参与计次
#   NOISE 被动计数 —— 不进图（跳跃是跑图顺带产生的，死亡次数多≠战斗强），明细里仍列原始值
ACT, FLOW, NOISE = 'act', 'flow', 'noise'

CATEGORIES = {
    '探索': {
        'items': [
            ('步行距离', 'walkDistance', 'm', 3_000_000, FLOW),
            ('疾跑距离', 'sprintDistance', 'm', 2_000_000, FLOW),
            ('飞行距离', 'flyDistance', 'm', 1_500_000, FLOW),
            ('鞘翅滑行', 'elytraDistance', 'm', 2_000_000, FLOW),
            ('骑马距离', 'horseDistance', 'm', 800_000, FLOW),
            ('游泳距离', 'swimDistance', 'm', 200_000, FLOW),
            ('攀爬距离', 'climbDistance', 'm', 150_000, FLOW),
            ('跳跃次数', 'jump', '', 300_000, NOISE),
            ('打开箱子', 'openChest', '', 300_000, ACT),
            ('打开木桶', 'openBarrel', '', 200_000, ACT)
        ]
    },
    '建筑': {
        'items': [
            ('使用工作台', 'interactWithCraftingTable', '', 150_000, ACT),
            ('使用切石机', 'interactWithStonecutter', '', 40_000, ACT),
            ('使用织布机', 'interactWithLoom', '', 15_000, ACT),
            ('使用制图台', 'interactWithCartographyTable', '', 15_000, ACT),
            ('花盆种花', 'potFlower', '', 3_000, ACT),
            ('填充炼药锅', 'fillCauldron', '', 5_000, ACT),
            ('清洗盔甲', 'cleanArmor', '', 5_000, ACT)
        ]
    },
    '工业': {
        'items': [
            ('使用熔炉', 'interactWithFurnace', '', 200_000, ACT),
            ('使用高炉', 'interactWithBlastFurnace', '', 80_000, ACT),
            ('使用烟熏炉', 'interactWithSmoker', '', 80_000, ACT),
            ('使用酿造台', 'interactWithBrewingStand', '', 80_000, ACT),
            ('使用铁砧', 'interactWithAnvil', '', 40_000, ACT),
            ('使用信标', 'interactWithBeacon', '', 150_000, ACT),
            ('查看漏斗', 'inspectHopper', '', 150_000, ACT),
            ('物品附魔', 'enchantItem', '', 15_000, ACT)
        ]
    },
    '战斗': {
        'items': [
            ('造成伤害', 'damageDealt', '点', 3_000_000, FLOW),
            ('承受伤害', 'damageTaken', '点', 1_000_000, FLOW),
            ('吸收伤害', 'damageAbsorbed', '点', 300_000, FLOW),
            ('盾牌格挡', 'damageBlockedByShield', '点', 200_000, FLOW),
            ('击杀生物', 'mobKills', '', 80_000, ACT),
            ('击杀玩家', 'playerKills', '', 300, ACT),
            ('死亡次数', 'deaths', '', 1_500, NOISE),
            ('击中标靶', 'targetHit', '', 20_000, ACT)
        ]
    },
    '社交': {
        'items': [
            ('与村民对话', 'talkedToVillager', '', 30_000, ACT),
            ('与村民交易', 'tradedWithVillager', '', 30_000, ACT),
            ('床上入眠', 'sleepInBed', '', 3_000, ACT),
            ('吃蛋糕', 'eatCakeSlice', '', 2_000, ACT),
            ('袭击胜利', 'raidWin', '', 250, ACT)
        ]
    },
    '养老': {
        'items': [
            ('繁殖动物', 'animalsBred', '', 30_000, ACT),
            ('钓鱼次数', 'fishCaught', '', 15_000, ACT),
            ('播放唱片', 'playRecord', '', 1_000, ACT),
            ('播放音符盒', 'playNoteblock', '', 15_000, ACT),
            ('音符盒调音', 'tuneNoteblock', '', 40_000, ACT),
            ('鸣钟', 'bellRing', '', 3_000, ACT),
            ('使用营火', 'interactWithCampfire', '', 30_000, ACT)
        ]
    }
}


VALUE_TIERS = ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K'))


def format_value(val, unit):
    """格式化数值：≥1000 依次进位为 K/M/B/T，保留一位小数"""
    try:
        num = float(val or 0)
    except (TypeError, ValueError):
        num = 0.0
    if not math.isfinite(num) or num < 0:
        num = 0.0

    if num < 1000:
        display = str(int(num)) if num % 1 == 0 else f"{num:.2f}"
    else:
        div, suffix = VALUE_TIERS[-1]
        for d, s in VALUE_TIERS:
            # 0.99995：进位后会显示成 1000.0K 的边界值，提前升一档
            if num >= d * 0.99995:
                div, suffix = d, s
                break
        display = f"{num / div:.1f}{suffix}"
    return f"{display} {unit}" if unit else display


REF_SPAN_ORDERS = 3      # 单项对数跨度：参考值/10^3 处记 0 分，参考值处记 100 分
CATEGORY_BLEND = 0.5     # 分类分 = 0.5*均值 + 0.5*最强项；0=只看广度，1=只看峰值

# 打分走"每小时强度"：值/游玩小时 对标 ref/CAREER_HOURS，
# 于是 5 小时的新号和 500 小时的老号在同一把尺上（否则新号整张图缩成圆心一点）。
CAREER_HOURS = 200.0     # "满级参考"对应的累计游玩小时
MIN_SAMPLE_HOURS = 10.0  # 低于此时长时强度不稳，图内标注样本不足
MIN_WINDOW_HOURS = 1.0   # 本期只玩了几分钟时，分母不再往下缩，防止强度爆表
TICKS_PER_HOUR = 72000.0


def play_hours(data):
    """模组 playTime 字段是 tick（Stats.PLAY_TIME），换算成小时；缺失返回 0"""
    try:
        t = float(data.get('playTime', 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return t / TICKS_PER_HOUR if t > 0 else 0.0


def _item_score(val, ref):
    """单项 0-1 分：对数刻度。距离/伤害/次数量级差几个数量级，
    线性归一会让大单位吃掉一切，所以每项先各自归一再聚合。"""
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0 or ref <= 0:
        return 0.0
    lo = ref / (10 ** REF_SPAN_ORDERS)
    return max(0.0, min(1.0, math.log10(v / lo) / REF_SPAN_ORDERS))


def calc_category_scores(data, hours=None):
    """每个分类 = 成员项（NOISE 除外）归一后的 均值/最强项 混合分，输出 0-100

    hours 为 None 时按该玩家自己的累计游玩小时折算。拿不到 playTime 时
    hours 退回 CAREER_HOURS，等价于退回旧的"累计值"口径，不会算出天文数字。
    """
    hours = hours if hours and hours > 0 else (play_hours(data) or CAREER_HOURS)
    scores = {}
    for cat_name, cat_info in CATEGORIES.items():
        got = [_item_score(data.get(key, 0) / hours, ref / CAREER_HOURS)
               for _label, key, _unit, ref, kind in cat_info['items']
               if kind != NOISE]
        if not got:
            scores[cat_name] = 0.0
            continue
        scores[cat_name] = 100.0 * (CATEGORY_BLEND * (sum(got) / len(got))
                                   + (1 - CATEGORY_BLEND) * max(got))
    return scores


def window_hours(current, baseline):
    """本期实际游玩小时 = playTime 差值。
    有 playTime 但本期几乎没玩时夹到 MIN_WINDOW_HOURS，防止几分钟样本炸出满格多边形；
    拿不到 playTime 返回 None，让调用方退回生涯口径而不是除以 1 小时把分数放大 200 倍。"""
    try:
        cur = float(current.get('playTime', 0) or 0)
        base = float((baseline or {}).get('playTime', 0) or 0)
    except (TypeError, ValueError):
        return None
    if cur <= 0:
        return None
    return max(MIN_WINDOW_HOURS, (cur - base) / TICKS_PER_HOUR)


def delta_stats(current, baseline):
    """增量 = 当前累计 - 基线累计。基线缺某项时按 0 处理（等于退回累计值），
    避免字段集变化时凭空把该项清零；模组侧 /stat reset 造成的负值夹到 0。"""
    out = {}
    for k, v in (current or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        try:
            base = float((baseline or {}).get(k, 0) or 0)
        except (TypeError, ValueError):
            base = 0.0
        out[k] = max(0.0, float(v) - base)
    return out


# ========== Pillow 图表（不依赖 matplotlib，避免 AstrBot 双份 numpy/PIL 副本原生崩溃） ==========
SS = 3                  # 相对设计单位的额外超采样，落图后下采样到 SCALE 得到抗锯齿
RADAR_GRID = '#dcdcd6'
RADAR_RING = '#ebebe6'
RADAR_LBL = '#555555'
RADAR_LBL_SIZE = 12
RADAR_TICK = '#c9c9c2'
RADAR_TICK_SIZE = 9
DONUT_HOLE_RATIO = 0.6
DONUT_EDGE = '#ffffff'
LEGEND_SIZE = 12
LEGEND_FG = '#555555'
GHOST_LINE = '#a3a39c'     # 历史累计轮廓：灰 + 虚线，与实心的"本期"区分
GHOST_DASH = 6
GHOST_GAP = 4
RD_LEGEND_SIZE = 10


def _finish(chart_img, w, h):
    """把 SS 倍画布收敛到 SCALE 倍像素，保持与引擎其它贴图的尺寸约定"""
    return chart_img.resize((int(round(w * SCALE)), int(round(h * SCALE))),
                            Image.LANCZOS)


def _chart_radius(w, h):
    """两图共用的设计半径：直径一致，且给雷达图外圈标签留出带高"""
    band = RADAR_LBL_SIZE * 1.8
    return max(20.0, min((h - 2 * band) / 2, w / 2 - band))


def _dash_path(draw, pts, on_len, off_len, fill, width, closed=True):
    """按弧长参数化的虚线路径，跨过顶点时相位保持连续"""
    edges = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    if closed and len(pts) > 1:
        edges.append((pts[-1], pts[0]))
    lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges]
    total = sum(lens)
    period = on_len + off_len
    if total <= 0 or period <= 0 or not edges:
        return

    def point_at(s):
        s = min(max(s, 0.0), total)
        for (a, b), length in zip(edges, lens):
            if s <= length:
                t = 0.0 if length <= 0 else s / length
                return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            s -= length
        return edges[-1][1]

    step = max(2.0, on_len / 4)
    k = 0
    while k * period < total:
        s0 = k * period
        s1 = min(s0 + on_len, total)
        n = max(2, int(math.ceil((s1 - s0) / step)))
        draw.line([point_at(s0 + (s1 - s0) * i / (n - 1)) for i in range(n)],
                  fill=fill, width=width)
        k += 1


def create_radar_chart(scores, category_names, w, h,
                       ghost_scores=None, window_days=None):
    """生成雷达图（w/h 为设计像素），返回 2x 像素 PIL Image。
    ghost_scores 给出时叠加一层灰色虚线生涯平均轮廓，实心表示本期强度。"""
    n = max(1, len(category_names))
    img = Image.new('RGBA', (w * SS, h * SS), '#ffffff')
    draw = ImageDraw.Draw(img)
    cx, cy = w * SS / 2, h * SS / 2
    lh = RADAR_LBL_SIZE * SS
    r = _chart_radius(w, h) * SS
    anchor_r = r + RADAR_LBL_SIZE * SS * 0.5

    def vertex(i, radius):
        a = -math.pi / 2 + i * 2 * math.pi / n
        return (cx + radius * math.cos(a), cy + radius * math.sin(a))

    def shape(vals):
        # 绝对刻度：分值 0-100，外环即满分参考值，两层的半径可直接比
        return [vertex(i, r * max(0.0, min(1.0, vals.get(cat, 0) / 100.0)))
                for i, cat in enumerate(category_names)]

    thin = max(1, int(round(0.5 * SS)))
    for ring in range(1, 6):
        rr = r * ring / 5
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                     outline=RADAR_RING, width=thin)
    for i in range(n):
        draw.line([cx, cy, *vertex(i, r)], fill=RADAR_GRID, width=thin)

    line_w = max(1, int(round(0.8 * SS)))
    if ghost_scores:
        _dash_path(draw, shape(ghost_scores), GHOST_DASH * SS, GHOST_GAP * SS,
                   GHOST_LINE, line_w)
    pts = shape(scores)
    draw.polygon(pts, fill=ACCENT + '38')
    draw.line(pts + pts[:1], fill=ACCENT, width=line_w)

    # 刻度写在两根轴之间的空档（-60°），避开轴线和分类标签
    ft = _font(RADAR_TICK_SIZE * SS)
    ta = -math.pi / 2 + math.pi / max(1, n)
    for ring in range(1, 6):
        rr = r * ring / 5
        tx = cx + (rr + 3 * SS) * math.cos(ta)
        ty = cy + (rr - RADAR_TICK_SIZE * SS * 0.5) * math.sin(ta)
        draw.text((tx, ty), str(ring * 20), font=ft, fill=RADAR_TICK)

    f = _font(lh)
    fs = _font(int(lh * 0.85))
    for i, cat in enumerate(category_names):
        a = -math.pi / 2 + i * 2 * math.pi / n
        px, py = vertex(i, anchor_r)
        num = f"{scores.get(cat, 0):.0f}"
        gap = 5 * SS
        tw = draw.textlength(cat, font=f)
        nw = draw.textlength(num, font=fs)
        total = tw + gap + nw
        cos_a, sin_a = math.cos(a), math.sin(a)
        if cos_a > 0.35:
            tx = px
        elif cos_a < -0.35:
            tx = px - total
        else:
            tx = px - total / 2
        tx = min(max(tx, 2), w * SS - total - 2)
        if abs(sin_a) < 0.35:
            ty = py - lh / 2
        elif sin_a < 0:
            ty = py - lh
        else:
            ty = py
        draw.text((tx, ty), cat, font=f, fill=RADAR_LBL)
        draw.text((tx + tw + gap, ty + (lh - fs.size) * 0.55), num,
                  font=fs, fill=ACCENT)

    if ghost_scores:
        # 图例放左上角：这里是外圈标签与多边形都够不到的空档
        fl = _font(RD_LEGEND_SIZE * SS)
        rows = [(f"近 {int(window_days or 7)} 天", True), ("生涯平均", False)]
        sw = 14 * SS
        sw_h = RD_LEGEND_SIZE * SS * 0.8
        ly = 6 * SS
        for text, solid in rows:
            mid = ly + RD_LEGEND_SIZE * SS * 0.55
            if solid:
                draw.rounded_rectangle([8 * SS, mid - sw_h / 2,
                                        8 * SS + sw, mid + sw_h / 2],
                                       radius=2 * SS, fill=ACCENT)
            else:
                _dash_path(draw, [(8 * SS, mid), (8 * SS + sw, mid)],
                           GHOST_DASH * SS * 0.6, GHOST_GAP * SS * 0.6,
                           GHOST_LINE, line_w, closed=False)
            draw.text((8 * SS + sw + 5 * SS, ly), text, font=fl, fill=LEGEND_FG)
            ly += RD_LEGEND_SIZE * SS + 5 * SS
    return _finish(img, w, h)


def _rounded_percents(shares, keys):
    """最大余数法取整，保证图例百分比之和恒为 100"""
    raw = {k: shares[k] * 100 for k in keys}
    out = {k: int(math.floor(raw[k])) for k in keys}
    left = 100 - sum(out.values())
    order = sorted(keys, key=lambda k: raw[k] - math.floor(raw[k]),
                   reverse=True)
    for k in order[:max(0, left)]:
        out[k] += 1
    return out


def create_donut_chart(shares, donut_cats, all_labels, w, h):
    """生成环形图（饼图仅非零分类，图例含全部分类+百分比），2x 像素"""
    img = Image.new('RGBA', (w * SS, h * SS), '#ffffff')
    draw = ImageDraw.Draw(img)
    color_map = {c: CATEGORY_COLORS[i % len(CATEGORY_COLORS)]
                 for i, c in enumerate(all_labels)}

    f = _font(LEGEND_SIZE * SS)
    pct = _rounded_percents(shares, all_labels)
    txt = [f"{c}  {pct[c]}%" for c in all_labels]
    sw_d = LEGEND_SIZE * SS * 0.9
    lw = max(draw.textlength(t, font=f) for t in txt)
    row_h = max(LEGEND_SIZE * SS, sw_d) + 8 * SS

    gap = 14 * SS
    ring_pad = 12 * SS
    r = min(_chart_radius(w, h) * SS,
            (w * SS - 2 * ring_pad - gap - sw_d - 6 * SS - lw) / 2)
    r = max(10.0, r)
    content_w = 2 * r + gap + sw_d + 6 * SS + lw
    cx = ring_pad + r
    if content_w < w * SS:
        cx = (w * SS - content_w) / 2 + r
    cy = h * SS / 2

    edge = max(2, int(round(1.5 * SS)))
    total = sum(max(0.0, shares[c]) for c in donut_cats) or 1.0
    acc = 90.0
    for c in donut_cats:
        sweep = 360.0 * max(0.0, shares[c]) / total
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], acc, acc + sweep,
                      fill=color_map[c], outline=DONUT_EDGE, width=edge)
        acc += sweep
    hole = r * DONUT_HOLE_RATIO
    draw.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill='#ffffff')

    lh = LEGEND_SIZE * SS
    ly = cy - (len(all_labels) * row_h - 8 * SS) / 2
    lx = cx + r + gap
    for c, t in zip(all_labels, txt):
        mid = ly + lh * 0.55
        add = sw_d / 2
        draw.rounded_rectangle([lx, mid - add, lx + sw_d, mid + add],
                               radius=add * 0.35, fill=color_map[c])
        draw.text((lx + sw_d + 6 * SS, ly), t, font=f, fill=LEGEND_FG)
        ly += row_h
    return _finish(img, w, h)


def create_blank_chart(w, h, text):
    """无数据时的占位图（尺寸与其它图表一致，避免卡片错位）"""
    img = Image.new('RGBA', (w * SS, h * SS), '#ffffff')
    draw = ImageDraw.Draw(img)
    f = _font(LEGEND_SIZE * SS)
    tw = draw.textlength(text, font=f)
    draw.text(((w * SS - tw) / 2, h * SS / 2 - LEGEND_SIZE * SS / 2), text,
              font=f, fill=LEGEND_FG)
    return _finish(img, w, h)


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
    for i, (title, chart) in enumerate([(doc['radar_title'], doc['radar']),
                                        (doc['donut_title'], doc['donut'])]):
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
DEFAULT_WINDOW_DAYS = 7


def draw_player_stats_image(
    stats_data: Dict[str, Any],
    player_name: str,
    server_name: str = None,
    is_online: bool = True,
    avatar_img: Optional[Image.Image] = None,
    show_server_label: bool = True,
    baseline_stats: Optional[Dict[str, Any]] = None,
    window_days: Optional[int] = None
) -> Image.Image:
    """生成玩家统计图片（雷达图+环形图+分类明细，2x 超采样）

    同步函数、纯 CPU 重活：调用方必须用 asyncio.to_thread 执行，
    直接在事件循环里调用会卡住整个后端。

    baseline_stats 为历史某日的累计值；给了它，两图就改画"近 window_days 天"
    的增量（雷达另用灰色虚线保留历史累计轮廓作参照）。
    """
    category_names = list(CATEGORIES.keys())
    hours = play_hours(stats_data)
    hist_scores = calc_category_scores(stats_data)     # 生涯每小时强度
    has_baseline = bool(baseline_stats)
    if has_baseline:
        window_days = int(window_days or DEFAULT_WINDOW_DAYS)
        period_tag = f"近 {window_days} 天"
        chart_data = delta_stats(stats_data, baseline_stats)
        win_h = window_hours(stats_data, baseline_stats)
        scores = calc_category_scores(chart_data, hours=win_h)
    else:
        window_days = None
        period_tag = ""
        chart_data = stats_data
        win_h = hours
        scores = hist_scores

    # 环形分母 = 六类强度得分之和。得分已各自对过参考值、无量纲，跨类可比；
    # 换成"次数求和"会被 jump 这类高频被动事件吃掉（实测一项占 74%），所以不用次数。
    total_score = sum(scores.values())
    shares = {c: (scores[c] / total_score if total_score > 0 else 0.0)
              for c in scores}

    # 样本不足只在底部说一句，不在图里到处贴警告
    if hours <= 0:
        note = "模组未提供游玩时长，按累计值折算"
    elif has_baseline and win_h is not None and win_h <= MIN_WINDOW_HOURS:
        note = f"本期游玩不足 {MIN_WINDOW_HOURS:.0f} 小时，强度按下限折算"
    elif hours < MIN_SAMPLE_HOURS:
        note = f"样本较少：累计游玩仅 {hours:.1f} 小时"
    else:
        note = ""

    display_categories = []
    for cat in category_names:
        items = []
        for label, key, unit, _ref, _kind in CATEGORIES[cat]['items']:
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
    radar = create_radar_chart(scores, category_names, chart_w, CH_H,
                               ghost_scores=hist_scores if has_baseline else None,
                               window_days=window_days)
    donut_cats = [c for c in category_names if shares.get(c, 0) > 0]
    if donut_cats:
        donut = create_donut_chart(shares, donut_cats, category_names,
                                   chart_w, CH_H)
    else:
        donut = create_blank_chart(chart_w, CH_H,
                                   "本期没有活动记录" if has_baseline
                                   else "暂无活动记录")

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
        'radar_title': (f"{period_tag}强度 · 雷达图" if has_baseline
                        else "生涯强度 · 雷达图"),
        'donut': donut,
        'donut_title': (f"{period_tag}精力构成 · 环形图" if has_baseline
                        else "精力构成 · 环形图"),
        'display_categories': display_categories,
        'now_str': " · ".join(s for s in (time.strftime("%Y/%m/%d  %H:%M:%S"),
                                          note) if s),
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
