import os
from PIL import Image, ImageDraw, ImageFont

# ========== 样式常量（高清优化版）==========
CONTAINER_WIDTH = 1080         # 提升画布宽度，消除模糊
CONTAINER_PADDING = 36         # 外框内边距
CONTAINER_BG = '#F9F9F8'
CONTAINER_RADIUS = 24

TITLE_TEXT = "命令帮助"
TITLE_COLOR = '#5b6abf'
TITLE_SIZE = 40                # 标题字号加大
TITLE_MARGIN_BOTTOM = 24

CARD_BG = '#FFFFFF'
CARD_RADIUS = 16
CARD_PADDING = 36              # 卡片内边距加大
CARD_MARGIN_BOTTOM = 0

SECTION_TITLE_COLOR = '#444444'
SECTION_TITLE_SIZE = 22        # 分区标题字号加大
SECTION_ITEM_NAME_COLOR = '#5b6abf'
SECTION_ITEM_DESC_COLOR = '#666666'
SECTION_ITEM_SIZE = 18         # 命令文字字号加大
LINE_SPACING = 10              # 行间距加大
DIVIDER_COLOR = '#e0e0e0'
NOTE_COLOR = '#999999'
NOTE_SIZE = 18
NOTE_MARGIN_TOP = 24

# ---------- 更新后的命令列表 ----------
COMMAND_SECTIONS = [
    {
        "icon": "",
        "title": "服务器管理",
        "items": [
            ("/mc add 名称 IP [端口]", "添加服务器，端口可选（默认为全局端口）"),
            ("/mc remove 名称", "删除服务器"),
            ("/mc edit 名称 name 新名称", "修改服务器名称"),
            ("/mc edit 名称 host 新IP", "修改服务器IP"),
            ("/mc edit 名称 port 新端口", "修改服务器API端口"),
            ("/mc batchadd 名:IP[:端口]", "批量添加，端口可选"),
            ("/mc batchremove 名,名", "批量删除"),
            ("/mc list", "查看已添加的服务器（显示独立端口）"),
            ("/mc move 名称 位置序号", "移动服务器到指定位置(从0开始)"),
            ("/mc swap 名称1 名称2", "交换两个服务器的位置"),
            ("/上次在线", "开关当前群的服务器卡片‘上次在线’显示"),
            ("/mc say <消息>", "向所有服务器发送广播"),
            ("/广播 [服务器名] <消息>", "向指定服务器广播，省略服务器名则广播至全部"),
        ]
    },
    {
        "icon": "",
        "title": "状态查询与玩家数据",
        "items": [
            ("/mc status", "查询所有服务器在线情况（图片）"),
            ("/在线  (或 /online)", "快捷查询，同 /mc status"),
            ("/mc stats 玩家名 [服务器名]", "查询玩家数据统计（图片）"),
            ("/查询 玩家名 [服务器名]", "快捷查询，同 /mc stats"),
        ]
    },
    {
        "icon": "",
        "title": "帮助",
        "items": [
            ("/mc help", "显示命令帮助（图片）"),
            ("/mc bindhelp", "显示绑定相关帮助（图片）"),
        ]
    }
]

