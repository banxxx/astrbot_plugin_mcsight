import asyncio
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger
from astrbot.api.message_components import Image as AstrImage
from ..config.server_config import ConfigManager
from ..config.whitelist_config import WhitelistManager
from ..features.player_status.checker import send_broadcast_via_mod, fetch_tps_via_mod
from ..features.player_status.controller import run_player_status, run_player_stats
from ..features.player_status.tps_image_generator import draw_tps_image

# ---------- 广播 ----------
async def handle_say(event: AstrMessageEvent, config: ConfigManager, parts: list):
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
    token = wm.mod_api_token

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
        port = wm.get_server_port(srv)
        success = await send_broadcast_via_mod(host, port, token, message)
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

# ---------- TPS ----------
async def handle_tps(event: AstrMessageEvent, config: ConfigManager, parts: list):
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
    token = wm.mod_api_token

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
        port = wm.get_server_port(srv)
        tasks.append(fetch_tps_via_mod(host, port, token))

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

# ---------- 状态和统计 ----------
async def handle_status(event: AstrMessageEvent, config: ConfigManager):
    async for result in run_player_status(event, config):
        yield result

async def handle_stats(event: AstrMessageEvent, config: ConfigManager, parts: list):
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