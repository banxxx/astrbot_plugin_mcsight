import json
import os
import re
import shutil
import unicodedata
from typing import List, Dict, Optional, Tuple

from ..utils.plugin_paths import sub_dir
from ..utils.bind_rule import normalize_rule
from ..utils.group_policy import (
    GROUP_FILE_SCHEMA_VERSION,
    POLICY_BOOL_KEYS,
    POLICY_DEFAULTS,
    default_policy,
    normalize_policy,
)
from . import onebot_store
from .onebot_store import OneBotError

# 每个群最多绑定的服务器数量（/mc add、/mc batchadd 时拦截）
MAX_SERVERS_PER_GROUP = 20

# 服务器名不能含方括号：/执行 用 [服名] 定界，名字里带括号会让解析错位
INVALID_NAME_CHARS = ("[", "]", "【", "】")

# 长度上限按显示宽度算：汉字与全角标点记 2，所以 20 字符＝10 个汉字
MAX_NAME_WIDTH = 20
MAX_REMARK_WIDTH = 50
# 群别名上限：与 QQ 官方的 alias_text 同一口径（8 个中文或 16 个英文字符）
MAX_ALIAS_WIDTH = 16

# 备注取值来源：手填（默认）/ 服务端 MOTD
REMARK_SRC_MANUAL = "manual"
REMARK_SRC_MOTD = "motd"
REMARK_SOURCES = (REMARK_SRC_MANUAL, REMARK_SRC_MOTD)

# §色码：卡片字体不渲染它，留在备注里是一串乱码
MOTD_COLOR_CODE = re.compile(r"\u00a7[0-9a-zA-Z]")

# Minecraft \u4f20\u7edf 16 \u8272 \u2192 \u6d45\u8272\u5361\u7247/\u767d\u5e95\u4e0a\u7684\u5b89\u5168\u914d\u8272\u3002
# \u539f\u59cb\u6df1\u8272\u5728\u767d\u5e95\u672c\u6765\u5c31\u6e05\u6670\uff0c\u6d45\u8272\u7cfb\uff08\u767d/\u9ec4/\u9752/\u7070\u2026\uff09\u538b\u6df1\u5230\u80fd\u770b\u6e05\uff1b\u663e\u5f0f \u00a7f \u767d\u8272\u6298\u6210\u4e2d\u7070\u3002
MC_COLOR_SAFE = {
    "black": "#1f2328",
    "dark_blue": "#2c3fa8",
    "dark_green": "#2e7d32",
    "dark_aqua": "#1f7a7a",
    "dark_red": "#b03a3a",
    "dark_purple": "#7d2b8c",
    "gold": "#a97a0a",
    "gray": "#6b7280",
    "dark_gray": "#3f4754",
    "blue": "#4a5fd6",
    "green": "#3aa23f",
    "aqua": "#2b9e9e",
    "red": "#d64545",
    "light_purple": "#a05bc0",
    "yellow": "#b5980a",
    "white": "#4a4a4a",
}

# \u00a70-\u00a7f \u2192 \u989c\u8272\u540d\uff08\u5c0f\u5199\uff09\uff1b\u00a7r \u5355\u72ec\u5904\u7406\uff1b\u5176\u4f59 \u00a7x \u662f\u65e0\u8272\u683c\u5f0f\u7801\uff0c\u89c1 _append_legacy_runs\u3002
_MC_LEGACY_CODE = {
    "0": "black", "1": "dark_blue", "2": "dark_green", "3": "dark_aqua",
    "4": "dark_red", "5": "dark_purple", "6": "gold", "7": "gray",
    "8": "dark_gray", "9": "blue", "a": "green", "b": "aqua",
    "c": "red", "d": "light_purple", "e": "yellow", "f": "white",
}
# ASCII \u5b57\u6bcd\u6570\u5b57\uff08\u4e0e\u65e7 \u00a7[0-9a-zA-Z] \u540c\u53e3\u5f84\uff0c\u4e0d\u542b\u4e2d\u6587/emoji\uff09
_ASCII_ALNUM = "0123456789abcdefghijklmnopqrstuvwxyz"


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def truncate_by_width(text: str, max_width: int) -> str:
    """按显示宽度截断，放不下以省略号收尾（上限与卡片折行规则配套，别放开）"""
    if display_width(text) <= max_width:
        return text
    kept = []
    used = 0
    for ch in text:
        width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + width > max_width - 1:
            break
        kept.append(ch)
        used += width
    return "".join(kept).rstrip() + "…"


