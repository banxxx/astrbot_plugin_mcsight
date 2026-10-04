"""通用帮助图片生成器（/mc help、/帮助）

架构：布局阶段不直接绘制，而是把绘制指令收集为 ops（背景层/前景层），
因此可以单遍布局得到总高度，再统一渲染（卡片背景自然位于文字之下）。
bind_help_generator 复用本模块的引擎与样式常量。
"""

import os
import re
import tempfile
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ========== 画布与配色 ==========
SCALE = 2             # 超采样倍数：布局按设计单位计算，渲染时整体放大，保证文字锐利
WIDTH = 1080
PAD = 36
CARD_PAD_X = 32
CARD_PAD_TOP = 24
CARD_PAD_BOTTOM = 18
CARD_GAP = 18
CARD_RADIUS = 16

BG = '#F9F9F8'
CARD_BG = '#FFFFFF'
ACCENT = '#5b6abf'
DIM = '#8c9ad8'
TEXT = '#444444'
DESC = '#666666'
MUTED = '#a5aabb'
FAINT = '#b7bccb'
NOTE = '#999999'
DASH = '#efefec'
GREEN = '#4caf7d'
AMBER = '#e6a23c'
ROSE = '#d06a6a'
QUESTION = '#a06a00'

PILL_FG, PILL_BG, PILL_BD = '#6b78c4', '#eef0fa', '#dfe3f6'
PILL_SIZE, PILL_H = 12, 22
ADMIN_FG, ADMIN_BG, ADMIN_BD = '#b26a00', '#fdf3e0', '#f3e0b8'
SUPER_FG, SUPER_BG, SUPER_BD = '#c0392b', '#fbeaea', '#f0d2d2'
TAG_SIZE, TAG_H = 12, 20

DESC_LINE_H = 23
CMD_LINE_H = 24
ROW_VPAD = 7
ROW_VPAD_TIGHT = 5
SEG_GAP = 10          # 命令区与描述区的间距
CMD_COL_W = 250       # 单列行的命令列最小宽度（对齐 HTML .cmd min-width）

# ========== 字体 ==========
_font_cache = {}


def _font(size):
    size = int(size + 0.5)
    if size not in _font_cache:
        base = os.path.dirname(os.path.abspath(__file__))
        paths = [
            os.path.join(base, '..', '..', 'resources', 'fonts', 'msyh.ttf'),
            "msyh.ttc", "msyh.ttf", "PingFang.ttc", "wqy-microhei.ttc",
        ]
        font = None
        for p in paths:
            try:
                font = ImageFont.truetype(p, size)
                break
            except Exception:
                continue
        _font_cache[size] = font or ImageFont.load_default()
    return _font_cache[size]


# ========== ops（延迟绘制指令收集） ==========
def new_ops():
    # shadow: 卡片柔和投影（最先渲染）；bg0: 卡片底；bg1: 胶囊/角标/流程框等小块；fg: 文字与虚线
    return {'shadow': [], 'bg0': [], 'bg1': [], 'fg': []}


SHADOW_BLUR = 7
SHADOW_DY = 4
SHADOW_COLOR = (40, 46, 78, 34)


def text_w(draw, s, size):
    return draw.textlength(s, font=_font(size))


def add_text(ops, x, y, s, size, color):
    ops['fg'].append(('text', x, y, s, size, color))


def add_rect(ops, layer, x, y, w, h, r, fill, outline=None, line_w=1):
    ops[layer].append(('rect', x, y, w, h, r, fill, outline, line_w))


def add_shadow(ops, x, y, w, h, r, blur=SHADOW_BLUR, dy=SHADOW_DY,
               color=SHADOW_COLOR):
    ops['shadow'].append(('shadow', x, y, w, h, r, blur, dy, color))


def add_ellipse(ops, layer, x, y, d, fill):
    ops[layer].append(('ellipse', x, y, d, fill))


def add_dash(ops, x0, x1, y):
    ops['fg'].append(('dash', x0, x1, y))


