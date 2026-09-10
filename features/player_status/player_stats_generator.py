# features/player_stats/player_stats_generator.py

import os
import time
import asyncio
import aiohttp
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Any, Optional, List
from ...utils.avatar_cache import download_avatar
from ...config.whitelist_config import WhitelistManager
from ...utils.text_renderer import draw_text_with_emoji, measure_text_with_emoji

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
CARD_PADDING_TOP = 20
CARD_PADDING_SIDE = 28
CARD_PADDING_BOTTOM = 20
CARD_MARGIN_BOTTOM = 24

STATUS_DOT_ONLINE = '#5cb85c'
STATUS_DOT_OFFLINE = '#d9534f'

PLAYER_NAME_COLOR = '#333333'
PLAYER_NAME_SIZE = 28
PLAYER_ID_COLOR = '#aaaaaa'
PLAYER_ID_SIZE = 14
PLAYER_SUMMARY_COLOR = '#888888'
PLAYER_SUMMARY_SIZE = 14

CHART_BG = '#FFFFFF'
CHART_RADIUS = 16

STAT_LABEL_COLOR = '#999999'
STAT_LABEL_SIZE = 13
STAT_VALUE_COLOR = '#333333'
STAT_VALUE_SIZE = 22

TIME_TEXT_COLOR = '#aaaaaa'
TIME_TEXT_SIZE = 16
TIME_MARGIN_TOP = 24

# 图表卡片
CHART_CARD_PADDING = 12
CHART_TITLE_SIZE = 14
CHART_HEIGHT = 180
CHART_WIDTH = (CONTAINER_WIDTH - 2 * CONTAINER_PADDING - 24) // 2  # 左右各一个，间距24

# 分类颜色（用于环形图和雷达图）
CATEGORY_COLORS = ['#5b6abf', '#7BA87F', '#D9A87C', '#C47D7D', '#6F8B9F', '#B8A9C9']

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

# ========== 辅助函数 ==========
def make_rounded_rect(img, size, radius):
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    result = img.resize((size, size), Image.LANCZOS).convert('RGBA')
    result.putalpha(mask)
    return result

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
    if unit:
        return f"{display} {unit}"
    return display

def calc_category_scores(data):
    """计算每个分类的得分（分类占比归一化）"""
    category_totals = {}
    grand_total = 0
    for cat_name, cat_info in CATEGORIES.items():
        total = 0
        for _, key, _ in cat_info['items']:
            total += data.get(key, 0)
        category_totals[cat_name] = total
        grand_total += total
    if grand_total == 0:
        return {cat: 0 for cat in CATEGORIES}
    scores = {}
    for cat in CATEGORIES:
        scores[cat] = category_totals[cat] / grand_total
    return scores

# ========== 配置 matplotlib 中文字体（解决中文乱码） ==========
def _setup_matplotlib_font():
    """设置 matplotlib 中文字体，支持 Windows / macOS / Linux"""
    try:
        plt.rcParams['font.sans-serif'] = [
            'Microsoft YaHei', 'SimHei', 'PingFang SC',
            'WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'DejaVu Sans'
        ]
        plt.rcParams['axes.unicode_minus'] = False
    except Exception:
        pass

# ========== 创建雷达图 ==========
def create_radar_chart(scores, category_names, width, height):
    """生成雷达图，返回 PIL Image"""
    _setup_matplotlib_font()  # 确保中文字体
    labels = category_names
    values = [scores[cat] for cat in labels]
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    values += values[:1]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(width/100, height/100), subplot_kw=dict(polar=True), dpi=100)
    ax.fill(angles, values, color='#5b6abf', alpha=0.2)
    ax.plot(angles, values, color='#5b6abf', linewidth=2)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9, color='#555')
    ax.set_ylim(0, 1)
    # 去除刻度标签（不显示百分比）
    ax.set_yticks([])
    ax.grid(color='#e0e0e0', linestyle='-', linewidth=0.5)
    ax.spines['polar'].set_visible(False)
    for r in np.linspace(0, 1, 6)[1:]:
        ax.add_patch(Circle((0,0), r, transform=ax.transData._b, fill=False, edgecolor='#e0e0e0'))
    plt.tight_layout(pad=0.5)
    buf = BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.05, facecolor='white')
    plt.close()
    buf.seek(0)
    return Image.open(buf)

