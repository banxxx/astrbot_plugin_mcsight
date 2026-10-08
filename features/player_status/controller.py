import asyncio
import os
import re
import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image as AstrImage
from .image_generator import draw_status_pages
from .checker import (
    fetch_from_plugin, query_one, query_all_servers, query_via_api,
    fetch_player_stats, ping_server,
    send_broadcast_via_mod, fetch_tps_via_mod
)
from ...config.whitelist_config import WhitelistManager
from .player_stats_generator import draw_player_stats_image
from ...utils.avatar_cache import download_avatar
from ...utils import stats_snapshot
from ...utils import mod_http
from ...utils.temp_image import make_temp_png, remove_quietly

# ========== 工具函数 ==========
# 合法 Minecraft 玩家名（同时也是 URL 路径/文件名的安全白名单）
PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")

# PNG 编码档位。9 + optimize 比 6 贵 3~4 倍（实测 295ms vs 80ms），体积只差百分之几
PNG_COMPRESS_LEVEL = 6

# /在线 分页发送
PAGE_SEND_GAP = 0.8                  # 多页之间的间隔，防刷屏/风控
PNG_SIZE_WARN = 10 * 1024 * 1024     # 单页 PNG 体积告警阈值


def _save_png(img, path: str) -> None:
    # 供 asyncio.to_thread 调用：编码是纯 CPU，留在事件循环里会连带卡住整个后端
    img.save(path, compress_level=PNG_COMPRESS_LEVEL)


def build_plugin_api_url(host: str, port: int) -> str:
    """构建插件 API URL（已废弃，保留仅作参考）"""
    if not host or host == "self":
        return None
    if ":" in host:
        ip, _ = host.split(":", 1)
    else:
        ip = host
    return f"http://{ip}:{port}/api/status"

def standardize_from_api(data: dict) -> dict:
    """标准化插件 API 数据（已废弃）"""
    srv = data.get("server", {})
    players = data.get("players", [])
    return {
        "name": srv.get("name", "未知"),
        "host": srv.get("host", ""),
        "online": srv.get("online_players", 0),
        "max": srv.get("max_players", 0),
        "players": [p["name"] for p in players],
        "version": srv.get("version", "未知"),
        "latency": data.get("latency", 0.0),
        "error": None,
        "extra": {
            "player_details": players,
            "tps": srv.get("tps"),
        }
    }

def standardize_from_ping(result: dict) -> dict:
    """标准化 mcstatus 数据"""
    result["extra"] = {}
    return result

# ========== 在线玩家状态查询 ==========
async def _collect_server_status(srv: dict, wm: WhitelistManager, token: str) -> dict:
    """采集单台服务器状态：装了模组走模组 API，失败或未装模组回落 mcstatus。

    延迟字段由调用方覆盖，mcstatus 路径自带 status 测出的延迟。
    """
    name = srv["name"]
    host = srv["host"]

    # ---- 判断是否安装了模组 ----
    if not wm.has_mod_api(srv):
        logger.info(f"服务器 {name} 未配置 api_port，视为未安装模组，使用 mcstatus 查询。")
        raw = await query_one(srv)
        return standardize_from_ping(raw)

    port = wm.get_server_port(srv)

    # ---- 请求模组 API ----
    ok, payload = await mod_http.request(host, port, "/api/status", method="GET", token=token)
    mod_data = payload.get("data", {}) if ok and isinstance(payload, dict) else None

    if mod_data:
        return {
            "name": name,
            "host": host,
            "remark": srv.get("remark", ""),
            "online": mod_data.get("online_players", 0),
            "max": mod_data.get("max_players", 0),
            "version": mod_data.get("version", "未知"),
            # 占位：真实延迟来自并发 ping（模组给的 latency 是在线玩家 ping 均值，语义不同）
            "latency": 0.0,
            "error": None,
            "last_activity_time": mod_data.get("last_activity_time", 0),
            "last_activity_player": mod_data.get("last_activity_player", ""),
            "players": [
                {"name": p.get("name"), "uuid": p.get("uuid"), "is_premium": p.get("is_premium", True)}
                for p in mod_data.get("players", [])
            ]
        }

    # 回退到 mcstatus
    raw = await query_one(srv)
    return standardize_from_ping(raw)


async def _ping_latency(hosts: list) -> list:
    """并发取「机器人 → 服务器」延迟；失败或不应答返回 0.0，调用方保留原值。"""
    if not hosts:
        return []
    return await asyncio.gather(*[ping_server(h) for h in hosts],
                                return_exceptions=True)


