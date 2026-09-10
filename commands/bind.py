import asyncio
import re
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from ..config.server_config import ConfigManager
from ..config.whitelist_config import WhitelistManager
from ..utils.permission import check_permission
from .common import call_mod_api

async def handle_bind(event: AstrMessageEvent, config: ConfigManager, parts: list):
    wm = WhitelistManager()

    token = wm.mod_api_token
    if not token:
        yield event.plain_result("❌ 模组 API Token 未配置，请联系管理员设置。")
        return

    if len(parts) < 2:
        yield event.plain_result(
            "请从游戏窗口中复制绑定码，格式：\n"
            "/绑定 <6位数字> 或 /bind <6位数字>\n"
            "（令牌在游戏内 Title/ActionBar 中显示）"
        )
        return

    token_code = parts[1].strip()
    if not re.match(r'^\d{6}$', token_code):
        yield event.plain_result(
            f"无效的绑定码「{token_code}」，请输入6位数字绑定码。\n"
            "请从游戏窗口中复制正确的绑定码。"
        )
        return

    qq = str(event.get_sender_id())
    servers = config.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    # ---- 步骤1：并发验证令牌 ----
    mod_servers = [s for s in servers if wm.has_mod_api(s)]
    if not mod_servers:
        yield event.plain_result("❌ 没有安装模组的服务器，无法验证绑定码。")
        return
    
    validation_tasks = []
    for srv in servers:
        host = srv["host"]
        port = wm.get_server_port(srv)
        validation_tasks.append(
            asyncio.create_task(
                call_mod_api(host, port, token, "/api/validate_token", "GET", {"token": token_code})
            )
        )

    validation_results = await asyncio.gather(*validation_tasks, return_exceptions=True)

    target_game_id = None
    found_server_name = None
    found_server_port = None
    found_server_host = None
    for idx, result in enumerate(validation_results):
        if isinstance(result, Exception):
            continue
        ok, response = result
        if not ok:
            continue
        response_data = response.get("data", {})
        if response_data.get("valid") is True:
            target_game_id = response_data.get("gameId")
            if target_game_id:
                found_server_host = servers[idx]["host"]
                found_server_name = servers[idx]["name"]
                found_server_port = wm.get_server_port(servers[idx])
                break

    if target_game_id is None:
        yield event.plain_result(
            "❌ 绑定码无效或已过期，请确认你输入的是游戏窗口内显示的6位数字绑定码。\n"
            "如果绑定码已过期，请重新登录游戏获取新的绑定码。"
        )
        return

    # ---- 步骤2：验证QQ群昵称 ----
    sender_name = event.get_sender_name()
    if not sender_name:
        yield event.plain_result(
            "❌ 无法获取您的群昵称，请确保您在群聊中发送命令。\n"
            "请将群昵称修改为「玩家（游戏ID）」格式，例如：张三（zhangsan）"
        )
        return

    match = re.search(r'[（(](.*?)[）)]', sender_name)
    if not match:
        yield event.plain_result(
            f"❌ 您的群昵称「{sender_name}」不包含括号，请修改为「玩家（游戏ID）」格式。\n"
            f"例如：张三（zhangsan）"
        )
        return

    nickname_game_id = match.group(1).strip()
    if not nickname_game_id:
        yield event.plain_result(
            f"❌ 您的群昵称「{sender_name}」中括号内为空，请填写您的游戏ID。\n"
            f"例如：张三（zhangsan）"
        )
        return

    if nickname_game_id != target_game_id:
        yield event.plain_result(
            f"❌ 昵称中的游戏ID「{nickname_game_id}」与当前登录的游戏ID「{target_game_id}」不一致。\n"
            f"请确保您的群昵称格式为「玩家（{target_game_id}）」，或重新登录游戏获取正确的绑定码。"
        )
        return

    # ---- 步骤3：调用模组 API 完成绑定 ----
    # 注意：存储后端由模组端配置决定（local/hybrid/remote），机器人无需关心
    if not found_server_host:
        yield event.plain_result("❌ 未找到有效的服务器地址，请检查配置。")
        return

    ok, result = await call_mod_api(
        found_server_host, found_server_port, token, "/api/bind", "POST",
        {"qq": qq, "gameId": target_game_id}
    )
    if not ok:
        yield event.plain_result(
            f"❌ 绑定失败（服务器：{found_server_name}）\n"
            f"错误：{result}"
        )
        return
    msg = result.get("message", "绑定成功")

    # ---- 步骤4：返回成功消息 ----
    yield event.plain_result(
        f"✅ 绑定成功！\n"
        f"游戏ID：{target_game_id}\n"
        f"消息：{msg}"
    )


