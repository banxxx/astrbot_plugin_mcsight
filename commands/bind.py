import asyncio
import re
import pymysql
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from ..config.server_config import ConfigManager
from ..config.whitelist_config import WhitelistManager
from ..utils.permission import check_permission
from .common import call_mod_api, execute_db_update, execute_db_query


# ============================================================
# 辅助函数：同步通知所有模组服务器清缓存，返回成功/失败列表
# ============================================================
async def _invalidate_all_mod_caches(config: ConfigManager, game_id: str) -> tuple:
    """
    并发通知所有安装了模组的服务器清除指定玩家的缓存。
    返回 (成功服务器名列表, 失败列表)，失败列表元素为 (服务器名, 错误信息)。
    整体加 3 秒超时，避免个别服务器慢导致长时间等待。
    """
    wm = WhitelistManager()
    token = wm.mod_api_token
    if not token:
        return [], []

    servers = config.get_all_servers()
    targets = []
    tasks = []
    for srv in servers:
        if not wm.has_mod_api(srv):
            continue
        targets.append(srv["name"])
        tasks.append(
            call_mod_api(
                srv["host"], wm.get_server_port(srv),
                token, "/api/cache/invalidate", "POST", {"gameId": game_id}
            )
        )

    if not tasks:
        return [], []

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=3.0
        )
    except asyncio.TimeoutError:
        return [], [(name, "整体超时") for name in targets]

    success = []
    failed = []
    for name, r in zip(targets, results):
        if isinstance(r, Exception):
            failed.append((name, str(r)))
        elif isinstance(r, tuple) and len(r) >= 2:
            ok = r[0]
            msg = r[1]
            if ok:
                success.append(name)
            else:
                failed.append((name, str(msg)))
        else:
            failed.append((name, "未知响应"))
    return success, failed


def _get_group_id(event: AstrMessageEvent, wm: WhitelistManager) -> str:
    """从事件或配置中获取当前群号"""
    gid = event.get_group_id()
    if gid:
        return str(gid)
    return str(wm._get_config_value("default_group_id", ""))