async def run_player_status(event: AstrMessageEvent, config_manager):
    """获取在线玩家状态，统一使用模组 API，若失败则回退到 mcstatus"""
    servers = config_manager.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    wm = WhitelistManager()
    token = wm.mod_api_token

    # 延迟与采集并发：它只用来覆盖一个展示字段，不该整段排在关键路径前面。
    # 只测模组服——mcstatus 路径的 status 结果本身就带这条链路的延迟。
    ping_idx = [i for i, srv in enumerate(servers) if wm.has_mod_api(srv)]
    ping_results, standardized = await asyncio.gather(
        _ping_latency([servers[i]["host"] for i in ping_idx]),
        asyncio.gather(*[
            _collect_server_status(srv, wm, token) for srv in servers
        ]),
    )
    for i, r in zip(ping_idx, ping_results):
        if not isinstance(r, Exception) and r:
            standardized[i]["latency"] = float(r)

    # 生成图片（按高度预算自动分页，绝大多数情况只有一页）
    try:
        show_last_online = config_manager.is_last_online_enabled()
        images = await draw_status_pages(standardized, show_last_online=show_last_online)
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        yield event.plain_result(f"生成图片失败: {e}")
        return

    # 逐页落盘直发：yield 出去的链会被 AstrBot 攒到最后才发，而图片是发送那一刻
    # 才读盘的，所以必须 event.send 直发（await 返回时字节已读走）后才能删文件
    paths = []
    try:
        for idx, img in enumerate(images):
            path = make_temp_png("mc_status_")
            await asyncio.to_thread(_save_png, img, path)
            paths.append(path)
            size = os.path.getsize(path)
            if size > PNG_SIZE_WARN:
                logger.warning(f"/在线 第 {idx + 1} 页图片体积 {size / 1048576:.1f}MB 偏大，"
                               "若发送失败可调低 image_generator.PAGE_HEIGHT_BUDGET")
            if idx:
                await asyncio.sleep(PAGE_SEND_GAP)
            try:
                await event.send(MessageChain([AstrImage(file=path)]))
            except Exception as e:
                logger.error(f"/在线 第 {idx + 1}/{len(images)} 页发送失败: {e}")
                yield event.plain_result(
                    f"❌ 第 {idx + 1}/{len(images)} 张图片发送失败，前面 {idx} 张已发出。")
                return
    finally:
        for path in paths:
            await asyncio.to_thread(remove_quietly, path)

# ========== 玩家统计数据查询 ==========
# 「拿不到数据」的原因有好几种，混成一句「未找到玩家」会把死服说成没这个人。
# 归类只在这里做一次，四个桶对应四种用户能听懂的说法。
_UNREACHABLE_CATS = (mod_http.CAT_REFUSED, mod_http.CAT_CONNECT_TIMEOUT,
                     mod_http.CAT_SKIPPED, mod_http.CAT_INVALID_HOST)
_ABNORMAL_CATS = (mod_http.CAT_TIMEOUT, mod_http.CAT_DISCONNECTED,
                  mod_http.CAT_BAD_RESPONSE, mod_http.CAT_SERVER_ERROR)
_MAX_LISTED = 3                      # 一条提示里同类原因最多列几台，多了刷屏


def _stats_fail_bucket(err) -> str:
    category = mod_http.category_of(err)
    if not category:
        return "abnormal"
    if category in _UNREACHABLE_CATS:
        return "unreachable"
    if category in _ABNORMAL_CATS:
        return "abnormal"
    # 服在线、模组明确回了 404：这台服上从来没有这个玩家的统计记录。
    # 模组原文是「玩家不在线」，语义不对（离线玩家会从磁盘读），所以不回显它
    if category == mod_http.CAT_BUSINESS_REJECTED and getattr(err, "status", 0) == 404:
        return "no_record"
    # 路由都没有 = 模组版本过旧，跟玩家的记录无关
    if category == mod_http.CAT_NOT_FOUND:
        return "unsupported"
    return "rejected"


def _stats_fail_detail(items) -> str:
    """同一原因的服务器合并成一段，例如「生存服、创造服（连不上）」"""
    grouped = {}
    for name, reason in items:
        grouped.setdefault(reason, []).append(name)
    chunks = []
    for reason, names in list(grouped.items())[:_MAX_LISTED]:
        shown = "、".join(names[:_MAX_LISTED])
        if len(names) > _MAX_LISTED:
            shown += f" 等 {len(names)} 台"
        chunks.append(f"{shown}（{reason}）" if reason else shown)
    if len(grouped) > _MAX_LISTED:
        chunks.append(f"另有 {len(grouped) - _MAX_LISTED} 类原因")
    return "；".join(chunks)