async def handle_unbind(event: AstrMessageEvent, config: ConfigManager, parts: list):
    wm = WhitelistManager()
    token = wm.mod_api_token
    if not token:
        yield event.plain_result("❌ 模组 API Token 未配置，请联系管理员设置。")
        return

    target_server_name = None
    game_id = None
    args = parts[1:]

    # ---- 解析参数 ----
    if "-s" in args:
        s_index = args.index("-s")
        if len(args) > s_index + 1:
            target_server_name = args[s_index + 1]
            args = args[:s_index] + args[s_index + 2:]
        else:
            yield event.plain_result("用法: /解绑 <游戏ID> -s <服务器名>")
            return

    if args:
        game_id = args[0]
    else:
        # 自动从昵称提取
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

    # 权限检查：如果尝试解绑其他玩家，需要管理员权限
    if args and args[0] != game_id:
        if not await check_permission(event, "unbind"):
            yield event.plain_result("权限不足：解绑其他玩家需要群管理员或插件管理员权限。")
            return

    servers = config.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    # ---- 情况1：用户指定了服务器名 ----
    if target_server_name:
        target = next((s for s in servers if s["name"] == target_server_name), None)
        if not target:
            yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
            return
        if not wm.has_mod_api(target):
            yield event.plain_result(f"服务器「{target_server_name}」未安装模组，无法解绑。")
            return

        # 统一调用模组 API 解绑
        ok, result = await call_mod_api(
            target["host"], wm.get_server_port(target), token, "/api/unbind", "POST", {"gameId": game_id}
        )
        if ok:
            yield event.plain_result(
                f"✅ 解绑成功！\n"
                f"服务器：{target_server_name}\n"
                f"游戏ID：{game_id}\n"
                f"消息：{result.get('message', '解绑成功')}"
            )
        else:
            yield event.plain_result(
                f"❌ 解绑失败（服务器：{target_server_name}）\n"
                f"错误：{result}"
            )
        return

    # ---- 情况2：未指定服务器，自动检测 ----
    mod_servers = [s for s in servers if wm.has_mod_api(s)]
    if not mod_servers:
        yield event.plain_result("没有安装模组的服务器，无法解绑。")
        return
    
    check_tasks = []
    for srv in servers:
        host = srv["host"]
        port = wm.get_server_port(srv)
        check_tasks.append(
            asyncio.create_task(
                call_mod_api(host, port, token, "/api/check", "GET", {"gameId": game_id})
            )
        )

    check_results = await asyncio.gather(*check_tasks, return_exceptions=True)

    bound_servers = []
    for idx, result in enumerate(check_results):
        if isinstance(result, Exception):
            continue
        ok, data = result
        if not ok:
            continue
        response_data = data.get("data", {})
        if response_data.get("bound") is True:
            bound_servers.append({
                "name": servers[idx]["name"],
                "host": servers[idx]["host"],
                "port": wm.get_server_port(servers[idx]),
                "qq": response_data.get("qq", ""),
                "gameId": response_data.get("gameId", game_id)
            })

    if len(bound_servers) == 0:
        yield event.plain_result(
            f"❌ 未找到游戏ID「{game_id}」的绑定记录。\n"
            "请确认游戏ID是否正确，或先进行绑定操作。"
        )
        return

    elif len(bound_servers) == 1:
        target = bound_servers[0]
        # 统一调用模组 API 解绑
        ok, result = await call_mod_api(
            target["host"], target["port"], token, "/api/unbind", "POST", {"gameId": game_id}
        )
        if ok:
            yield event.plain_result(
                f"✅ 解绑成功！\n"
                f"服务器：{target['name']}\n"
                f"游戏ID：{game_id}\n"
                f"绑定的QQ：{target['qq']}\n"
                f"消息：{result.get('message', '解绑成功')}"
            )
        else:
            yield event.plain_result(
                f"❌ 解绑失败（服务器：{target['name']}）\n"
                f"错误：{result}"
            )
    else:
        # 多个服务器存在绑定，提示用户指定
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


