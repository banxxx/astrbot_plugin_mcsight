import asyncio
import aiohttp
import json
import re
from urllib.parse import urlparse
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from astrbot.api import logger  # 添加 logger 导入
from ..config.server_config import ConfigManager
from ..features.player_status.checker import send_broadcast, fetch_tps, send_broadcast_via_mod, fetch_tps_via_mod
from ..features.player_status.controller import run_player_status, run_player_stats, build_plugin_api_url
from ..features.help_image.image_generator import draw_help_image
from ..utils.permission import check_permission, is_group_admin, LEVEL_GROUP_ADMIN
from ..config.whitelist_config import WhitelistManager
from ..features.player_status.tps_image_generator import draw_tps_image

async def handle_mc_command(event: AstrMessageEvent):
    group_id = event.get_group_id()
    if group_id:
        config = ConfigManager(group_id=group_id)
    else:
        config = ConfigManager(session_id=event.session_id)

    msg = event.message_str.strip()
    if msg.startswith('/'):
        msg = msg[1:]
    if msg.lower().startswith('mc'):
        msg = msg[2:].strip()

    if not msg:
        yield event.plain_result("请提供子命令。使用 /mc help 查看帮助。")
        return

    parts = msg.split()
    sub_cmd = parts[0].lower()

    # ---------- 白名单管理命令（超级管理员专属）----------
    if sub_cmd == "whitelist":
        if not await check_permission(event, "whitelist"):
            yield event.plain_result("权限不足：该命令仅限超级管理员使用。")
            return
        wm = WhitelistManager()
        if len(parts) < 2:
            yield event.plain_result(
                "白名单管理已迁移至 WebUI 插件配置面板。\n"
                "可用命令：/mc whitelist list - 查看当前白名单配置"
            )
            return
        action = parts[1].lower()
        if action == "list":
            msg_list = [
                "当前白名单配置：",
                f"超级管理员：{', '.join(wm.super_admins) or '无'}",
                f"白名单管理员：{', '.join(wm.whitelist_admins) or '无'}",
                f"黑名单：{', '.join(wm.blacklist) or '无'}"
            ]
            yield event.plain_result("\n".join(msg_list))
        elif action in ("add", "remove"):
            yield event.plain_result(
                "白名单的添加和移除请前往 AstrBot WebUI 插件配置面板操作。\n"
                "使用 /mc whitelist list 可以查看当前配置。"
            )
        else:
            yield event.plain_result("未知操作，支持：list（查看配置）")
        return

    # ---------- 其他命令的权限检查 ----------
    admin_cmds = {"add", "remove", "edit", "batchadd", "batchremove", "move", "swap"}
    if sub_cmd in admin_cmds:
        if not await check_permission(event, sub_cmd):
            yield event.plain_result("权限不足：该操作需要群管理员或插件管理员权限。")
            return

    # ---------- 命令逻辑 ----------
    if sub_cmd == "help":
        try:
            img = draw_help_image()
            img.save("mc_help_temp.png")
            yield event.chain_result([AstrImage(file="mc_help_temp.png")])
        except Exception as e:
            yield event.plain_result(f"生成帮助图片失败: {e}")

    elif sub_cmd == "status":
        async for result in run_player_status(event, config):
            yield result

    elif sub_cmd == "stats":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc stats <玩家名> <服务器名> 或 /mc stats <玩家名> -s <服务器名>")
            return
        player_name = parts[1]
        target_server = None
        if len(parts) >= 3:
            if parts[2] == "-s":
                if len(parts) >= 4:
                    target_server = parts[3]
                else:
                    yield event.plain_result("用法: /mc stats <玩家名> -s <服务器名>")
                    return
            else:
                target_server = parts[2]
        async for result in run_player_stats(event, config, player_name, target_server):
            yield result

    elif sub_cmd == "add":
        if len(parts) < 3:
            yield event.plain_result("用法: /mc add <名称> <IP>")
            return
        success = config.add_server(parts[1], parts[2])
        yield event.plain_result(
            f"添加{'成功' if success else '失败（名称已存在）'}：{parts[1]}"
        )

    elif sub_cmd == "remove":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc remove <名称>")
            return
        success = config.remove_server(parts[1])
        yield event.plain_result(
            f"删除{'成功' if success else '失败（未找到）'}：{parts[1]}"
        )

    elif sub_cmd == "edit":
        if len(parts) < 4:
            yield event.plain_result(
                "用法:\n"
                "/mc edit <名称> name <新名称>\n"
                "/mc edit <名称> host <新IP>"
            )
            return
        target = parts[1]
        mode = parts[2].lower()
        value = parts[3]
        if mode == "name":
            success = config.rename_server(target, value)
            yield event.plain_result(
                f"重命名{'成功' if success else '失败（名称不存在或新名称已占用）'}。"
            )
        elif mode == "host":
            success = config.edit_server_host(target, value)
            yield event.plain_result(
                f"修改IP{'成功' if success else '失败（名称不存在）'}。"
            )
        else:
            yield event.plain_result("第二个参数必须为 name 或 host。")

    elif sub_cmd == "batchadd":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc batchadd name1:ip1,name2:ip2,...")
            return
        s, f = config.batch_add(parts[1])
        yield event.plain_result(
            f"批量添加：成功 {s} 个" + (f"，失败: {', '.join(f)}" if f else "")
        )

    elif sub_cmd == "batchremove":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc batchremove name1,name2,...")
            return
        s, f = config.batch_remove(parts[1])
        yield event.plain_result(
            f"批量删除：成功 {s} 个" + (f"，失败: {', '.join(f)}" if f else "")
        )

    elif sub_cmd == "list":
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("当前没有任何服务器。")
        else:
            yield event.plain_result(
                "已添加的服务器：\n" +
                "\n".join(f"{s['name']} -> {s['host']}" for s in servers)
            )

    elif sub_cmd == "move":
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

    elif sub_cmd == "swap":
        if len(parts) < 3:
            yield event.plain_result("用法: /mc swap <名称1> <名称2>")
            return
        success = config.swap_servers(parts[1], parts[2])
        yield event.plain_result(f"交换{'成功' if success else '失败（请检查名称是否正确）'}。")

    elif sub_cmd == "say":
        if len(parts) < 2:
            yield event.plain_result("用法: /mc say <消息> 或 /mc say -s <服务器名> <消息>")
            return
        target_server = None
        msg_start = 1
        if parts[1] == "-s":
            if len(parts) < 4:
                yield event.plain_result("用法: /mc say -s <服务器名> <消息>")
                return
            target_server = parts[2]
            msg_start = 3
        message = " ".join(parts[msg_start:])
        if not message:
            yield event.plain_result("消息内容不能为空")
            return
        MAX_LENGTH = 40
        if len(message) > MAX_LENGTH:
            yield event.plain_result(f"广播消息过长（最多 {MAX_LENGTH} 个字符），当前长度：{len(message)}")
            return
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return

        wm = WhitelistManager()
        use_mod_api = wm.enable_mod_api
        mod_api_port = wm.mod_api_port
        mod_token = wm.mod_api_token
        plugin_api_port = wm.plugin_api_port

        if target_server:
            target = next((s for s in servers if s["name"] == target_server), None)
            if not target:
                yield event.plain_result(f"未找到名为「{target_server}」的服务器。")
                return
            servers_to_broadcast = [target]
        else:
            servers_to_broadcast = servers

        success_count = 0
        fail_count = 0
        for srv in servers_to_broadcast:
            host = srv["host"]
            success = False
            if use_mod_api:
                success = await send_broadcast_via_mod(host, mod_api_port, mod_token, message)
            else:
                api_url = build_plugin_api_url(host, plugin_api_port)
                if api_url:
                    base_url = api_url.replace("/api/status", "")
                    success = await send_broadcast(base_url, message)
            if success:
                success_count += 1
            else:
                fail_count += 1

        if fail_count == 0:
            if target_server:
                yield event.plain_result(f"✔️ 已在服务器「{target_server}」发送广播。")
            else:
                yield event.plain_result(f"✔️ 已在所有 {success_count} 个服务器发送广播。")
        else:
            yield event.plain_result(f"❌ 广播发送完成，成功 {success_count} 个，失败 {fail_count} 个。")

    elif sub_cmd == "tps":
        target_server = None
        if len(parts) >= 2:
            if parts[1] == "-s":
                if len(parts) >= 3:
                    target_server = parts[2]
                else:
                    yield event.plain_result("用法: /mc tps [-s 服务器名]")
                    return
            else:
                target_server = parts[1]
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return

        wm = WhitelistManager()
        use_mod_api = wm.enable_mod_api
        mod_api_port = wm.mod_api_port
        mod_token = wm.mod_api_token
        plugin_api_port = wm.plugin_api_port

        if target_server:
            target = next((s for s in servers if s["name"] == target_server), None)
            if not target:
                yield event.plain_result(f"未找到名为「{target_server}」的服务器。")
                return
            targets = [target]
        else:
            targets = servers

        tasks = []
        for srv in targets:
            host = srv["host"]
            if use_mod_api:
                tasks.append(fetch_tps_via_mod(host, mod_api_port, mod_token))
            else:
                api_url = build_plugin_api_url(host, plugin_api_port)
                if api_url:
                    base_url = api_url.replace("/api/status", "")
                    tasks.append(fetch_tps(base_url))
                else:
                    continue

        if not tasks:
            yield event.plain_result("没有有效的服务器可查询。")
            return

        results = await asyncio.gather(*tasks)
        server_data = []
        for srv, tps in zip(targets, results):
            server_data.append({
                "name": srv["name"],
                "tps": tps
            })
        try:
            img = draw_tps_image(server_data)
            img.save("tps_temp.png")
            yield event.chain_result([AstrImage(file="tps_temp.png")])
        except Exception as e:
            logger.error(f"生成 TPS 图片失败: {e}")
            yield event.plain_result(f"生成图片失败: {e}")

    # ---------- bind 命令（支持多服务器）----------
    elif sub_cmd == "bind":
        wm = WhitelistManager()
        if not wm.enable_mod_api:
            yield event.plain_result("❌ 模组 API 功能未启用，请在插件配置中启用「enable_mod_api」。")
            return
        port = wm.mod_api_port
        token = wm.mod_api_token
        if not token:
            yield event.plain_result("模组 API Token 未配置，请联系管理员设置。")
            return

        qq = str(event.get_sender_id())

        # 获取游戏ID
        if len(parts) >= 2:
            game_id = parts[1]
        else:
            sender_name = event.get_sender_name()
            if not sender_name:
                yield event.plain_result(
                    "无法获取您的群昵称，请手动指定游戏ID。\n"
                    "用法: /绑定 <游戏ID> 或 /bind <游戏ID>"
                )
                return
            match = re.search(r'[（(](.*?)[）)]', sender_name)
            if match:
                game_id = match.group(1).strip()
                if not game_id:
                    yield event.plain_result(
                        f"您的群昵称「{sender_name}」中括号内为空，请修改昵称或手动指定。"
                    )
                    return
            else:
                yield event.plain_result(
                    f"无法从您的群昵称「{sender_name}」中解析出游戏ID。\n"
                    "请确保昵称格式为「玩家（游戏ID）」，或手动指定: /绑定 <游戏ID>"
                )
                return

        # 获取目标服务器列表
        target_server_name = parts[2] if len(parts) >= 3 else None
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return

        if target_server_name:
            target = next((s for s in servers if s["name"] == target_server_name), None)
            if not target:
                yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
                return
            targets = [target]
        else:
            targets = servers  # 所有服务器

        # 遍历执行绑定
        results = []
        for srv in targets:
            host = srv["host"]
            ok, msg = await call_mod_api(host, port, token, "/api/bind", "POST", {"qq": qq, "gameId": game_id})
            results.append((srv["name"], ok, msg))

        # 汇总结果
        success_list = [f"✔️ {name}：{msg}" for name, ok, msg in results if ok]
        fail_list = [f"❌ {name}：{msg}" for name, ok, msg in results if not ok]

        reply = []
        if success_list:
            reply.append("绑定成功：\n" + "\n".join(success_list))
        if fail_list:
            reply.append("绑定失败：\n" + "\n".join(fail_list))
        if not reply:
            reply = ["没有执行任何绑定操作。"]

        yield event.plain_result("\n\n".join(reply))

    # ---------- unbind 命令（支持多服务器）----------
    elif sub_cmd == "unbind":
        wm = WhitelistManager()
        if not wm.enable_mod_api:
            yield event.plain_result("❌ 模组 API 功能未启用，请在插件配置中启用「enable_mod_api」。")
            return
        port = wm.mod_api_port
        token = wm.mod_api_token
        if not token:
            yield event.plain_result("模组 API Token 未配置，请联系管理员设置。")
            return

        # 判断是否有参数
        if len(parts) >= 2:
            # 有参数：需要管理员权限
            if not await check_permission(event, "unbind"):
                yield event.plain_result("权限不足：解绑其他玩家需要群管理员或插件管理员权限。")
                return
            game_id = parts[1]
            target_server_name = parts[2] if len(parts) >= 3 else None
        else:
            # 无参数：解绑自己（从昵称提取游戏ID）
            sender_name = event.get_sender_name()
            if not sender_name:
                yield event.plain_result(
                    "无法获取您的群昵称，请手动指定游戏ID。\n"
                    "用法: /解绑 <游戏ID> 或 /unbind <游戏ID>（管理员可指定其他玩家）"
                )
                return
            match = re.search(r'[（(](.*?)[）)]', sender_name)
            if match:
                game_id = match.group(1).strip()
                if not game_id:
                    yield event.plain_result(
                        f"您的群昵称「{sender_name}」中括号内为空，请修改昵称或手动指定。"
                    )
                    return
            else:
                yield event.plain_result(
                    f"无法从您的群昵称「{sender_name}」中解析出游戏ID。\n"
                    "请确保昵称格式为「玩家（游戏ID）」，或手动指定: /解绑 <游戏ID>"
                )
                return
            target_server_name = None

        # 获取服务器列表
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return

        if target_server_name:
            target = next((s for s in servers if s["name"] == target_server_name), None)
            if not target:
                yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
                return
            targets = [target]
        else:
            targets = servers

        # 遍历执行解绑
        results = []
        for srv in targets:
            host = srv["host"]
            ok, msg = await call_mod_api(host, port, token, "/api/unbind", "POST", {"gameId": game_id})
            results.append((srv["name"], ok, msg))

        # 汇总结果
        success_list = [f"✔️ {name}：{msg}" for name, ok, msg in results if ok]
        fail_list = [f"❌ {name}：{msg}" for name, ok, msg in results if not ok]

        reply = []
        if success_list:
            reply.append("解绑成功：\n" + "\n".join(success_list))
        if fail_list:
            reply.append("解绑失败：\n" + "\n".join(fail_list))
        if not reply:
            reply = ["没有执行任何解绑操作。"]

        yield event.plain_result("\n\n".join(reply))

    # ---------- check 命令（支持多服务器，支持 ID/QQ 前缀查询）----------
    elif sub_cmd == "check":
        wm = WhitelistManager()
        if not wm.enable_mod_api:
            yield event.plain_result("❌ 模组 API 功能未启用，请在插件配置中启用「enable_mod_api」。")
            return
        port = wm.mod_api_port
        token = wm.mod_api_token
        if not token:
            yield event.plain_result("模组 API Token 未配置，请联系管理员设置。")
            return

        # ---------- 解析查询参数 ----------
        query_type = None  # 'ID' 或 'QQ' 或 'SMART'
        query_value = None
        target_server_name = None

        # 检查是否带前缀（ID 或 QQ）
        if len(parts) >= 3 and parts[1].upper() in ("ID", "QQ"):
            query_type = parts[1].upper()
            query_value = parts[2]
            # 剩余参数可能是服务器名（位置3）
            if len(parts) >= 4:
                target_server_name = parts[3]
        elif len(parts) >= 2:
            # 单参数
            query_value = parts[1]
            # 检查是否还有参数（可能是服务器名）
            if len(parts) >= 3:
                # 如果第三个参数不是 "ID" 或 "QQ"，则视为服务器名
                if parts[2].upper() not in ("ID", "QQ"):
                    target_server_name = parts[2]
            # 判断查询类型：如果是纯数字，设为 SMART；否则设为 ID
            if query_value.isdigit():
                query_type = "SMART"
            else:
                query_type = "ID"
        else:
            # 无参数：从群昵称提取
            sender_name = event.get_sender_name()
            if not sender_name:
                yield event.plain_result(
                    "无法获取您的群昵称，请手动指定查询参数。\n"
                    "用法: /查绑定 <游戏ID> 或 /查绑定 QQ <QQ号> 或 /查绑定 ID <游戏ID>"
                )
                return
            match = re.search(r'[（(](.*?)[）)]', sender_name)
            if match:
                query_value = match.group(1).strip()
                if not query_value:
                    yield event.plain_result(
                        f"您的群昵称「{sender_name}」中括号内为空，请修改昵称或手动指定。"
                    )
                    return
            else:
                yield event.plain_result(
                    f"无法从您的群昵称「{sender_name}」中解析出查询参数。\n"
                    "请确保昵称格式为「玩家（游戏ID）」，或手动指定: /查绑定 <游戏ID>"
                )
                return
            query_type = "ID"  # 默认按 ID

        # 获取服务器列表
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return

        if target_server_name:
            target = next((s for s in servers if s["name"] == target_server_name), None)
            if not target:
                yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
                return
            targets = [target]
        else:
            targets = servers

        # ---------- 执行查询 ----------
        results = []
        for srv in targets:
            host = srv["host"]
            if query_type == "ID":
                ok, data = await call_mod_api(host, port, token, "/api/check", "GET", {"gameId": query_value})
                if ok:
                    bound = data.get("bound", False)
                    if bound:
                        qq = data.get("qq", "")
                        results.append((srv["name"], True, f"绑定的QQ：{qq}"))
                    else:
                        results.append((srv["name"], False, "未绑定"))
                else:
                    results.append((srv["name"], False, f"查询失败：{data}"))

            elif query_type == "QQ":
                ok, data = await call_mod_api(host, port, token, "/api/check", "GET", {"qq": query_value})
                if ok:
                    bound = data.get("bound", False)
                    if bound:
                        game_id = data.get("gameId", "")
                        results.append((srv["name"], True, f"绑定的游戏ID：{game_id}"))
                    else:
                        results.append((srv["name"], False, "该QQ号未绑定"))
                else:
                    results.append((srv["name"], False, f"查询失败：{data}"))

            elif query_type == "SMART":
                # 智能查询：先按QQ，未绑定再按ID
                ok, data = await call_mod_api(host, port, token, "/api/check", "GET", {"qq": query_value})
                if ok and data.get("bound", False):
                    game_id = data.get("gameId", "")
                    results.append((srv["name"], True, f"绑定的游戏ID：{game_id}"))
                else:
                    # 按游戏ID查
                    ok2, data2 = await call_mod_api(host, port, token, "/api/check", "GET", {"gameId": query_value})
                    if ok2:
                        bound2 = data2.get("bound", False)
                        if bound2:
                            qq2 = data2.get("qq", "")
                            results.append((srv["name"], True, f"绑定的QQ：{qq2}"))
                        else:
                            results.append((srv["name"], False, "未绑定（QQ和游戏ID均未绑定）"))
                    else:
                        results.append((srv["name"], False, f"查询失败：{data2}"))
            else:
                results.append((srv["name"], False, "未知查询类型"))

        # 格式化输出
        if query_type == "SMART":
            title = f"智能查询「{query_value}」在各服务器的状态："
        elif query_type == "ID":
            title = f"游戏ID「{query_value}」在各服务器的状态："
        elif query_type == "QQ":
            title = f"QQ号「{query_value}」在各服务器的状态："
        else:
            title = "查询结果："

        reply_lines = [title]
        for name, bound, info in results:
            if bound:
                status = f"✔️ {info}"
            else:
                status = f"❌ {info}"
            reply_lines.append(f"  {name}：{status}")

        yield event.plain_result("\n".join(reply_lines))

    else:
        yield event.plain_result(f"未知子命令: {sub_cmd}，使用 /mc help 查看帮助。")