def _stats_fail_text(player_name: str, fails: dict) -> str:
    no_record = fails["no_record"]
    silent = bool(fails["unreachable"] or fails["abnormal"])
    # 「未响应」只在真的没有一台回话时才能说；有服务器答了（哪怕答的是没接口/拒绝）就得换说法
    any_answered = bool(no_record or fails["unsupported"] or fails["rejected"])

    if no_record and silent:
        head = (f"未查到玩家 {player_name} 的统计数据："
                f"已响应的 {len(no_record)} 台服务器里没有这个玩家的记录。")
    elif no_record:
        names = "、".join(n for n, _ in no_record[:_MAX_LISTED])
        if len(no_record) > _MAX_LISTED:
            names += f" 等 {len(no_record)} 台"
        head = (f"未查到玩家 {player_name} 的统计数据："
                f"{names} 都没有这个玩家的统计记录（玩家从未在该服进过游戏时就是这个结果）。")
    elif silent and any_answered:
        head = f"拿不到玩家 {player_name} 的统计数据，各服务器情况如下。"
    elif silent:
        head = f"服务器均未响应，暂时拿不到玩家 {player_name} 的统计数据。"
    elif fails["unsupported"]:
        head = (f"已连接的服务器都没有统计查询接口（模组版本过旧），"
                f"查不到玩家 {player_name} 的统计数据。")
    else:
        head = f"未查到玩家 {player_name} 的统计数据。"

    lines = [head]
    for label, items, skip in (("该服没有这个玩家的记录", no_record, not silent),
                               ("未响应", fails["unreachable"], False),
                               ("响应异常", fails["abnormal"], False),
                               ("模组没有统计接口", fails["unsupported"],
                                not (no_record or silent or fails["rejected"])),
                               ("模组拒绝", fails["rejected"], False)):
        if items and not skip:
            lines.append(f"· {label}：{_stats_fail_detail(items)}")
    return "\n".join(lines)


async def run_player_stats(event: AstrMessageEvent, config_manager, player_name: str, target_server: str = None):
    """查询玩家统计数据，统一使用模组 API"""
    if not player_name:
        yield event.plain_result("请指定玩家名称，例如：/mc stats 玩家名")
        return
    # 防止恶意玩家名注入 URL 路径（如 ../../api/unbind）或穿透文件路径
    if not PLAYER_NAME_RE.match(player_name):
        yield event.plain_result("玩家名无效：仅支持 1-16 位英文字母、数字和下划线。")
        return

    wm = WhitelistManager()
    token = wm.mod_api_token
    servers = config_manager.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    found_data = None
    found_server_name = None
    all_servers_with_data = []

    # 确定目标服务器列表
    if target_server:
        target_srv = next((s for s in servers if s["name"] == target_server), None)
        if not target_srv:
            yield event.plain_result(f"未找到名为「{target_server}」的服务器，请检查配置。")
            return
        if not wm.has_mod_api(target_srv):
            yield event.plain_result(f"服务器「{target_server}」未安装模组，无法查询统计数据。")
            return
        targets = [target_srv]
    else:
        targets = [s for s in servers if wm.has_mod_api(s)]
        if not targets:
            yield event.plain_result("没有安装模组的服务器，无法查询统计数据。")
            return

    # ---- 统一使用模组 API ----
    fails = {"unreachable": [], "abnormal": [], "no_record": [],
             "unsupported": [], "rejected": []}
    for srv in targets:
        host = srv["host"]
        port = wm.get_server_port(srv)
        ok, payload = await mod_http.request(
            host, port, f"/api/stats/{player_name}", method="GET", token=token)
        if not ok or not isinstance(payload, dict):
            bucket = _stats_fail_bucket(payload)
            fails[bucket].append((srv["name"], "" if bucket == "no_record" else str(payload)))
            continue
        response_data = payload.get("data") or {}
        if response_data:
            found_data = response_data
            found_server_name = srv["name"]
            all_servers_with_data.append((found_server_name, payload))
            break  # 找到第一个有效数据即跳出
        fails["abnormal"].append((srv["name"], "模组返回了空的统计数据"))

    if found_data is None:
        yield event.plain_result(_stats_fail_text(player_name, fails))
        return

    # 获取头像并生成图片
    async with aiohttp.ClientSession() as session:
        avatar = await download_avatar(session, player_name, 72, is_premium=None, uuid=None)

    # 先读历史快照（不含当天），再落当日快照：同一天的重复查询不会自己给自己当基线
    snapshots = stats_snapshot.load_snapshots(player_name, found_server_name)
    stats_snapshot.save_snapshot(player_name, found_server_name, found_data)

    is_online = found_data.get('online', False)
    img_path = make_temp_png("mc_stats_")
    try:
        # 渲染 + PNG 编码都是纯 CPU 重活，放线程池执行，避免卡住事件循环
        img = await asyncio.to_thread(
            draw_player_stats_image,
            found_data,
            player_name,
            server_name=found_server_name,
            is_online=is_online,
            avatar_img=avatar,
            show_server_label=(len(servers) > 1),
            snapshots=snapshots
        )
        await asyncio.to_thread(_save_png, img, img_path)
        await event.send(MessageChain([AstrImage(file=img_path)]))
    except Exception as e:
        logger.error(f"生成玩家统计图片失败: {e}")
        yield event.plain_result(f"生成图片失败: {e}")
    finally:
        await asyncio.to_thread(remove_quietly, img_path)

    # 多服务器提示
    if not target_server and len(all_servers_with_data) > 1:
        other_servers = [name for name, _ in all_servers_with_data if name != found_server_name]
        msg = f"检测到玩家 {player_name} 在多个服务器有数据，当前展示的是「{found_server_name}」的数据。\n"
        msg += f"如需查看其他服务器（{', '.join(other_servers)}）的数据，请使用命令：/mc stats {player_name} -s <服务器名称>"
        yield event.plain_result(msg)