# ========== 基础组件 ==========
def add_pill(ops, draw, x, y, text):
    w = text_w(draw, text, PILL_SIZE) + 18
    add_rect(ops, 'bg1', x, y, w, PILL_H, PILL_H // 2, PILL_BG, PILL_BD)
    add_text(ops, x + 9, y + 4, text, PILL_SIZE, PILL_FG)
    return w


def add_tag(ops, draw, x, y, payload):
    t, fg, bg, bd = payload
    w = text_w(draw, t, TAG_SIZE) + 14
    add_rect(ops, 'bg1', x, y, w, TAG_H, 4, bg, bd)
    add_text(ops, x + 7, y + 3, t, TAG_SIZE, fg)
    return w


def place_runs(ops, draw, x, y, runs, size):
    for t, c in runs:
        add_text(ops, x, y, t, size, c)
        x += text_w(draw, t, size)
    return x


# ========== 文本折行 ==========
_TOKEN_RE = re.compile(r'[A-Za-z0-9:/_\-.()\[\]<>|,;:%#&+*=!@^~$?{}' + r'"]+|\s|.')


def _force_split(draw, token, size, max_w):
    parts, cur = [], ''
    for ch in token:
        if cur and text_w(draw, cur + ch, size) > max_w:
            parts.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def wrap_runs(draw, runs, size, max_w):
    """把 (text,color) 运行串按 max_w 折行，返回行列表（每行是运行列表）"""
    lines, cur, cur_w = [], [], 0.0

    def flush():
        nonlocal cur, cur_w
        if cur:
            lines.append(cur)
        cur, cur_w = [], 0.0

    for text, color in runs:
        tokens = []
        for m in _TOKEN_RE.finditer(text):
            tk = m.group(0)
            if text_w(draw, tk, size) > max_w and not tk.isspace():
                tokens.extend(_force_split(draw, tk, size, max_w))
            else:
                tokens.append(tk)
        for tk in tokens:
            w = text_w(draw, tk, size)
            if cur_w + w > max_w and cur:
                if tk.isspace():
                    flush()
                    continue
                line_tail_space = bool(cur and cur[-1][0].isspace())
                if line_tail_space:
                    cur.pop()
                flush()
                cur_w = 0.0
            cur.append((tk, color))
            cur_w += w
    flush()
    return lines


# ========== 行布局 ==========
def place_width(draw, runs, size):
    return sum(text_w(draw, t, size) for t, _ in runs)


def add_row(draw, ops, x, y, max_w, row, tight=False,
            cmd_size=16, desc_size=15):
    """绘制一行，返回行总高"""
    vpad = ROW_VPAD_TIGHT if tight else ROW_VPAD
    top = y + vpad
    cx = x

    if row.get('q'):
        qw = text_w(draw, row['q'], cmd_size)
        add_text(ops, cx, top + 3, row['q'], cmd_size, QUESTION)
        cx = x + max(qw + 10, 180)
    if row.get('cmd'):
        cx = place_runs(ops, draw, cx, top + 3, row['cmd'], cmd_size) + 6
        if not tight:
            # 命令列定宽：胶囊/描述从同一竖线开始（同 HTML min-width: 250px）
            cx = max(cx, x + CMD_COL_W)
    for p in row.get('pills', []):
        cx += add_pill(ops, draw, cx, top + 1, p) + 6
    if row.get('pills'):
        cx += 4
    for t in row.get('tags', []):
        cx += add_tag(ops, draw, cx, top + 2, t) + 8

    content_h = CMD_LINE_H
    desc = row.get('desc')
    if desc:
        lead_end = cx + (SEG_GAP if cx > x else 0)
        avail = max_w - (lead_end - x)
        if cx > x and avail >= 160:
            lines = wrap_runs(draw, desc, desc_size, avail)
            dy = top + 3
            for ln in lines:
                place_runs(ops, draw, lead_end, dy, ln, desc_size)
                dy += DESC_LINE_H
            content_h = max(CMD_LINE_H, 3 + len(lines) * DESC_LINE_H - 1)
        else:
            lines = wrap_runs(draw, desc, desc_size, max_w)
            dy = top + CMD_LINE_H + 2
            for ln in lines:
                place_runs(ops, draw, x, dy, ln, desc_size)
                dy += DESC_LINE_H
            content_h = CMD_LINE_H + 2 + len(lines) * DESC_LINE_H

    return vpad + content_h + vpad


def add_grid2(draw, ops, x, y, max_w, rows, cmd_size=15, desc_size=14):
    """双列网格，按 HTML 的 grid 行为：逐行填充（1→左, 2→右, 3→左…）"""
    col_gap = 28
    col_w = (max_w - col_gap) // 2
    for i in range(0, len(rows), 2):
        pair_top = y
        hh = [add_row(draw, ops, x, pair_top, col_w, rows[i],
                      tight=True, cmd_size=cmd_size, desc_size=desc_size)]
        if i + 1 < len(rows):
            hh.append(add_row(draw, ops, x + col_w + col_gap, pair_top, col_w,
                              rows[i + 1], tight=True,
                              cmd_size=cmd_size, desc_size=desc_size))
        y = pair_top + max(hh)
    return y


# ========== 分区 ==========
def add_section(draw, ops, x, y, max_w, sec):
    # 标题行：色条 + 名称 + 提示
    add_rect(ops, 'bg1', x, y + 4, 4, 18, 2, sec['bar'])
    tx = x + 14
    add_text(ops, tx, y, sec['title'], 20, TEXT)
    cx = tx + text_w(draw, sec['title'], 20) + 12
    if sec.get('hint_tag'):
        cx += add_tag(ops, draw, cx, y + 2, sec['hint_tag']) + 6
    if sec.get('hint'):
        add_text(ops, cx, y + 6, sec['hint'], 13, MUTED)
    y += 20 + 12

    rows = sec['rows']
    if sec.get('grid'):
        y = add_grid2(draw, ops, x, y, max_w, rows)
    else:
        for i, r in enumerate(rows):
            if i:
                add_dash(ops, x, x + max_w, y + 1)
                y += 2
            y += add_row(draw, ops, x, y, max_w, r)
    return y + 16


# ========== 头部 / 底部 ==========
def add_header(ops, draw, title, brand_runs, y):
    add_text(ops, PAD, y - 6, title, 32, ACCENT)
    bw = place_width(draw, brand_runs, 14)
    place_runs(ops, draw, WIDTH - PAD - bw, y + 10, brand_runs, 14)
    return y + 32 + 22


def add_footer(ops, draw, runs, y, size=14):
    total = place_width(draw, runs, size)
    place_runs(ops, draw, (WIDTH - total) / 2, y + 8, runs, size)
    return y + 8 + size + 14


# ========== 渲染 ==========
def render_ops(draw, ops, scale=SCALE, img=None, extra_handlers=None):
    s = scale

    def S(v):
        return v * s

    for layer in ('shadow', 'bg0', 'bg1', 'fg'):
        for op in ops[layer]:
            kind = op[0]
            if extra_handlers and kind in extra_handlers:
                extra_handlers[kind](op, img)
                continue
            if kind == 'shadow':
                _, x, y, w, h, r, blur, dy, color = op
                pad = blur * 2 + 2
                tile = Image.new('RGBA', (int((w + pad * 2) * s),
                                          int((h + pad * 2) * s)),
                                 (color[0], color[1], color[2], 0))
                # RGB 通道保持纯阴影色，模糊只混合 alpha，边缘才不会发灰
                ImageDraw.Draw(tile).rounded_rectangle(
                    [pad * s, pad * s, (pad + w) * s, (pad + h) * s],
                    radius=r * s, fill=color)
                tile = tile.filter(ImageFilter.GaussianBlur(blur * s))
                img.paste(tile, (int((x - pad) * s), int((y - pad + dy) * s)),
                          tile)
            elif kind == 'rect':
                _, x, y, w, h, r, fill, outline, lw = op
                draw.rounded_rectangle([S(x), S(y), S(x + w), S(y + h)],
                                       radius=S(r), fill=fill,
                                       outline=outline, width=max(1, int(S(lw))))
            elif kind == 'ellipse':
                _, x, y, d, fill = op
                draw.ellipse([S(x), S(y), S(x + d), S(y + d)], fill=fill)
            elif kind == 'text':
                _, x, y, txt, size, color = op
                draw.text((S(x), S(y)), txt, font=_font(size * s), fill=color)
            elif kind == 'dash':
                _, x0, x1, y = op
                xx = S(x0)
                while xx < S(x1):
                    draw.line([(xx, S(y)), (min(xx + S(4), S(x1)), S(y))],
                              fill=DASH, width=max(1, int(s / 2)))
                    xx += S(8)


def build_image(builder, doc, extra_handlers=None):
    scratch = Image.new('RGB', (8, 8), BG)
    d = ImageDraw.Draw(scratch)
    ops = new_ops()
    total_h = builder(d, ops, doc, PAD)
    img = Image.new('RGB', (WIDTH * SCALE, (int(total_h) + PAD) * SCALE), BG)
    render_ops(ImageDraw.Draw(img), ops, img=img, extra_handlers=extra_handlers)
    return img


# ========== 数据助手 ==========
def _c(text):
    return [(text, ACCENT)]


def _plain(text):
    return [(text, DESC)]


def _dim(text):
    return [(text, DIM)]


def _runs(*pairs):
    return list(pairs)


ADMIN_TAG = ("管理员", ADMIN_FG, ADMIN_BG, ADMIN_BD)
SUPER_TAG = ("超管", SUPER_FG, SUPER_BG, SUPER_BD)


def _row(cmd=None, pills=None, desc=None, tags=None, q=None):
    r = {}
    if q:
        r['q'] = q
    if cmd:
        r['cmd'] = cmd
    if pills:
        r['pills'] = pills
    if tags:
        r['tags'] = tags
    if desc:
        r['desc'] = desc
    return r


def _sec(bar, title, rows, hint=None, hint_tag=None, grid=False):
    s = {'bar': bar, 'title': title, 'rows': rows}
    if hint:
        s['hint'] = hint
    if hint_tag:
        s['hint_tag'] = hint_tag
    if grid:
        s['grid'] = True
    return s


# ========== 主帮助内容 ==========
HELP_CARDS = [
    [
        _sec(GREEN, "状态查询", [
            _row(_c("/在线"), pills=["/online", "/mc status"],
                 desc=_plain("查询所有服务器在线情况（生成状态卡片图片）")),
            _row(_runs(("/查询 ", ACCENT), ("<玩家名> [服务器名]", DIM)),
                 pills=["/mc stats"],
                 desc=_plain("查询玩家数据统计（图片）；也可用 -s 服务器名 指定服务器")),
            _row(_runs(("/tps ", ACCENT), ("[-s 服务器名]", DIM)),
                 pills=["/mc tps"],
                 desc=_plain("查询服务器 TPS，缺省查询全部服务器")),
            _row(_runs(("/广播 ", ACCENT), ("[服务器名] <消息>", DIM)),
                 pills=["/mc say"],
                 desc=_plain("向游戏内发送广播；省略服务器名则广播至全部服务器")),
        ], hint="所有人可用"),
    ],
    [
        _sec(ACCENT, "账号绑定", [
            _row(_runs(("/绑定 ", ACCENT), ("<6位绑定码>", DIM)),
                 pills=["/bind"],
                 desc=_plain("用游戏内 Title 显示的绑定码绑定 QQ（绑定码 5 分钟有效）")),
            _row(_runs(("/解绑 ", ACCENT), ("[游戏ID]", DIM)),
                 pills=["/unbind"],
                 desc=_plain("解绑自己的账号；管理员可用 /解绑 ID <游戏ID>、/解绑 QQ <QQ号>")),
            _row(_runs(("/查绑定 ", ACCENT), ("[游戏ID|QQ号]", DIM)),
                 pills=["/绑定状态", "/checkbind", "/mc check"],
                 desc=_plain("查询绑定关系，缺省自动读取您的群昵称")),
            _row(_c("/绑定检测"), pills=["/mc bindcheck"], tags=[ADMIN_TAG],
                 desc=_plain("生成本群未绑定游戏账号成员的名单图")),
        ], hint="详细用法见 /绑定帮助"),
    ],
    [
        _sec(ACCENT, "帮助", [
            _row(_c("/帮助"), pills=["/mc help"], desc=_plain("显示本帮助图片")),
            _row(_c("/绑定帮助"), pills=["/bindhelp", "/mc bindhelp"],
                 desc=_plain("查看绑定专用帮助（图片，含完整流程与常见问题）")),
        ]),
    ],
    [
        _sec(AMBER, "服务器管理", [
            _row(_runs(("/mc add ", ACCENT), ("名称 IP [端口]", DIM)),
                 desc=_plain("添加服务器（端口缺省用全局值）")),
            _row(_runs(("/mc remove ", ACCENT), ("名称", DIM)),
                 desc=_plain("删除服务器")),
            _row(_runs(("/mc batchadd ", ACCENT), ("名:IP[:端口],…", DIM)),
                 desc=_plain("批量添加")),
            _row(_runs(("/mc batchremove ", ACCENT), ("名,名", DIM)),
                 desc=_plain("批量删除")),
            _row(_runs(("/mc edit ", ACCENT), ("名称 字段 值", DIM)),
                 desc=_plain("改 name / host / port / remark 字段")),
            _row(_c("/mc list"), desc=_plain("查看服务器列表（含独立端口）")),
            _row(_runs(("/mc move ", ACCENT), ("名称 序号", DIM)),
                 desc=_plain("移动排序位置（从 0 开始）")),
            _row(_runs(("/mc swap ", ACCENT), ("名1 名2", DIM)),
                 desc=_plain("交换两台服务器位置")),
        ], hint_tag=ADMIN_TAG, hint="需要群管或插件管理员权限", grid=True),
        _sec(AMBER, "昵称检测与显示", [
            _row(_c("/检测昵称"), pills=["/mc checknick"],
                 desc=_plain("检测群成员昵称是否含「玩家（游戏ID）」")),
            _row(_runs(("/检测昵称 ", ACCENT), ("开|关", DIM)),
                 pills=["/mc nicknamecheck"], desc=_plain("本群昵称检测开关")),
            _row(_runs(("/检测昵称 ", ACCENT), ("忽略 QQ", DIM)),
                 desc=_plain("加入忽略列表（移除：忽略→取消忽略）")),
            _row(_runs(("/检测昵称 ", ACCENT), ("忽略列表", DIM)),
                 desc=_plain("查看当前忽略名单")),
            _row(_runs(("/上次在线 ", ACCENT), ("开|关", DIM)),
                 pills=["/mc lastonline"], desc=_plain("状态卡片“上次在线”显示开关")),
        ], grid=True),
        _sec(ROSE, "高级", [
            _row(_c("/mc whitelist list"), tags=[SUPER_TAG],
                 desc=_plain("查看白名单配置（修改请前往 AstrBot WebUI）")),
        ]),
    ],
]

BRAND_RUNS = _runs(("QQ 群 × ", FAINT), ("Minecraft", ACCENT), (" 服务器助手", FAINT))
FOOT_RUNS = _runs(
    ("提示：中文命令可直接发送，英文别名见各命令后的胶囊 · ", NOTE),
    ("-s 服务器名", ACCENT),
    (" 为通用指定参数 · 端口缺省继承全局配置", NOTE))


def _build_main(draw, ops, cards, y):
    y = add_header(ops, draw, "MCSight 命令帮助", BRAND_RUNS, y)
    inner_w = WIDTH - PAD * 2 - CARD_PAD_X * 2
    for sections in cards:
        card_top = y
        cy = y + CARD_PAD_TOP
        for sec in sections:
            cy = add_section(draw, ops, PAD + CARD_PAD_X, cy, inner_w, sec)
        card_h = cy - card_top + CARD_PAD_BOTTOM
        add_rect(ops, 'bg0', PAD, card_top, WIDTH - PAD * 2, card_h,
                 CARD_RADIUS, CARD_BG)
        y = card_top + card_h + CARD_GAP
    y = add_footer(ops, draw, FOOT_RUNS, y)
    return y


def draw_help_image() -> Image.Image:
    return build_image(_build_main, HELP_CARDS)


# ========== 渲染缓存（内容静态，进程内渲染一次后复用文件） ==========
_CACHE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', '..', 'data', 'help_cache'))
_cached_paths = {}


def cached_image_path(key: str, filename: str, factory) -> str:
    path = _cached_paths.get(key)
    if path and os.path.exists(path):
        return path
    os.makedirs(_CACHE_DIR, exist_ok=True)
    final = os.path.join(_CACHE_DIR, filename)
    img = factory()
    fd, tmp = tempfile.mkstemp(dir=_CACHE_DIR, suffix='.png')
    os.close(fd)
    try:
        img.save(tmp)
        os.replace(tmp, final)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    _cached_paths[key] = final
    return final


def get_help_image_path() -> str:
    return cached_image_path('main', 'mc_help.png', draw_help_image)
