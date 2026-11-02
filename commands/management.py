from astrbot.api.event import AstrMessageEvent
from ..config.server_config import ConfigManager, MAX_SERVERS_PER_GROUP, validate_remark, validate_server_name
from ..config.whitelist_config import WhitelistManager
from ..utils.host_validator import normalize_host_input, validate_host
from ..utils.permission import is_astrbot_super_admin

ADD_USAGE = (
    "用法: /mc add <名称> <主机[:游戏端口]> [模组API端口]\n"
    "例: /mc add 一服 vefc.ovo.ci\n"
    "    /mc add 一服 vefc.ovo.ci:25501\n"
    "    /mc add 一服 1.2.3.4 25566\n"
    "说明: 名称不能含空格；主机与端口之间的空格会被忽略；"
    "单独的数字参数是模组 API 端口（最多一个），游戏端口要写在主机里。"
)
EDIT_USAGE = (
    "用法:\n"
    "/mc edit <名称> name <新名称>\n"
    "/mc edit <名称> host <主机[:游戏端口]>\n"
    "/mc edit <名称> port <模组API端口>\n"
    "/mc edit <名称> remark <备注>\n"
    "说明: host 模式支持「主机 : 端口」的空格写法；port 改的是模组 API 端口。"
)


def _parse_host_and_ports(tokens: list, allow_api_port: bool = True):
    """解析「主机 [游戏端口] [模组API端口]」，容忍冒号前后的空格和全角冒号。

    tokens 不含服务器名。返回 (host, api_port_text, error)；
    error 非空时 host 为 None。
    """
    tokens = [t.replace("：", ":") for t in tokens]
    if not tokens:
        return None, None, "缺少主机地址"
    host = tokens[0]
    if host.startswith(":"):
        return None, None, "缺少主机地址（冒号前面没有地址）"

    pos = 1
    if host.endswith(":"):
        if pos >= len(tokens) or not tokens[pos].isdecimal():
            return None, None, "游戏端口格式不对：冒号后面要写 1-65535 的数字"
        host += tokens[pos]
        pos += 1

    api_tokens = []
    while pos < len(tokens):
        tok = tokens[pos]
        if tok == ":":
            if pos + 1 >= len(tokens) or not tokens[pos + 1].isdecimal():
                return None, None, "游戏端口格式不对：冒号后面要写 1-65535 的数字"
            fragment = ":" + tokens[pos + 1]
            pos += 2
        elif tok.startswith(":"):
            if not tok[1:].isdecimal():
                return None, None, "游戏端口格式不对：冒号后面要写 1-65535 的数字"
            fragment = tok
            pos += 1
        elif tok.endswith(":"):
            if pos + 1 >= len(tokens) or not tokens[pos + 1].isdecimal():
                return None, None, "游戏端口格式不对：冒号后面要写 1-65535 的数字"
            fragment = tok + tokens[pos + 1]
            pos += 2
        else:
            if allow_api_port and tok.isdecimal():
                api_tokens.append(tok)
                pos += 1
                continue
            return None, None, f"主机地址只能写一个，无法识别多余参数「{tok}」"
        # fragment 只会是 ":端口"，主机里已经有端口就不能再写
        if ":" in host:
            return None, None, "游戏端口只能写一次（主机里已经带了端口）"
        host += fragment

    if len(api_tokens) > 1:
        return None, None, "模组 API 端口只能写一个，收到：" + "、".join(api_tokens)
    return host, (api_tokens[0] if api_tokens else None), None


def _parse_add_args(tokens: list):
    """解析 /mc add 的参数，返回 (name, host, api_port, error)。"""
    if len(tokens) < 2:
        return None, None, None, "参数不足：至少需要「名称」和「主机」。\n" + ADD_USAGE

    name = tokens[0]
    host, api_text, err = _parse_host_and_ports(tokens[1:], allow_api_port=True)
    if err:
        hint = ("\n提示：服务器名不能含空格，请用 - 或 _ 连接"
                if "无法识别多余参数" in err else "")
        return None, None, None, f"{err}{hint}\n{ADD_USAGE}"

    api_port = None
    if api_text is not None:
        try:
            api_port = int(api_text)
            if not 1 <= api_port <= 65535:
                raise ValueError
        except ValueError:
            return None, None, None, (
                f"模组 API 端口必须是 1-65535 的数字（收到「{api_text}」）。\n"
                "提示：单独的数字参数是模组 API 端口；游戏端口请写成 主机:端口。\n"
                + ADD_USAGE)
    return name, host, api_port, None


