"""绑定帮助图片生成模块（/绑定帮助、/mc bindhelp）

复用 image_generator 的延迟绘制布局引擎，专有内容为“三步完成绑定”流程框。
"""

from .image_generator import (
    WIDTH, PAD, CARD_PAD_X, CARD_PAD_TOP, CARD_PAD_BOTTOM, CARD_GAP,
    CARD_RADIUS, BG, CARD_BG, ACCENT, DIM, TEXT, DESC, MUTED, ROSE, NOTE,
    DESC_LINE_H, _font, text_w, add_text, add_rect, add_ellipse,
    place_runs, place_width, wrap_runs, add_section, add_header, add_footer,
    new_ops, render_ops, build_image, cached_image_path, _c, _plain, _runs,
    ADMIN_TAG, _row, _sec,
)

STEP_BG = '#f6f7fc'
STEP_BD = '#e6e9f7'
STEP_TX = '#4a4f6b'
ARROW_C = '#b9c0dd'


def add_steps(draw, ops, x, y, total_w, steps):
    n = len(steps)
    arrow_w = 36
    box_w = (total_w - (n - 1) * arrow_w) // n
    pad = 16
    box_lines, max_h = [], 0
    for runs in steps:
        lines = wrap_runs(draw, runs, 15, box_w - pad * 2)
        box_lines.append(lines)
        max_h = max(max_h, 12 + 22 + 8 + len(lines) * DESC_LINE_H + 12)
    cx = x
    for idx, lines in enumerate(box_lines):
        add_rect(ops, 'bg1', cx, y, box_w, max_h, 12, STEP_BG, STEP_BD)
        add_ellipse(ops, 'bg1', cx + pad, y + 12, 22, ACCENT)
        no = str(idx + 1)
        nw = text_w(draw, no, 13)
        add_text(ops, cx + pad + (22 - nw) / 2, y + 17, no, 13, '#FFFFFF')
        dy = y + 12 + 22 + 8
        for ln in lines:
            place_runs(ops, draw, cx + pad, dy, ln, 15)
            dy += DESC_LINE_H
        if idx < n - 1:
            aw = text_w(draw, '→', 18)
            add_text(ops, cx + box_w + (arrow_w - aw) / 2,
                     y + max_h / 2 - 11, '→', 18, ARROW_C)
        cx += box_w + arrow_w
    return y + max_h


# ---------- 内容 ----------
BRAND_RUNS = _runs(("未绑定玩家将被", MUTED), ("限制为旁观者模式", ROSE),
                   ("，绑定后自动恢复", MUTED))

STEPS = [
    _runs(("进入服务器，屏幕中央显示 ", STEP_TX), ("6 位绑定码", ACCENT),
          ("（Title / 快捷栏提示，5 分钟有效）", STEP_TX)),
    _runs(("确认群昵称为 ", STEP_TX), ("玩家（游戏ID）", ACCENT),
          (" 格式，例如「张三（zhangsan）」", STEP_TX)),
    _runs(("在本群发送 ", STEP_TX), ("/绑定 绑定码", ACCENT),
          ("，成功即恢复正常游戏", STEP_TX)),
]

