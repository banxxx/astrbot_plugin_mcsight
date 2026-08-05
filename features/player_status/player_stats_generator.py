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
from typing import Dict, Any, Optional
from ...utils.avatar_cache import download_avatar
from ...config.whitelist_config import WhitelistManager

# ========== 样式常量（与 HTML 设计一致） ==========
CONTAINER_WIDTH = 1080
CONTAINER_PADDING = 36
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 24

PAGE_TITLE_COLOR = '#5b6abf'
PAGE_TITLE_SIZE = 36

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

# ========== 字体加载（复用 image_generator 的字体加载） ==========
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

# 预加载字体（懒加载）
_font_cache = {}
def get_font(size, bold=False):
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = _load_font(size, bold)
    return _font_cache[key]

# ========== 辅助绘图函数 ==========
def create_avatar_from_url(session, player_name, size=72):
    """异步获取玩家头像，若无则返回默认占位图"""
    # 利用已有的 download_avatar 获取头像
    # 注意：download_avatar 返回的是 PIL Image，但我们需要的是圆形裁剪
    # 我们先获取头像，然后裁剪为圆形
    # 由于 download_avatar 是异步的，我们必须在异步上下文中调用
    # 这里先返回一个占位图，由调用者处理
    # 为了统一，我们在调用时传入 session 和 player_name
    pass

def crop_circle(img, size):
    """将图片裁剪为圆形"""
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    result = img.resize((size, size), Image.LANCZOS).convert('RGBA')
    result.putalpha(mask)
    return result

def create_radar_chart(labels, values, width=400, height=280):
    """生成雷达图（开方归一化）"""
    if not values or len(values) == 0:
        return Image.new('RGB', (width, height), 'white')
    sqrt_vals = np.sqrt(values)
    max_sqrt = max(sqrt_vals)
    if max_sqrt == 0:
        norm = [0] * len(values)
    else:
        norm = (sqrt_vals / max_sqrt).tolist()
    
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    norm += norm[:1]
    angles += angles[:1]
    
    fig, ax = plt.subplots(figsize=(width/100, height/100), subplot_kw=dict(polar=True), dpi=100)
    ax.fill(angles, norm, color='#5b6abf', alpha=0.2)
    ax.plot(angles, norm, color='#5b6abf', linewidth=2)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9, color='#555')
    ax.set_ylim(0, 1)
    ax.set_yticks([])  # 隐藏刻度标签
    # 修改为 '#e0e0e0'
    ax.grid(color='#e0e0e0', linestyle='-', linewidth=0.5)
    ax.spines['polar'].set_visible(False)
    # 设置圆环网格线
    for r in np.linspace(0, 1, 6)[1:]:
        ax.add_patch(Circle((0,0), r, transform=ax.transData._b, fill=False, edgecolor='#e0e0e0'))
    plt.tight_layout(pad=0.5)
    buf = BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.05, facecolor='white')
    plt.close()
    buf.seek(0)
    return Image.open(buf)

