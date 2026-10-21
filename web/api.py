"""WebUI 配置页（views/config）的后端接口。

内核把页面请求拼成 /api/v1/plugins/extensions/<插件名>/<endpoint>，再拿完整路径去匹配
registered_web_apis，所以这里的路由必须自带插件名前缀，页面侧只写 view/xxx。
写操作一律复用命令侧的校验函数与拒绝文案，页面每颗按钮都对应一条现成的 /mc 命令；
密钥（命令 Token、模组 Token、数据库密码）按实体单独存、只写不读，任何接口都不回明文。
"""
import asyncio
import json
import os
import re

from astrbot.api import logger

try:
    from astrbot.api.web import request as web_request
    WEB_API_SUPPORTED = True
except ImportError:  # 4.24 之前的内核没有 astrbot.api.web
    web_request = None
    WEB_API_SUPPORTED = False

from ..commands.management import _validate_console_token
from ..config.server_config import (
    ConfigManager,
    MAX_SERVERS_PER_GROUP,
    console_token_of,
    group_config_path,
    validate_remark,
    validate_server_name,
)
from ..config.whitelist_config import WhitelistManager
from ..utils import mod_http
from ..utils.host_validator import validate_host
from ..utils.plugin_paths import PLUGIN_NAME, sub_dir
from .constants import ROWS

ROUTE_PREFIX = f"/{PLUGIN_NAME}/view"
# 面板里点开关要等回执，超时给得比聊天侧短：读一次状态 3s，写一次 5s
MOD_READ_TIMEOUT = 3.0
MOD_WRITE_TIMEOUT = 5.0
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(PLUGIN_DIR, "_conf_schema.json")
METADATA_PATH = os.path.join(PLUGIN_DIR, "metadata.yaml")


