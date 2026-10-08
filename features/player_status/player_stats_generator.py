"""玩家数据统计图片生成器（/查询、/mc stats）

复用 help_image 引擎：延迟 ops 布局 + 2x 超采样渲染；
雷达图/环形图由 Pillow 单独绘制，按 2x 像素输出后经 'paste' 专有 op 贴入。
"""

import math
import time
from datetime import date, timedelta
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

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

CATEGORY_COLORS = ['#b9c1ea', '#a9d8bc', '#ecd7ac', '#e9b9bd', '#b3d6de',
                   '#d3c7e6']

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
    """本期实际游玩小时 = playTime 差值，不夹下限（0 就是 0，调用方要据此判"没上线"）。
    拿不到 playTime 返回 None，让调用方退回生涯口径而不是除以 1 小时把分数放大 200 倍。"""
    try:
        cur = float(current.get('playTime', 0) or 0)
        base = float((baseline or {}).get('playTime', 0) or 0)
    except (TypeError, ValueError):
        return None
    if cur <= 0:
        return None
    return max(0.0, (cur - base) / TICKS_PER_HOUR)


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


# ========== 本期 / 生涯 两态口径 ==========
WINDOW_DAYS = 7                 # 本期跨度：固定 7 个日历天
BASELINE_TOLERANCE_DAYS = 2     # 采样缺天时允许往前多找 2 天
COMPARE_MIN_TOTAL_HOURS = 20.0  # 累计不足 20 小时不做对比：本期就是他玩过的全部
COMPARE_MIN_PRIOR_HOURS = 10.0  # 基线前不足 10 小时＝拿近乎为空的历史跟自己比，无意义
TINY_ACCOUNT_HOURS = 1.0        # 累计不足 1 小时连图都不画：分母太小会把每项顶到满分


class WindowPlan(NamedTuple):
    mode: str                        # 'window' 本期增量 / 'career' 生涯累计
    day_from: str                    # 本期起点＝基线那天，career 时为空
    day_to: str
    span_days: int
    data: Dict[str, float]           # 打分用的原始值：本期增量或生涯累计
    hours: float                     # 打分分母（游玩小时）
    raw_hours: float                 # 未夹下限的本期游玩小时
    scores: Dict[str, float]
    hist_scores: Dict[str, float]    # 生涯强度，本期态下作虚影
    reason: str                      # 落到生涯的来路，决定底部那句说明


def _career_plan(current, hist_scores, reason):
    hours = play_hours(current)
    return WindowPlan('career', '', '', 0, current, hours, 0.0,
                      hist_scores, hist_scores, reason)


def resolve_window(current, snapshots) -> WindowPlan:
    """按日历天挑基线，决定这张图走本期增量还是生涯累计。

    快照由每日采样按日历落盘，所以"7 天"是真实历；挑中之后还要两段都够长，
    否则本期等于全部历史，画出来是自己跟自己重合，不如老实退回生涯。
    """
    hist_scores = calc_category_scores(current)
    today = date.today()
    target = today - timedelta(days=WINDOW_DAYS)
    floor = target - timedelta(days=BASELINE_TOLERANCE_DAYS)

    baseline = None
    for day, stats in reversed(snapshots or []):   # 升序列表，取 ≤target 的最新一份
        try:
            d = date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        if d > target:
            continue
        if d >= floor:
            baseline = (day, stats)
        break
    if not baseline:
        return _career_plan(current, hist_scores, 'no_baseline')

    total_h = play_hours(current)
    if total_h < COMPARE_MIN_TOTAL_HOURS:
        return _career_plan(current, hist_scores, 'small_total')
    if play_hours(baseline[1]) < COMPARE_MIN_PRIOR_HOURS:
        return _career_plan(current, hist_scores, 'thin_history')

    raw_h = window_hours(current, baseline[1])
    if raw_h is None:
        return _career_plan(current, hist_scores, 'no_playtime')
    if raw_h <= 0:
        return _career_plan(current, hist_scores, 'idle')

    data = delta_stats(current, baseline[1])
    scores = calc_category_scores(data, hours=max(MIN_WINDOW_HOURS, raw_h))
    if sum(scores.values()) <= 0:
        # 在线挂着但一项玩法统计都没动，本期画出来是圆心一个点——不如画生涯
        return _career_plan(current, hist_scores, 'idle')

    return WindowPlan('window', baseline[0], _day_str(today),
                      (today - date.fromisoformat(baseline[0])).days,
                      data, max(MIN_WINDOW_HOURS, raw_h), raw_h,
                      scores, hist_scores, '')


