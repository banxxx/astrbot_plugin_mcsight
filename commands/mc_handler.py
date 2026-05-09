from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from ..config.server_config import (add_server, remove_server, edit_server,
                                   batch_add, batch_remove, get_all_servers)
from ..features.player_status.controller import run_player_status

async def handle_mc_command(event: AstrMessageEvent):
    """解析 /mc 子命令并转发到对应功能板块"""
    msg = event.message_str.strip()

    # 统一去除可能存在的命令前缀 (支持 /mc 或 mc)
    if msg.startswith('/'):
        msg = msg[1:]  # 去掉开头的 '/'
    if msg.lower().startswith('mc'):
        msg = msg[2:].strip()
        
    if not msg:
        yield event.plain_result(
            "请提供子命令。使用 /mc help 查看帮助。"
        )
        return

    parts = msg.split()
    sub_cmd = parts[0].lower()

    # ---- 帮助 ----
    if sub_cmd == "help":
        yield event.plain_result(
            "/mc status              查看所有服务器在线情况\n"
            "/mc add <名字> <IP>      添加服务器\n"
            "/mc remove <名字>        删除服务器\n"
            "/mc edit <名字> <新IP>   修改服务器IP\n"
            "/mc batchadd <name1:ip1,name2:ip2,...>\n"
            "/mc batchremove <name1,name2,...>\n"
            "/mc list                列出所有已添加服务器"
        )

    # ---- 状态查询（核心功能板块）----
    elif sub_cmd == "status":
        yield event.plain_result("正在查询服务器，请稍候...")
        async for result in run_player_status(event):
            yield result

    # ---- 添加 ----
    elif sub_cmd == "add":
        if len(parts) < 3:
            yield event.plain_result("用法: /mc add <名称> <IP>")
            return
        success = add_server(parts[1], parts[2])
        yield event.plain_result(
            f"添加{'成功' if success else '失败（名称已存在）'}：{parts[1]}"
        )

    # ---- 删除 ----
    elif sub_cmd == "remove":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc remove <名称>")
            return
        success = remove_server(parts[1])
        yield event.plain_result(
            f"删除{'成功' if success else '失败（未找到）'}：{parts[1]}"
        )

    # ---- 修改 ----
    elif sub_cmd == "edit":
        if len(parts) < 3:
            yield event.plain_result("用法: /mc edit <名称> <新IP>")
            return
        success = edit_server(parts[1], parts[2])
        yield event.plain_result(
            f"修改{'成功' if success else '失败（未找到）'}：{parts[1]} -> {parts[2]}"
        )

    # ---- 批量添加 ----
    elif sub_cmd == "batchadd":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc batchadd name1:ip1,name2:ip2,...")
            return
        s, f = batch_add(parts[1])
        yield event.plain_result(
            f"批量添加：成功 {s} 个" + (f"，失败: {', '.join(f)}" if f else "")
        )

    # ---- 批量删除 ----
    elif sub_cmd == "batchremove":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc batchremove name1,name2,...")
            return
        s, f = batch_remove(parts[1])
        yield event.plain_result(
            f"批量删除：成功 {s} 个" + (f"，失败: {', '.join(f)}" if f else "")
        )

    # ---- 列表 ----
    elif sub_cmd == "list":
        servers = get_all_servers()
        if not servers:
            yield event.plain_result("当前没有任何服务器。")
        else:
            yield event.plain_result(
                "已添加的服务器：\n" +
                "\n".join(f"{s['name']} -> {s['host']}" for s in servers)
            )

    else:
        yield event.plain_result(f"未知子命令: {sub_cmd}，使用 /mc help 查看帮助。")