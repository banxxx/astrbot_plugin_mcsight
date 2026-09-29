"""在线状态图片生成器（/在线、/mc status）

复用 help_image 引擎的延迟绘制 ops + 2x 超采样；
专有 op：'paste'（玩家头像）、'emoji'（服务器名含 emoji）。
"""

import asyncio
import time
from typing import List, Dict, Any

import aiohttp
from PIL import Image, ImageDraw

from ...utils.avatar_cache import download_avatar, get_default_avatar
from ...utils.text_renderer import draw_text_with_emoji, measure_text_with_emoji
from ...config.whitelist_config import WhitelistManager
from ..help_image.image_generator import (
    WIDTH, PAD, SCALE, BG, ACCENT,
    new_ops, add_text, add_rect, add_ellipse, add_dash,
    text_w, _font, place_runs, build_image,
)

# ========== 布局常量（设计单位，渲染时统一 ×SCALE） ==========
TITLE_TEXT = "在线玩家列表"
TITLE_SIZE = 40
TOTAL_SIZE = 24
TOTAL_COLOR = '#555555'
HEADER_GAP = 12
HEADER_BOTTOM = 26

CARD_PAD_X = 28
CARD_PAD_TOP = 22
CARD_PAD_BOTTOM = 24
CARD_GAP = 22
CARD_RADIUS = 16
CARD_BG = '#FFFFFF'
CARD_BG_OFFLINE = '#fbfbfa'

HEAD_H = 26
DOT_SIZE = 14
DOT_ON = '#5cb85c'
DOT_OFF = '#d9534f'
NAME_SIZE = 24
NAME_COLOR = '#444444'

CHIP_SIZE = 15
CHIP_H = 26
CHIP_PAD_X = 14
CHIP_GAP = 8
# 头部胶囊整体下移量：与服务器名/在线人数的视觉中心对齐（否则底部齐平显高）
CHIP_Y_OFF = 3
CHIP_VER = ('#8a7bc0', '#f4f2fb', '#e6e0f5')
CHIP_LAT_A = ('#3d8b61', '#e9f5ee', '#cfe9da')
CHIP_LAT_B = ('#b26a00', '#fdf3e0', '#f3e0b8')
CHIP_LAT_C = ('#c0392b', '#fbeaea', '#f0d2d2')
CHIP_LAST = ('#8b93a7', '#f5f6f8', '#e6e9ef')

COUNT_NUM_SIZE = 22
COUNT_TXT_SIZE = 18
COUNT_TXT_COLOR = '#777777'
COUNT_OFF_COLOR = '#c9ccd6'

CAP_Y_GAP = 12
CAP_H = 4
CAP_BG_COLOR = '#eef0f4'
CAP_FILL_OFF = '#e3e5ea'

DIV_GAP_TOP = 14
DIV_GAP_BOTTOM = 14

COLS = 5
GAP_COL = 10
GAP_ROW = 16
GAP_NAME = 10
AVATAR = 44
AVATAR_RADIUS = 8
PNAME_SIZE = 15
PNAME_COLOR = '#555555'

NOTE_SIZE = 15
NOTE_COLOR = '#a5aabb'
NOTE_H = 30

TIME_SIZE = 16
TIME_COLOR = '#aaaaaa'
TIME_GAP = 24

AVATAR_DOWNLOAD_CONCURRENCY = 10

OFFLINE_KEYWORDS = ['积极拒绝', 'Connection refused', 'timeout', '超时',
                    '连接超时', '连接失败']


def format_last_online(ts_ms: int) -> str:
    if not ts_ms or ts_ms <= 0:
        return "无记录"
    try:
        diff_sec = (time.time() * 1000 - ts_ms) / 1000.0
    except Exception:
        return "无记录"
    if diff_sec < 60:
        return "不到1分钟"
    if diff_sec < 3600:
        return f"{int(diff_sec / 60)}分钟"
    if diff_sec < 86400:
        h, m = int(diff_sec / 3600), int((diff_sec % 3600) / 60)
        return f"{h}小时{m}分钟" if m > 0 else f"{h}小时"
    if diff_sec < 365 * 86400:
        d, h = int(diff_sec / 86400), int((diff_sec % 86400) / 3600)
        return f"{d}天{h}小时" if h > 0 else f"{d}天"
    y, d = int(diff_sec / (365 * 86400)), int((diff_sec % (365 * 86400)) / 86400)
    return f"{y}年{d}天" if d > 0 else f"{y}年"