def _caller_allows_private(event: AstrMessageEvent) -> bool:
    """内网/回环地址只允许超级管理员（插件配置或 AstrBot admins_id）添加，
    防止群管理员借机器人探测内网。"""
    wm = WhitelistManager()
    return is_astrbot_super_admin(event) or wm.is_super_admin(str(event.get_sender_id()))


async def handle_add(event: AstrMessageEvent, config: ConfigManager, parts: list):
    name, host, port, err = _parse_add_args(parts[1:])
    if err:
        yield event.plain_result("❌ 添加失败：" + err)
        return

    host = normalize_host_input(host)
    name_err = validate_server_name(name)
    if name_err:
        yield event.plain_result(f"❌ 添加失败：{name_err}")
        return
    host_err = validate_host(host, allow_private=_caller_allows_private(event))
    if host_err:
        yield event.plain_result(
            f"❌ 添加失败：{host_err}\n"
            f"主机：{host}\n"
            "提示：游戏端口写成 主机:端口；主机与端口之间的空格会被忽略。")
        return
    if len(config.get_all_servers()) >= MAX_SERVERS_PER_GROUP:
        yield event.plain_result(
            f"❌ 添加失败：每个群最多绑定 {MAX_SERVERS_PER_GROUP} 个服务器。")
        return
    if not config.add_server(name, host, port):
        yield event.plain_result(f"❌ 添加失败：名称「{name}」已存在。")
        return

    lines = [f"✅ 添加成功：{name}", f"主机：{host}"]
    if port is None:
        lines.append("模组 API 端口：未配置（/执行、/广播 不可用）")
    else:
        lines.append(f"模组 API 端口：{port}")
        if port == 25565 and ":" not in host:
            lines.append("提示：25565 已按模组 API 端口处理；"
                         "若本意是游戏端口，请写成 主机:25565。")
    if host.endswith(":25565"):
        lines.append("提示：显式写了默认端口 25565；使用 SRV 的服务器建议只写域名。")
    yield event.plain_result("\n".join(lines))