# ---------- 辅助函数 ----------
async def call_mod_api(host: str, port: int, token: str, endpoint: str, method: str = "POST", data: dict = None):
    """
    调用模组 HTTP API
    :return: (是否成功, 消息)
    """
    if not host or host == "self":
        return False, "无效的服务器地址"
    if ":" in host:
        ip, _ = host.split(":", 1)
    else:
        ip = host
    url = f"http://{ip}:{port}{endpoint}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession() as session:
            if method.upper() == "POST":
                async with session.post(url, json=data, headers=headers, timeout=5.0) as resp:
                    result = await resp.json()
                    if resp.status == 200 and result.get("success"):
                        return True, result.get("message", "操作成功")
                    else:
                        return False, result.get("message", f"API 返回错误 (HTTP {resp.status})")
            elif method.upper() == "GET":
                async with session.get(url, params=data, headers=headers, timeout=5.0) as resp:
                    result = await resp.json()
                    if resp.status == 200:
                        return True, result
                    else:
                        return False, f"检查失败 (HTTP {resp.status})"
    except aiohttp.ClientError as e:
        return False, f"连接失败: {e}"
    except asyncio.TimeoutError:
        return False, "请求超时，请检查服务器是否在线"
    except Exception as e:
        return False, f"未知错误: {e}"