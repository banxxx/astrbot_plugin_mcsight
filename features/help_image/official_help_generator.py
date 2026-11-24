"""QQ 官方群的命令帮助图片（/mc help）。

官方平台有独立的登记、绑定和未决操作流程，因此不再复用 OneBot 的
帮助图，避免把「数字 QQ 号」「旧 OneBot 绑定」等不适用内容展示给官方群。
"""

from .image_generator import (
    WIDTH, PAD, CARD_PAD_X, CARD_RADIUS, CARD_BG, CARD_GAP,
    ACCENT, DIM, NOTE, FAINT, GREEN, ROSE,
    ADMIN_TAG, SUPER_TAG,
    add_header, add_section, add_footer, add_rect,
    build_image, cached_image_path, _c, _plain, _runs, _row, _sec,
)

BRAND_RUNS = _runs(("QQ 官方群 × ", FAINT), ("Minecraft", ACCENT), (" 独立命令帮助", FAINT))
FOOT_RUNS = _runs(
    ("官方身份使用成员标识，不显示数字 QQ 号 · 成员检测缺昵称/权限时标为无法核验 · ", NOTE),
    ("未决写操作不会自动重发", ACCENT))

HELP_CARDS = [
    [
        _sec(GREEN, "登记与查询", [
            _row(_runs(("/mc setup ", ACCENT), ("<验证码>", DIM)),
                 tags=[ADMIN_TAG],
                 desc=_plain("后台「新增群」生成验证码后，在目标群发送以创建本群配置")),
            _row(_runs(("/在线 ", ACCENT), ("[-s 服务器名]", DIM)),
                 pills=["/mc status"],
                 desc=_plain("在线状态卡片；不带参数查全部，隐藏服不出卡")),
            _row(_runs(("/查询 ", ACCENT), ("<玩家名> [服务器名]", DIM)),
                 pills=["/mc stats"],
                 desc=_plain("玩家数据统计图；服务器名也可写成 -s 服务器名")),
            _row(_runs(("/排行 ", ACCENT), ("[类型] [总|周|月] -s <服务器名>", DIM)),
                 pills=["/mc rank"],
                 desc=_plain("时长 / 死亡 / 击杀生物 / 钓鱼排行；周/月只看近 7/30 天活跃玩家")),
            _row(_runs(("/tps ", ACCENT), ("[服务器名]", DIM)),
                 pills=["/mc tps"],
                 desc=_plain("查询服务器 TPS，缺省查询全部；也可写 -s 服务器名")),
            _row(_runs(("/mc page ", ACCENT), ("<页码>", DIM)),
                 desc=_plain("继续查看上一条分页结果（10 分钟内有效）")),
            _row(_runs(("/mc guide ", ACCENT), ("[1|2]", DIM)),
                 pills=["/mc 命令指引"],
                 desc=_plain("Markdown 按钮菜单；点击填入命令，发送后执行；2 为管理员页")),
        ]),
    ],
    [
        _sec(ACCENT, "账号绑定", [
            _row(_runs(("/绑定 ", ACCENT), ("<6位码> [-s 服务器名]", DIM)),
                 pills=["/bind"],
                 desc=_plain("取码账号须满足本群昵称规则（括号 / 等于 / 包含 / 模板）")),
            _row(_runs(("/解绑 ", ACCENT), ("[游戏名|all|@成员] [-s 服务器名]", DIM)),
                 pills=["/unbind"],
                 desc=_plain("缺省或 all 解绑自己名下全部账号；@成员与他人完整关联需管理员")),
            _row(_runs(("/查绑定 ", ACCENT), ("[游戏名|@成员] [-s 服务器名]", DIM)),
                 pills=["/绑定状态", "/checkbind"],
                 desc=_plain("查询绑定关系；官方平台使用成员标识，不显示数字 QQ 号")),
            _row(_runs(("/mc operation ", ACCENT), ("<编号> [retry|cancel]", DIM)),
                 desc=_plain("原请求人查询、重试或取消未决写操作；结果未知时不会自动重发")),
            _row(_c("/绑定帮助"), pills=["/mc bindhelp"],
                 desc=_plain("查看官方群独立的配置与绑定流程")),
            _row(_c("/绑定检测"), pills=["/mc bindcheck"], tags=[ADMIN_TAG],
                 desc=_plain("生成本群未绑定游戏账号成员名单")),
        ]),
    ],
    [
        _sec(ROSE, "管理与写操作", [
            _row(_runs(("/广播 ", ACCENT), ("[服务器名|all] <消息>", DIM)),
                 pills=["/mc say"], tags=[ADMIN_TAG],
                 desc=_plain("游戏内广播；省略服务器名或写 all 发往全部已装模组服务器")),
            _row(_runs(("/执行 ", ACCENT), ("[服务器名] <命令>", DIM)),
                 pills=["/mc exec"], tags=[ADMIN_TAG],
                 desc=_plain("在指定服务器执行控制台命令；需先配置该服独立 Token")),
            _row(_runs(("/mc ", ACCENT), ("add|edit|del|hide|show", DIM)),
                 tags=[ADMIN_TAG],
                 desc=_plain("维护服务器名称、主机、端口、备注与隐藏状态")),
            _row(_runs(("/mc ", ACCENT), ("batchadd|batchremove|move|swap", DIM)),
                 tags=[ADMIN_TAG],
                 desc=_plain("批量添加 / 删除与排序；整批校验，失败不会写入")),
            _row(_runs(("/mc token ", ACCENT), ("<服务器名> <Token|清除>", DIM)),
                 tags=[SUPER_TAG],
                 desc=_plain("设置该服控制台 Token；不会在群内回显明文")),
            _row(_runs(("/mc lastonline ", ACCENT), ("开|关", DIM)),
                 tags=[ADMIN_TAG],
                 desc=_plain("状态卡片是否显示「上次在线」")),
            _row(_runs(("/mc nicknamecheck ", ACCENT), ("开|关|忽略|取消忽略|忽略列表", DIM)),
                 tags=[ADMIN_TAG],
                 desc=_plain("本群昵称检测开关与忽略名单")),
            _row(_runs(("/mc command ", ACCENT), ("<操作编号>", DIM)),
                 desc=_plain("查询写操作回执；结果未知时以回执为准，不要重复提交")),
            _row(_runs(("/mc whitelist ", ACCENT), ("list", DIM)),
                 tags=[SUPER_TAG],
                 desc=_plain("查看超级管理员、白名单管理员与黑名单配置（官方标识脱敏）")),
            _row(_runs(("/mc whitelist ", ACCENT), ("add|remove|super add|remove @成员", DIM)),
                 tags=[SUPER_TAG],
                 desc=_plain("按 @成员 维护本环境补充名单（按当前群标识生效）；不回显官方标识")),
            _row(_runs(("/mc blacklist ", ACCENT), ("add|remove @成员", DIM)),
                 tags=[SUPER_TAG],
                 desc=_plain("按 @成员 管理本环境黑名单（按当前群标识生效）；公共名单仍由 WebUI 管理")),
        ]),
    ],
]


def _build_official_help(draw, ops, doc, y):
    y = add_header(ops, draw, "MCSight 官方命令帮助", BRAND_RUNS, y)
    inner_w = WIDTH - PAD * 2 - CARD_PAD_X * 2
    for sections in doc:
        card_top = y
        cy = y + 16
        for sec in sections:
            cy = add_section(draw, ops, PAD + CARD_PAD_X, cy, inner_w, sec)
        card_h = cy - card_top + 16
        add_rect(ops, 'bg0', PAD, card_top, WIDTH - PAD * 2, card_h,
                 CARD_RADIUS, CARD_BG)
        y = card_top + card_h + CARD_GAP
    y = add_footer(ops, draw, FOOT_RUNS, y)
    return y


def draw_official_help_image():
    return build_image(_build_official_help, HELP_CARDS)


def get_official_help_image_path():
    return cached_image_path('official_help', 'mc_official_help.png',
                             draw_official_help_image)