SECTIONS_BIND = [
    _sec(ACCENT, "绑定", [
        _row(_runs(("/绑定 ", ACCENT), ("<6位数字> [-s 服务器名]", DIM)),
             pills=["/bind <6位数字>"],
             desc=_plain("使用游戏内绑定码完成绑定；多服时可用 -s 指定验证哪一台")),
    ]),
    _sec(ACCENT, "解绑", [
        _row(_c("/解绑"), pills=["/unbind"],
             desc=_plain("解绑自己的账号（解绑当前身份名下全部游戏ID；也可写 all）")),
        _row(_runs(("/解绑 ", ACCENT), ("<游戏ID>", DIM)),
             desc=_plain("解绑指定游戏ID（须属于本人）；多服重名时可加 -s 服务器名")),
        _row(_runs(("/解绑 ", ACCENT), ("ID|QQ ", DIM), ("<目标>", DIM)),
             tags=[ADMIN_TAG],
             desc=_plain("按游戏ID或QQ强制解绑，QQ 可对应多条记录")),
    ]),
    _sec(ACCENT, "查询绑定", [
        _row(_c("/查绑定"), pills=["/绑定状态", "/checkbind"],
             desc=_plain("不带参数时查询自己的绑定情况")),
        _row(_runs(("/查绑定 ", ACCENT), ("<游戏ID>", DIM)),
             pills=["ID <游戏ID>"],
             desc=_plain("查询游戏ID绑定的 QQ 号")),
        _row(_runs(("/查绑定 QQ ", ACCENT), ("<QQ号>", DIM)),
             desc=_plain("查询 QQ 绑定的全部游戏ID")),
    ]),
    _sec(ROSE, "常见问题", [
        _row(q="绑定码过期？",
             desc=_plain("重新登录游戏即可获取新的 6 位绑定码")),
        _row(q="昵称校验失败？",
             desc=_plain("群昵称须包含与游戏ID一致的括号内容：玩家（zhangsan）")),
        _row(q="绑定后仍受限？",
             desc=_plain("绑定成功即已入库；若服务器暂离线，"
                         "将在其恢复后自动生效，无需重新绑定")),
    ]),
]

FOOT_RUNS = _runs(("更多命令请发送 ", NOTE), ("/mc help", ACCENT),
                  (" · 本帮助：", NOTE), ("/绑定帮助", ACCENT))


def _build_bind(draw, ops, doc, y):
    y = add_header(ops, draw, "绑定帮助", BRAND_RUNS, y)
    inner_w = WIDTH - PAD * 2 - CARD_PAD_X * 2
    x0 = PAD + CARD_PAD_X

    # 卡片 1：三步完成绑定
    card_top = y
    cy = y + CARD_PAD_TOP
    cy = add_section(draw, ops, x0, cy, inner_w,
                     _sec('#4caf7d', "三步完成绑定", [])) - 16
    cy = add_steps(draw, ops, x0, cy + 4, inner_w, STEPS)
    card_h = cy - card_top + CARD_PAD_BOTTOM
    add_rect(ops, 'bg0', PAD, card_top, WIDTH - PAD * 2, card_h,
             CARD_RADIUS, CARD_BG)
    y = card_top + card_h + CARD_GAP

    # 卡片 2：命令分区
    card_top = y
    cy = y + CARD_PAD_TOP - 6
    for sec in SECTIONS_BIND:
        cy = add_section(draw, ops, x0, cy, inner_w, sec)
    card_h = cy - card_top + CARD_PAD_BOTTOM - 16
    add_rect(ops, 'bg0', PAD, card_top, WIDTH - PAD * 2, card_h,
             CARD_RADIUS, CARD_BG)
    y = card_top + card_h + CARD_GAP

    y = add_footer(ops, draw, FOOT_RUNS, y - CARD_GAP + 4)
    return y


def draw_bind_help_image():
    return build_image(_build_bind, None)


def get_bind_help_image_path() -> str:
    return cached_image_path('bind', 'mc_bind_help.png', draw_bind_help_image)


# ---------- QQ 官方群独立帮助 ----------
OFFICIAL_BRAND_RUNS = _runs(("官方群须", ROSE), ("独立配置与绑定", ROSE),
                            ("，不沿用旧 OneBot 身份或记录", MUTED))

OFFICIAL_STEPS = [
    _runs(("管理员在后台启用本群，并确认服务器与存储权威", STEP_TX)),
    _runs(("进入服务器获取 ", STEP_TX), ("6 位绑定码", ACCENT)),
    _runs(("确认群昵称符合本群规则，在本群发送 ", STEP_TX),
          ("/绑定 绑定码", ACCENT), ("，成功即完成绑定", STEP_TX)),
]