def _day_str(d: date) -> str:
    return d.strftime('%Y-%m-%d')


# ========== Pillow 图表（不依赖 matplotlib，避免 AstrBot 双份 numpy/PIL 副本原生崩溃） ==========
SS = 3                  # 相对设计单位的额外超采样，落图后下采样到 SCALE 得到抗锯齿
# —— 雷达图（2026-10-06 定案：保持线框本体、不加填充，线按相邻分类渐变上色，
#    下面垫一层同色扩散阴影；冷调网格与环形图的粉彩配成一族）——
RADAR_GRID = '#e3e7f5'          # 轴线
RADAR_RING = '#eef0f9'          # 层级环
RADAR_FACET = ('#f5f6fc', '#eff1fa')   # 底面分瓣交替色
RADAR_GRID_W = 0.7              # 网格线宽（设计像素）
RADAR_LINE_W = 1.8              # 主线宽
RADAR_GHOST_W = 0.9             # 生涯平均虚线宽
RADAR_DEPTH = 0.40              # 粉彩压深幅度：白卡上要撑得住扩散阴影才敢更浅
RADAR_EDGE_SEGS = 16            # 每条边的渐变插值段数（6 边 × 16 = 96 段）
RADAR_GLOW_R = 9.0              # 扩散阴影半径
RADAR_GLOW_ALPHA = 0.35         # 扩散阴影浓度
RADAR_GLOW_SPREAD = 0.8         # 阴影描边比主线宽出 半径×此值
RADAR_GLOW_BLUR = 0.55          # 高斯 σ = 半径×此值（与样片 feGaussianBlur 同式）
RADAR_DOT_R = 2.5               # 轴端圆点半径（0 分轴不画）
RADAR_LBL = '#555555'
RADAR_LBL_SIZE = 12
DONUT_THICK = 0.56      # 带厚 / 外径：内圈半径 = 外径 × (1 − 此值)
DONUT_CORNER = 0.12     # 端头圆角 = 该段带厚 × 此系数
DONUT_GAP = 2.0         # 缝宽（设计像素），内圈到外圈处处等宽
DONUT_JITTER = 0.34     # 外径跳动幅度：最短段外径 = 外径 × (1 − 此值)
DONUT_OUTER = [1.0, 0.86, 0.93, 0.80, 0.87, 0.62]   # 按分类序号取外径系数
DONUT_LABEL_SIZES = (11, 10, 9)   # 带内百分比字号，最低 9px 保证手机上可读
LEGEND_SIZE = 12
LEGEND_FG = '#555555'
RADAR_GHOST = '#a9aecb'     # 生涯平均轮廓：冷灰蓝虚线，与渐变主线区分
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


