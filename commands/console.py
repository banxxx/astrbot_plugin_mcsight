"""通过模组 HTTP 接口在指定服务器上执行控制台命令（POST /api/console）。

命令在服务端的权限等同控制台，因此必须用 [服务器名] 点名单台服务器，
省略即拒绝（不做"默认服"也不做全服扇出），入口要求群管理员及以上权限。
Token 按服务器单独配置在服务器条目里（/mc token），与绑定用的
mod_api_token 相互独立；条目没配 Token 即视为该服务器不启用本功能。
"""
import re

from astrbot.api import logger
from ..config.server_config import ConfigManager, console_token_of
from ..config.whitelist_config import WhitelistManager
from ..utils import mod_http

MAX_COMMAND_CHARS = 200
MAX_OUTPUT_CHARS = 1200
# 模组端主线程等待上限是 8 秒，这里留出网络余量
REQUEST_TIMEOUT = 10.0

USAGE = ("用法: /执行 [服务器名] <命令...>\n"
         "例：/执行 [一服] say 这是一条测试命令")

# 服名用方括号包起来是这条命令唯一的定界手段，所以全角括号一并收下：
# 中文输入法下 【一服】 比 [一服] 更容易打出来
_TARGET_RE = re.compile(r"^\s*[\[【]([^\[\]【】]*)[\]】]\s*")


def parse_target(rest: str):
    """取出开头的 [服务器名]，返回 (服名, 命令)；没有括号时服名为 None"""
    matched = _TARGET_RE.match(rest)
    if not matched:
        return None, ""
    return matched.group(1).strip(), rest[matched.end():].strip()


async def handle_exec(event, config: ConfigManager, rest: str):
    server_name, command = parse_target(rest)
    if server_name is None:
        yield event.plain_result(USAGE + "\n\n必须用 [服务器名] 点名要操作哪一台，不能省略。")
        return
    if not server_name:
        yield event.plain_result(USAGE + "\n\n方括号里的服务器名是空的。")
        return
    if command.startswith("/"):
        command = command[1:]
    if not command:
        yield event.plain_result("命令内容不能为空")
        return
    if len(command) > MAX_COMMAND_CHARS:
        yield event.plain_result(f"命令过长（最多 {MAX_COMMAND_CHARS} 个字符），当前长度：{len(command)}")
        return

    servers = config.get_all_servers()
    target = next((s for s in servers if s["name"] == server_name), None)
    if not target:
        yield event.plain_result(f"未找到名为「{server_name}」的服务器。")
        return

    wm = WhitelistManager()
    if not wm.has_mod_api(target):
        yield event.plain_result(f"服务器「{server_name}」未启用模组 API（未配置 api_port），无法执行命令。")
        return

    token = console_token_of(target)
    if not token:
        yield event.plain_result(
            f"服务器「{server_name}」未配置命令 Token，/执行 对它未启用。\n"
            f"需超级管理员执行：/mc token {server_name} <与模组 consoleToken 一致的值>")
        return

    ok, payload = await mod_http.request(
        target["host"],
        wm.get_server_port(target),
        "/api/console",
        method="POST",
        data={"command": command, "actor": f"qq:{event.get_sender_id()}"},
        token=token,
        timeout=REQUEST_TIMEOUT,
    )

    if not ok:
        category = mod_http.category_of(payload)
        if category == mod_http.CAT_UNAUTHORIZED:
            # 只可能是这一台的 Token 不一致：Token 按服独立，不再牵连其它服
            logger.info(f"[exec] {server_name} 鉴权失败: {command}")
            yield event.plain_result(
                f"❌ 「{server_name}」拒绝了鉴权：机器人里配的 Token 与该服务器模组的 consoleToken 不一致。")
            return
        if category == mod_http.CAT_NOT_FOUND:
            yield event.plain_result(
                f"❌ 「{server_name}」没有命令接口：该服务器模组未启用（consoleToken 留空）或模组版本过旧。")
            return
        yield event.plain_result(f"❌ 「{server_name}」: {payload}")
        return

    data = payload.get("data") or {}
    status = data.get("status")
    sender = event.get_sender_id()
    logger.info(f"[exec] qq:{sender} @ {server_name}: {command} -> "
                f"status={status} result={data.get('result')} lines={len(data.get('output') or [])}")
    yield event.plain_result(format_exec_reply(server_name, command, data))


def format_exec_reply(server_name: str, command: str, data: dict) -> str:
    """把模组返回的执行结果渲染成聊天文本（纯函数，便于离线核对）"""
    status = data.get("status")
    if status == "timeout":
        return (f"⚠️ 「{server_name}」命令等待超时（{data.get('elapsedMs')} ms），"
                f"命令可能已经执行，请在游戏内确认。")

    lines = [str(line) for line in (data.get("output") or [])]
    body = "\n".join(lines) if lines else "（该命令没有输出）"
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS] + "\n…（输出过长，已截断）"

    detail = f"耗时 {data.get('elapsedMs')} ms，影响 {data.get('result')} 个目标"
    if data.get("truncated"):
        detail += "，输出已截断"
    return f"✔️ 「{server_name}」已执行：{command}\n{body}\n— {detail}"