NOTE_TEXT = "提示：所有命令均支持别名，可在主配置中自定义；端口参数若不指定则使用全局默认端口。"

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

    # 卡片可用宽度
    card_inner_width = CONTAINER_WIDTH - CONTAINER_PADDING * 2 - CARD_PADDING * 2
    # 命令名固定宽度（加大以容纳更长命令）
    command_name_width = 280   # 稍加宽以容纳 "batchadd" 等长命令
    # 描述宽度
    command_desc_width = card_inner_width - command_name_width

    # 计算每个分区高度
    section_heights = []
    for section in COMMAND_SECTIONS:
        sec_h = 0
        sec_h += FONT_SECTION_TITLE.size + 10  # 标题行
        for _ in section["items"]:
            sec_h += SECTION_ITEM_SIZE + LINE_SPACING
        section_heights.append(sec_h)

    # 分割线高度与间距
    DIVIDER_HEIGHT = 1
    DIVIDER_MARGIN = 18

    # 卡片内容总高度
    card_content_height = 0
    for i, h in enumerate(section_heights):
        card_content_height += h
        if i < len(section_heights) - 1:
            card_content_height += DIVIDER_MARGIN * 2 + DIVIDER_HEIGHT

    # 卡片高度（上下内边距 + 内容 + 备注）
    card_height = CARD_PADDING * 2 + card_content_height
    if NOTE_TEXT:
        card_height += NOTE_MARGIN_TOP + NOTE_SIZE

    # 总画布高度
    total_height = CONTAINER_PADDING + TITLE_SIZE + TITLE_MARGIN_BOTTOM + card_height + CONTAINER_PADDING

    # 创建画布
    img = Image.new('RGB', (CONTAINER_WIDTH, total_height), CONTAINER_BG)
    draw = ImageDraw.Draw(img)

    # 绘制圆角容器背景
    draw.rounded_rectangle(
        [(0, 0), (CONTAINER_WIDTH, total_height)],
        radius=CONTAINER_RADIUS, fill=CONTAINER_BG
    )

    # 居中标题
    title_bbox = draw.textbbox((0, 0), TITLE_TEXT, font=FONT_TITLE)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (CONTAINER_WIDTH - title_w) // 2
    draw.text((title_x, CONTAINER_PADDING), TITLE_TEXT, fill=TITLE_COLOR, font=FONT_TITLE)

    # 卡片位置
    card_x0 = CONTAINER_PADDING
    card_y0 = CONTAINER_PADDING + TITLE_SIZE + TITLE_MARGIN_BOTTOM
    card_x1 = CONTAINER_WIDTH - CONTAINER_PADDING
    card_y1 = card_y0 + card_height

    # 绘制卡片圆角背景
    draw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=CARD_RADIUS, fill=CARD_BG
    )

    # 绘制各分区
    y_cursor = card_y0 + CARD_PADDING
    for idx, section in enumerate(COMMAND_SECTIONS):
        # 分区标题
        title = f"{section['icon']} {section['title']}"
        draw.text((card_x0 + CARD_PADDING, y_cursor), title, fill=SECTION_TITLE_COLOR, font=FONT_SECTION_TITLE)
        y_cursor += FONT_SECTION_TITLE.size + 10

        # 命令项
        for name, desc in section["items"]:
            name_x = card_x0 + CARD_PADDING
            name_y = y_cursor
            draw.text((name_x, name_y), name, fill=SECTION_ITEM_NAME_COLOR, font=FONT_ITEM)

            desc_x = name_x + command_name_width
            draw.text((desc_x, name_y), desc, fill=SECTION_ITEM_DESC_COLOR, font=FONT_ITEM)

            y_cursor += SECTION_ITEM_SIZE + LINE_SPACING

        # 非最后一个分区，画分割线
        if idx < len(COMMAND_SECTIONS) - 1:
            y_cursor += DIVIDER_MARGIN
            line_y = y_cursor
            draw.line(
                [(card_x0 + CARD_PADDING, line_y), (card_x1 - CARD_PADDING, line_y)],
                fill=DIVIDER_COLOR, width=2
            )
            y_cursor += DIVIDER_HEIGHT + DIVIDER_MARGIN

    # 底部署注
    if NOTE_TEXT:
        y_cursor += NOTE_MARGIN_TOP
        note_bbox = draw.textbbox((0, 0), NOTE_TEXT, font=FONT_NOTE)
        note_w = note_bbox[2] - note_bbox[0]
        note_x = card_x0 + (card_x1 - card_x0 - note_w) // 2
        draw.text((note_x, y_cursor), NOTE_TEXT, fill=NOTE_COLOR, font=FONT_NOTE)

    return img