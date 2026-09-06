"""
绑定帮助图片生成模块
专门负责生成 /绑定帮助 命令的图片
"""

import os
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any

# ========== 从主 image_generator 复用样式常量 ==========
# 为了保持风格一致，直接复用原文件中的常量定义
# 如果原文件有变动，这里需同步调整

CONTAINER_WIDTH = 1080
CONTAINER_PADDING = 36
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 24

TITLE_TEXT = "绑定帮助"
TITLE_COLOR = '#5b6abf'
TITLE_SIZE = 40
TITLE_MARGIN_BOTTOM = 24

CARD_BG = '#FFFFFF'
CARD_RADIUS = 16
CARD_PADDING = 36
CARD_MARGIN_BOTTOM = 0

SECTION_TITLE_COLOR = '#444444'
SECTION_TITLE_SIZE = 22
SECTION_ITEM_NAME_COLOR = '#5b6abf'
SECTION_ITEM_DESC_COLOR = '#666666'
SECTION_ITEM_SIZE = 18
LINE_SPACING = 10
DIVIDER_COLOR = '#e0e0e0'
NOTE_COLOR = '#999999'
NOTE_SIZE = 18
NOTE_MARGIN_TOP = 24

# 绑定帮助专用数据
BIND_COMMAND_SECTIONS = [
    {
        "icon": "",
        "title": "绑定账号",
        "items": [
            ("/绑定 <6位数字>", "使用游戏内的绑定码完成绑定"),
            ("/bind <6位数字>", "同上，英文别名"),
            ("/绑定", "从群昵称自动提取游戏ID（需昵称含括号）"),
        ]
    },
    {
        "icon": "",
        "title": "解绑账号",
        "items": [
            ("/解绑 <游戏ID>", "自动检测并解绑唯一绑定的服务器"),
            ("/解绑 <游戏ID> -s <服务器名>", "精确解绑指定服务器上的绑定"),
            ("/解绑", "从群昵称自动提取游戏ID（需昵称含括号）"),
        ]
    },
    {
        "icon": "",
        "title": "查询绑定状态",
        "items": [
            ("/查绑定 ID <游戏ID>", "查询游戏ID在各服务器的绑定状态"),
            ("/查绑定 QQ <QQ号>", "查询QQ号在各服务器的绑定状态"),
            ("/查绑定", "从群昵称自动提取查询参数"),
        ]
    },
    {
        "icon": "",
        "title": "其他",
        "items": [
            ("/绑定帮助", "显示本帮助图片"),
            ("/mc bindhelp", "同上，英文别名"),
        ]
    }
]

NOTE_TEXT_BIND = "绑定码在游戏内 Title/ActionBar 中显示，有效期5分钟"

# ========== 字体加载 ==========
def _load_font(size):
    """加载中文字体，与主 image_generator 保持一致"""
    base = os.path.dirname(os.path.abspath(__file__))
    # 尝试多个常见字体路径
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

# 字体实例（仅加载一次）
FONT_TITLE = _load_font(TITLE_SIZE)
FONT_SECTION_TITLE = _load_font(SECTION_TITLE_SIZE)
FONT_ITEM = _load_font(SECTION_ITEM_SIZE)
FONT_NOTE = _load_font(NOTE_SIZE)


def draw_bind_help_image() -> Image.Image:
    """
    绘制绑定相关命令的帮助图片，返回 Image 对象。
    包含绑定、解绑、查询、令牌使用说明等。
    """
    # 计算布局
    card_inner_width = CONTAINER_WIDTH - CONTAINER_PADDING * 2 - CARD_PADDING * 2
    command_name_width = 320
    command_desc_width = card_inner_width - command_name_width

    # 计算分区高度
    section_heights = []
    for section in BIND_COMMAND_SECTIONS:
        sec_h = FONT_SECTION_TITLE.size + 10
        for _ in section["items"]:
            sec_h += SECTION_ITEM_SIZE + LINE_SPACING
        section_heights.append(sec_h)

    DIVIDER_HEIGHT = 1
    DIVIDER_MARGIN = 18

    card_content_height = 0
    for i, h in enumerate(section_heights):
        card_content_height += h
        if i < len(section_heights) - 1:
            card_content_height += DIVIDER_MARGIN * 2 + DIVIDER_HEIGHT

    card_height = CARD_PADDING * 2 + card_content_height
    if NOTE_TEXT_BIND:
        card_height += NOTE_MARGIN_TOP + NOTE_SIZE

    total_height = (CONTAINER_PADDING + TITLE_SIZE + TITLE_MARGIN_BOTTOM +
                    card_height + CONTAINER_PADDING)

    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)

    # 圆角容器背景
    draw.rounded_rectangle(
        [(0, 0), (CONTAINER_WIDTH, total_height)],
        radius=CONTAINER_RADIUS, fill=CONTAINER_BG
    )

    # 标题
    title_text = "绑定帮助"
    title_bbox = draw.textbbox((0, 0), title_text, font=FONT_TITLE)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (CONTAINER_WIDTH - title_w) // 2
    draw.text((title_x, CONTAINER_PADDING), title_text, fill=TITLE_COLOR, font=FONT_TITLE)

    # 卡片
    card_x0 = CONTAINER_PADDING
    card_y0 = CONTAINER_PADDING + TITLE_SIZE + TITLE_MARGIN_BOTTOM
    card_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
    card_y1 = card_y0 + card_height

    draw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=CARD_RADIUS, fill=CARD_BG
    )

    y_cursor = card_y0 + CARD_PADDING
    for idx, section in enumerate(BIND_COMMAND_SECTIONS):
        # 分区标题
        title = f"{section['icon']} {section['title']}"
        draw.text((card_x0 + CARD_PADDING, y_cursor), title,
                  fill=SECTION_TITLE_COLOR, font=FONT_SECTION_TITLE)
        y_cursor += FONT_SECTION_TITLE.size + 10

        # 命令项
        for name, desc in section["items"]:
            name_x = card_x0 + CARD_PADDING
            name_y = y_cursor
            draw.text((name_x, name_y), name,
                      fill=SECTION_ITEM_NAME_COLOR, font=FONT_ITEM)

            desc_x = name_x + command_name_width
            draw.text((desc_x, name_y), desc,
                      fill=SECTION_ITEM_DESC_COLOR, font=FONT_ITEM)

            y_cursor += SECTION_ITEM_SIZE + LINE_SPACING

        # 分割线
        if idx < len(BIND_COMMAND_SECTIONS) - 1:
            y_cursor += DIVIDER_MARGIN
            line_y = y_cursor
            draw.line(
                [(card_x0 + CARD_PADDING, line_y), (card_x1 - CARD_PADDING, line_y)],
                fill=DIVIDER_COLOR, width=2
            )
            y_cursor += DIVIDER_HEIGHT + DIVIDER_MARGIN

    # 底部署注
    if NOTE_TEXT_BIND:
        y_cursor += NOTE_MARGIN_TOP
        note_bbox = draw.textbbox((0, 0), NOTE_TEXT_BIND, font=FONT_NOTE)
        note_w = note_bbox[2] - note_bbox[0]
        note_x = card_x0 + (card_x1 - card_x0 - note_w) // 2
        draw.text((note_x, y_cursor), NOTE_TEXT_BIND, fill=NOTE_COLOR, font=FONT_NOTE)

    return img