async def handle_check(event: AstrMessageEvent, config: ConfigManager, parts: list):
    wm = WhitelistManager()
    token = wm.mod_api_token
    if not token:
        yield event.plain_result("❌ 模组 API Token 未配置，请联系管理员设置。")
        return

    query_type = None
    query_value = None
    target_server_name = None

    if len(parts) >= 3 and parts[1].upper() in ("ID", "QQ"):
        query_type = parts[1].upper()
        query_value = parts[2]
        if len(parts) >= 4:
            target_server_name = parts[3]
    elif len(parts) >= 2:
        query_value = parts[1]
        if len(parts) >= 3:
            if parts[2].upper() not in ("ID", "QQ"):
                target_server_name = parts[2]
        if query_value.isdigit():
            query_type = "SMART"
        else:
            query_type = "ID"
    else:
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
        query_type = "ID"

    servers = config.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    if target_server_name:
        target = next((s for s in servers if s["name"] == target_server_name), None)
        if not target:
            yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
            return
        if not wm.has_mod_api(target):
            yield event.plain_result(f"服务器「{target_server_name}」未安装模组，无法查询绑定状态。")
            return
        targets = [target]
    else:
        targets = [s for s in servers if wm.has_mod_api(s)]
        if not targets:
            yield event.plain_result("没有安装模组的服务器，无法查询绑定状态。")
            return

    results = []
    for srv in targets:
        host = srv["host"]
        port = wm.get_server_port(srv)
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
            ok, data = await call_mod_api(host, port, token, "/api/check", "GET", {"qq": query_value})
            if ok:
                response_data = data.get("data", {})
                if response_data.get("bound", False):
                    game_id = response_data.get("gameId", "")
                    results.append((srv["name"], True, f"绑定的游戏ID：{game_id}"))
                else:
                    ok2, data2 = await call_mod_api(host, port, token, "/api/check", "GET", {"gameId": query_value})
                    if ok2:
                        response_data2 = data2.get("data", {})
                        if response_data2.get("bound", False):
                            qq2 = response_data2.get("qq", "")
                            results.append((srv["name"], True, f"绑定的QQ：{qq2}"))
                        else:
                            results.append((srv["name"], False, "未绑定（QQ和游戏ID均未绑定）"))
                    else:
                        results.append((srv["name"], False, f"查询失败：{data2}"))
            else:
                results.append((srv["name"], False, f"查询失败：{data}"))

    title = f"{query_value} 查询结果："
    reply_lines = [title]
    for name, bound, info in results:
        if bound:
            status = f"✔️ {info}"
        else:
            status = f"❌ {info}"
        reply_lines.append(f"  {name}：{status}")

    yield event.plain_result("\n".join(reply_lines))

async def invalidate_mod_cache(event, config, game_id):
    """通知所有安装了模组的服务器清除该玩家的本地缓存"""
    wm = WhitelistManager()
    token = wm.mod_api_token
    servers = config.get_all_servers()
    if not token:
        return
    for srv in servers:
        if not wm.has_mod_api(srv):
            continue
        host = srv["host"]
        port = wm.get_server_port(srv)
        await call_mod_api(host, port, token, "/api/cache/invalidate", "POST", {"gameId": game_id})