def _latency_chip(lat_ms) -> tuple:
    if lat_ms is None:
        return CHIP_LAT_B
    if lat_ms <= 100:
        return CHIP_LAT_A
    if lat_ms <= 500:
        return CHIP_LAT_B
    return CHIP_LAT_C


def add_chip(ops, draw, x, y, text, colors):
    fg, bg, bd = colors
    w = text_w(draw, text, CHIP_SIZE) + CHIP_PAD_X * 2
    add_rect(ops, 'bg1', x, y, w, CHIP_H, CHIP_H // 2, bg, bd)
    add_text(ops, x + CHIP_PAD_X, y + 5, text, CHIP_SIZE, fg)
    return w


def _truncate(draw, name, size, max_w):
    if name is None:
        return ""
    if text_w(draw, name, size) <= max_w:
        return name
    ell = '…'
    ew = text_w(draw, ell, size)
    acc = ''
    for ch in name:
        if text_w(draw, acc + ch, size) + ew > max_w:
            break
        acc += ch
    return acc + ell


def _make_rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new('L', img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0], img.size[1]],
                                           radius=radius, fill=255)
    img = img.copy()
    img.putalpha(mask)
    return img


# ========== 卡片绘制 ==========
def _draw_card(draw, ops, card, y):
    x0, x1 = PAD, WIDTH - PAD
    px0 = x0 + CARD_PAD_X
    px1 = x1 - CARD_PAD_X
    inner_w = px1 - px0
    is_offline = bool(card['error'])
    card_bg = CARD_BG_OFFLINE if is_offline else CARD_BG

    head_top = y + CARD_PAD_TOP
    col_w = (inner_w - (COLS - 1) * GAP_COL) / COLS

    # 内容区高度
    players = card['players']
    if players:
        rows = (len(players) + COLS - 1) // COLS
        content_h = rows * AVATAR + (rows - 1) * GAP_ROW
        content_kind = 'grid'
    elif is_offline:
        content_h = 0
        content_kind = 'none'
    else:
        content_h = NOTE_H
        content_kind = 'note'

    if content_kind == 'none':
        card_h = CARD_PAD_TOP + HEAD_H + CAP_Y_GAP + CAP_H + 16
    else:
        card_h = (CARD_PAD_TOP + HEAD_H + CAP_Y_GAP + CAP_H
                  + DIV_GAP_TOP + 1 + DIV_GAP_BOTTOM
                  + content_h + CARD_PAD_BOTTOM)

    add_rect(ops, 'bg0', x0, y, x1 - x0, card_h, CARD_RADIUS, card_bg)

    # 状态圆点
    add_ellipse(ops, 'bg1', px0, head_top + (HEAD_H - DOT_SIZE) / 2 + 1,
                DOT_SIZE, DOT_OFF if is_offline else DOT_ON)

    # 服务器名（可能含 emoji）
    name_x = px0 + DOT_SIZE + 12
    ops['fg'].append(('emoji', name_x, head_top, card['name'],
                      NAME_SIZE, NAME_COLOR))

    # 右侧信息区：胶囊组 + 在线人数（原位置）
    # 元组：('chip', text, colors, w, drop_priority)，priority 越小越先被丢弃
    items_w = []
    if is_offline:
        err_text = "服务器不在线" if any(
            k in (card['error'] or '') for k in OFFLINE_KEYWORDS
        ) else f"查询失败: {card['error']}"
        items_w.append(('chip', err_text, CHIP_LAT_C,
                        text_w(draw, err_text, CHIP_SIZE) + CHIP_PAD_X * 2, 99))
    else:
        if card.get('show_version'):
            v = card['version']
            items_w.append(('chip', v, CHIP_VER,
                            text_w(draw, v, CHIP_SIZE) + CHIP_PAD_X * 2, 1))
        if card.get('show_latency'):
            lat = card.get('latency')
            t = "?ms" if lat is None else f"{lat:.0f}ms"
            items_w.append(('chip', t, _latency_chip(lat),
                            text_w(draw, t, CHIP_SIZE) + CHIP_PAD_X * 2, 2))
        if card.get('show_last') and card.get('last_activity_time', 0) > 0:
            t = f"上次在线 {format_last_online(card['last_activity_time'])}前"
            items_w.append(('chip', t, CHIP_LAST,
                            text_w(draw, t, CHIP_SIZE) + CHIP_PAD_X * 2, 0))

    num_s, suf_s = card['online_str'], f" / {card['max_str']} 人在线"
    num_w = text_w(draw, num_s, COUNT_NUM_SIZE)
    suf_w = text_w(draw, suf_s, COUNT_TXT_SIZE)
    count_w = num_w + suf_w

    def _meta_total(chips):
        t = sum(w for *_, w, _p in chips) + count_w
        if chips:
            t += (len(chips) - 1) * CHIP_GAP + 12
        return t

    # 防溢出：名称与胶囊冲突时按优先级丢弃（上次在线→版本→延迟）
    name_w = measure_text_with_emoji(draw, card['name'], _font(NAME_SIZE))
    max_meta = px1 - (name_x + name_w) - 16
    while items_w and _meta_total(items_w) > max_meta:
        victim = min(range(len(items_w)), key=lambda i: items_w[i][4])
        if items_w[victim][4] >= 99:
            break
        items_w.pop(victim)

    total_w = _meta_total(items_w)
    cursor = px1 - total_w

    for kind, text, colors, w, _p in items_w:
        add_chip(ops, draw, cursor, head_top + CHIP_Y_OFF, text, colors)
        cursor += w + CHIP_GAP
    if items_w:
        cursor += 12 - CHIP_GAP
    num_color = COUNT_OFF_COLOR if is_offline else ACCENT
    add_text(ops, cursor, head_top + 2, num_s, COUNT_NUM_SIZE, num_color)
    add_text(ops, cursor + num_w, head_top + 6, suf_s, COUNT_TXT_SIZE,
             COUNT_TXT_COLOR)

    # 容量条
    cap_y = head_top + HEAD_H + CAP_Y_GAP
    add_rect(ops, 'bg1', px0, cap_y, inner_w, CAP_H, CAP_H // 2, CAP_BG_COLOR)
    mx = card.get('max', 0)
    ratio = 0.0 if (is_offline or not mx) else min(1.0, card['online'] / mx)
    if ratio > 0:
        add_rect(ops, 'bg1', px0, cap_y, max(CAP_H, inner_w * ratio),
                 CAP_H, CAP_H // 2, ACCENT)

    if content_kind == 'none':
        return card_h

    # 虚线 + 内容区
    div_y = cap_y + CAP_H + DIV_GAP_TOP
    add_dash(ops, px0, px1, div_y)
    gy = div_y + 1 + DIV_GAP_BOTTOM

    if content_kind == 'grid':
        for i, p in enumerate(players):
            row, col = divmod(i, COLS)
            sx = px0 + col * (col_w + GAP_COL)
            sy = gy + row * (AVATAR + GAP_ROW)
            ava = card['avatars'][i]
            ops['bg1'].append(('paste', sx, sy, ava))
            pn = _truncate(draw, p[0], PNAME_SIZE,
                           col_w - AVATAR - GAP_NAME)
            add_text(ops, sx + AVATAR + GAP_NAME,
                     sy + (AVATAR - PNAME_SIZE) / 2 + 2,
                     pn, PNAME_SIZE, PNAME_COLOR)
    else:
        if card['online'] > 0:
            t = f"{card['online']} 人在线（玩家列表不可见）"
            chip_text = "列表隐藏"
            chip_w = text_w(draw, chip_text, CHIP_SIZE) + CHIP_PAD_X * 2
            add_chip(ops, draw, px0, gy + (NOTE_H - 22) / 2, chip_text,
                     CHIP_LAST)
            add_text(ops, px0 + chip_w + 10, gy + 6, t, NOTE_SIZE, NOTE_COLOR)
        else:
            t = "当前无人在线"
            tw = text_w(draw, t, NOTE_SIZE)
            add_text(ops, px0 + (inner_w - tw) / 2, gy + 6, t, NOTE_SIZE,
                     NOTE_COLOR)
    return card_h


# ========== 总绘制 ==========
def _build_status(draw, ops, doc, y):
    title_w = text_w(draw, TITLE_TEXT, TITLE_SIZE)
    add_text(ops, (WIDTH - title_w) / 2, y, TITLE_TEXT, TITLE_SIZE, ACCENT)
    y += TITLE_SIZE + HEADER_GAP

    runs = [("总在线: ", TOTAL_COLOR), (str(doc['total_online']), ACCENT),
            (" 人", TOTAL_COLOR)]
    tw = sum(text_w(draw, t, TOTAL_SIZE) for t, _ in runs)
    place_runs(ops, draw, (WIDTH - tw) / 2, y, runs, TOTAL_SIZE)
    y += TOTAL_SIZE + HEADER_BOTTOM

    for card in doc['cards']:
        y += _draw_card(draw, ops, card, y) + CARD_GAP
    y = y - CARD_GAP + TIME_GAP

    now = doc['now_str']
    nw = text_w(draw, now, TIME_SIZE)
    add_text(ops, (WIDTH - nw) / 2, y, now, TIME_SIZE, TIME_COLOR)
    return y + TIME_SIZE


async def draw_multi_server_image(servers_data: List[Dict[str, Any]],
                                  show_last_online: bool = False) -> Image.Image:
    wm = WhitelistManager()
    show_version = wm.show_server_version
    show_latency = wm.show_server_latency
    now_str = time.strftime("%Y/%m/%d  %H:%M:%S")

    # ---- 归一化 + 收集头像下载请求 ----
    cards = []
    requests = {}
    for srv in servers_data:
        players_raw = srv.get("players", [])
        plist = []
        for p in players_raw:
            if isinstance(p, dict):
                plist.append((p.get("name"), p.get("uuid"), p.get("is_premium")))
            else:
                plist.append((p, None, None))
        card = {
            'name': srv['name'],
            'players': plist,
            'online': srv.get('online', 0),
            'online_str': str(srv.get('online', 0)),
            'max': srv.get('max', 0),
            'max_str': str(srv.get('max', 1)),
            'error': srv.get('error'),
            'version': srv.get('version', '未知'),
            'latency': srv.get('latency'),
            'last_activity_time': srv.get('last_activity_time', 0),
            'show_version': show_version,
            'show_latency': show_latency,
            'show_last': show_last_online,
        }
        cards.append(card)
        for pname, puuid, prem in plist:
            if pname:
                requests.setdefault((pname, puuid), prem)

    # ---- 并发下载头像（2x 尺寸，NEAREST 语义由源尺寸保证） ----
    avatar_px = int(AVATAR * SCALE)
    avatar_map = {}
    async with aiohttp.ClientSession() as session:
        sem = asyncio.Semaphore(AVATAR_DOWNLOAD_CONCURRENCY)

        async def _get(pname, puuid, prem):
            async with sem:
                try:
                    return await download_avatar(session, pname, avatar_px,
                                                 prem, uuid=puuid)
                except Exception:
                    return get_default_avatar(avatar_px)

        keys = list(requests.keys())
        results = await asyncio.gather(
            *[_get(k[0], k[1], requests[k]) for k in keys]) if keys else []
        for k, img in zip(keys, results):
            avatar_map[k] = _make_rounded(img, AVATAR_RADIUS * SCALE)

    for card in cards:
        card['avatars'] = [
            avatar_map.get((n, u)) or _make_rounded(
                get_default_avatar(avatar_px), AVATAR_RADIUS * SCALE)
            for n, u, _ in card['players']
        ]

    total_online = sum(c['online'] for c in cards if not c['error'])
    doc = {'cards': cards, 'now_str': now_str, 'total_online': total_online}

    def _paste(op, img):
        _, x, y, ava = op
        img.paste(ava, (int(x * SCALE), int(y * SCALE)), ava)

    def _render_emoji(op, img):
        _, x, y, text, size, color = op
        d = ImageDraw.Draw(img)
        draw_text_with_emoji(img, d, (x * SCALE, y * SCALE), text,
                             _font(size * SCALE), color, emoji_scale=0.95)

    # 绘制是纯 CPU，挪出事件循环，避免 /在线 期间卡住整个后端
    return await asyncio.to_thread(
        build_image, _build_status, doc,
        extra_handlers={'paste': _paste, 'emoji': _render_emoji})