def create_donut_chart(categories_data, width=400, height=280):
    """生成环形图，categories_data: {'标签': 值, ...}"""
    if not categories_data:
        return Image.new('RGB', (width, height), 'white')
    labels = list(categories_data.keys())
    values = list(categories_data.values())
    colors = ['#5b6abf', '#7BA87F', '#D9A87C']
    
    fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
    wedges, texts, autotexts = ax.pie(values, labels=labels, colors=colors[:len(labels)],
                                      autopct='%1.1f%%', startangle=90, pctdistance=0.85,
                                      wedgeprops=dict(width=0.4, edgecolor='white'))
    for autotext in autotexts:
        autotext.set_color('#555')
        autotext.set_fontsize(9)
    ax.legend(wedges, labels, loc='lower center', bbox_to_anchor=(0.5, -0.15),
              fontsize=10, ncol=3, frameon=False)
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
    avatar_img: Optional[Image.Image] = None
) -> Image.Image:
    """
    生成玩家统计图片
    stats_data: 从服务端API获取的统计字典，包含：
        totalPlayTimeMinutes, deaths, mobKills, playerKills, jumps,
        fallDistanceMeters, villagerTrades, chestsOpened, damageDealt, fishCaught
    """
    # 提取数据
    playtime = stats_data.get('totalPlayTimeMinutes', 0)
    deaths = stats_data.get('deaths', 0)
    mobKills = stats_data.get('mobKills', 0)
    playerKills = stats_data.get('playerKills', 0)
    jumps = stats_data.get('jumps', 0)
    fall = stats_data.get('fallDistanceMeters', 0)
    villager = stats_data.get('villagerTrades', 0)
    chests = stats_data.get('chestsOpened', 0)
    damage = stats_data.get('damageDealt', 0)
    fish = stats_data.get('fishCaught', 0)
    
    # 构造雷达图数据（排除在线时长和死亡次数）
    radar_labels = ['击杀怪物', '击杀玩家', '跳跃次数', '摔落距离', '村民交易', '打开箱子', '造成伤害', '捕鱼数']
    radar_values = [mobKills, playerKills, jumps, fall, villager, chests, damage, fish]
    
    # 环形图分类汇总
    categories = {
        '战斗': mobKills + playerKills + damage,
        '探索': jumps + fall + fish,
        '生存': villager + chests + deaths
    }
    
    # 计算布局
    content_width = CONTAINER_WIDTH - 2 * CONTAINER_PADDING
    title_height = PAGE_TITLE_SIZE + 12
    avatar_size = 72
    player_card_height = max(avatar_size + 10, 50) + 20
    chart_height = 280
    stat_card_height = 60
    grid_rows = 2
    grid_gap = 16
    grid_height = grid_rows * stat_card_height + (grid_rows - 1) * grid_gap
    time_height = TIME_TEXT_SIZE + 16
    
    total_height = (
        CONTAINER_PADDING + title_height + 24 +
        player_card_height + 28 +
        chart_height + 28 +
        grid_height + 24 +
        time_height + CONTAINER_PADDING
    )
    
    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)
    y = CONTAINER_PADDING
    
    # ---- 标题 ----
    title_text = "玩家数据统计"
    title_w = draw.textbbox((0,0), title_text, font=get_font(PAGE_TITLE_SIZE, bold=True))[2]
    title_x = (CONTAINER_WIDTH - title_w) // 2
    draw.text((title_x, y), title_text, fill=PAGE_TITLE_COLOR, font=get_font(PAGE_TITLE_SIZE, bold=True))
    y += PAGE_TITLE_SIZE + 12
    
    # ---- 玩家信息卡 ----
    card_x0 = CONTAINER_PADDING
    card_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
    card_y0 = y
    card_y1 = y + player_card_height
    draw.rounded_rectangle((card_x0, card_y0, card_x1, card_y1), radius=CARD_RADIUS, fill=CARD_BG)
    
    # 头像
    if avatar_img is None:
        # 生成默认占位头像
        avatar_img = Image.new('RGBA', (avatar_size, avatar_size), (176, 176, 176, 255))
        draw_avatar = ImageDraw.Draw(avatar_img)
        draw_avatar.ellipse((avatar_size*0.25, avatar_size*0.2, avatar_size*0.75, avatar_size*0.7), fill=(136,136,136,255))
        draw_avatar.rectangle((avatar_size*0.25, avatar_size*0.65, avatar_size*0.75, avatar_size*0.95), fill=(136,136,136,255))
    else:
        avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.LANCZOS)
    # 裁剪为圆形
    avatar_circle = crop_circle(avatar_img, avatar_size)
    avatar_x = card_x0 + CARD_PADDING_SIDE
    avatar_y = card_y0 + (player_card_height - avatar_size) // 2
    img.paste(avatar_circle, (avatar_x, avatar_y), avatar_circle)
    
    # 在线状态指示器
    dot_size = 18
    dot_x = avatar_x + avatar_size - dot_size + 4
    dot_y = avatar_y + avatar_size - dot_size + 4
    dot_color = STATUS_DOT_ONLINE if is_online else STATUS_DOT_OFFLINE
    draw.ellipse((dot_x, dot_y, dot_x+dot_size, dot_y+dot_size), fill=dot_color, outline='white', width=2)
    
    # 玩家信息
    info_x = avatar_x + avatar_size + 24
    info_y = card_y0 + (player_card_height - 50) // 2
    # 名称
    name_text = player_name
    name_w = draw.textbbox((0,0), name_text, font=get_font(PLAYER_NAME_SIZE, bold=True))[2]
    draw.text((info_x, info_y), name_text, fill=PLAYER_NAME_COLOR, font=get_font(PLAYER_NAME_SIZE, bold=True))

    if server_name:
        # 在名称右侧显示服务器名称（灰色小字）
        server_text = f" # {server_name}"
        server_font = get_font(16, bold=False)  # 稍小字号
        server_color = '#888888'
        server_x = info_x + name_w + 10
        server_y = info_y + (PLAYER_NAME_SIZE - 16) // 2  # 垂直居中
        draw.text((server_x, server_y), server_text, fill=server_color, font=server_font)

    # UUID后缀（暂时省略，因为没有uuid）
    # 摘要信息
    summary_y = info_y + PLAYER_NAME_SIZE + 6
    summary_items = [
        f"⚔ 击杀 {mobKills}",
        f"💀 死亡 {deaths}",
        f"⏱ 在线 {playtime} 分钟"
    ]
    summary_spacing = 20
    curr_x = info_x
    for item in summary_items:
        draw.text((curr_x, summary_y), item, fill=PLAYER_SUMMARY_COLOR, font=get_font(PLAYER_SUMMARY_SIZE))
        curr_x += draw.textbbox((0,0), item, font=get_font(PLAYER_SUMMARY_SIZE))[2] + summary_spacing
    
    y += player_card_height + 28
    
    # ---- 图表区域 ----
    chart_w = (CONTAINER_WIDTH - 2 * CONTAINER_PADDING - 24) // 2
    # 生成雷达图
    radar_img = create_radar_chart(radar_labels, radar_values, chart_w, chart_height)
    # 生成环形图
    donut_img = create_donut_chart(categories, chart_w, chart_height)
    
    chart_card_h = chart_height + 50
    for i, chart_img in enumerate([radar_img, donut_img]):
        cx0 = CONTAINER_PADDING + i * (chart_w + 24)
        cx1 = cx0 + chart_w
        cy0 = y
        cy1 = y + chart_card_h
        draw.rounded_rectangle((cx0, cy0, cx1, cy1), radius=CARD_RADIUS, fill=CARD_BG)
        title = "能力分布 · 雷达图" if i == 0 else "分类占比 · 环形图"
        tw = draw.textbbox((0,0), title, font=get_font(16, bold=True))[2]
        draw.text((cx0 + (chart_w - tw)//2, cy0 + 10), title, fill='#444444', font=get_font(16, bold=True))
        img_w, img_h = chart_img.size
        paste_x = cx0 + (chart_w - img_w) // 2
        paste_y = cy0 + 36
        img.paste(chart_img, (paste_x, paste_y), chart_img)
    
    y += chart_card_h + 28
    
    # ---- 统计卡片网格 ----
    stat_keys = ['总在线时长', '死亡次数', '击杀怪物', '击杀玩家', '跳跃次数',
                 '摔落距离', '村民交易', '打开箱子', '造成伤害', '捕鱼数']
    stat_units = {'摔落距离': '米', '造成伤害': '点', '总在线时长': '分钟'}
    stat_values = [playtime, deaths, mobKills, playerKills, jumps, fall, villager, chests, damage, fish]
    
    cols = 5
    card_w = (CONTAINER_WIDTH - 2 * CONTAINER_PADDING - (cols-1)*16) // cols
    card_h = 60
    
    for idx, (key, value) in enumerate(zip(stat_keys, stat_values)):
        row = idx // cols
        col = idx % cols
        cx0 = CONTAINER_PADDING + col * (card_w + 16)
        cx1 = cx0 + card_w
        cy0 = y + row * (card_h + 16)
        cy1 = cy0 + card_h
        draw.rounded_rectangle((cx0, cy0, cx1, cy1), radius=12, fill=CARD_BG)
        # 标签
        label_w = draw.textbbox((0,0), key, font=get_font(STAT_LABEL_SIZE))[2]
        label_x = cx0 + (card_w - label_w) // 2
        draw.text((label_x, cy0 + 8), key, fill=STAT_LABEL_COLOR, font=get_font(STAT_LABEL_SIZE))
        # 数值
        unit = stat_units.get(key, '')
        value_text = f"{value:,}"
        if unit:
            value_text += f" {unit}"
        val_w = draw.textbbox((0,0), value_text, font=get_font(STAT_VALUE_SIZE, bold=True))[2]
        val_x = cx0 + (card_w - val_w) // 2
        draw.text((val_x, cy0 + 28), value_text, fill=STAT_VALUE_COLOR, font=get_font(STAT_VALUE_SIZE, bold=True))
    
    y += grid_height + 24
    
    # ---- 底部时间 ----
    now_str = time.strftime("%Y/%m/%d  %H:%M:%S")
    time_w = draw.textbbox((0,0), now_str, font=get_font(TIME_TEXT_SIZE))[2]
    time_x = (CONTAINER_WIDTH - time_w) // 2
    draw.text((time_x, y), now_str, fill=TIME_TEXT_COLOR, font=get_font(TIME_TEXT_SIZE))
    
    return img