def _plugin_version() -> str:
    """从 metadata.yaml 取版本号，面板顶栏显示用；取不到就留空，不编一个数"""
    try:
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^version:\s*['\"]?([\w.\-]+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""

# 只有这两个键算密钥：走专用接口、不回显、留空即关闭对应功能
SECRET_KEYS = ("mod_api_token", "db_password")
# 群号要拼进配置文件名，先卡死字符集再碰文件系统
GROUP_ID_RE = re.compile(r"^\d{5,12}$")
REDACTED = "«已隐藏»"

# 全局参数的分区展示：(分区名, 分区说明, 键)；键必须与 _conf_schema.json 一致，漏列的自动归「其他」
SECTIONS = (
    ("状态与权限", "决定谁能用功能",
     ("enable_group_owner", "enable_group_admin",
      "super_admins", "whitelist_admins", "blacklist")),
    ("模组 API", "插件调用服务器内模组的地址与鉴权",
     ("default_api_port", "mod_api_token")),
    ("中心数据库", "绑定数据的云端落点",
     ("use_central_db", "db_host", "db_port", "db_user",
      "db_password", "db_name")),
    ("卡片显示", "影响 /在线 状态卡片内容",
     ("show_server_version", "show_server_latency")),
    ("其他", "兜底参数", ("default_group_id",)),
)

_plugin_config = None
_schema: dict = {}


def _ok(data=None):
    return {"status": "ok", "data": data if data is not None else {}}


def _err(message: str):
    return {"status": "error", "message": message}


async def _body() -> tuple[dict, str]:
    """读取并校验 JSON 请求体，返回 (字典, 错误原因)"""
    raw = await web_request.json(default=None)
    if not isinstance(raw, dict):
        return {}, "请求体必须是 JSON 对象"
    return raw, ""


def _clean_gid(raw) -> str:
    gid = str(raw if raw is not None else "").strip()
    return gid if GROUP_ID_RE.match(gid) else ""


def _group_ids() -> list:
    """扫出可用的群配置文件：只认 5-12 位数字群号，其他会话文件不进面板"""
    base = sub_dir("group_configs")
    ids = []
    for name in os.listdir(base):
        if not (name.startswith("servers_") and name.endswith(".json")):
            continue
        gid = name[len("servers_"):-len(".json")]
        if GROUP_ID_RE.match(gid):
            ids.append(gid)
    return sorted(ids)


def _server_view(entry: dict) -> dict:
    """服务器条目的面板视图：console_token 只以布尔出现，绝不下发明文"""
    port = entry.get("api_port")
    return {
        "name": entry.get("name", ""),
        "host": entry.get("host", ""),
        "api_port": port,
        "remark": entry.get("remark", ""),
        "has_token": bool(console_token_of(entry)),
    }


def _token_count(servers: list) -> int:
    """与「命令 Token」列同判据：存的值要真能当 Bearer 头发出去才计数"""
    return sum(1 for s in servers if console_token_of(s))


def _group_payload(gid: str) -> dict:
    cm = ConfigManager(group_id=int(gid))
    wm = WhitelistManager()
    servers = cm.get_all_servers()
    return {
        "gid": gid,
        "file": f"servers_{gid}.json",
        "servers": [_server_view(s) for s in servers],
        "mod_count": sum(1 for s in servers if s.get("api_port") is not None),
        "token_count": _token_count(servers),
        "last_online_enabled": cm.is_last_online_enabled(),
        "nickname_check_enabled": cm.is_nickname_check_enabled(),
        "nickname_check_ignore_ids": cm.get_nickname_check_ignore_ids(),
        "max_servers_per_group": MAX_SERVERS_PER_GROUP,
        "default_api_port": wm.default_api_port,
    }


def _parse_port(raw):
    """端口输入：空值＝不装模组（返回 None），非法返回错误原因"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, ""
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, "端口必须是 1-65535 的数字"
    if port < 1 or port > 65535:
        return None, "端口必须是 1-65535 的数字"
    return port, ""


# ---------- 群与服务器 ----------

async def view_summary():
    groups = []
    for gid in _group_ids():
        servers = ConfigManager(group_id=int(gid)).get_all_servers()
        groups.append({
            "gid": gid,
            "server_count": len(servers),
            "mod_count": sum(1 for s in servers if s.get("api_port") is not None),
            "token_count": sum(1 for s in servers if s.get("console_token")),
        })
    wm = WhitelistManager()
    return _ok({
        "groups": groups,
        "max_servers_per_group": MAX_SERVERS_PER_GROUP,
        "default_api_port": wm.default_api_port,
        "plugin_version": _plugin_version(),
        "plugin_name": PLUGIN_NAME,
    })


async def view_group():
    gid = _clean_gid(web_request.query.get("gid", ""))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    return _ok(_group_payload(gid))


async def view_group_create():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if os.path.exists(group_config_path(gid)):
        return _err(f"该群已有配置文件 servers_{gid}.json")
    ConfigManager(group_id=int(gid))  # 构造函数即建文件，与 /mc add 首次落盘同一路径
    return _ok(_group_payload(gid))


async def view_group_delete():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    path = group_config_path(gid)
    if not os.path.exists(path):
        return _err(f"servers_{gid}.json 不存在")
    os.remove(path)
    return _ok({"gid": gid})


async def view_group_flag():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    key = body.get("key")
    value = body.get("value")
    if key not in ("last_online_enabled", "nickname_check_enabled"):
        return _err("不支持的开关项")
    if not isinstance(value, bool):
        return _err("开关值必须是 true/false")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    cm = ConfigManager(group_id=int(gid))
    if key == "last_online_enabled":
        cm.set_last_online_enabled(value)
    else:
        cm.set_nickname_check_enabled(value)
    return _ok({"key": key, "value": value})


async def view_group_ignore_add():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    qq = str(body.get("qq", "")).strip()
    if not qq.isdigit():
        return _err("QQ 号必须是纯数字")
    cm = ConfigManager(group_id=int(gid))
    ids = cm.get_nickname_check_ignore_ids()
    if qq in ids:
        return _err(f"QQ「{qq}」已在忽略列表中")
    ids.append(qq)
    cm.set_nickname_check_ignore_ids(ids)
    return _ok({"nickname_check_ignore_ids": ids})


async def view_group_ignore_remove():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    qq = str(body.get("qq", "")).strip()
    cm = ConfigManager(group_id=int(gid))
    ids = cm.get_nickname_check_ignore_ids()
    if qq not in ids:
        return _err(f"QQ「{qq}」不在忽略列表中")
    ids.remove(qq)
    cm.set_nickname_check_ignore_ids(ids)
    return _ok({"nickname_check_ignore_ids": ids})


async def view_server_create():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    name = str(body.get("name", "")).strip()
    host = str(body.get("host", "")).strip()
    if not name:
        return _err("名称不能为空")
    name_err = validate_server_name(name)
    if name_err:
        return _err(name_err)
    # 面板的打开者是 WebUI 登录用户，权限等同插件超级管理员：
    # 能改这个文件本来就能直接手写字段，放行内网地址不额外扩大暴露面。
    host_err = validate_host(host, allow_private=True)
    if host_err:
        return _err(host_err)
    port, port_err = _parse_port(body.get("api_port"))
    if port_err:
        return _err(port_err)
    remark = str(body.get("remark", "")).strip()
    remark_err = validate_remark(remark)
    if remark_err:
        return _err(remark_err)
    token = str(body.get("token", "")).strip()
    if token:
        token_err = _validate_console_token(token)
        if token_err:
            return _err(token_err)
    cm = ConfigManager(group_id=int(gid))
    if len(cm.get_all_servers()) >= MAX_SERVERS_PER_GROUP:
        return _err(f"添加失败：每个群最多绑定 {MAX_SERVERS_PER_GROUP} 个服务器。")
    if not cm.add_server(name, host, port):
        return _err(f"失败（名称已存在）：{name}")
    if remark:
        cm.edit_server(name, remark=remark)
    if token:
        cm.edit_server_console_token(name, token)
    return _ok(_group_payload(gid))


async def view_server_update():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    cm = ConfigManager(group_id=int(gid))
    name = str(body.get("name", ""))
    servers = cm.get_all_servers()
    if not any(s.get("name") == name for s in servers):
        return _err(f"未找到名为「{name}」的服务器。")

    new_name = str(body.get("new_name", name)).strip()
    if not new_name:
        return _err("名称不能为空")
    name_err = validate_server_name(new_name)
    if name_err:
        return _err(name_err)
    host = str(body.get("host", "")).strip()
    host_err = validate_host(host, allow_private=True)
    if host_err:
        return _err(host_err)
    api_port, port_err = _parse_port(body.get("api_port"))
    if port_err:
        return _err(port_err)
    remark = str(body.get("remark", "")).strip()
    remark_err = validate_remark(remark)
    if remark_err:
        return _err(remark_err)

    ok, why = cm.edit_server(name, new_name=new_name, host=host,
                              api_port=api_port, remark=remark)
    if not ok:
        return _err(why)
    return _ok(_group_payload(gid))


async def view_server_delete():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    name = str(body.get("name", ""))
    cm = ConfigManager(group_id=int(gid))
    if not cm.remove_server(name):
        return _err(f"删除失败（未找到）：{name}")
    return _ok(_group_payload(gid))


async def view_server_move():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    name = str(body.get("name", ""))
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return _err("位置序号必须是整数。")
    cm = ConfigManager(group_id=int(gid))
    if not cm.move_server(name, index):
        return _err("移动失败（名称不存在或序号无效）")
    return _ok(_group_payload(gid))


async def view_server_token():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    name = str(body.get("name", ""))
    token = body.get("token")
    if not isinstance(token, str):
        return _err("Token 必须是文本")
    cm = ConfigManager(group_id=int(gid))
    servers = cm.get_all_servers()
    entry = next((s for s in servers if s.get("name") == name), None)
    if entry is None:
        return _err(f"未找到名为「{name}」的服务器。")
    if not token:
        cm.edit_server_console_token(name, "")
        return _ok({
            "has_token": False,
            "message": f"已清除「{name}」的命令 Token，该服务器的 /执行 已关闭。",
        })
    token_err = _validate_console_token(token)
    if token_err:
        return _err(token_err)
    cm.edit_server_console_token(name, token)
    # 回执只报字符数，不回报文：面板同样会留档
    message = (f"已为「{name}」设置命令 Token（{len(token)} 个字符）。\n"
               f"需与该服务器模组的 consoleToken 完全一致；模组侧改动要重启服务器才生效。")
    if entry.get("api_port") is None:
        message += f"\n⚠️ 该服务器还没有配置 API 端口，先用 /mc edit {name} port <端口> 补上。"
    return _ok({"has_token": True, "char_count": len(token), "message": message})


async def view_server_batch_add():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    raw = str(body.get("text", ""))
    cm = ConfigManager(group_id=int(gid))
    success, failed = cm.batch_add(
        raw.replace("\n", ",").replace("，", ","),
        host_validator=lambda h: validate_host(h, allow_private=True),
        name_validator=validate_server_name)
    return _ok({
        "success": success,
        "failed": failed,
        "message": f"批量添加：成功 {success} 个" + (f"，失败: {', '.join(failed)}" if failed else ""),
        "group": _group_payload(gid),
    })


async def view_server_batch_remove():
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    names = body.get("names")
    if not isinstance(names, list):
        return _err("names 必须是数组")
    cm = ConfigManager(group_id=int(gid))
    # 逐条走 remove_server：服务器名里没有逗号分隔的约束，但 JSON 数组本就不必再拼字符串
    removed, failed = 0, []
    for raw_name in names:
        name = str(raw_name)
        if cm.remove_server(name):
            removed += 1
        else:
            failed.append(name)
    return _ok({
        "success": removed,
        "failed": failed,
        "message": f"批量删除：成功 {removed} 个" + (f"，失败: {', '.join(failed)}" if failed else ""),
        "group": _group_payload(gid),
    })


def _mod_target(wm, entry: dict):
    """把服务器条目换成 (host, port)；没配 api_port 就不是装了模组的服，返回 None"""
    if not wm.has_mod_api(entry):
        return None
    return str(entry.get("host") or ""), wm.get_server_port(entry)


def _mod_token() -> str:
    """取调用模组用的全局 Token：直接读内核传进来的那份配置对象。
    WhitelistManager 会另走一条「读 config 文件 + 缓存」的路径，面板刚改完的值可能读不到。"""
    return str((_plugin_config or {}).get("mod_api_token", "") or "")


async def view_server_whitelist():
    """下发「登录开关」到单台服务器：改模组的 enableWhitelistCheck，即时生效并写回模组配置"""
    body, err = await _body()
    if err:
        return _err(err)
    gid = _clean_gid(body.get("gid"))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    name = str(body.get("name", ""))
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return _err("开关值必须是 true/false")
    cm = ConfigManager(group_id=int(gid))
    entry = next((s for s in cm.get_all_servers() if s.get("name") == name), None)
    if entry is None:
        return _err(f"未找到名为「{name}」的服务器。")
    target = _mod_target(WhitelistManager(), entry)
    if target is None:
        return _err(f"服务器「{name}」未配置模组 API 端口，无法下发。")
    token = _mod_token()
    if not token:
        return _err("未配置全局 mod_api_token，无法调用模组接口。")

    ok, payload = await mod_http.request(
        target[0], target[1], "/api/whitelist_check",
        method="POST", data={"enabled": enabled}, token=token, timeout=MOD_WRITE_TIMEOUT)
    if not ok:
        return _err(f"下发失败：{payload}")
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    back = bool(data.get("enabled", enabled))
    released = int(data.get("released") or 0)
    message = f"「{name}」登录开关已{'开启' if back else '关闭'}"
    if not back and released:
        message += f"，放开 {released} 名在线受限玩家"
    if data.get("persisted") is False:
        message += "（模组配置文件写入失败，重启后会回到旧值）"
    return _ok({"enabled": back, "released": released, "persisted": data.get("persisted"), "message": message})


async def view_server_mod_state():
    """并发读每台已配端口的服当前的登录开关值；读不到的标 unreachable，不猜值"""
    gid = _clean_gid(web_request.query.get("gid", ""))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    if not os.path.exists(group_config_path(gid)):
        return _err(f"servers_{gid}.json 不存在")
    wm = WhitelistManager()
    token = _mod_token()
    servers = ConfigManager(group_id=int(gid)).get_all_servers()

    async def one(entry: dict):
        name = str(entry.get("name") or "")
        target = _mod_target(wm, entry)
        if target is None:
            return name, {"reachable": False, "enabled": None, "reason": "未配置模组 API 端口，开关不可用"}
        if not token:
            return name, {"reachable": False, "enabled": None, "reason": "未配置全局 mod_api_token"}
        ok, payload = await mod_http.request(
            target[0], target[1], "/api/status", method="GET", token=token, timeout=MOD_READ_TIMEOUT)
        if not ok or not isinstance(payload, dict):
            return name, {"reachable": False, "enabled": None, "reason": str(payload or "模组未响应")}
        data = payload.get("data") or {}
        if "whitelist_check_enabled" not in data:
            return name, {"reachable": True, "enabled": None,
                          "reason": "该服模组版本没有登录开关，需更新模组后重启服务器"}
        return name, {"reachable": True, "enabled": bool(data["whitelist_check_enabled"]), "reason": ""}

    pairs = await asyncio.gather(*(one(s) for s in servers))
    return _ok({"states": dict(pairs)})


async def view_json_group():
    gid = _clean_gid(web_request.query.get("gid", ""))
    if not gid:
        return _err("群号必须是 5-12 位数字")
    path = group_config_path(gid)
    if not os.path.exists(path):
        return _err(f"servers_{gid}.json 不存在")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return _err(f"读取 servers_{gid}.json 失败：{e}")
    for entry in data.get("servers", []):
        if isinstance(entry, dict) and "console_token" in entry:
            entry["console_token"] = REDACTED
    return _ok({"file": f"servers_{gid}.json",
                "text": json.dumps(data, ensure_ascii=False, indent=2)})


# ---------- 全局参数 ----------

def _load_schema() -> dict:
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[MCSight] 读取 _conf_schema.json 失败：{e}")
        return {}
    return data if isinstance(data, dict) else {}


def _section_of(key: str) -> str:
    for title, _desc, keys in SECTIONS:
        if key in keys:
            return title
    return "其他"


def _coerce(kind: str, key: str, value):
    """按 schema 声明的类型校验并转换，返回 (值, 错误原因)"""
    if kind == "bool":
        if not isinstance(value, bool):
            return None, f"{key} 必须是 true/false"
        return value, ""
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                value = int(str(value).strip())
            except (TypeError, ValueError):
                return None, f"{key} 必须是整数"
        if key.endswith("_port") or key == "default_api_port":
            if value < 1 or value > 65535:
                return None, "端口必须是 1-65535 的数字"
        return value, ""
    if kind == "list":
        if not isinstance(value, list):
            return None, f"{key} 必须是列表"
        return [str(x).strip() for x in value if str(x).strip()], ""
    if value is None:
        return "", ""
    if not isinstance(value, str):
        return None, f"{key} 必须是文本"
    return value.strip(), ""


def _config_item(key: str) -> dict:
    meta = _schema.get(key, {})
    kind = meta.get("type", "string")
    secret = key in SECRET_KEYS
    current = _plugin_config.get(key, meta.get("default"))
    if secret:
        value = None
        has_value = bool(current)
    else:
        value = current
        has_value = True
    return {
        "key": key,
        "type": kind,
        "section": _section_of(key),
        "desc": meta.get("description", key),
        "hint": meta.get("hint", ""),
        "default": meta.get("default"),
        "secret": secret,
        "value": value,
        "has_value": has_value,
        # 连接池只在插件加载时按当时的库参数建立（commands/common.py 的 _pool 全局量）
        "restart_required": key.startswith("db_"),
    }


async def view_config():
    if _plugin_config is None:
        return _err("内核未向插件传入配置对象，全局参数请改配置文件")
    known = list(_schema.keys())
    # 按分区顺序出条目：schema 的书写顺序与分区无关，直接沿用它会把同一个分区拆成好几张卡
    ordered = [k for _, _, keys in SECTIONS for k in keys if k in _schema]
    ordered += [k for k in known if k not in ordered]
    items = [_config_item(k) for k in ordered]
    extra = [k for k in _plugin_config.keys() if k not in _schema]
    return _ok({
        "items": items,
        "extra_keys": sorted(extra),
        "sections": [{"title": t, "desc": d} for t, d, _ in SECTIONS],
    })


async def view_config_update():
    if _plugin_config is None:
        return _err("内核未向插件传入配置对象，全局参数请改配置文件")
    body, err = await _body()
    if err:
        return _err(err)
    key = str(body.get("key", ""))
    if key in SECRET_KEYS:
        return _err(f"{key} 是密钥，请用「更换密钥」单独设置")
    if key not in _schema:
        return _err(f"配置项 {key} 不在 _conf_schema.json 里，不予写入")
    value, coerce_err = _coerce(_schema[key].get("type", "string"), key, body.get("value"))
    if coerce_err:
        return _err(coerce_err)
    await _plugin_config.save_config_async({key: value})
    WhitelistManager().clear_cache()
    return _ok({"item": _config_item(key)})


async def view_config_secret():
    if _plugin_config is None:
        return _err("内核未向插件传入配置对象，全局参数请改配置文件")
    body, err = await _body()
    if err:
        return _err(err)
    key = str(body.get("key", ""))
    if key not in SECRET_KEYS:
        return _err(f"{key} 不是密钥项")
    raw = body.get("value")
    if raw is not None and not isinstance(raw, str):
        return _err("密钥必须是文本")
    # 模组 Token 要进 Bearer 头，前后空格会造成「配了但鉴权失败」；库密码不strip，空格可能是口令本身
    value = (raw or "").strip() if key == "mod_api_token" else (raw or "")
    await _plugin_config.save_config_async({key: value})
    WhitelistManager().clear_cache()
    return _ok({
        "key": key,
        "has_value": bool(value),
        "char_count": len(value),
        "restart_required": key.startswith("db_"),
    })


async def view_config_json():
    if _plugin_config is None:
        return _err("内核未向插件传入配置对象，全局参数请改配置文件")
    snapshot = {}
    for key in _schema:
        current = _plugin_config.get(key)
        snapshot[key] = REDACTED if (key in SECRET_KEYS and current) else current
    for key in sorted(k for k in _plugin_config.keys() if k not in _schema):
        snapshot[key] = _plugin_config.get(key)
    return _ok({"file": f"{PLUGIN_NAME}_config.json",
                "text": json.dumps(snapshot, ensure_ascii=False, indent=4)})


async def view_constants():
    rows = [{
        "title": title,
        "name": name,
        "value": "、".join(str(x) for x in value) if isinstance(value, (list, tuple)) else str(value),
        "unit": unit,
        "source": source,
    } for (title, name, value, unit, source) in ROWS]
    return _ok({"rows": rows})


def _runs_text(runs) -> str:
    """HELP_CARDS 里的文案是一段段 (文本, 颜色) 组合，面板只取文本"""
    if not isinstance(runs, list):
        return str(runs or "")
    return "".join(str(t) for t, _ in runs).strip()


async def view_commands():
    """命令帮助页的数据源：与 /帮助 图片同一个结构，逐条摊平成表格，页面只读"""
    from ..features.help_image import image_generator as hi

    sections = []
    for card in hi.HELP_CARDS:
        for sec in card:
            rows = []
            for r in sec.get("rows", []):
                if not isinstance(r, dict):
                    continue
                rows.append({
                    "cmd": _runs_text(r.get("cmd")),
                    "desc": _runs_text(r.get("desc")),
                    "aliases": " · ".join(str(p) for p in r.get("pills", [])),
                    "tags": " · ".join(str(t[0]) for t in r.get("tags", [])),
                })
            sections.append({
                "title": sec.get("title", ""),
                "hint": sec.get("hint", ""),
                "rows": rows,
            })
    return _ok({
        "sections": sections,
        "total": sum(len(s["rows"]) for s in sections),
    })


# ---------- 插件自身 ----------

def _plugin_manager():
    """取内核的 PluginManager：面板「插件列表 → 重载」那颗按钮走的就是它。

    内核没给插件侧公开入口，只能顺着本次请求摸到 app.state.core_lifecycle；
    摸不到就返回 None，由调用侧退回「只重读数据」。
    """
    raw = getattr(web_request, "_request", None)
    state = getattr(getattr(raw, "app", None), "state", None)
    return getattr(getattr(state, "core_lifecycle", None), "plugin_manager", None)


async def view_plugin_reload():
    """真重载本插件：拆掉模块再按当前配置文件重建，与面板插件列表等效。

    响应必须先发出去——reload() 会把正在处理本次请求的这个模块实例终止并替换。
    """
    pm = _plugin_manager()
    if pm is None or not hasattr(pm, "reload"):
        return _err("当前内核没给插件开放重载入口，请到面板的插件列表里手动重载。")

    async def do_reload() -> None:
        await asyncio.sleep(0.4)  # 让本次响应先回到浏览器
        try:
            success, err = await pm.reload(PLUGIN_NAME)
            if success:
                logger.info(f"[MCSight] {PLUGIN_NAME} 已从配置页触发重载")
            else:
                logger.error(f"[MCSight] 配置页触发重载失败：{err}")
        except Exception as e:  # 重载失败不能让后台任务静默吞掉
            logger.error(f"[MCSight] 配置页触发重载异常：{e}")

    asyncio.create_task(do_reload())
    return _ok({"reloading": True, "message": f"正在重载 {PLUGIN_NAME}…"})


ROUTES = (
    ("summary", view_summary, ["GET"], "群与服务器总览"),
    ("group", view_group, ["GET"], "读取单个群的配置"),
    ("group/create", view_group_create, ["POST"], "创建群配置文件"),
    ("group/delete", view_group_delete, ["POST"], "删除群配置文件"),
    ("group/flag", view_group_flag, ["POST"], "本群开关"),
    ("group/ignore_add", view_group_ignore_add, ["POST"], "昵称检测忽略名单加人"),
    ("group/ignore_remove", view_group_ignore_remove, ["POST"], "昵称检测忽略名单减人"),
    ("server/create", view_server_create, ["POST"], "添加服务器"),
    ("server/update", view_server_update, ["POST"], "编辑服务器"),
    ("server/delete", view_server_delete, ["POST"], "删除服务器"),
    ("server/move", view_server_move, ["POST"], "调整服务器顺序"),
    ("server/token", view_server_token, ["POST"], "写入或清除命令 Token"),
    ("server/batch_add", view_server_batch_add, ["POST"], "批量添加服务器"),
    ("server/batch_remove", view_server_batch_remove, ["POST"], "批量删除服务器"),
    ("server/whitelist", view_server_whitelist, ["POST"], "下发模组登录开关"),
    ("server/mod_state", view_server_mod_state, ["GET"], "读取各服模组状态"),
    ("json/group", view_json_group, ["GET"], "群配置文件预览"),
    ("config", view_config, ["GET"], "全局参数列表"),
    ("config/update", view_config_update, ["POST"], "写入全局参数"),
    ("config/secret", view_config_secret, ["POST"], "写入或清除密钥"),
    ("config/json", view_config_json, ["GET"], "全局参数预览"),
    ("constants", view_constants, ["GET"], "代码常量清单"),
    ("commands", view_commands, ["GET"], "命令帮助清单"),
    ("plugin/reload", view_plugin_reload, ["POST"], "重载插件本体"),
)


def register_view_api(context, config=None) -> None:
    """把配置页接口挂到内核的插件 Web API 表上；老内核静默跳过"""
    global _plugin_config, _schema
    if not WEB_API_SUPPORTED:
        logger.info("[MCSight] 当前内核没有 astrbot.api.web，配置页接口未注册")
        return
    if config is None:
        logger.warning("[MCSight] 内核未传入插件配置对象，配置页接口未注册")
        return
    _plugin_config = config
    _schema = _load_schema()
    for name, handler, methods, desc in ROUTES:
        context.register_web_api(
            f"{ROUTE_PREFIX}/{name}", handler, methods, f"MCSight 配置页 · {desc}")
    logger.info(f"[MCSight] 配置页接口已注册 {len(ROUTES)} 个")