# ============================================================
# 内部辅助：按 game_id 执行解绑（不做权限判断）
# ============================================================
async def _do_unbind_by_game_id(event, config, wm, use_central, game_id,
                                 target_server_name):
    """按 game_id 执行解绑"""
    token = wm.mod_api_token
    servers = config.get_all_servers()

    # ---- 中心模式：直连数据库 ----
    if use_central:
        group_id = _get_group_id(event, wm)
        if not group_id:
            yield event.plain_result("❌ 无法获取群号，请确保在群聊中使用本命令。")
            return
        row = await execute_db_query(
            "SELECT qq FROM bindings WHERE group_id = %s AND game_id = %s",
            (group_id, game_id), fetch_one=True
        )
        if row is None:
            yield event.plain_result(f"❌ 未找到游戏ID「{game_id}」的绑定记录。")
            return
        bound_qq = row["qq"]
        try:
            rows = await execute_db_update(
                "DELETE FROM bindings WHERE group_id = %s AND game_id = %s",
                (group_id, game_id)
            )
            if rows > 0:
                success, failed = await _invalidate_all_mod_caches(config, game_id)
                msg = (
                    f"✅ 解绑成功！\n"
                    f"游戏ID：{game_id}\n"
                    f"绑定的QQ：{bound_qq}\n"
                    f"消息：解绑成功"
                )
                if failed:
                    failed_names = ", ".join(name for name, _ in failed)
                    msg += f"\n⚠️ 以下服务器缓存未刷新（不影响解绑，稍后自动生效）：{failed_names}"
                yield event.plain_result(msg)
            else:
                yield event.plain_result("❌ 解绑失败，未知错误。")
        except Exception as e:
            logger.error(f"数据库删除失败: {e}")
            yield event.plain_result(f"❌ 解绑失败（数据库操作失败）\n错误：{str(e)}")
        return

    # ---- 本地模式：调用模组 API ----
    mod_servers = [s for s in servers if wm.has_mod_api(s)]
    if not mod_servers:
        yield event.plain_result("没有安装模组的服务器，无法解绑。")
        return

    # 指定服务器
    if target_server_name:
        target = next((s for s in mod_servers if s["name"] == target_server_name), None)
        if not target:
            yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
            return
        ok, result = await call_mod_api(
            target["host"], wm.get_server_port(target),
            token, "/api/unbind", "POST", {"gameId": game_id}
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
                f"❌ 解绑失败（服务器：{target_server_name}）\n错误：{result}"
            )
        return

    # 自动检测：并发查询所有模组服务器
    check_tasks = []
    for srv in mod_servers:
        check_tasks.append(
            asyncio.create_task(
                call_mod_api(srv["host"], wm.get_server_port(srv),
                             token, "/api/check", "GET", {"gameId": game_id})
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
        if response_data.get("bound"):
            bound_servers.append({
                "name": mod_servers[idx]["name"],
                "host": mod_servers[idx]["host"],
                "port": wm.get_server_port(mod_servers[idx]),
                "qq": response_data.get("qq", ""),
            })

    if not bound_servers:
        yield event.plain_result(f"❌ 未找到游戏ID「{game_id}」的绑定记录。")
        return

    if len(bound_servers) == 1:
        target = bound_servers[0]
        ok, result = await call_mod_api(
            target["host"], target["port"],
            token, "/api/unbind", "POST", {"gameId": game_id}
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
                f"❌ 解绑失败（服务器：{target['name']}）\n错误：{result}"
            )
    else:
        server_list = "\n".join([f"  • {s['name']}（QQ：{s['qq']}）" for s in bound_servers])
        yield event.plain_result(
            f"⚠️ 游戏ID「{game_id}」在以下多个服务器存在绑定记录：\n"
            f"{server_list}\n\n"
            f"请使用以下命令精确指定要解绑的服务器：\n"
            f"/解绑 {game_id} -s <服务器名>\n\n"
            f"例如：/解绑 {game_id} -s {bound_servers[0]['name']}"
        )


# ============================================================
# 内部辅助：按 QQ 执行解绑（可能删除多条记录，仅管理员使用）
# ============================================================
async def _do_unbind_by_qq(event, config, wm, use_central, target_qq,
                           target_server_name):
    """按 QQ 执行解绑，可能删除多条记录"""
    token = wm.mod_api_token
    servers = config.get_all_servers()

    # ---- 中心模式 ----
    if use_central:
        group_id = _get_group_id(event, wm)
        if not group_id:
            yield event.plain_result("❌ 无法获取群号，请确保在群聊中使用本命令。")
            return
        rows = await execute_db_query(
            "SELECT game_id FROM bindings WHERE group_id = %s AND qq = %s",
            (group_id, target_qq), fetch_all=True
        )
        if not rows:
            yield event.plain_result(f"❌ QQ「{target_qq}」在本群没有任何绑定记录。")
            return
        game_ids = [r["game_id"] for r in rows]
        try:
            await execute_db_update(
                "DELETE FROM bindings WHERE group_id = %s AND qq = %s",
                (group_id, target_qq)
            )
            failed_names_set = set()
            for gid in game_ids:
                _, failed = await _invalidate_all_mod_caches(config, gid)
                for name, _ in failed:
                    failed_names_set.add(name)

            msg = (
                f"✅ 解绑成功！\n"
                f"QQ：{target_qq}\n"
                f"已解绑的游戏ID：{', '.join(game_ids)}\n"
                f"消息：解绑成功"
            )
            if failed_names_set:
                msg += f"\n⚠️ 以下服务器缓存未刷新（不影响解绑，稍后自动生效）：{', '.join(sorted(failed_names_set))}"
            yield event.plain_result(msg)
        except Exception as e:
            logger.error(f"数据库删除失败: {e}")
            yield event.plain_result(f"❌ 解绑失败（数据库操作失败）\n错误：{str(e)}")
        return

    # ---- 本地模式 ----
    mod_servers = [s for s in servers if wm.has_mod_api(s)]
    if not mod_servers:
        yield event.plain_result("没有安装模组的服务器，无法解绑。")
        return

    if target_server_name:
        t = next((s for s in mod_servers if s["name"] == target_server_name), None)
        if not t:
            yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
            return
        check_targets = [t]
    else:
        check_targets = mod_servers

    # ---- 并发查询所有服务器（一轮网络请求） ----
    check_tasks = [
        call_mod_api(
            srv["host"], wm.get_server_port(srv),
            token, "/api/check", "GET", {"qq": target_qq}
        )
        for srv in check_targets
    ]
    check_results = await asyncio.gather(*check_tasks, return_exceptions=True)

    found_pairs = []  # [(srv, game_id), ...]
    for srv, result in zip(check_targets, check_results):
        if isinstance(result, Exception):
            continue
        ok, data = result
        if not ok:
            continue
        rd = data.get("data", {})
        if rd.get("bound"):
            gid = rd.get("gameId", "")
            if gid:
                found_pairs.append((srv, gid))

    if not found_pairs:
        yield event.plain_result(f"❌ QQ「{target_qq}」没有任何绑定记录。")
        return

    # ---- 并发解绑（一轮网络请求） ----
    unbind_tasks = [
        call_mod_api(
            srv["host"], wm.get_server_port(srv),
            token, "/api/unbind", "POST", {"gameId": gid}
        )
        for srv, gid in found_pairs
    ]
    unbind_results = await asyncio.gather(*unbind_tasks, return_exceptions=True)

    success_ids = []
    failed = []
    for (srv, gid), result in zip(found_pairs, unbind_results):
        if isinstance(result, Exception):
            failed.append(f"{srv['name']} 上的 {gid}")
            continue
        ok, _ = result
        if ok:
            success_ids.append(gid)
        else:
            failed.append(f"{srv['name']} 上的 {gid}")

    if success_ids:
        msg = (
            f"✅ 解绑成功！\n"
            f"QQ：{target_qq}\n"
            f"已解绑的游戏ID：{', '.join(success_ids)}"
        )
        if failed:
            msg += f"\n⚠️ 以下解绑失败：{', '.join(failed)}"
        yield event.plain_result(msg)
    else:
        yield event.plain_result(f"❌ 解绑失败\n错误：{', '.join(failed)}")


# ============================================================
# 绑定
# ============================================================
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

    use_central = wm.use_central_db

    # ---- 步骤1：并发验证令牌（需要至少一台模组服务器在线，因为令牌存在内存里） ----
    mod_servers = [s for s in servers if wm.has_mod_api(s)]
    if not mod_servers:
        yield event.plain_result("❌ 没有安装模组的服务器，无法验证绑定码。")
        return

    validation_tasks = []
    for srv in mod_servers:
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
                found_server_host = mod_servers[idx]["host"]
                found_server_name = mod_servers[idx]["name"]
                found_server_port = wm.get_server_port(mod_servers[idx])
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

    # ============================================================
    # 步骤3A：中心模式 —— 直接操作数据库
    # ============================================================
    if use_central:
        group_id = _get_group_id(event, wm)
        if not group_id:
            yield event.plain_result("❌ 无法获取群号，请确保在群聊中使用本命令。")
            return

        sql = "INSERT INTO bindings (group_id, game_id, qq) VALUES (%s, %s, %s)"
        try:
            rows = await execute_db_update(sql, (group_id, target_game_id, qq))
            if rows > 0:
                success, failed = await _invalidate_all_mod_caches(config, target_game_id)
                msg = (
                    f"✅ 绑定成功！\n"
                    f"游戏ID：{target_game_id}\n"
                    f"消息：绑定成功"
                )
                if failed:
                    failed_names = ", ".join(name for name, _ in failed)
                    msg += f"\n⚠️ 以下服务器缓存未刷新（不影响绑定，稍后自动生效）：{failed_names}"
                yield event.plain_result(msg)
            else:
                yield event.plain_result("❌ 绑定失败，未知错误。")
        except pymysql.err.IntegrityError:
            yield event.plain_result(
                f"❌ 绑定失败！游戏ID「{target_game_id}」在本群已被绑定。\n"
                "请勿重复绑定，如需解绑请使用 /解绑 命令。"
            )
        except Exception as e:
            logger.error(f"数据库写入失败: {e}")
            yield event.plain_result(
                f"❌ 绑定失败（数据库操作失败）\n"
                f"错误：{str(e)}"
            )
        return

    # ============================================================
    # 步骤3B：本地模式 —— 调用模组 API
    # ============================================================
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

    yield event.plain_result(
        f"✅ 绑定成功！\n"
        f"游戏ID：{target_game_id}\n"
        f"消息：{msg}"
    )


# ============================================================
# 解绑
# ============================================================
async def handle_unbind(event: AstrMessageEvent, config: ConfigManager, parts: list):
    wm = WhitelistManager()
    token = wm.mod_api_token
    if not token:
        yield event.plain_result("❌ 模组 API Token 未配置，请联系管理员设置。")
        return

    use_central = wm.use_central_db
    current_qq = str(event.get_sender_id())

    target_server_name = None
    args = parts[1:]

    # ---- 解析 -s 参数 ----
    if "-s" in args:
        s_index = args.index("-s")
        if len(args) > s_index + 1:
            target_server_name = args[s_index + 1]
            args = args[:s_index] + args[s_index + 2:]
        else:
            yield event.plain_result("用法: /解绑 [ID|QQ] <目标> [-s 服务器名]")
            return

    # ---- 解析 ID/QQ 关键字 ----
    explicit_mode = None   # None | "ID" | "QQ"
    explicit_value = None
    if len(args) >= 2 and args[0].upper() in ("ID", "QQ"):
        explicit_mode = args[0].upper()
        explicit_value = args[1]
    elif len(args) >= 1:
        explicit_value = args[0]

    servers = config.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    # ============================================================
    # 分支 1：显式 ID/QQ 模式（管理员操作）
    # ============================================================
    if explicit_mode is not None:
        if not await check_permission(event, "unbind"):
            yield event.plain_result(
                "权限不足：使用 ID/QQ 关键字解绑需要群管理员或插件管理员权限。\n"
                "如需解绑自己的账号，请直接使用：/解绑 <游戏ID> 或 /解绑"
            )
            return

        if explicit_mode == "ID":
            async for r in _do_unbind_by_game_id(
                event, config, wm, use_central, explicit_value, target_server_name
            ):
                yield r
        else:  # QQ
            async for r in _do_unbind_by_qq(
                event, config, wm, use_central, explicit_value, target_server_name
            ):
                yield r
        return

    # ============================================================
    # 分支 2：默认模式 /解绑 [game_id] 或 /解绑
    # ============================================================
    # 无参时：直接以当前 QQ 为目标，解绑该 QQ 在本群绑定的所有 game_id
    if not explicit_value:
        async for r in _do_unbind_by_qq(
            event, config, wm, use_central, current_qq, target_server_name
        ):
            yield r
        return

    # 有参时：验证 game_id 归属
    game_id = explicit_value

    # ---- 查询该 game_id 绑定的 QQ，验证是否属于当前用户 ----
    bound_qq = None
    if use_central:
        group_id = _get_group_id(event, wm)
        if not group_id:
            yield event.plain_result("❌ 无法获取群号，请确保在群聊中使用本命令。")
            return
        row = await execute_db_query(
            "SELECT qq FROM bindings WHERE group_id = %s AND game_id = %s",
            (group_id, game_id), fetch_one=True
        )
        if row is None:
            yield event.plain_result(
                f"❌ 未找到游戏ID「{game_id}」的绑定记录。\n"
                "请确认游戏ID是否正确。"
            )
            return
        bound_qq = row["qq"]
    else:
        # 本地模式：通过 /api/check 查询
        mod_servers = [s for s in servers if wm.has_mod_api(s)]
        if not mod_servers:
            yield event.plain_result("没有安装模组的服务器，无法解绑。")
            return
        check_targets = []
        if target_server_name:
            t = next((s for s in mod_servers if s["name"] == target_server_name), None)
            if not t:
                yield event.plain_result(f"未找到名为「{target_server_name}」的服务器。")
                return
            check_targets = [t]
        else:
            check_targets = mod_servers
        for srv in check_targets:
            ok, data = await call_mod_api(
                srv["host"], wm.get_server_port(srv),
                token, "/api/check", "GET", {"gameId": game_id}
            )
            if ok and data.get("data", {}).get("bound"):
                bound_qq = data["data"].get("qq", "")
                break
        if not bound_qq:
            yield event.plain_result(
                f"❌ 未找到游戏ID「{game_id}」的绑定记录。\n"
                "请确认游戏ID是否正确。"
            )
            return

    # ---- 权限验证：账号不属于自己时，需要管理员权限 ----
    if bound_qq != current_qq:
        if not await check_permission(event, "unbind"):
            yield event.plain_result(
                f"权限不足：游戏ID「{game_id}」绑定的QQ是「{bound_qq}」，不属于您。\n"
                "如需解绑其他玩家，请使用管理员命令：\n"
                "  /解绑 ID <游戏ID>\n"
                "  /解绑 QQ <QQ号>"
            )
            return

    # ---- 执行解绑 ----
    async for r in _do_unbind_by_game_id(
        event, config, wm, use_central, game_id, target_server_name
    ):
        yield r


# ============================================================
# 查询
# ============================================================
async def handle_check(event: AstrMessageEvent, config: ConfigManager, parts: list):
    wm = WhitelistManager()
    token = wm.mod_api_token
    if not token:
        yield event.plain_result("❌ 模组 API Token 未配置，请联系管理员设置。")
        return

    use_central = wm.use_central_db

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

    # ============================================================
    # 中心模式：直连数据库查询
    # ============================================================
    if use_central:
        group_id = _get_group_id(event, wm)
        if not group_id:
            yield event.plain_result("❌ 无法获取群号，请确保在群聊中使用本命令。")
            return

        title = f"{query_value} 查询结果："
        try:
            if query_type == "ID":
                row = await execute_db_query(
                    "SELECT qq FROM bindings WHERE group_id = %s AND game_id = %s",
                    (group_id, query_value), fetch_one=True
                )
                if row:
                    yield event.plain_result(f"{title}\n  ✔️ 绑定的QQ：{row['qq']}")
                else:
                    yield event.plain_result(f"{title}\n  ❌ 未绑定")

            elif query_type == "QQ":
                rows = await execute_db_query(
                    "SELECT game_id FROM bindings WHERE group_id = %s AND qq = %s",
                    (group_id, query_value), fetch_all=True
                )
                if rows:
                    game_ids = [r["game_id"] for r in rows]
                    yield event.plain_result(
                        f"{title}\n  ✔️ 绑定的游戏ID：{', '.join(game_ids)}"
                    )
                else:
                    yield event.plain_result(f"{title}\n  ❌ 该QQ号未绑定")

            elif query_type == "SMART":
                rows = await execute_db_query(
                    "SELECT game_id FROM bindings WHERE group_id = %s AND qq = %s",
                    (group_id, query_value), fetch_all=True
                )
                if rows:
                    game_ids = [r["game_id"] for r in rows]
                    yield event.plain_result(
                        f"{title}\n  ✔️ 绑定的游戏ID：{', '.join(game_ids)}"
                    )
                else:
                    row2 = await execute_db_query(
                        "SELECT qq FROM bindings WHERE group_id = %s AND game_id = %s",
                        (group_id, query_value), fetch_one=True
                    )
                    if row2:
                        yield event.plain_result(f"{title}\n  ✔️ 绑定的QQ：{row2['qq']}")
                    else:
                        yield event.plain_result(f"{title}\n  ❌ 未绑定（QQ和游戏ID均未绑定）")
        except Exception as e:
            logger.error(f"数据库查询失败: {e}")
            yield event.plain_result(f"❌ 查询失败（数据库操作失败）\n错误：{str(e)}")
        return

    # ============================================================
    # 本地模式：调用模组 API（保持原有逻辑）
    # ============================================================
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
        status = f"✔️ {info}" if bound else f"❌ {info}"
        reply_lines.append(f"  {name}：{status}")

    yield event.plain_result("\n".join(reply_lines))