def _hex_rgb(color):
    n = int(color[1:], 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def _deepen(color, depth):
    """HSL 空间加饱和、压亮度：粉彩原色（亮度 195~216）在白卡上撑不起扩散阴影"""
    r, g, b = (v / 255 for v in _hex_rgb(color))
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    l = (mx + mn) / 2
    if d:
        if mx == r:
            h = (g - b) / d + (6 if g < b else 0)
        elif mx == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        h *= 60
        s = d / (1 - abs(2 * l - 1))
    else:
        h, s = 0.0, 0.0
    s = min(1.0, s + depth * 0.30)
    l = max(0.0, min(1.0, l - depth * 0.24))
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs(h / 60 % 2 - 1))
    m = l - c / 2
    rgb = [(c, x, 0), (x, c, 0), (0, c, x),
           (0, x, c), (x, 0, c), (c, 0, x)][min(5, int(h // 60))]
    return '#%02x%02x%02x' % tuple(round((v + m) * 255) for v in rgb)


def _mix(c1, c2, t):
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    return '#%02x%02x%02x' % tuple(int(round(a[i] + (b[i] - a[i]) * t))
                                   for i in range(3))


def _glow_under(img, segs, width, sigma, alpha, dy):
    """把彩色扩散阴影垫在主线下方。

    先按"预乘 alpha"卷积再合成：直接模糊 RGBA 会让透明区的黑底渗进颜色，
    光晕发灰；SVG 的 feGaussianBlur 本来就是预乘口径，这样才对得上样片。
    只在分段的外接框里做（含 3σ 拖尾），否则整张画布的高斯模糊白烧几十毫秒。
    """
    if sigma <= 0 or alpha <= 0:
        return
    pad = width / 2 + 3 * sigma + 2
    flat = [p for a, b, _ in segs for p in ((a[0], a[1] + dy), (b[0], b[1] + dy))]
    x0 = min(p[0] for p in flat)
    y0 = min(p[1] for p in flat)
    x1 = max(p[0] for p in flat)
    y1 = max(p[1] for p in flat)
    box = (max(0, int(math.floor(x0 - pad))), max(0, int(math.floor(y0 - pad))),
           min(img.width, int(math.ceil(x1 + pad))),
           min(img.height, int(math.ceil(y1 + pad))))
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    size = (box[2] - box[0], box[3] - box[1])
    blur = ImageFilter.GaussianBlur(radius=max(1, int(round(sigma))))
    premult = Image.new('RGB', size, (0, 0, 0))
    cover = Image.new('L', size, 0)
    pd, cd = ImageDraw.Draw(premult), ImageDraw.Draw(cover)
    for a, b, col in segs:
        line = (a[0] - box[0], a[1] - box[1] + dy,
                b[0] - box[0], b[1] - box[1] + dy)
        pd.line(line, fill=col, width=width)
        cd.line(line, fill=255, width=width)
    premult = premult.filter(blur).point(lambda v: int(v * alpha))
    cover = cover.filter(blur).point(lambda v: int(v * alpha))
    base = img.crop(box).convert('RGB')
    dim = ImageChops.multiply(base, ImageChops.invert(cover).convert('RGB'))
    img.paste(ImageChops.add(dim, premult), box)


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


def create_radar_chart(scores, category_names, w, h, ghost_scores=None):
    """生成雷达图（w/h 为设计像素），返回 2x 像素 PIL Image。
    线框本体不加填充：每条边按相邻两个分类的颜色渐变，主线下方垫一层同色扩散阴影。
    ghost_scores 给出时叠加一层冷灰蓝虚线生涯平均轮廓（只有本期态会给）。"""
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

    # 底面分瓣 + 层级六边形 + 轴线：浅冷色，只当"宝石切面"用，不参与读数
    for k in range(1, 6):
        r0, r1 = r * (k - 1) / 5, r * k / 5
        for i in range(n):
            j = (i + 1) % n
            quad = ([(cx, cy), vertex(i, r1), vertex(j, r1)] if k == 1 else
                    [vertex(i, r0), vertex(i, r1), vertex(j, r1), vertex(j, r0)])
            draw.polygon(quad,
                         fill=RADAR_FACET[0] if (k + i) % 2 else RADAR_FACET[1])
    gw = max(1, int(round(RADAR_GRID_W * SS)))
    for k in range(1, 6):
        rr = r * k / 5
        draw.polygon([vertex(i, rr) for i in range(n)],
                     outline=RADAR_RING, width=gw)
    for i in range(n):
        draw.line([cx, cy, *vertex(i, r)], fill=RADAR_GRID, width=gw)

    ghost_w = max(1, int(round(RADAR_GHOST_W * SS)))
    if ghost_scores:
        _dash_path(draw, shape(ghost_scores), GHOST_DASH * SS, GHOST_GAP * SS,
                   RADAR_GHOST, ghost_w)

    pts = shape(scores)
    cols = [_deepen(CATEGORY_COLORS[i % len(CATEGORY_COLORS)], RADAR_DEPTH)
            for i in range(n)]
    segs = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for k in range(RADAR_EDGE_SEGS):
            t0, t1 = k / RADAR_EDGE_SEGS, (k + 1) / RADAR_EDGE_SEGS
            segs.append(((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                         (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1),
                         _mix(cols[i], cols[(i + 1) % n], t0)))

    main_w = max(1, int(round(RADAR_LINE_W * SS)))
    glow_w = max(1, int(round((RADAR_LINE_W + RADAR_GLOW_R * RADAR_GLOW_SPREAD)
                              * SS)))
    _glow_under(img, segs, glow_w, RADAR_GLOW_R * RADAR_GLOW_BLUR * SS,
                RADAR_GLOW_ALPHA, 0.0)
    draw = ImageDraw.Draw(img)
    for a, b, col in segs:
        draw.line([a[0], a[1], b[0], b[1]], fill=col, width=main_w)
    for i, p in enumerate(pts):
        # 分段线是平头，相邻两边颜色相同、只在顶点处咬出缺口 ⇒ 补一个同色圆头
        rad = main_w / 2
        draw.ellipse([p[0] - rad, p[1] - rad, p[0] + rad, p[1] + rad],
                     fill=cols[i])
    if RADAR_DOT_R > 0:
        for i, p in enumerate(pts):
            if scores.get(category_names[i], 0) <= 0:
                continue
            for rr, cc in ((RADAR_DOT_R * SS + 0.5 * SS, '#ffffff'),
                           (RADAR_DOT_R * SS, cols[i])):
                draw.ellipse([p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr],
                             fill=cc)

    # 刻度数字已去掉：分瓣层本身已给出 5 级等距台阶，外置「分类名+分值」才是读数处

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
        rows = [("本期", True), ("生涯平均", False)]
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
                           RADAR_GHOST, ghost_w, closed=False)
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


def _polar(cx, cy, r, ang):
    return (cx + r * math.cos(ang * math.pi / 180),
            cy + r * math.sin(ang * math.pi / 180))


def _sweep(out, cx, cy, r, a_from, delta, step):
    """沿**指定的带符号角度差**取样弧线。不能用最短弧：单段可以超过 180°。"""
    n = max(1, int(abs(delta) / step))
    for k in range(1, n + 1):
        out.append(_polar(cx, cy, r, a_from + delta * k / n))


def _petal_outline(cx, cy, ri, ro, a0, a1, rho, half_gap, step=1.0):
    """圆角花瓣轮廓。两条侧边不是半径线，而是「平行于各自边界半径、距其 half_gap」
    的直线，所以同一条缝从内圈到外圈宽度恒等于 2*half_gap。
    返回 (轮廓点, 首边界角, 末边界角)。"""
    d2r = math.pi / 180
    r0, r1 = ri + rho, ro - rho
    w = rho + half_gap
    cons = math.degrees(math.asin(min(1.0, w / r0)))
    ex = math.degrees(math.asin(min(1.0, w / r1)))
    b0, b1 = a0 - cons, a1 + cons
    a0o, a1o = b0 + ex, b1 - ex
    DI, DT = _polar(cx, cy, r0, a1), _polar(cx, cy, r1, a1o)
    AI, AT = _polar(cx, cy, r0, a0), _polar(cx, cy, r1, a0o)

    def normal(b):
        return (-math.sin(b * d2r), math.cos(b * d2r))

    d1, d0 = normal(b1), normal(b0)
    pts = []
    _sweep(pts, cx, cy, ri, a0, a1 - a0, step)                    # 内弧
    _sweep(pts, DI[0], DI[1], rho, a1 + 180, cons - 90, step)     # 内-末圆角
    pts.append((DI[0] + rho * d1[0], DI[1] + rho * d1[1]))        # 末侧边
    pts.append((DT[0] + rho * d1[0], DT[1] + rho * d1[1]))
    _sweep(pts, DT[0], DT[1], rho, b1 + 90, -(90 + ex), step)     # 外-末圆角
    _sweep(pts, cx, cy, ro, a1o, a0o - a1o, step)                 # 外弧
    _sweep(pts, AT[0], AT[1], rho, a0o, -(90 + ex), step)         # 外-首圆角
    pts.append((AT[0] - rho * d0[0], AT[1] - rho * d0[1]))        # 首侧边
    pts.append((AI[0] - rho * d0[0], AI[1] - rho * d0[1]))
    _sweep(pts, AI[0], AI[1], rho, b0 - 90, cons - 90, step)      # 内-首圆角
    return pts, b0, b1


def _donut_segments(shares, ri, r_max, half_gap):
    """排布花瓣：百分比为 0 的分类既不画、也不占角隙。
    shares 为 {分类: 百分比}（按 all_labels 顺序），角度以 12 点为起点顺时针。
    返回 [(分类, 外径, 圆角, 起始角, 终止角)]。"""
    ro, rho, cons = {}, {}, {}
    for i, c in enumerate(shares):
        outer = r_max * (1 - DONUT_JITTER *
                         (1 - DONUT_OUTER[i % len(DONUT_OUTER)]))
        band = max(2.0, outer - ri)
        corner = min(band * DONUT_CORNER, band / 2 - 0.5)
        ro[c], rho[c] = outer, corner
        cons[c] = math.degrees(math.asin(
            min(1.0, (corner + half_gap) / (ri + corner))))
    act = [c for c in shares if shares[c] > 0]
    if len(act) < 2:          # 只有一个非零 → 整圈，无缝也无跳动
        return [(c, r_max, rho[c], -90.0, 270.0) for c in act]
    m = len(act)
    gaps = [cons[act[k]] + cons[act[(k + 1) % m]] for k in range(m)]
    budget = max(0.0, 360.0 - sum(gaps))
    total = sum(shares[c] for c in act) or 1.0
    segs, acc = [], -90.0
    for k, c in enumerate(act):
        span = budget * shares[c] / total
        segs.append((c, ro[c], rho[c], acc, acc + span))
        acc += span + gaps[k]
    return segs


def _donut_label(draw, txt, room, band):
    """带内百分比：先按切向逐档缩字号，全放不下就改成沿半径方向（径向空间＝带厚）。
    room/band 均为超采样像素。"""
    for sz in DONUT_LABEL_SIZES:
        if room > draw.textlength(txt, font=_font(sz * SS)) + 3 * SS:
            return sz, False
    for sz in DONUT_LABEL_SIZES:
        if band > draw.textlength(txt, font=_font(sz * SS)) + 3 * SS:
            return sz, True
    return DONUT_LABEL_SIZES[-1], True


def _paste_rotated_text(img, draw, x, y, txt, sz, ang, radial):
    """把标签写在环带上（坐标为超采样像素）；切向字随半径旋转，倒置时翻 180°"""
    f = _font(sz * SS)
    tw = int(draw.textlength(txt, font=f))
    tile = Image.new('RGBA', (tw + 8 * SS, int(sz * SS + 10 * SS)), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((4 * SS, 5 * SS), txt, font=f,
                              fill=(255, 255, 255, 255))
    rot = ang if radial else ang + 90
    phi = (rot % 360 + 360) % 360
    if 90 < phi < 270:
        rot += 180
    tile = tile.rotate(-rot, expand=True, resample=Image.BICUBIC)
    img.paste(tile, (int(x - tile.width / 2), int(y - tile.height / 2)), tile)


def create_donut_chart(shares, donut_cats, all_labels, w, h):
    """生成环形图（圆角花瓣 + 等宽缝，图例含全部分类+百分比），2x 像素"""
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

    # 带内数字与图例同源：取整后为 0% 的分类不画，环上不会出现零宽花瓣
    plan = {c: (pct[c] if c in donut_cats else 0) for c in all_labels}
    ri = r * (1 - DONUT_THICK)
    half_gap = DONUT_GAP * SS / 2
    for c, outer, corner, a0, a1 in _donut_segments(plan, ri, r, half_gap):
        label = f"{pct[c]}%"
        if a1 - a0 >= 359.9:      # 只有一个非零分类 → 整圈
            draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                         outline=color_map[c], width=int(round(r - ri)))
            _paste_rotated_text(img, draw, *_polar(cx, cy, (ri + r) / 2, -90),
                                label, DONUT_LABEL_SIZES[0], -90, False)
            continue
        pts, b0, b1 = _petal_outline(cx, cy, ri, outer, a0, a1, corner, half_gap)
        draw.polygon(pts, fill=color_map[c])
        mid_r = (ri + outer) / 2
        mouth = math.degrees(math.asin(min(1.0, half_gap / mid_r)))
        room = mid_r * (b1 - b0 - 2 * mouth) * math.pi / 180
        sz, radial = _donut_label(draw, label, room, outer - ri)
        ang = (a0 + a1) / 2
        _paste_rotated_text(img, draw, *_polar(cx, cy, mid_r, ang), label,
                            sz, ang, radial)

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
CAREER_NOTE = {
    'no_baseline': "还没有 7 天前的历史快照，两图按生涯累计",
    'small_total': f"累计游玩不足 {COMPARE_MIN_TOTAL_HOURS:.0f} 小时，两图按生涯累计",
    'thin_history': "7 天前几乎没有游玩记录，本期与生涯没有差别",
    'idle': "最近 7 天没有活动记录，两图按生涯累计",
    'no_playtime': "基线缺少游玩时长，两图按生涯累计",
}


def draw_player_stats_image(
    stats_data: Dict[str, Any],
    player_name: str,
    server_name: str = None,
    is_online: bool = True,
    avatar_img: Optional[Image.Image] = None,
    show_server_label: bool = True,
    snapshots: Optional[List[Tuple[str, Dict[str, float]]]] = None
) -> Image.Image:
    """生成玩家统计图片（雷达图+环形图+分类明细，2x 超采样）

    同步函数、纯 CPU 重活：调用方必须用 asyncio.to_thread 执行，
    直接在事件循环里调用会卡住整个后端。

    snapshots 为历史每日快照 [(day, 当日累计值)]；够对比条件时两图改画最近
    7 天增量（雷达叠一层生涯平均虚影作参照），否则一律画生涯累计。
    """
    category_names = list(CATEGORIES.keys())
    hours = play_hours(stats_data)
    tiny = hours < TINY_ACCOUNT_HOURS
    plan = resolve_window(stats_data, snapshots or [])
    is_window = plan.mode == 'window'
    scores = plan.scores
    period = (f"{plan.day_from[5:]} ~ {plan.day_to[5:]} " if is_window else "")

    # 环形分母 = 六类强度得分之和。得分已各自对过参考值、无量纲，跨类可比；
    # 换成"次数求和"会被 jump 这类高频被动事件吃掉（实测一项占 74%），所以不用次数。
    total_score = sum(scores.values())
    shares = {c: (scores[c] / total_score if total_score > 0 else 0.0)
              for c in scores}

    # 口径与样本说明只在底部说一句，不在图里到处贴警告
    if hours <= 0:
        note = "模组未提供游玩时长，按累计值折算"
    elif tiny:
        note = f"累计游玩仅 {hours:.1f} 小时，样本太小，两图暂不出"
    elif is_window and plan.raw_hours < MIN_WINDOW_HOURS:
        note = f"本期游玩不足 {MIN_WINDOW_HOURS:.0f} 小时，强度按下限折算"
    elif is_window and plan.hours < MIN_SAMPLE_HOURS:
        note = f"样本较少：本期游玩仅 {plan.hours:.1f} 小时"
    elif not is_window and plan.reason:
        note = CAREER_NOTE.get(plan.reason, "")
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
    if tiny:
        # 分母不足 1 小时时每项强度都会被放大到封顶，画出来像个肝帝，宁可不画
        radar = create_blank_chart(chart_w, CH_H, "游玩时长不足 1 小时，暂不出图")
        donut = create_blank_chart(chart_w, CH_H, "游玩时长不足 1 小时，暂不出图")
    else:
        radar = create_radar_chart(scores, category_names, chart_w, CH_H,
                                   ghost_scores=plan.hist_scores if is_window else None)
        donut_cats = [c for c in category_names if shares.get(c, 0) > 0]
        if donut_cats:
            donut = create_donut_chart(shares, donut_cats, category_names,
                                       chart_w, CH_H)
        else:
            donut = create_blank_chart(chart_w, CH_H, "暂无活动记录")

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
        'radar_title': (f"{period}强度 · 雷达图" if is_window
                        else "生涯强度 · 雷达图"),
        'donut': donut,
        'donut_title': (f"{period}精力构成 · 环形图" if is_window
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