# ========== 创建环形图 ==========
def create_donut_chart(scores, category_names, width, height, all_labels=None):
    """生成环形图，返回 PIL Image
    scores: 字典 {分类名: 得分}，用于绘制饼图
    category_names: 饼图对应的分类名列表（与 scores 键一致）
    width, height: 图表尺寸
    all_labels: 图例要显示的全部分类名列表（可选，默认为 category_names）
    """
    _setup_matplotlib_font()
    labels = category_names
    values = [scores[cat] for cat in labels]
    
    if all_labels is None:
        all_labels = labels
    # 构建分类到颜色的映射（基于全部分类顺序）
    color_map = {cat: CATEGORY_COLORS[i] for i, cat in enumerate(all_labels)}
    # 饼图扇区的颜色（按 labels 顺序）
    colors = [color_map[cat] for cat in labels]
    # 图例颜色（按 all_labels 顺序）
    legend_colors = [color_map[cat] for cat in all_labels]

    fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
    # 绘制饼图（只有非零分类）
    wedges, texts, autotexts = ax.pie(values, labels=None, colors=colors,
                                      startangle=90, pctdistance=0.85, autopct='',
                                      wedgeprops=dict(width=0.4, edgecolor='white'))
    # 手动创建图例（包含全部分类）
    from matplotlib.patches import Patch
    ax.set_yticks([])
    legend_patches = [Patch(color=legend_colors[i], label=all_labels[i]) for i in range(len(all_labels))]
    # 图例放在右侧，竖排（一列）
    ax.legend(handles=legend_patches, loc='center left', bbox_to_anchor=(1.02, 0.5),
              fontsize=10, ncol=1, frameon=False)
    # 调整布局，为右侧图例留出空间，饼图自动左移
    fig.subplots_adjust(left=0.05, right=0.75, bottom=0.05, top=0.95)
    plt.tight_layout(pad=0.5)
    buf = BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.05, facecolor='white')
    plt.close()
    buf.seek(0)
    return Image.open(buf)