def validate_server_name(name: str) -> str:
    """返回拒绝原因；合法返回空串"""
    if any(c.isspace() for c in name):
        return "名称不能含空格（指令用空格分隔参数），请用 - 或 _ 连接"
    bad = [c for c in INVALID_NAME_CHARS if c in name]
    if bad:
        return "名称不能包含方括号（{}），/执行 要用 [服务器名] 定界".format(" ".join(bad))
    if display_width(name) > MAX_NAME_WIDTH:
        return "名称最多 {} 字符（约 {} 个汉字），当前 {} 字符".format(
            MAX_NAME_WIDTH, MAX_NAME_WIDTH // 2, display_width(name))
    return ""


def validate_remark(remark: str) -> str:
    """备注只进 /在线 卡片，长度上限防把卡片撑爆"""
    if display_width(remark) > MAX_REMARK_WIDTH:
        return "备注最多 {} 字符（约 {} 个汉字），当前 {} 字符".format(
            MAX_REMARK_WIDTH, MAX_REMARK_WIDTH // 2, display_width(remark))
    return ""


def validate_group_alias(alias: str) -> str:
    """群别名校验；空串表示「不设置别名」，合法返回空串"""
    value = str(alias or "").strip()
    if display_width(value) > MAX_ALIAS_WIDTH:
        return "群别名最多 8 个中文字符（或 16 个英文字符）"
    return ""


def _normalize_alias(value) -> str:
    """把磁盘上的别名收敛成标准值；超长按显示宽度截断（不追加省略号，避免存进坏值）"""
    text = str(value or "").strip()
    if display_width(text) <= MAX_ALIAS_WIDTH:
        return text
    kept, used = [], 0
    for ch in text:
        width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + width > MAX_ALIAS_WIDTH:
            break
        kept.append(ch)
        used += width
    return "".join(kept)


ONEBOT_DIR = "onebot"
GROUP_STATE_FILE = "state.json"
LEGACY_GROUP_DIR = "group_configs"

# 旧 OneBot 群文件里的扁平开关 -> policy 字段；只在新目录还没有文件时用于一次性迁移。
_LEGACY_POLICY_KEYS = {
    "enable_group_owner": "enable_group_owner",
    "enable_group_admin": "enable_group_admin",
    "show_server_version": "show_server_version",
    "show_server_latency": "show_server_latency",
    "last_online_enabled": "show_last_online",
    "nickname_check_enabled": "nickname_check_enabled",
    "bind_require_nickname": "bind_require_nickname",
    "use_central_db": "use_central_db",
}


def _safe_group_key(group_id) -> str:
    key = str(group_id).strip()
    if not key or key in (".", "..") or any(c in key for c in '<>:"/\\|?*'):
        raise ValueError("群标识不能包含路径分隔符")
    return key


def group_dir(group_id) -> str:
    """onebot/<群号>/，只拼路径；目录由写入方创建。"""
    return os.path.join(sub_dir(ONEBOT_DIR), _safe_group_key(group_id))


def group_config_path(group_id) -> str:
    """新标准路径：onebot/<群号>/state.json。"""
    return os.path.join(group_dir(group_id), GROUP_STATE_FILE)


def legacy_group_config_path(group_id) -> str:
    """旧版路径：group_configs/servers_<群号>.json，仅用于一次性迁移/删除。"""
    return os.path.join(sub_dir(LEGACY_GROUP_DIR), f"servers_{_safe_group_key(group_id)}.json")


def _scope_text(group_id) -> str:
    """数据库里的群/会话标识；不再拼路径，但仍限制长度与控制字符。"""
    value = str(group_id).strip()
    if not value or len(value) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise OneBotError("群标识无效")
    return value


def _store_root() -> str:
    return sub_dir(ONEBOT_DIR)


def _ready_store():
    """打开 OneBot 库；首次调用时完成旧 JSON 一次性迁移。"""
    store = onebot_store.get_store(_store_root())
    created, sources = store.ensure_ready(_bootstrap_documents)
    if created and sources:
        _archive_sources(sources)
    return store


def ensure_store_ready():
    return _ready_store()


def open_store_for_import():
    """显式导入备份时打开存储；允许在 legacy/ 留档仍在时先建空库。

    返回 ``(store, created)``。只有本次新建的空库才应在导入失败后删除，
    已有正式库失败时保持原样。
    """
    store = onebot_store.get_store(_store_root())
    existed = store.db_path.is_file()
    try:
        created, _sources = store.ensure_ready(
            lambda: ([], []), allow_legacy_archive=True)
    except Exception:
        if not existed and store.db_path.is_file():
            discard_created_store()
        raise
    return store, created


def discard_created_store():
    """导入失败且本次新建过空库时删除它，恢复失败关闭状态。"""
    root = _store_root()
    onebot_store.close_store(root)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(os.path.join(root, onebot_store.DB_FILE_NAME + suffix))
        except OSError:
            pass


def import_store_state(document, expected_revision):
    """整份导入备份；失败时只清理由本次导入新建的空库。"""
    store, created = open_store_for_import()
    try:
        return store.import_state(
            document, expected_revision, validator=validate_backup_document)
    except Exception:
        if created:
            discard_created_store()
        raise


def has_group_config(group_id) -> bool:
    return _ready_store().group_exists("group", _scope_text(group_id))


def delete_group_config(group_id) -> bool:
    """删除群配置；legacy/ 留档不在这里删除。"""
    return _ready_store().delete_group("group", _scope_text(group_id))


def _normalize_ignore(value) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    result, seen = [], set()
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result[:1000]


def _legacy_document(data, group_id) -> dict:
    """把旧扁平群文件映射成标准文档；只读一次，之后按新结构写回。"""
    data = data if isinstance(data, dict) else {}
    policy = data.get("policy")
    if isinstance(policy, dict):
        policy = normalize_policy(policy)
    else:
        overrides = {}
        for old, new in _LEGACY_POLICY_KEYS.items():
            if type(data.get(old)) is bool:
                overrides[new] = data[old]
        if isinstance(data.get("bind_nickname_rule"), str):
            overrides["bind_nickname_rule"] = data["bind_nickname_rule"]
        if isinstance(data.get("bind_nickname_template"), str):
            overrides["bind_nickname_template"] = data["bind_nickname_template"]
        policy = default_policy(overrides)
    enabled = data.get("enabled", True)
    servers = data.get("servers")
    ignore = data.get("nickname_ignore")
    if not isinstance(ignore, list):
        ignore = data.get("nickname_check_ignore_ids")
    return {
        "schema_version": GROUP_FILE_SCHEMA_VERSION,
        "platform": "onebot",
        "group_id": str(group_id),
        "enabled": enabled if type(enabled) is bool else True,
        "alias": _normalize_alias(data.get("alias")),
        "servers": servers if isinstance(servers, list) else [],
        "policy": policy,
        "nickname_ignore": _normalize_ignore(ignore),
    }


def _write_document(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json_object(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise OneBotError(
            f"OneBot 旧配置迁移失败：{os.path.basename(path)} 无法读取或不是合法 JSON。") from e
    if not isinstance(data, dict):
        raise OneBotError(
            f"OneBot 旧配置迁移失败：{os.path.basename(path)} 不是 JSON 对象。")
    return data


def _normalize_server_entry(raw) -> dict:
    if not isinstance(raw, dict):
        raise OneBotError("OneBot 旧配置迁移失败：服务器条目不是对象。")
    name = raw.get("name")
    host = raw.get("host")
    if not isinstance(name, str) or not name.strip():
        raise OneBotError("OneBot 旧配置迁移失败：服务器名称为空。")
    if not isinstance(host, str) or not host.strip():
        raise OneBotError(f"OneBot 旧配置迁移失败：服务器「{name}」地址为空。")
    port = raw.get("api_port")
    if port in (None, ""):
        port = None
    elif type(port) is bool:
        raise OneBotError(f"OneBot 旧配置迁移失败：服务器「{name}」模组端口无效。")
    else:
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise OneBotError(
                f"OneBot 旧配置迁移失败：服务器「{name}」模组端口无效。") from None
        if not 1 <= port <= 65535:
            raise OneBotError(f"OneBot 旧配置迁移失败：服务器「{name}」模组端口无效。")
    hidden = raw.get("hidden", False)
    if type(hidden) is not bool:
        raise OneBotError(f"OneBot 旧配置迁移失败：服务器「{name}」隐藏标记无效。")
    remark = raw.get("remark", "")
    remark_motd = raw.get("remark_motd", "")
    if not isinstance(remark, str) or not isinstance(remark_motd, str):
        raise OneBotError(f"OneBot 旧配置迁移失败：服务器「{name}」备注格式无效。")
    remark_src = raw.get("remark_src", "manual")
    if remark_src not in REMARK_SOURCES:
        remark_src = REMARK_SRC_MANUAL
    remark_motd_at = raw.get("remark_motd_at", 0)
    if type(remark_motd_at) not in (int, float):
        remark_motd_at = 0
    runs = raw.get("remark_motd_runs")
    if not isinstance(runs, list):
        runs = None
    token = raw.get("console_token")
    if token is not None and not isinstance(token, str):
        raise OneBotError(f"OneBot 旧配置迁移失败：服务器「{name}」命令 Token 无效。")
    entry = {
        "name": name,
        "host": host,
        "api_port": port,
        "hidden": hidden,
        "remark": remark,
        "remark_src": remark_src,
        "remark_motd": remark_motd,
        "remark_motd_at": remark_motd_at,
    }
    if runs is not None:
        entry["remark_motd_runs"] = runs
    if token:
        entry["console_token"] = token
    server_id = raw.get("server_id")
    if server_id is not None:
        if not isinstance(server_id, str) or not server_id or len(server_id) > 64:
            raise OneBotError(f"OneBot 旧配置迁移失败：服务器「{name}」标识无效。")
        entry["server_id"] = server_id
    return entry


def _migrated_scope_kind(scope_id) -> str:
    """旧布局用目录名区分作用域：5–12 位数字是 QQ 群，其余按私聊会话处理。"""
    return "group" if re.fullmatch(r"\d{5,12}", str(scope_id)) else "private"


def _normalize_migrated_document(data, scope_id, *, legacy=False, scope_kind="group") -> dict:
    if data.get("platform") not in (None, "onebot"):
        raise OneBotError("OneBot 旧配置迁移失败：配置文件平台标记不是 onebot。")
    base = _legacy_document(data, scope_id) if legacy else data
    policy = base.get("policy")
    policy = normalize_policy(policy if isinstance(policy, dict) else None)
    servers = base.get("servers", [])
    if not isinstance(servers, list):
        raise OneBotError("OneBot 旧配置迁移失败：服务器列表不是数组。")
    return {
        "scope_kind": scope_kind,
        "scope_id": _scope_text(scope_id),
        "enabled": base.get("enabled") if type(base.get("enabled")) is bool else True,
        "alias": _normalize_alias(base.get("alias")),
        "servers": [_normalize_server_entry(s) for s in servers],
        "policy": policy,
        "nickname_ignore": _normalize_ignore(base.get("nickname_ignore")),
    }


def validate_backup_document(document):
    """校验后台导出文件，返回可直接交给 OneBotStore 导入的文档列表。"""
    keys = {"format", "format_version", "exported_at", "schema_version",
            "revision", "groups"}
    if (not isinstance(document, dict) or set(document) != keys
            or document.get("format") != onebot_store.BACKUP_FORMAT
            or type(document.get("format_version")) is not int
            or document.get("format_version") != onebot_store.BACKUP_FORMAT_VERSION
            or type(document.get("revision")) is not int
            or document.get("revision") < 0
            or not isinstance(document.get("groups"), list)):
        raise OneBotError("OneBot 配置备份格式无效。")
    docs = []
    for raw in document["groups"]:
        if not isinstance(raw, dict):
            raise OneBotError("OneBot 配置备份中的群条目无效。")
        kind = raw.get("scope_kind", "group")
        if kind not in ("group", "private"):
            raise OneBotError("OneBot 配置备份中的群类型无效。")
        scope_id = _scope_text(raw.get("scope_id"))
        for server in raw.get("servers", []):
            if isinstance(server, dict) and "console_token" in server:
                raise OneBotError("OneBot 配置备份不能包含命令 Token。")
        doc = _normalize_migrated_document(raw, scope_id)
        doc["scope_kind"] = kind
        docs.append(doc)
    return docs


def _bootstrap_documents():
    """读取旧 JSON，返回 (可直接写入数据库的文档, 需要留档的源文件)。"""
    root = _store_root()
    docs = []
    seen = set()
    sources = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        if name == onebot_store.LEGACY_DIR_NAME or name.startswith("."):
            continue
        path = os.path.join(root, name, GROUP_STATE_FILE)
        if not os.path.isfile(path):
            continue
        data = _read_json_object(path)
        doc = _normalize_migrated_document(
            data, name, scope_kind=_migrated_scope_kind(name))
        docs.append(doc)
        seen.add((doc["scope_kind"], doc["scope_id"]))
        sources.append((path, os.path.join(
            root, onebot_store.LEGACY_DIR_NAME, "groups", name, GROUP_STATE_FILE)))

    legacy_base = sub_dir(LEGACY_GROUP_DIR)
    try:
        legacy_names = sorted(os.listdir(legacy_base))
    except OSError:
        legacy_names = []
    for filename in legacy_names:
        if not (filename.startswith("servers_") and filename.endswith(".json")):
            continue
        group_id = filename[len("servers_"):-len(".json")]
        path = os.path.join(legacy_base, filename)
        target = os.path.join(root, onebot_store.LEGACY_DIR_NAME,
                              LEGACY_GROUP_DIR, filename)
        if ("group", _scope_text(group_id)) in seen:
            sources.append((path, target))
            continue
        data = _read_json_object(path)
        docs.append(_normalize_migrated_document(data, group_id, legacy=True))
        seen.add(group_id)
        sources.append((path, target))
    return docs, sources


def _unique_archive_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    index = 2
    while os.path.exists(f"{stem}.{index}{ext}"):
        index += 1
    return f"{stem}.{index}{ext}"


def _archive_sources(sources):
    """迁移成功后把旧文件移入 legacy/；失败只保留原文件，不影响已提交的库。"""
    for source, target in sources:
        try:
            if not os.path.exists(source):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, _unique_archive_path(target))
            try:
                os.rmdir(os.path.dirname(source))
            except OSError:
                pass
        except OSError:
            continue
    try:
        os.rmdir(sub_dir(LEGACY_GROUP_DIR))
    except OSError:
        pass


def migrate_legacy_group_configs() -> tuple:
    """兼容旧调用：首次打开时迁移；返回 (本次导入群数, 失败数)。"""
    store = onebot_store.get_store(_store_root())
    created, sources = store.ensure_ready(_bootstrap_documents)
    if created and sources:
        _archive_sources(sources)
    return (len(sources), 0) if created else (0, 0)


def group_ids() -> list:
    return _ready_store().list_groups("group")


def group_summaries() -> list:
    return _ready_store().list_group_summaries("group")


def all_enabled_server_endpoints() -> list:
    return _ready_store().all_enabled_server_endpoints()


def read_group_flags(group_id, keys, defaults=None) -> dict:
    """一次读取多个群策略开关，供权限检查使用。"""
    defaults = defaults or {}
    # 权限检查在每条命令前执行：只读群策略，不把整库的 Token 一起取出来。
    doc = _ready_store().get_group(
        "group", _scope_text(group_id), include_tokens=False)
    policy = doc.get("policy") if isinstance(doc, dict) else None
    if not isinstance(policy, dict):
        policy = {}
    out = {}
    for key in keys:
        fallback = defaults.get(key)
        if fallback is None:
            fallback = POLICY_DEFAULTS.get(key, False)
        out[key] = bool(policy.get(key, fallback))
    return out


def read_group_flag(group_id, key: str, default: Optional[bool] = None) -> bool:
    """只读本群 policy 开关；群不存在时返回默认值，不在读路径上创建群。"""
    return read_group_flags(group_id, (key,), {key: default})[key]


def console_token_of(entry: dict) -> str:
    """取该服真正能用的命令 Token：空白、含空格或不可见字符的一律按「未配置」处理。

    这个判据同时供 /执行 与面板的「已配置/未设置」标签使用，避免机器人里存了个发不出去的
    字符串、面板却显示已配置。
    """
    raw = str(entry.get("console_token") or "").strip()
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in raw):
        return ""
    return raw


def remark_src_of(entry: dict) -> str:
    """该服备注当前用哪个来源；认不了的写法一律回落到手填（默认值）"""
    src = str(entry.get("remark_src") or "").strip().lower()
    return src if src in REMARK_SOURCES else REMARK_SRC_MANUAL


def cached_remark_motd(entry: dict) -> str:
    return str(entry.get("remark_motd") or "").strip()


def effective_remark(entry: dict) -> str:
    """备注的最终显示值，全插件只有这一个口径。

    开关选了 MOTD 但还没采到过值时用空串会让卡片名字下面凭空缺一行，所以退回手填值
    （手填默认也是空，那就等于没有备注）——面板要靠 remark_motd 是否为空提示用户去同步。
    """
    if remark_src_of(entry) == REMARK_SRC_MOTD:
        cached = cached_remark_motd(entry)
        if cached:
            return cached
    return str(entry.get("remark") or "")


def _flatten_json_text(raw: str) -> Optional[str]:
    """MOTD 写成 JSON 文本组件时把所有 text/extra 拼成一行；解析不了返回 None。

    （纯文本口径仍保留；彩色结构由 motd_runs/_walk_json_runs 处理。）
    """
    try:
        node = json.loads(raw)
    except (ValueError, TypeError):
        return None
    parts: List[str] = []

    def walk(item):
        if isinstance(item, dict):
            for key in ("text", "extra", "contents", "content"):
                if key in item:
                    walk(item[key])
        elif isinstance(item, list):
            for sub in item:
                walk(sub)
        elif isinstance(item, str):
            parts.append(item)

    walk(node)
    return " ".join("".join(parts).split())


def _append_legacy_runs(text: str, ambient: Optional[str], runs: List[Tuple[str, Optional[str]]]) -> None:
    """把一段文本按 § 色码切成 (text, color_name|None) 段。

    ambient 是 JSON 父组件继承下来的颜色名（可为 None）；§r 回到 ambient。
    只认 §0-§f 颜色与 §r 重置，§l/o/n/m/k 等格式码与 §x(hex) 一律当无色跳过，
    clickEvent/hoverEvent 不在这里处理（由 JSON 遍历方直接不读）。
    """
    buf: List[str] = []
    color = ambient
    i, n = 0, len(text)

    def flush():
        if buf:
            runs.append(("".join(buf), color))
            buf.clear()

    while i < n:
        ch = text[i]
        if ch == "§" and i + 1 < n:
            code = text[i + 1].lower()
            if code in _MC_LEGACY_CODE:
                flush()
                color = _MC_LEGACY_CODE[code]
                i += 2
                continue
            if code == "r":
                flush()
                color = ambient
                i += 2
                continue
            if code in _ASCII_ALNUM:
                # 其它 ASCII 格式码（l/o/n/m/k…）：无色，跳两字符
                i += 2
                continue
            # § 后跟非 ASCII 字符（空格/中文/emoji/另一个 §）：§ 当普通字符
        buf.append(ch)
        i += 1
    flush()


def _walk_json_runs(node, ambient: Optional[str], runs: List[Tuple[str, Optional[str]]]) -> None:
    """递归 JSON 文本组件，按 color 字段继承分段着色；clickEvent/hoverEvent 一律忽略。"""
    if isinstance(node, dict):
        color = ambient
        c = node.get("color")
        if isinstance(c, str) and c in MC_COLOR_SAFE:
            color = c
        for key in ("text", "extra", "contents", "content"):
            if key in node:
                _walk_json_runs(node[key], color, runs)
    elif isinstance(node, list):
        for sub in node:
            _walk_json_runs(sub, ambient, runs)
    elif isinstance(node, str):
        _append_legacy_runs(node, ambient, runs)


def _coalesce(seq: List[Tuple[str, Optional[str]]]) -> List[Tuple[str, Optional[str]]]:
    """合并相邻同色段。"""
    out: List[Tuple[str, Optional[str]]] = []
    for text, color in seq:
        if out and out[-1][1] == color:
            out[-1] = (out[-1][0] + text, color)
        else:
            out.append((text, color))
    return out


def _truncate_chars(seq, max_width: int):
    """按显示宽度截断字符段，放不下以 … 收尾（与 truncate_by_width 同口径）。"""
    kept = []
    used = 0
    for ch, color in seq:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > max_width - 1:
            break
        kept.append((ch, color))
        used += w
    while kept and kept[-1][0].isspace():
        kept.pop()
    tail = kept[-1][1] if kept else None
    kept.append(("…", tail))
    return kept


def motd_runs(raw) -> Tuple[str, List[Tuple[str, Optional[str]]]]:
    """解析 MOTD 的颜色结构，返回 (plain, runs)。

    plain 与旧 clean_motd 完全一致（纯文本一行、按 MAX_REMARK_WIDTH 截断）；
    runs 是 [(text, 安全色hex|None), ...]，None 表示默认色，供 WebUI 与 /在线 图片分段着色。
    支持 §0-§f / §r 色码与 JSON 文本组件的 color 名称（16 传统色名）；hex 自定义色与
    clickEvent/hoverEvent 忽略。解析不出颜色时 runs 为 []，纯文本照样可用。
    """
    if raw is None:
        return "", []
    text = raw if isinstance(raw, str) else str(raw)
    text = text.strip()
    runs: List[Tuple[str, Optional[str]]] = []
    parsed_json = False
    if text.startswith("{") or text.startswith("["):
        try:
            node = json.loads(text)
            parsed_json = True
        except (ValueError, TypeError):
            node = None
        if node is not None:
            _walk_json_runs(node, None, runs)
    if not parsed_json:
        _append_legacy_runs(text, None, runs)

    # 字面量 \n → 空格，丢弃空段，合并相邻同色
    runs = [(t.replace("\\n", " "), c) for t, c in runs if t]
    runs = _coalesce(runs)
    # 字符级压缩空白（跨段统一成单个空格）
    seq: List[Tuple[str, Optional[str]]] = []
    prev_space = False
    for t, c in runs:
        for ch in t:
            if ch.isspace():
                if not prev_space:
                    seq.append((" ", c))
                    prev_space = True
            else:
                seq.append((ch, c))
                prev_space = False
    # 剥首尾空白
    while seq and seq[0][0] == " ":
        seq.pop(0)
    while seq and seq[-1][0] == " ":
        seq.pop()
    # 按宽度截断
    if display_width("".join(ch for ch, _ in seq)) > MAX_REMARK_WIDTH:
        seq = _truncate_chars(seq, MAX_REMARK_WIDTH)
    runs_hex = [(ch, MC_COLOR_SAFE.get(c)) for ch, c in seq]
    plain = "".join(ch for ch, _ in runs_hex)
    return plain, _coalesce(runs_hex)


def clean_motd(raw) -> str:
    """把服务端 MOTD 收成能进备注的一行（纯文本口径，与 motd_runs 的 plain 一致）。"""
    return motd_runs(raw)[0]


def remark_runs_of(entry: dict):
    """MOTD 备注当前生效时的彩色分段；否则 None（前端按纯文本渲染 remark_display）。"""
    if remark_src_of(entry) == REMARK_SRC_MOTD and cached_remark_motd(entry):
        runs = entry.get("remark_motd_runs")
        if isinstance(runs, list) and runs:
            return runs
    return None


def is_hidden(entry: dict) -> bool:
    """隐藏＝只从 /在线 里撤下去，条目本身照旧存在（绑定、Token、备注都不动）"""
    return bool(entry.get("hidden"))


def visible_servers(entries: list) -> list:
    """参与 /在线 采集与出图的服务器：隐藏的那些连查询都不发，省得白等一轮超时。"""
    return [e for e in entries if not is_hidden(e)]


# edit_server 的「这个字段本次不动」哨兵，和显式传入的 None/"" 区分开
_UNSET = object()


class ConfigManager:
    def __init__(self, group_id: Optional[int] = None, session_id: Optional[str] = None):
        if group_id is not None:
            self.scope_kind, self.scope_id = "group", _scope_text(group_id)
        elif session_id is not None:
            self.scope_kind, self.scope_id = "private", _scope_text(session_id)
        else:
            raise ValueError("必须提供 group_id 或 session_id")
        self.group_id = self.scope_id
        self._store = _ready_store()
        # 与旧行为一致：首次接触一个群时建立群配置，后台群列表才能看到它。
        self._store.ensure_group(self.scope_kind, self.scope_id, self._new_document())

    @property
    def config_file(self) -> str:
        return f"onebot/config.sqlite3#{self.scope_kind}={self.scope_id}"

    def _new_document(self) -> dict:
        return {
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "enabled": True,
            "alias": "",
            "servers": [],
            "policy": default_policy(),
            "nickname_ignore": [],
        }

    def _normalize(self, data) -> dict:
        data = data if isinstance(data, dict) else {}
        servers = data.get("servers")
        if not isinstance(servers, list):
            servers = []
        clean_servers = [dict(item) for item in servers if isinstance(item, dict)]
        return {
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "enabled": data.get("enabled") if type(data.get("enabled")) is bool else True,
            "alias": _normalize_alias(data.get("alias")),
            "servers": clean_servers,
            "policy": normalize_policy(data.get("policy")),
            "nickname_ignore": _normalize_ignore(data.get("nickname_ignore")),
        }

    def _load(self) -> dict:
        doc = self._store.get_group(self.scope_kind, self.scope_id)
        return self._normalize(doc if isinstance(doc, dict) else {})

    def _save(self, data: dict):
        self._store.save_group(self._normalize(data))

    def _mutate(self, operation):
        """在单个写事务里读取-修改-写回；operation 返回 (结果, 是否变化)。"""
        def inner(doc):
            normalized = self._normalize(doc)
            result, changed = operation(normalized)
            if changed:
                doc.clear()
                doc.update(normalized)
            return result, changed
        return self._store.mutate_group(self.scope_kind, self.scope_id, inner)

    def snapshot(self) -> dict:
        return self._load()

    def get_all_servers(self) -> List[Dict[str, str]]:
        return self._load().get("servers", [])

    # ---------- 服务器增删改 ----------

    def add_server(self, name: str, host: str, port: int = None) -> bool:
        def op(doc):
            if any(s.get("name") == name for s in doc["servers"]):
                return False, False
            entry = {"name": name, "host": host}
            if port is not None:
                entry["api_port"] = port
            doc["servers"].append(entry)
            return True, True
        return self._mutate(op)

    def remove_server(self, name: str) -> bool:
        def op(doc):
            old = doc["servers"]
            new = [s for s in old if s.get("name") != name]
            if len(new) == len(old):
                return False, False
            doc["servers"] = new
            return True, True
        return self._mutate(op)

    def edit_server_host(self, name: str, new_host: str) -> bool:
        def op(doc):
            for s in doc["servers"]:
                if s.get("name") == name:
                    s["host"] = new_host
                    return True, True
            return False, False
        return self._mutate(op)

    def edit_server_port(self, name: str, new_port: int) -> bool:
        def op(doc):
            for s in doc["servers"]:
                if s.get("name") == name:
                    s["api_port"] = new_port
                    return True, True
            return False, False
        return self._mutate(op)

    def edit_server_remark(self, name: str, remark: str) -> bool:
        def op(doc):
            for s in doc["servers"]:
                if s.get("name") == name:
                    s["remark"] = remark
                    return True, True
            return False, False
        return self._mutate(op)

    def set_server_hidden(self, name: str, hidden: bool) -> bool:
        def op(doc):
            for s in doc["servers"]:
                if s.get("name") != name:
                    continue
                if hidden:
                    s["hidden"] = True
                else:
                    s.pop("hidden", None)
                return True, True
            return False, False
        return self._mutate(op)

    def set_remark_src(self, name: str, src: str) -> bool:
        if src not in REMARK_SOURCES:
            return False

        def op(doc):
            for s in doc["servers"]:
                if s.get("name") != name:
                    continue
                if src == REMARK_SRC_MANUAL:
                    s.pop("remark_src", None)
                else:
                    s["remark_src"] = src
                return True, True
            return False, False
        return self._mutate(op)

    def set_remark_motd(self, name: str, text: str, captured_at: int, runs=None) -> bool:
        text = (text or "").strip()
        if not text:
            return False

        def op(doc):
            for s in doc["servers"]:
                if s.get("name") != name:
                    continue
                if s.get("remark_motd") == text and s.get("remark_motd_runs") == runs:
                    return False, False
                s["remark_motd"] = text
                s["remark_motd_at"] = captured_at
                if runs:
                    s["remark_motd_runs"] = runs
                else:
                    s.pop("remark_motd_runs", None)
                return True, True
            return False, False
        return self._mutate(op)

    def edit_server_console_token(self, name: str, token: str) -> bool:
        def op(doc):
            for s in doc["servers"]:
                if s.get("name") != name:
                    continue
                if token:
                    s["console_token"] = token
                else:
                    s.pop("console_token", None)
                return True, True
            return False, False
        return self._mutate(op)

    def rename_server(self, old_name: str, new_name: str) -> bool:
        def op(doc):
            if any(s.get("name") == new_name for s in doc["servers"]):
                return False, False
            for s in doc["servers"]:
                if s.get("name") == old_name:
                    s["name"] = new_name
                    return True, True
            return False, False
        return self._mutate(op)

    def edit_server(self, name: str, new_name=_UNSET, host=_UNSET,
                    api_port=_UNSET, remark=_UNSET, remark_src=_UNSET) -> Tuple[bool, str]:
        def op(doc):
            servers = doc["servers"]
            target = next((s for s in servers if s.get("name") == name), None)
            if target is None:
                return (False, "失败（名称不存在）"), False
            if new_name is not _UNSET and new_name != name:
                if any(s.get("name") == new_name for s in servers):
                    return (False, "失败（名称已存在）"), False
                target["name"] = new_name
            if host is not _UNSET:
                target["host"] = host
            if api_port is not _UNSET:
                if api_port is None:
                    target.pop("api_port", None)
                else:
                    target["api_port"] = api_port
            if remark is not _UNSET:
                if remark:
                    target["remark"] = remark
                else:
                    target.pop("remark", None)
            if remark_src is not _UNSET:
                if remark_src == REMARK_SRC_MANUAL:
                    target.pop("remark_src", None)
                elif remark_src in REMARK_SOURCES:
                    target["remark_src"] = remark_src
            return (True, ""), True
        return self._mutate(op)

    def batch_add(self, servers_str: str, host_validator=None, name_validator=None) -> Tuple[int, List[str]]:
        servers_str = servers_str.replace("：", ":").replace("，", ",")

        def op(doc):
            existing = {s.get("name") for s in doc["servers"]}
            success = 0
            failed = []
            for pair in servers_str.split(","):
                pair = pair.strip()
                if not pair:
                    continue
                parts = pair.split(":")
                if len(parts) < 2 or len(parts) > 3:
                    failed.append(f"{pair}（格式应为 名称:主机[:模组API端口]）")
                    continue
                name = parts[0].strip()
                host = parts[1].strip()
                port = None
                if len(parts) == 3:
                    port_str = parts[2].strip()
                    try:
                        port = int(port_str)
                        if port < 1 or port > 65535:
                            raise ValueError
                    except ValueError:
                        failed.append(f"{pair}（模组 API 端口无效）")
                        continue
                if not name or not host:
                    failed.append(f"{pair}（名称和主机都不能为空）")
                    continue
                if name_validator is not None:
                    name_err = name_validator(name)
                    if name_err:
                        failed.append(f"{name} ({name_err})")
                        continue
                if host_validator is not None:
                    host_err = host_validator(host)
                    if host_err:
                        failed.append(f"{name} ({host_err})")
                        continue
                if name in existing:
                    failed.append(f"{name} (已存在)")
                    continue
                if len(doc["servers"]) >= MAX_SERVERS_PER_GROUP:
                    failed.append(f"{name} (超出每群 {MAX_SERVERS_PER_GROUP} 个服务器上限)")
                    continue
                entry = {"name": name, "host": host}
                if port is not None:
                    entry["api_port"] = port
                doc["servers"].append(entry)
                existing.add(name)
                success += 1
            return (success, failed), success > 0
        return self._mutate(op)

    def batch_remove(self, names_str: str) -> Tuple[int, List[str]]:
        names_str = names_str.replace("，", ",")
        to_remove = [n.strip() for n in names_str.split(",") if n.strip()]

        def op(doc):
            original_names = {s.get("name") for s in doc["servers"]}
            new_servers = []
            removed = 0
            for s in doc["servers"]:
                if s.get("name") in to_remove:
                    removed += 1
                else:
                    new_servers.append(s)
            failed = [name for name in to_remove if name not in original_names]
            if removed:
                doc["servers"] = new_servers
            return (removed, failed), removed > 0
        return self._mutate(op)

    def move_server(self, name: str, new_index: int) -> bool:
        def op(doc):
            servers = doc.get("servers", [])
            current_index = next((i for i, s in enumerate(servers)
                                  if s.get("name") == name), None)
            if current_index is None or new_index < 0 or new_index >= len(servers):
                return False, False
            item = servers.pop(current_index)
            servers.insert(new_index, item)
            return True, current_index != new_index
        return self._mutate(op)

    def swap_servers(self, name1: str, name2: str) -> bool:
        def op(doc):
            servers = doc["servers"]
            idx1 = next((i for i, s in enumerate(servers) if s.get("name") == name1), None)
            idx2 = next((i for i, s in enumerate(servers) if s.get("name") == name2), None)
            if idx1 is None or idx2 is None:
                return False, False
            servers[idx1], servers[idx2] = servers[idx2], servers[idx1]
            return True, idx1 != idx2
        return self._mutate(op)

    # ---------- 群别名 ----------

    def get_alias(self) -> str:
        return str(self._load().get("alias") or "").strip()

    def set_alias(self, alias: str) -> Tuple[bool, str]:
        why = validate_group_alias(alias)
        if why:
            return False, why
        value = str(alias or "").strip()

        def op(doc):
            if value:
                doc["alias"] = value
            else:
                doc.pop("alias", None)
            return (True, ""), True
        return self._mutate(op)

    # ---------- 群策略 ----------

    def get_policy(self) -> dict:
        return self._load()["policy"]

    def get_flag(self, key: str, default: Optional[bool] = None) -> bool:
        if key not in POLICY_BOOL_KEYS:
            raise ValueError(f"不支持的群策略开关：{key}")
        fallback = POLICY_DEFAULTS[key] if default is None else default
        return bool(self.get_policy().get(key, fallback))

    def set_flag(self, key: str, enabled: bool):
        if key not in POLICY_BOOL_KEYS:
            raise ValueError(f"不支持的群策略开关：{key}")

        def op(doc):
            doc["policy"][key] = bool(enabled)
            return None, True
        self._mutate(op)

    def is_group_enabled(self) -> bool:
        return bool(self._load().get("enabled", True))

    def set_group_enabled(self, enabled: bool):
        def op(doc):
            doc["enabled"] = bool(enabled)
            return None, True
        self._mutate(op)

    def is_last_online_enabled(self) -> bool:
        return self.get_flag("show_last_online")

    def set_last_online_enabled(self, enabled: bool):
        self.set_flag("show_last_online", enabled)

    def is_nickname_check_enabled(self) -> bool:
        return self.get_flag("nickname_check_enabled")

    def set_nickname_check_enabled(self, enabled: bool):
        self.set_flag("nickname_check_enabled", enabled)

    def is_bind_require_nickname(self) -> bool:
        return self.get_flag("bind_require_nickname")

    def set_bind_require_nickname(self, enabled: bool):
        self.set_flag("bind_require_nickname", enabled)

    def get_bind_nickname_rule(self) -> str:
        return normalize_rule(self.get_policy().get("bind_nickname_rule"))

    def set_bind_nickname_rule(self, rule: str):
        def op(doc):
            doc["policy"]["bind_nickname_rule"] = normalize_rule(rule)
            return None, True
        self._mutate(op)

    def get_bind_nickname_template(self) -> str:
        return str(self.get_policy().get("bind_nickname_template") or "")

    def set_bind_nickname_template(self, template: str):
        def op(doc):
            doc["policy"]["bind_nickname_template"] = str(template or "")
            return None, True
        self._mutate(op)

    # ---------- 昵称检测忽略名单 ----------

    def get_nickname_ignore(self) -> list:
        return list(self._load()["nickname_ignore"])

    def set_nickname_ignore(self, ids: list):
        def op(doc):
            doc["nickname_ignore"] = _normalize_ignore(ids)
            return None, True
        self._mutate(op)