async def handle_remove(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 2:
        yield event.plain_result("用法: /mc remove <名称>")
        return
    success = config.remove_server(parts[1])
    yield event.plain_result(f"删除{'成功' if success else '失败（未找到）'}：{parts[1]}")

async def handle_edit(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 3:
        yield event.plain_result(EDIT_USAGE)
        return
    target = parts[1]
    mode = parts[2].lower()

    if mode not in ("name", "host", "port", "remark"):
        yield event.plain_result(
            f"❌ 修改失败：未知字段「{parts[2]}」。\n{EDIT_USAGE}")
        return
    if len(parts) < 4:
        yield event.plain_result(EDIT_USAGE)
        return

    if mode == "name":
        if len(parts) > 4:
            yield event.plain_result(
                "❌ 修改失败：名称不能含空格。\n" + EDIT_USAGE)
            return
        value = parts[3]
        name_err = validate_server_name(value)
        if name_err:
            yield event.plain_result(f"❌ 修改失败：{name_err}")
            return
        success = config.rename_server(target, value)
        yield event.plain_result(
            f"重命名{'成功' if success else '失败（名称不存在或新名称已占用）'}。")
    elif mode == "host":
        host, _, err = _parse_host_and_ports(parts[3:], allow_api_port=False)
        if err:
            hint = ("\n提示：host 模式只接受一个地址；游戏端口请写成 主机:端口，"
                    f"模组 API 端口请用 /mc edit {target} port <端口>"
                    if "无法识别多余参数" in err else "")
            yield event.plain_result(
                f"❌ 修改失败：{err}{hint}\n{EDIT_USAGE}")
            return
        host = normalize_host_input(host)
        host_err = validate_host(host, allow_private=_caller_allows_private(event))
        if host_err:
            yield event.plain_result(
                f"❌ 修改失败：{host_err}\n"
                f"主机：{host}\n"
                "提示：游戏端口写成 主机:端口；主机与端口之间的空格会被忽略。")
            return
        success = config.edit_server_host(target, host)
        yield event.plain_result(
            f"修改主机地址{'成功' if success else '失败（名称不存在）'}。")
    elif mode == "port":
        if len(parts) > 4:
            yield event.plain_result(
                "❌ 修改失败：模组 API 端口只能写一个数字。\n" + EDIT_USAGE)
            return
        value = parts[3]
        try:
            new_port = int(value)
            if new_port < 1 or new_port > 65535:
                raise ValueError
        except ValueError:
            yield event.plain_result(
                f"❌ 修改失败：模组 API 端口必须是 1-65535 的数字"
                f"（收到「{value}」）。\n"
                "提示：游戏端口不在这里改，请用 "
                f"/mc edit {target} host <主机:端口>。\n" + EDIT_USAGE)
            return
        success = config.edit_server_port(target, new_port)
        yield event.plain_result(
            f"修改模组 API 端口{'成功' if success else '失败（名称不存在）'}。")
    else:
        # 备注可含空格，取 mode 之后的全部片段
        remark = " ".join(parts[3:]).strip()
        remark_err = validate_remark(remark)
        if remark_err:
            yield event.plain_result(f"❌ 修改失败：{remark_err}")
            return
        success = config.edit_server_remark(target, remark)
        yield event.plain_result(
            f"修改备注{'成功' if success else '失败（名称不存在）'}。")

# Token 会进 Bearer 头：必须无空白可见 ASCII；长度下限与模组端的弱口令拒绝启动门一致
MIN_TOKEN_CHARS = 16
MAX_TOKEN_CHARS = 128
CLEAR_TOKEN_WORDS = ("清除", "删除", "unset", "clear", "off")


def _validate_console_token(raw: str) -> str:
    """返回错误原因，合法返回空串"""
    if len(raw) < MIN_TOKEN_CHARS:
        return f"Token 太短（至少 {MIN_TOKEN_CHARS} 个字符），当前 {len(raw)} 个"
    if len(raw) > MAX_TOKEN_CHARS:
        return f"Token 太长（最多 {MAX_TOKEN_CHARS} 个字符），当前 {len(raw)} 个"
    if any(ord(c) < 0x21 or ord(c) > 0x7e for c in raw):
        return "Token 只能由不含空格的可见字符组成"
    return ""


async def handle_token(event: AstrMessageEvent, config: ConfigManager, parts: list):
    """/mc token <服务器名> <Token|清除>：按服务器单独配置命令 Token（超管）"""
    if len(parts) < 3:
        yield event.plain_result(
            "用法:\n"
            "/mc token <服务器名> <Token>   为该服设置命令 Token\n"
            "/mc token <服务器名> 清除      关闭该服的 /执行"
        )
        return
    target = parts[1]
    value = parts[2]
    servers = config.get_all_servers()
    if not any(s["name"] == target for s in servers):
        yield event.plain_result(f"未找到名为「{target}」的服务器。")
        return

    if value in CLEAR_TOKEN_WORDS or value.lower() in CLEAR_TOKEN_WORDS:
        config.edit_server_console_token(target, "")
        yield event.plain_result(f"✅ 已清除「{target}」的命令 Token，该服务器的 /执行 已关闭。")
        return

    err = _validate_console_token(value)
    if err:
        yield event.plain_result(f"❌ 设置失败：{err}")
        return

    entry = next(s for s in servers if s["name"] == target)
    config.edit_server_console_token(target, value)
    # 不回显 Token 明文：聊天记录和日志都会留档
    reply = (f"✅ 已为「{target}」设置命令 Token（{len(value)} 个字符）。\n"
             f"需与该服务器模组的 consoleToken 完全一致；模组侧改动要重启服务器才生效。")
    if entry.get("api_port") is None:
        reply += f"\n⚠️ 该服务器还没有配置 API 端口，先用 /mc edit {target} port <端口> 补上。"
    yield event.plain_result(reply)

async def handle_batchadd(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 2:
        yield event.plain_result(
            "用法: /mc batchadd 名1:主机1[:模组API端口1],名2:主机2[:模组API端口2],...\n"
            "说明: 条目用逗号分隔；第三段是模组 API 端口，不是游戏端口；"
            "主机要带游戏端口时请用 /mc add 逐个添加。")
        return
    allow_private = _caller_allows_private(event)
    # 空格可能出现在逗号/冒号旁边，拼回去交给 batch_add 逐段 strip；
    # 批量语法不支持名称/主机内部含空格。
    raw = " ".join(parts[1:])
    s, f = config.batch_add(
        raw,
        host_validator=lambda h: validate_host(h, allow_private=allow_private),
        name_validator=validate_server_name)
    reply = f"批量添加：成功 {s} 个"
    if f:
        reply += f"，失败: {', '.join(f)}"
    yield event.plain_result(reply)

async def handle_batchremove(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 2:
        yield event.plain_result("用法: /mc batchremove 名1,名2,...（名称不能含空格）")
        return
    s, f = config.batch_remove("".join(parts[1:]))
    yield event.plain_result(f"批量删除：成功 {s} 个" + (f"，失败: {', '.join(f)}" if f else ""))

async def handle_list(event: AstrMessageEvent, config: ConfigManager):
    servers = config.get_all_servers()
    if not servers:
        yield event.plain_result("当前没有任何服务器。")
    else:
        wm = WhitelistManager()
        lines = ["已添加的服务器："]
        for s in servers:
            # 端口必须能真正拿来调模组才算「装了模组」，否则 /执行、/广播 的可用性会被说反
            port_display = (f" (模组端口: {wm.get_server_port(s)})"
                            if wm.has_mod_api(s) else " (未装模组)")
            lines.append(f"{s['name']} -> {s['host']}{port_display}")
        yield event.plain_result("\n".join(lines))

async def handle_move(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 3:
        yield event.plain_result("用法: /mc move <名称> <位置序号(从0开始)>")
        return
    name = parts[1]
    try:
        pos = int(parts[2])
    except ValueError:
        yield event.plain_result("位置序号必须是整数。")
        return
    success = config.move_server(name, pos)
    yield event.plain_result(f"移动{'成功' if success else '失败（名称不存在或序号无效）'}。")

async def handle_swap(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 3:
        yield event.plain_result("用法: /mc swap <名称1> <名称2>")
        return
    success = config.swap_servers(parts[1], parts[2])
    yield event.plain_result(f"交换{'成功' if success else '失败（请检查名称是否正确）'}。")

async def handle_lastonline(event: AstrMessageEvent, config: ConfigManager, parts: list):
    """
    处理 /上次在线 命令
    用法：
      /上次在线          查看当前状态
      /上次在线 开       开启显示
      /上次在线 关       关闭显示
    """
    # 参数解析
    action = None
    if len(parts) >= 2:
        action = parts[1].strip().lower()

    if action in ("开", "on", "true", "1", "开启"):
        config.set_last_online_enabled(True)
        yield event.plain_result("✅ 已开启「上次在线」显示，下次查询服务器时会显示最后活动时间。")
    elif action in ("关", "off", "false", "0", "关闭"):
        config.set_last_online_enabled(False)
        yield event.plain_result("✅ 已关闭「上次在线」显示。")
    elif action is None or action in ("查询", "status", "state"):
        enabled = config.is_last_online_enabled()
        state = "已开启" if enabled else "已关闭"
        yield event.plain_result(
            f"当前群组的「上次在线」显示：{state}\n"
            f"用法：/上次在线 开 或 /上次在线 关"
        )
    else:
        yield event.plain_result(
            "用法：\n"
            "/上次在线          查看当前状态\n"
            "/上次在线 开       开启显示\n"
            "/上次在线 关       关闭显示"
        )