# ========== 主绘图函数 ==========
async def draw_player_stats_image(
    stats_data: Dict[str, Any],
    player_name: str,
    server_name: str = None,
    is_online: bool = True,
    avatar_img: Optional[Image.Image] = None,
    show_server_label: bool = True
) -> Image.Image:
    """
    生成玩家统计图片（新版：雷达图+环形图+分类数据）
    """
    # ===== 提取数据 =====
    playtime = stats_data.get('playTimeFormatted', '0分钟')
    deaths = stats_data.get('deaths', 0)
    mobKills = stats_data.get('mobKills', 0)
    uuid_suffix = stats_data.get('uuidSuffix', '')

    # 计算分类得分
    scores = calc_category_scores(stats_data)
    category_names = list(CATEGORIES.keys())
    category_values = [scores[cat] for cat in category_names]

    # ===== 计算布局 =====
    content_width = CONTAINER_WIDTH - 2 * CONTAINER_PADDING
    avatar_size = 72
    avatar_radius = 12
    player_card_height = max(avatar_size + 10, 50) + 20
    chart_height = CHART_HEIGHT
    chart_card_height = chart_height + 40  # 标题+内边距

    # 详细数据卡片区域高度：根据实际数据动态计算
    display_categories = []
    for cat in category_names:
        items = []
        for label, key, unit in CATEGORIES[cat]['items']:
            val = stats_data.get(key, 0)
            if val != 0:
                items.append((label, key, unit, val))
        if items:
            display_categories.append((cat, items))

    # 重新计算详细数据布局（仿HTML网格）
    cols = 3
    detail_padding = 16
    gap = 16
    # 白色卡片内部可用宽度
    inner_width = CONTAINER_WIDTH - 2 * CONTAINER_PADDING - 2 * detail_padding
    col_width = (inner_width - (cols - 1) * gap) // cols  # 整数除法
    # 计算每行高度：根据最大子项数量
    row_height = 35  # 标签+数值+间距
    row_gap = 4
    card_padding = 12
    title_height = 24
    card_heights = []
    for cat, items in display_categories:
        rows = (len(items) + 1) // 2
        h = title_height + rows * (row_height + row_gap) + card_padding * 2
        card_heights.append(h)
    detail_card_height = max(card_heights) if card_heights else 60
    total_rows = (len(display_categories) + cols - 1) // cols
    # 总内容高度 = 行数 * (卡片高度 + 行间距) - 行间距
    row_spacing = 12
    total_height_detail = total_rows * (detail_card_height + row_spacing) - row_spacing
    total_height_detail += detail_padding * 2  # 加上白色卡片内边距

    # 总高度 = 标题 + 玩家卡 + 图表行 + 详细数据行 + 底部时间
    total_height = (
        CONTAINER_PADDING
        + PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM
        + player_card_height + 24
        + chart_card_height + 24
        + total_height_detail + 24  # 额外上下内边距
        + TIME_TEXT_SIZE + TIME_MARGIN_TOP
        + CONTAINER_PADDING
    )

    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)

    # 容器圆角背景
    draw.rounded_rectangle(
        [(0, 0), (CONTAINER_WIDTH, total_height)],
        radius=CONTAINER_RADIUS,
        fill=CONTAINER_BG
    )

    y = CONTAINER_PADDING

    # ---- 标题 ----
    title_text = "玩家数据统计"
    title_font = get_font(PAGE_TITLE_SIZE, bold=True)
    title_w = draw.textbbox((0, 0), title_text, font=title_font)[2]
    title_x = (CONTAINER_WIDTH - title_w) // 2
    draw.text((title_x, y), title_text, fill=PAGE_TITLE_COLOR, font=title_font)
    y += PAGE_TITLE_SIZE + PAGE_TITLE_MARGIN_BOTTOM

    # ---- 玩家信息卡 ----
    card_x0 = CONTAINER_PADDING
    card_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
    card_y0 = y
    card_y1 = y + player_card_height
    draw.rounded_rectangle((card_x0, card_y0, card_x1, card_y1), radius=CARD_RADIUS, fill=CARD_BG)

    # 头像
    if avatar_img is None:
        avatar_img = Image.new('RGBA', (avatar_size, avatar_size), (176, 176, 176, 255))
        draw_avatar = ImageDraw.Draw(avatar_img)
        draw_avatar.ellipse((avatar_size*0.25, avatar_size*0.2, avatar_size*0.75, avatar_size*0.7), fill=(136,136,136,255))
        draw_avatar.rectangle((avatar_size*0.25, avatar_size*0.65, avatar_size*0.75, avatar_size*0.95), fill=(136,136,136,255))
    else:
        avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.LANCZOS)
    avatar_rounded = make_rounded_rect(avatar_img, avatar_size, avatar_radius)
    avatar_x = card_x0 + CARD_PADDING_SIDE
    avatar_y = card_y0 + (player_card_height - avatar_size) // 2
    img.paste(avatar_rounded, (avatar_x, avatar_y), avatar_rounded)

    # 在线状态指示器
    dot_size = 18
    dot_x = avatar_x + avatar_size - dot_size + 4
    dot_y = avatar_y + avatar_size - dot_size + 4
    dot_color = STATUS_DOT_ONLINE if is_online else STATUS_DOT_OFFLINE
    draw.ellipse((dot_x, dot_y, dot_x+dot_size, dot_y+dot_size), fill=dot_color, outline='white', width=2)

    # 玩家信息
    info_x = avatar_x + avatar_size + 24
    info_y = card_y0 + (player_card_height - 50) // 2

    # 名称（可能含 emoji）
    name_font = get_font(PLAYER_NAME_SIZE, bold=True)
    name_end_x = draw_text_with_emoji(
        img, draw, (info_x, info_y),
        player_name,
        name_font, PLAYER_NAME_COLOR,
        emoji_scale=0.9
    )

    # UUID后缀（通常无 emoji，但用新函数统一处理更稳）
    if uuid_suffix:
        suffix_x = name_end_x + 10
        suffix_y = info_y + (PLAYER_NAME_SIZE - 14) // 2
        draw_text_with_emoji(
            img, draw, (suffix_x, suffix_y),
            f"#{uuid_suffix}",
            get_font(14, bold=False), PLAYER_ID_COLOR,
            emoji_scale=0.9
        )

    # 摘要信息（含 emoji）
    summary_y = info_y + PLAYER_NAME_SIZE + 6
    summary_font = get_font(PLAYER_SUMMARY_SIZE)
    summary_items = [
        f"🗡️ 击杀 {mobKills}",
        f"💀 死亡 {deaths}",
        f"⏱️ 在线 {playtime}"
    ]
    summary_spacing = 20
    curr_x = info_x
    for item in summary_items:
        next_x = draw_text_with_emoji(
            img, draw, (curr_x, summary_y),
            item,
            summary_font, PLAYER_SUMMARY_COLOR,
            emoji_scale=1.0
        )
        curr_x = next_x + summary_spacing

    # 服务器名称（右上角）
    if server_name and show_server_label:
        server_text = f"服务器: {server_name}"
        server_font = get_font(14, bold=False)
        server_color = '#888888'
        tw = draw.textbbox((0,0), server_text, font=server_font)[2]
        server_x = card_x1 - CARD_PADDING_SIDE - tw
        server_y = card_y0 + CARD_PADDING_TOP
        draw.text((server_x, server_y), server_text, fill=server_color, font=server_font)

    y += player_card_height + 24

    # ---- 图表区域（雷达图 + 环形图） ----
    chart_w = CHART_WIDTH
    chart_h = CHART_HEIGHT

    # 过滤出得分>0的分类用于环形图（仅去除图表中的零值分类）
    donut_categories = [cat for cat in category_names if scores[cat] > 0]
    donut_scores = {cat: scores[cat] for cat in donut_categories}  # 字典

    # 生成雷达图
    radar_img = create_radar_chart(scores, category_names, chart_w, chart_h)
    # 生成环形图（饼图只显示非零分类，图例显示全部分类）
    donut_img = create_donut_chart(donut_scores, donut_categories, chart_w, chart_h, all_labels=category_names)

    chart_card_h = chart_h + 40  # 标题+内边距
    for i, chart_img in enumerate([radar_img, donut_img]):
        cx0 = CONTAINER_PADDING + i * (chart_w + 24)
        cx1 = cx0 + chart_w
        cy0 = y
        cy1 = y + chart_card_h
        draw.rounded_rectangle((cx0, cy0, cx1, cy1), radius=CARD_RADIUS, fill=CARD_BG)
        title = "能力分布 · 雷达图" if i == 0 else "分类占比 · 环形图"
        tw = draw.textbbox((0,0), title, font=get_font(CHART_TITLE_SIZE, bold=True))[2]
        draw.text((cx0 + (chart_w - tw)//2, cy0 + 6), title, fill='#444444', font=get_font(CHART_TITLE_SIZE, bold=True))
        img_w, img_h = chart_img.size
        paste_x = cx0 + (chart_w - img_w) // 2
        paste_y = cy0 + 28
        img.paste(chart_img, (paste_x, paste_y), chart_img)

    y += chart_card_h + 24

    # ---- 详细数据卡片 ----
    if display_categories:
        # 白色背景卡片
        detail_x0 = CONTAINER_PADDING
        detail_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
        detail_y0 = y
        detail_y1 = y + total_height_detail
        draw.rounded_rectangle((detail_x0, detail_y0, detail_x1, detail_y1), radius=CARD_RADIUS, fill=CARD_BG)

        # 绘制每个灰色卡片（三列网格）
        for idx, (cat, items) in enumerate(display_categories):
            row = idx // cols
            col = idx % cols
            # 计算灰色卡片位置
            cx0 = detail_x0 + detail_padding + col * (col_width + gap)
            cx1 = cx0 + col_width
            cy0 = detail_y0 + detail_padding + row * (detail_card_height + row_spacing)
            cy1 = cy0 + detail_card_height

            # 灰色背景卡片（圆角）
            draw.rounded_rectangle((cx0, cy0, cx1, cy1), radius=12, fill='#f6f6f5')

            # 标题
            title_font = get_font(14, bold=True)
            draw.text((cx0 + 12, cy0 + 8), cat, fill='#5b6abf', font=title_font)

            # 子项网格（两列）
            x_start = cx0 + 12
            y_start = cy0 + 30
            col_w = (cx1 - cx0 - 24) // 2
            for i, (label, key, unit, val) in enumerate(items):
                row_item = i // 2
                col_item = i % 2
                x = x_start + col_item * (col_w + 8)
                y = y_start + row_item * (row_height + row_gap)
                draw.text((x, y), label, fill='#888888', font=get_font(11, bold=False))
                display_val = format_value(val, unit)
                draw.text((x, y + 14), display_val, fill='#333333', font=get_font(16, bold=True))
    else:
        # 无数据时的简化处理
        detail_x0 = CONTAINER_PADDING
        detail_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
        detail_y0 = y
        detail_y1 = y + 60
        draw.rounded_rectangle((detail_x0, detail_y0, detail_x1, detail_y1), radius=CARD_RADIUS, fill=CARD_BG)
        draw.text((CONTAINER_WIDTH//2, y+20), "暂无详细数据", fill='#999999', font=get_font(18, bold=False), anchor="mm")
        y = detail_y1

    # 更新 y 到详细数据之后
    if display_categories:
        y = detail_y1 + 24
    else:
        y = detail_y1 + 24

    # ---- 底部时间 ----
    now_str = time.strftime("%Y/%m/%d  %H:%M:%S")
    time_font = get_font(TIME_TEXT_SIZE, bold=False)
    time_w = draw.textbbox((0,0), now_str, font=time_font)[2]
    time_x = (CONTAINER_WIDTH - time_w) // 2
    draw.text((time_x, y), now_str, fill=TIME_TEXT_COLOR, font=time_font)

    return img