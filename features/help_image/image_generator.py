import os
from PIL import Image, ImageDraw, ImageFont

# 样式常量（与 HTML 保持一致）
CONTAINER_WIDTH = 660
CONTAINER_PADDING = 24
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 16

TITLE_TEXT = "📋 命令帮助"
TITLE_COLOR = '#5b6abf'
TITLE_SIZE = 28
TITLE_MARGIN_BOTTOM = 20

CARD_BG = '#FFFFFF'
CARD_RADIUS = 12
CARD_PADDING = 24
CARD_MARGIN_BOTTOM = 0

SECTION_TITLE_COLOR = '#444444'
SECTION_TITLE_SIZE = 16
SECTION_ITEM_NAME_COLOR = '#5b6abf'
SECTION_ITEM_DESC_COLOR = '#666666'
SECTION_ITEM_SIZE = 14
LINE_SPACING = 6
DIVIDER_COLOR = '#e0e0e0'
NOTE_COLOR = '#999999'
NOTE_SIZE = 13
NOTE_MARGIN_TOP = 16

# 命令列表数据（静态）
COMMAND_SECTIONS = [
    {
        "icon": "🔧",
        "title": "服务器管理",
        "items": [
            ("/mc add 名称 IP", "添加服务器"),
            ("/mc remove 名称", "删除服务器"),
            ("/mc edit 名称 name 新名称", "修改服务器名称"),
            ("/mc edit 名称 host 新IP", "修改服务器IP"),
            ("/mc batchadd 名:IP,名:IP", "批量添加"),
            ("/mc batchremove 名,名", "批量删除"),
            ("/mc list", "查看已添加的服务器"),
            ("/mc move 名称 位置序号", "移动服务器到指定位置(从0开始)"),
            ("/mc swap 名称1 名称2", "交换两个服务器的位置"),
        ]
    },
    {
        "icon": "📊",
        "title": "状态查询",
        "items": [
            ("/mc status", "查询所有服务器在线情况（图片）"),
            ("/在线  (或 /online)", "快捷查询，同 /mc status"),
        ]
    },
    {
        "icon": "❓",
        "title": "帮助",
        "items": [
            ("/mc help", "显示本帮助（图片）"),
        ]
    }
]

NOTE_TEXT = "提示：所有命令均支持别名，可在主配置中自定义"


def _load_font(size):
    """加载字体，优先使用插件自带字体"""
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


def draw_help_image() -> Image.Image:
    """绘制帮助图片并返回 Image 对象"""
    FONT_TITLE = _load_font(TITLE_SIZE)
    FONT_SECTION_TITLE = _load_font(SECTION_TITLE_SIZE)
    FONT_ITEM = _load_font(SECTION_ITEM_SIZE)
    FONT_NOTE = _load_font(NOTE_SIZE)

    # 计算卡片高度
    card_inner_width = CONTAINER_WIDTH - CONTAINER_PADDING * 2 - CARD_PADDING * 2
    command_name_width = 200
    command_desc_width = card_inner_width - command_name_width

    section_heights = []
    for section in COMMAND_SECTIONS:
        sec_h = 0
        sec_h += FONT_SECTION_TITLE.size + 6
        for _ in section["items"]:
            sec_h += SECTION_ITEM_SIZE + LINE_SPACING
        section_heights.append(sec_h)

    DIVIDER_HEIGHT = 1
    DIVIDER_MARGIN = 14

    card_content_height = 0
    for i, h in enumerate(section_heights):
        card_content_height += h
        if i < len(section_heights) - 1:
            card_content_height += DIVIDER_MARGIN * 2 + DIVIDER_HEIGHT

    card_height = CARD_PADDING * 2 + card_content_height
    if NOTE_TEXT:
        card_height += NOTE_MARGIN_TOP + NOTE_SIZE

    total_height = CONTAINER_PADDING + TITLE_SIZE + TITLE_MARGIN_BOTTOM + card_height + CONTAINER_PADDING

    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [(0, 0), (CONTAINER_WIDTH, total_height)],
        radius=CONTAINER_RADIUS, fill=CONTAINER_BG
    )

    # 标题
    title_bbox = draw.textbbox((0, 0), TITLE_TEXT, font=FONT_TITLE)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (CONTAINER_WIDTH - title_w) // 2
    draw.text((title_x, CONTAINER_PADDING), TITLE_TEXT, fill=TITLE_COLOR, font=FONT_TITLE)

    # 卡片位置
    card_x0 = CONTAINER_PADDING
    card_y0 = CONTAINER_PADDING + TITLE_SIZE + TITLE_MARGIN_BOTTOM
    card_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
    card_y1 = card_y0 + card_height

    draw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=CARD_RADIUS, fill=CARD_BG
    )

    y_cursor = card_y0 + CARD_PADDING

    for idx, section in enumerate(COMMAND_SECTIONS):
        title = f"{section['icon']} {section['title']}"
        draw.text((card_x0 + CARD_PADDING, y_cursor), title, fill=SECTION_TITLE_COLOR, font=FONT_SECTION_TITLE)
        y_cursor += FONT_SECTION_TITLE.size + 6

        for name, desc in section["items"]:
            name_x = card_x0 + CARD_PADDING
            name_y = y_cursor
            draw.text((name_x, name_y), name, fill=SECTION_ITEM_NAME_COLOR, font=FONT_ITEM)

            desc_x = name_x + command_name_width
            draw.text((desc_x, name_y), desc, fill=SECTION_ITEM_DESC_COLOR, font=FONT_ITEM)

            y_cursor += SECTION_ITEM_SIZE + LINE_SPACING

        if idx < len(COMMAND_SECTIONS) - 1:
            y_cursor += DIVIDER_MARGIN
            line_y = y_cursor
            draw.line(
                [(card_x0 + CARD_PADDING, line_y), (card_x1 - CARD_PADDING, line_y)],
                fill=DIVIDER_COLOR, width=1
            )
            y_cursor += DIVIDER_HEIGHT + DIVIDER_MARGIN

    if NOTE_TEXT:
        y_cursor += NOTE_MARGIN_TOP
        note_bbox = draw.textbbox((0, 0), NOTE_TEXT, font=FONT_NOTE)
        note_w = note_bbox[2] - note_bbox[0]
        note_x = card_x0 + (card_x1 - card_x0 - note_w) // 2
        draw.text((note_x, y_cursor), NOTE_TEXT, fill=NOTE_COLOR, font=FONT_NOTE)

    return img