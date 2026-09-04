import asyncio
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from ..config.server_config import ConfigManager
from ..features.player_status.checker import send_broadcast, fetch_tps
from ..features.player_status.controller import run_player_status, run_player_stats, build_plugin_api_url
from ..features.help_image.image_generator import draw_help_image
from ..utils.permission import check_permission
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
    # （此处省略重复逻辑，与之前完全相同）
    # 为了完整性，以下是完整实现：

    # 定义需要权限检查的子命令
    admin_cmds = {"add", "remove", "edit", "batchadd", "batchremove", "move", "swap"}
    public_cmds = {"help", "status", "list", "stats"}  # 完全公开
    if sub_cmd in admin_cmds:
        if not await check_permission(event, sub_cmd):
            yield event.plain_result("权限不足：该操作需要群管理员或插件管理员权限。")
            return
    # elif sub_cmd == "help":
    #     pass  # 所有人可查看
    # elif sub_cmd in ("status", "list", "stats"):
    #     if not await check_permission(event, sub_cmd):
    #         yield event.plain_result("权限不足。")
    #         return

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
                # 使用 -s 选项
                if len(parts) >= 4:
                    target_server = parts[3]
                else:
                    yield event.plain_result("用法: /mc stats <玩家名> -s <服务器名>")
                    return
            else:
                # 直接指定服务器名
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
        # 检查是否有 -s 选项
        target_server = None
        msg_start = 1
        if parts[1] == "-s":
            if len(parts) < 4:
                yield event.plain_result("用法: /mc say -s <服务器名> <消息>")
                return
            target_server = parts[2]
            msg_start = 3
        # 提取消息
        message = " ".join(parts[msg_start:])
        if not message:
            yield event.plain_result("消息内容不能为空")
            return
        # 长度限制
        MAX_LENGTH = 40
        if len(message) > MAX_LENGTH:
            yield event.plain_result(f"广播消息过长（最多 {MAX_LENGTH} 个字符），当前长度：{len(message)}")
            return
        # 获取服务器列表
        servers = config.get_all_servers()
        if not servers:
            yield event.plain_result("还没有添加任何服务器。")
            return
        wm = WhitelistManager()
        plugin_api_port = wm.plugin_api_port
        # 确定广播目标
        if target_server:
            # 查找指定服务器
            target = None
            for s in servers:
                if s["name"] == target_server:
                    target = s
                    break
            if not target:
                yield event.plain_result(f"未找到名为「{target_server}」的服务器。")
                return
            servers_to_broadcast = [target]
        else:
            servers_to_broadcast = servers
        # 遍历广播
        success_count = 0
        fail_count = 0
        for srv in servers_to_broadcast:
            host = srv["host"]
            api_url = build_plugin_api_url(host, plugin_api_port)
            if not api_url:
                fail_count += 1
                continue
            base_url = api_url.replace("/api/status", "")
            success = await send_broadcast(base_url, message)
            if success:
                success_count += 1
            else:
                fail_count += 1
        if fail_count == 0:
            if target_server:
                yield event.plain_result(f"✅ 已在服务器「{target_server}」发送广播。")
            else:
                yield event.plain_result(f"✅ 已在所有 {success_count} 个服务器发送广播。")
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
        plugin_api_port = wm.plugin_api_port

        if target_server:
            target = None
            for s in servers:
                if s["name"] == target_server:
                    target = s
                    break
            if not target:
                yield event.plain_result(f"未找到名为「{target_server}」的服务器。")
                return
            targets = [target]
        else:
            targets = servers

        # 并发获取 TPS
        tasks = []
        for srv in targets:
            host = srv["host"]
            api_url = build_plugin_api_url(host, plugin_api_port)
            if not api_url:
                continue
            base_url = api_url.replace("/api/status", "")
            tasks.append(fetch_tps(base_url))

        if not tasks:
            yield event.plain_result("没有有效的服务器可查询。")
            return

        results = await asyncio.gather(*tasks)

        # 组装数据
        server_data = []
        for srv, tps in zip(targets, results):
            server_data.append({
                "name": srv["name"],
                "tps": tps  # 可能为 None
            })

        # 生成图片
        try:
            img = draw_tps_image(server_data)
            # 保存为临时文件并发送
            img.save("tps_temp.png")
            yield event.chain_result([AstrImage(file="tps_temp.png")])
        except Exception as e:
            logger.error(f"生成 TPS 图片失败: {e}")
            yield event.plain_result(f"生成图片失败: {e}")


    else:
        yield event.plain_result(f"未知子命令: {sub_cmd}，使用 /mc help 查看帮助。")