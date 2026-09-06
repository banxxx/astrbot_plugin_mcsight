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
from ..features.help_image.bind_help_generator import draw_bind_help_image

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

    elif sub_cmd == "bindhelp":
        try:
            img = draw_bind_help_image()
            img.save("bind_help_temp.png")
            yield event.chain_result([AstrImage(file="bind_help_temp.png")])
        except Exception as e:
            yield event.plain_result(f"生成绑定帮助图片失败: {e}")

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

    # ---------- bind 命令（令牌模式：先验证服务器，再绑定）----------
    elif sub_cmd == "bind":
        wm = WhitelistManager()
        if not wm.enable_mod_api:
            yield event.plain_result("❌ 模组 API 功能未启用，请在插件配置中启用「enable_mod_api」。")
            return
        port = wm.mod_api_port
        api_token = wm.mod_api_token
        if not api_token:
            yield event.plain_result("模组 API Token 未配置，请联系管理员设置。")
            return

        # 检查是否提供了令牌（6位数字）
        if len(parts) < 2:
            yield event.plain_result(
                "请从游戏窗口中复制绑定码，格式：\n"
                "/绑定 <6位数字> 或 /bind <6位数字>\n"
                "（令牌在游戏内 Title/ActionBar 中显示）"
            )
            return

        token_code = parts[1].strip()
        # 验证是否为6位数字
        if not re.match(r'^\d{6}$', token_code):
            yield event.plain_result(
                f"无效的绑定码「{token_code}」，请输入6位数字绑定码。\n"
                "请从游戏窗口中复制正确的绑定码。"
            )
            return

        qq = str(event.get_sender_id())

        # 获取所有已配置的服务器
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return

        # ---- 步骤1：并发验证令牌，找到所属服务器 ----
        validation_tasks = []
        for srv in servers:
            host = srv["host"]
            validation_tasks.append(
                asyncio.create_task(
                    call_mod_api(host, port, api_token, "/api/validate_token", "GET", {"token": token_code})
                )
            )

        # 等待所有验证任务完成
        validation_results = await asyncio.gather(*validation_tasks, return_exceptions=True)

        # 寻找第一个有效的令牌
        target_host = None
        target_game_id = None
        found_server_name = None
        for idx, (ok, result) in enumerate(validation_results):
            # 如果任务抛出异常或返回失败，跳过
            if isinstance((ok, result), Exception):
                continue
            if not ok:
                continue
            # 新响应格式：业务数据在 data 字段中
            response_data = result.get("data", {})
            if response_data.get("valid") is True:
                target_game_id = response_data.get("gameId")
                target_server_id = response_data.get("serverId")
                if target_game_id:
                    target_host = servers[idx]["host"]
                    found_server_name = servers[idx]["name"]
                    break

        # 如果没有找到有效令牌
        if target_host is None:
            yield event.plain_result(
                "❌ 绑定码无效或已过期，请确认你输入的是游戏窗口内显示的6位数字绑定码。\n"
                "如果绑定码已过期，请重新登录游戏获取新的绑定码。"
            )
            return

        # ---- 步骤2：向目标服务器发送绑定请求（携带 qq 和 gameId） ----
        # 注意：这里使用传统模式，传递 gameId，因为已经通过验证接口确认了令牌对应此 gameId
        ok, result = await call_mod_api(
            target_host, port, api_token, "/api/bind", "POST",
            {"qq": qq, "gameId": target_game_id}
        )

        if ok:
            msg = result.get("message", "绑定成功")
            yield event.plain_result(
                f"✅ 绑定成功！\n"
                f"服务器：{found_server_name}\n"
                f"游戏ID：{target_game_id}\n"
                f"消息：{msg}"
            )
        else:
            yield event.plain_result(
                f"❌ 绑定失败（服务器：{found_server_name}）\n"
                f"错误：{result}"
            )

    # ---------- unbind 命令（混合模式：自动检测 + 手动指定）----------
    elif sub_cmd == "unbind":
        wm = WhitelistManager()
        if not wm.enable_mod_api:
            yield event.plain_result("❌ 模组 API 功能未启用，请在插件配置中启用「enable_mod_api」。")
            return
        port = wm.mod_api_port
        api_token = wm.mod_api_token
        if not api_token:
            yield event.plain_result("模组 API Token 未配置，请联系管理员设置。")
            return

        # ---- 解析参数 ----
        # 支持格式：
        # /解绑 <gameId> [-s <服务器名>]
        # /解绑（从群昵称自动提取 gameId）
        target_server_name = None
        game_id = None
        args = parts[1:]  # 去掉子命令

        # 检查是否有 -s 参数（手动指定服务器模式）
        if "-s" in args:
            s_index = args.index("-s")
            if len(args) > s_index + 1:
                target_server_name = args[s_index + 1]
                # 移除 -s 和服务器名，剩下的作为 gameId
                args = args[:s_index] + args[s_index + 2:]
            else:
                yield event.plain_result("用法: /解绑 <游戏ID> -s <服务器名>")
                return

        # 剩余的第一个参数作为 gameId
        if args:
            game_id = args[0]
        else:
            # 无参数：从群昵称提取游戏ID（仅限解绑自己）
            sender_name = event.get_sender_name()
            if not sender_name:
                yield event.plain_result(
                    "无法获取您的群昵称，请手动指定游戏ID。\n"
                    "用法: /解绑 <游戏ID> 或 /解绑（自动从昵称提取）"
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

        # ---- 权限检查 ----
        # 如果 gameId 来自昵称，说明是玩家自己，无需额外权限
        # 如果 gameId 来自参数，需要检查是否有管理员权限（解绑他人）
        if args and args[0] != game_id:
            # 有参数，需要管理员权限
            if not await check_permission(event, "unbind"):
                yield event.plain_result("权限不足：解绑其他玩家需要群管理员或插件管理员权限。")
                return

        # 获取所有已配置的服务器
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return

        # ---- 模式1：手动指定服务器（使用 -s 参数） ----
        if target_server_name:
            target = next((s for s in servers if s["name"] == target_server_name), None)
            if not target:
                yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
                return
            # 只向该服务器发送解绑请求
            ok, result = await call_mod_api(
                target["host"], port, api_token, "/api/unbind", "POST", {"gameId": game_id}
            )
            if ok:
                msg = result.get("message", "解绑成功")
                yield event.plain_result(
                    f"✅ 解绑成功！\n"
                    f"服务器：{target_server_name}\n"
                    f"游戏ID：{game_id}\n"
                    f"消息：{msg}"
                )
            else:
                yield event.plain_result(
                    f"❌ 解绑失败（服务器：{target_server_name}）\n"
                    f"错误：{msg}"
                )
            return

        # ---- 模式2：自动检测模式（无 -s 参数） ----
        # 步骤1：并发查询所有服务器，检查 gameId 的绑定状态
        check_tasks = []
        for srv in servers:
            host = srv["host"]
            check_tasks.append(
                asyncio.create_task(
                    call_mod_api(host, port, api_token, "/api/check", "GET", {"gameId": game_id})
                )
            )

        # 等待所有查询完成
        check_results = await asyncio.gather(*check_tasks, return_exceptions=True)

        # 收集绑定了该 gameId 的服务器列表
        bound_servers = []
        for idx, result in enumerate(check_results):
            # 如果任务抛出异常或返回失败，跳过
            if isinstance(result, Exception):
                continue
            ok, data = result
            if not ok:
                continue
            # 新响应格式：业务数据在 data 字段中
            response_data = data.get("data", {})
            if response_data.get("bound") is True:
                bound_servers.append({
                    "name": servers[idx]["name"],
                    "host": servers[idx]["host"],
                    "qq": response_data.get("qq", ""),
                    "gameId": response_data.get("gameId", game_id)
                })

        # 步骤2：根据查询结果处理
        if len(bound_servers) == 0:
            # 没有任何服务器绑定该 gameId
            yield event.plain_result(
                f"❌ 未找到游戏ID「{game_id}」的绑定记录。\n"
                "请确认游戏ID是否正确，或先进行绑定操作。"
            )
            return

        elif len(bound_servers) == 1:
            # 只有唯一绑定，直接解绑
            target = bound_servers[0]
            ok, result = await call_mod_api(
                target["host"], port, api_token, "/api/unbind", "POST", {"gameId": game_id}
            )
            if ok:
                msg = result.get("message", "解绑成功")
                yield event.plain_result(
                    f"✅ 解绑成功！\n"
                    f"服务器：{target['name']}\n"
                    f"游戏ID：{game_id}\n"
                    f"绑定的QQ：{target['qq']}\n"
                    f"消息：{msg}"
                )
            else:
                yield event.plain_result(
                    f"❌ 解绑失败（服务器：{target['name']}）\n"
                    f"错误：{result}"
                )

        else:
            # 多个服务器绑定了该 gameId，列出所有服务器，提示用户手动指定
            server_list = "\n".join([
                f"  • {s['name']}（QQ：{s['qq']}）"
                for s in bound_servers
            ])
            yield event.plain_result(
                f"⚠️ 游戏ID「{game_id}」在以下多个服务器存在绑定记录：\n"
                f"{server_list}\n\n"
                f"请使用以下命令精确指定要解绑的服务器：\n"
                f"/解绑 {game_id} -s <服务器名>\n\n"
                f"例如：/解绑 {game_id} -s {bound_servers[0]['name']}"
            )

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
                    response_data = data.get("data", {})
                    bound = response_data.get("bound", False)
                    if bound:
                        qq = response_data.get("qq", "")
                        results.append((srv["name"], True, f"绑定的QQ：{qq}"))
                    else:
                        results.append((srv["name"], False, "未绑定"))
                else:
                    results.append((srv["name"], False, f"查询失败：{data}"))

            elif query_type == "QQ":
                ok, data = await call_mod_api(host, port, token, "/api/check", "GET", {"qq": query_value})
                if ok:
                    response_data = data.get("data", {})
                    bound = response_data.get("bound", False)
                    if bound:
                        game_id = response_data.get("gameId", "")
                        results.append((srv["name"], True, f"绑定的游戏ID：{game_id}"))
                    else:
                        results.append((srv["name"], False, "该QQ号未绑定"))
                else:
                    results.append((srv["name"], False, f"查询失败：{data}"))

            elif query_type == "SMART":
                # 智能查询：先按QQ，未绑定再按ID
                ok, data = await call_mod_api(host, port, token, "/api/check", "GET", {"qq": query_value})
                response_data = data.get("data", {})
                if ok and response_data.get("bound", False):
                    game_id = response_data.get("gameId", "")
                    results.append((srv["name"], True, f"绑定的游戏ID：{game_id}"))
                else:
                    # 按游戏ID查
                    ok2, data2 = await call_mod_api(host, port, token, "/api/check", "GET", {"gameId": query_value})
                    if ok2:
                        response_data2 = data2.get("data", {})
                        bound2 = response_data2.get("bound", False)
                        if bound2:
                            qq2 = response_data2.get("qq", "")
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
    :return: (是否成功, 响应内容)
             成功时返回 (True, 完整JSON响应字典)
             失败时返回 (False, 错误消息字符串)
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
                    if resp.status == 200 and result.get("success") is True:
                        return True, result
                    else:
                        return False, result.get("message", f"API 返回错误 (HTTP {resp.status})")
            elif method.upper() == "GET":
                async with session.get(url, params=data, headers=headers, timeout=5.0) as resp:
                    result = await resp.json()
                    if resp.status == 200:
                        if result.get("success") is False:
                            return False, result.get("message", "API 返回错误")
                        return True, result
                    else:
                        return False, f"请求失败 (HTTP {resp.status})"
    except aiohttp.ClientError as e:
        return False, f"连接失败: {e}"
    except asyncio.TimeoutError:
        return False, "请求超时，请检查服务器是否在线"
    except Exception as e:
        return False, f"未知错误: {e}"