SECTIONS_OFFICIAL_BIND = [
    _sec(ACCENT, "绑定", [
        _row(_runs(("/绑定 ", ACCENT), ("<6位数字> [-s 服务器名]", DIM)),
             pills=["/bind <6位数字>"],
             desc=_plain("绑定码 5 分钟内有效；取码账号须满足本群昵称规则（括号 / 等于 / 包含 / 模板）")),
    ]),
    _sec(ACCENT, "解绑", [
        _row(_runs(("/解绑 ", ACCENT), ("[游戏名|all] [-s 服务器名]", DIM)),
             pills=["/unbind"],
             desc=_plain("缺省或写 all 解绑自己名下全部游戏账号")),
        _row(_runs(("/解绑 ", ACCENT), ("@成员 [-s 服务器名]", DIM)),
             tags=[ADMIN_TAG],
             desc=_plain("管理员按官方成员身份解绑；也可用 /mc unbind user <标识>")),
    ]),
    _sec(ACCENT, "查询与未决操作", [
        _row(_runs(("/查绑定 ", ACCENT), ("[游戏名|@成员] [-s 服务器名]", DIM)),
             pills=["/绑定状态", "/checkbind"],
             desc=_plain("查询绑定关系；官方身份使用成员标识，不显示数字 QQ 号")),
        _row(_runs(("/mc operation ", ACCENT), ("<编号> [retry|cancel]", DIM)),
             desc=_plain("原请求人查询、重试或取消未决写操作；结果未知时不会自动重发")),
        _row(_c("/绑定检测"), pills=["/mc bindcheck"], tags=[ADMIN_TAG],
             desc=_plain("生成本群未绑定游戏账号成员名单")),
    ]),
    _sec(ROSE, "常见问题", [
        _row(q="绑定码过期？",
             desc=_plain("重新进入服务器即可获取新的 6 位绑定码")),
        _row(q="昵称校验失败？",
             desc=_plain("群昵称须符合本群当前规则；规则由管理员在后台「本群规则」配置")),
        _row(q="为什么没有 QQ 号？",
             desc=_plain("官方群只使用平台成员标识；查询他人请 @成员，数字 QQ 号不当作官方身份")),
        _row(q="成员检测不准？",
             desc=_plain("需要平台权限与可靠群昵称；缺失时标为无法核验，不会据此自动解绑")),
    ]),
]

OFFICIAL_FOOT_RUNS = _runs(("更多命令请发送 ", NOTE), ("/mc help", ACCENT),
                           (" · 本帮助：", NOTE), ("/绑定帮助", ACCENT))


def _build_official_bind(draw, ops, doc, y):
    y = add_header(ops, draw, "绑定帮助 · QQ 官方", OFFICIAL_BRAND_RUNS, y)
    inner_w = WIDTH - PAD * 2 - CARD_PAD_X * 2
    x0 = PAD + CARD_PAD_X

    card_top = y
    cy = y + CARD_PAD_TOP
    cy = add_section(draw, ops, x0, cy, inner_w,
                     _sec('#4caf7d', "三步完成绑定", [])) - 16
    cy = add_steps(draw, ops, x0, cy + 4, inner_w, OFFICIAL_STEPS)
    card_h = cy - card_top + CARD_PAD_BOTTOM
    add_rect(ops, 'bg0', PAD, card_top, WIDTH - PAD * 2, card_h,
             CARD_RADIUS, CARD_BG)
    y = card_top + card_h + CARD_GAP

    card_top = y
    cy = y + CARD_PAD_TOP - 6
    for sec in SECTIONS_OFFICIAL_BIND:
        cy = add_section(draw, ops, x0, cy, inner_w, sec)
    card_h = cy - card_top + CARD_PAD_BOTTOM - 16
    add_rect(ops, 'bg0', PAD, card_top, WIDTH - PAD * 2, card_h,
             CARD_RADIUS, CARD_BG)
    y = card_top + card_h + CARD_GAP

    y = add_footer(ops, draw, OFFICIAL_FOOT_RUNS, y - CARD_GAP + 4)
    return y


def draw_official_bind_help_image():
    return build_image(_build_official_bind, None)


def get_official_bind_help_image_path() -> str:
    return cached_image_path('official_bind', 'mc_official_bind_help.png',
                             draw_official_bind_help_image)
