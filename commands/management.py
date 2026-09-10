from astrbot.api.event import AstrMessageEvent
from ..config.server_config import ConfigManager
from ..config.whitelist_config import WhitelistManager

async def handle_add(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 3 or len(parts) > 4:
        yield event.plain_result("用法: /mc add <名称> <IP> [端口]")
        return
    name = parts[1]
    host = parts[2]
    port = None
    if len(parts) == 4:
        try:
            port = int(parts[3])
            if port < 1 or port > 65535:
                raise ValueError
        except ValueError:
            yield event.plain_result("端口必须是 1-65535 的数字")
            return
    success = config.add_server(name, host, port)
    yield event.plain_result(f"添加{'成功' if success else '失败（名称已存在）'}：{name}")

async def handle_remove(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 2:
        yield event.plain_result("用法: /mc remove <名称>")
        return
    success = config.remove_server(parts[1])
    yield event.plain_result(f"删除{'成功' if success else '失败（未找到）'}：{parts[1]}")

async def handle_edit(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 4:
        yield event.plain_result(
            "用法:\n"
            "/mc edit <名称> name <新名称>\n"
            "/mc edit <名称> host <新IP>\n"
            "/mc edit <名称> port <新端口>"
        )
        return
    target = parts[1]
    mode = parts[2].lower()
    value = parts[3]
    if mode == "name":
        success = config.rename_server(target, value)
        yield event.plain_result(f"重命名{'成功' if success else '失败（名称不存在或新名称已占用）'}。")
    elif mode == "host":
        success = config.edit_server_host(target, value)
        yield event.plain_result(f"修改IP{'成功' if success else '失败（名称不存在）'}。")
    elif mode == "port":
        try:
            new_port = int(value)
            if new_port < 1 or new_port > 65535:
                raise ValueError
        except ValueError:
            yield event.plain_result("端口必须是 1-65535 的数字")
            return
        success = config.edit_server_port(target, new_port)
        yield event.plain_result(f"修改端口{'成功' if success else '失败（名称不存在）'}。")
    else:
        yield event.plain_result("第二个参数必须为 name、host 或 port。")

async def handle_batchadd(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 2:
        yield event.plain_result("用法: /mc batchadd name1:ip1[:port1],name2:ip2[:port2],...")
        return
    s, f = config.batch_add(parts[1])
    yield event.plain_result(f"批量添加：成功 {s} 个" + (f"，失败: {', '.join(f)}" if f else ""))

async def handle_batchremove(event: AstrMessageEvent, config: ConfigManager, parts: list):
    if len(parts) < 2:
        yield event.plain_result("用法: /mc batchremove name1,name2,...")
        return
    s, f = config.batch_remove(parts[1])
    yield event.plain_result(f"批量删除：成功 {s} 个" + (f"，失败: {', '.join(f)}" if f else ""))

async def handle_list(event: AstrMessageEvent, config: ConfigManager):
    servers = config.get_all_servers()
    if not servers:
        yield event.plain_result("当前没有任何服务器。")
    else:
        wm = WhitelistManager()
        default_port = wm.default_api_port
        lines = ["已添加的服务器："]
        for s in servers:
            port = s.get("api_port")
            if port is not None:
                port_display = f" (端口: {port})"
            else:
                port_display = f" (默认端口: {default_port})"
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