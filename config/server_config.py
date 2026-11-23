import json
import os
import re
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


def has_group_config(group_id) -> bool:
    return os.path.exists(group_config_path(group_id)) or os.path.exists(legacy_group_config_path(group_id))


def delete_group_config(group_id) -> bool:
    """删除新群文件和旧版文件，并尝试移除空的群目录。"""
    removed = False
    for path in (group_config_path(group_id), legacy_group_config_path(group_id)):
        if os.path.exists(path):
            os.remove(path)
            removed = True
    try:
        os.rmdir(group_dir(group_id))
    except OSError:
        pass
    return removed


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


def migrate_group_file(group_id) -> bool:
    """旧文件存在且新文件不存在时，迁移一份到 onebot/<群号>/state.json。

    旧文件保留不动，方便升级后核对；插件运行时只读新文件。
    """
    new_path = group_config_path(group_id)
    if os.path.exists(new_path):
        return False
    old_path = legacy_group_config_path(group_id)
    if not os.path.exists(old_path):
        return False
    with open(old_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _write_document(new_path, _legacy_document(data, group_id))
    return True


def migrate_legacy_group_configs() -> tuple:
    """启动时扫描旧 group_configs/servers_*.json；返回 (迁移数, 失败数)。"""
    base = sub_dir(LEGACY_GROUP_DIR)
    migrated = failed = 0
    try:
        names = os.listdir(base)
    except OSError:
        return 0, 0
    for name in names:
        if not (name.startswith("servers_") and name.endswith(".json")):
            continue
        group_id = name[len("servers_"):-len(".json")]
        try:
            if migrate_group_file(group_id):
                migrated += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            failed += 1
    return migrated, failed


def read_group_flag(group_id, key: str, default: Optional[bool] = None) -> bool:
    """只读本群 policy 开关；文件不存在或损坏时返回默认值，不在读路径上创建文件。

    群规则只从本群配置读取，不再回落到全局插件配置。
    """
    try:
        with open(group_config_path(group_id), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return bool(POLICY_DEFAULTS.get(key, default) if default is None else default)
    if not isinstance(data, dict):
        return bool(POLICY_DEFAULTS.get(key, default) if default is None else default)
    policy = data.get("policy")
    if not isinstance(policy, dict):
        return bool(POLICY_DEFAULTS.get(key, default) if default is None else default)
    fallback = POLICY_DEFAULTS.get(key, default) if default is None else default
    return bool(policy.get(key, fallback))


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
        # 优先使用 group_id，如果为 None 则使用 session_id（私聊场景）
        if group_id is not None:
            self.group_id = str(group_id)
        elif session_id is not None:
            self.group_id = str(session_id)
        else:
            raise ValueError("必须提供 group_id 或 session_id")
        self.config_file = group_config_path(self.group_id)

        if not os.path.exists(self.config_file):
            try:
                migrated = migrate_group_file(self.group_id)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                migrated = False
            if not migrated:
                self._save(self._new_document())

    def _new_document(self) -> dict:
        return {
            "schema_version": GROUP_FILE_SCHEMA_VERSION,
            "platform": "onebot",
            "group_id": self.group_id,
            "enabled": True,
            "alias": "",
            "servers": [],
            "policy": default_policy(),
            "nickname_ignore": [],
        }

    def _normalize(self, data) -> dict:
        """收敛成标准群文件；旧版扁平开关只由 migrate_group_file 一次性迁移。"""
        data = data if isinstance(data, dict) else {}
        servers = data.get("servers")
        enabled = data.get("enabled", True)
        return {
            "schema_version": GROUP_FILE_SCHEMA_VERSION,
            "platform": "onebot",
            "group_id": self.group_id,
            "enabled": enabled if type(enabled) is bool else True,
            "alias": _normalize_alias(data.get("alias")),
            "servers": servers if isinstance(servers, list) else [],
            "policy": normalize_policy(data.get("policy")),
            "nickname_ignore": _normalize_ignore(data.get("nickname_ignore")),
        }

    def _load(self) -> dict:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return self._normalize(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return self._normalize({})

    def _save(self, data: dict):
        _write_document(self.config_file, self._normalize(data))

    def get_all_servers(self) -> List[Dict[str, str]]:
        return self._load().get("servers", [])

    # ---------- 修改：add_server 增加 port 参数 ----------
    def add_server(self, name: str, host: str, port: int = None) -> bool:
        data = self._load()
        if any(s["name"] == name for s in data["servers"]):
            return False
        entry = {"name": name, "host": host}
        if port is not None:
            entry["api_port"] = port
        data["servers"].append(entry)
        self._save(data)
        return True

    def remove_server(self, name: str) -> bool:
        data = self._load()
        new_servers = [s for s in data["servers"] if s["name"] != name]
        if len(new_servers) == len(data["servers"]):
            return False
        data["servers"] = new_servers
        self._save(data)
        return True

    def edit_server_host(self, name: str, new_host: str) -> bool:
        data = self._load()
        for s in data["servers"]:
            if s["name"] == name:
                s["host"] = new_host
                self._save(data)
                return True
        return False

    # ---------- 新增：编辑服务器端口 ----------
    def edit_server_port(self, name: str, new_port: int) -> bool:
        data = self._load()
        for s in data["servers"]:
            if s["name"] == name:
                s["api_port"] = new_port
                self._save(data)
                return True
        return False

    # ---------- 新增：编辑服务器备注（/在线 卡片名字下方一行） ----------
    def edit_server_remark(self, name: str, remark: str) -> bool:
        data = self._load()
        for s in data["servers"]:
            if s["name"] == name:
                s["remark"] = remark
                self._save(data)
                return True
        return False

    # ---------- 隐藏条目：只影响 /在线 是否采集与出图，不删任何东西 ----------
    def set_server_hidden(self, name: str, hidden: bool) -> bool:
        """hidden=False 时直接删键：老文件里没有 hidden 就是显示，别留一堆 false。"""
        data = self._load()
        for s in data["servers"]:
            if s["name"] != name:
                continue
            if hidden:
                s["hidden"] = True
            else:
                s.pop("hidden", None)
            self._save(data)
            return True
        return False

    # ---------- 备注来源开关：只改取值口径，手填值与 MOTD 缓存各自留着 ----------
    def set_remark_src(self, name: str, src: str) -> bool:
        """src 传 manual|motd；回 manual 时不删 MOTD 缓存，来回切不丢数据。"""
        if src not in REMARK_SOURCES:
            return False
        data = self._load()
        for s in data["servers"]:
            if s["name"] == name:
                if src == REMARK_SRC_MANUAL:
                    s.pop("remark_src", None)
                else:
                    s["remark_src"] = src
                self._save(data)
                return True
        return False

    def set_remark_motd(self, name: str, text: str, captured_at: int, runs=None) -> bool:
        """写回采集到的 MOTD。/在线 每次都会走到这里，值没变就不落盘，免得配置文件白挨写。

        时间戳跟着值一起更新，所以面板上那个时间是「这份备注什么时候采到的」，
        不是「最近一次查询的时间」——MOTD 不变时它就该停在那一刻。
        runs 是 motd_runs() 出的彩色分段，与 text 一起落盘（无颜色时传 None/空）。
        """
        text = (text or "").strip()
        if not text:
            return False
        data = self._load()
        for s in data["servers"]:
            if s["name"] != name:
                continue
            if s.get("remark_motd") == text and s.get("remark_motd_runs") == runs:
                return False
            s["remark_motd"] = text
            s["remark_motd_at"] = captured_at
            if runs:
                s["remark_motd_runs"] = runs
            else:
                s.pop("remark_motd_runs", None)
            self._save(data)
            return True
        return False

    # ---------- 新增：按服设置命令 Token；token 传空表示清除 ----------
    def edit_server_console_token(self, name: str, token: str) -> bool:
        data = self._load()
        for s in data["servers"]:
            if s["name"] == name:
                if token:
                    s["console_token"] = token
                else:
                    s.pop("console_token", None)
                self._save(data)
                return True
        return False

    def rename_server(self, old_name: str, new_name: str) -> bool:
        data = self._load()
        servers = data["servers"]
        if any(s["name"] == new_name for s in servers):
            return False
        for s in servers:
            if s["name"] == old_name:
                s["name"] = new_name
                self._save(data)
                return True
        return False

    def edit_server(self, name: str, new_name=_UNSET, host=_UNSET,
                    api_port=_UNSET, remark=_UNSET, remark_src=_UNSET) -> Tuple[bool, str]:
        """一次写回一条服务器的名称/地址/模组端口/备注/备注来源（WebUI 编辑弹窗用）。

        api_port=None 表示删除该字段（该服退回「未装模组」），remark="" 表示清除备注；
        未传的字段保持原样。返回 (是否成功, 面向用户的失败原因)。
        """
        data = self._load()
        servers = data["servers"]
        target = next((s for s in servers if s["name"] == name), None)
        if target is None:
            return False, "失败（名称不存在）"
        if new_name is not _UNSET and new_name != name:
            if any(s["name"] == new_name for s in servers):
                return False, "失败（名称已存在）"
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
        self._save(data)
        return True, ""

    # ---------- 修改：batch_add 支持端口 ----------
    def batch_add(self, servers_str: str, host_validator=None, name_validator=None) -> Tuple[int, List[str]]:
        """host_validator: 可选回调 (host) -> 拒绝原因或 None，非法 host 记入失败列表
        name_validator: 同上，校验服务器名"""
        data = self._load()
        existing = {s["name"] for s in data["servers"]}
        success = 0
        failed = []
        servers_str = servers_str.replace("：", ":").replace("，", ",")
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
            if len(data["servers"]) >= MAX_SERVERS_PER_GROUP:
                failed.append(f"{name} (超出每群 {MAX_SERVERS_PER_GROUP} 个服务器上限)")
                continue
            entry = {"name": name, "host": host}
            if port is not None:
                entry["api_port"] = port
            data["servers"].append(entry)
            existing.add(name)
            success += 1
        self._save(data)
        return success, failed

    def batch_remove(self, names_str: str) -> Tuple[int, List[str]]:
        data = self._load()
        names_str = names_str.replace("，", ",")
        to_remove = [n.strip() for n in names_str.split(",") if n.strip()]
        # 记录删除前的所有服务器名称，用于判断哪些名称真正不存在
        original_names = {s["name"] for s in data["servers"]}
        removed = 0
        failed = []
        new_servers = []
        for s in data["servers"]:
            if s["name"] in to_remove:
                removed += 1
            else:
                new_servers.append(s)
        # 只有原始列表中不存在的名称才算失败
        for name in to_remove:
            if name not in original_names:
                failed.append(name)
        data["servers"] = new_servers
        self._save(data)
        return removed, failed

    def move_server(self, name: str, new_index: int) -> bool:
        data = self._load()
        servers = data.get("servers", [])
        current_index = None
        for i, s in enumerate(servers):
            if s["name"] == name:
                current_index = i
                break
        if current_index is None:
            return False
        if new_index < 0 or new_index >= len(servers):
            return False
        item = servers.pop(current_index)
        servers.insert(new_index, item)
        self._save(data)
        return True

    def swap_servers(self, name1: str, name2: str) -> bool:
        data = self._load()
        servers = data["servers"]
        idx1, idx2 = None, None
        for i, s in enumerate(servers):
            if s["name"] == name1:
                idx1 = i
            elif s["name"] == name2:
                idx2 = i
        if idx1 is None or idx2 is None:
            return False
        servers[idx1], servers[idx2] = servers[idx2], servers[idx1]
        self._save(data)
        return True

    # ---------- 群别名（选填；用于「当前群」下拉里区分同名/纯数字群号） ----------
    def get_alias(self) -> str:
        """返回本群别名；未设置时为空串。"""
        return str(self._load().get("alias") or "").strip()

    def set_alias(self, alias: str) -> Tuple[bool, str]:
        """写入本群别名；空串表示清除。返回 (是否成功, 面向用户的失败原因)。"""
        why = validate_group_alias(alias)
        if why:
            return False, why
        data = self._load()
        value = str(alias or "").strip()
        if value:
            data["alias"] = value
        else:
            data.pop("alias", None)
        self._save(data)
        return True, ""

    # ---------- 群策略（统一放在 policy 下） ----------
    def get_policy(self) -> dict:
        """返回完整的本群策略；所有群规则都从这里读写。"""
        return self._load()["policy"]

    def get_flag(self, key: str, default: Optional[bool] = None) -> bool:
        """读取本群 policy 布尔开关；不存在的键按共享默认值处理。"""
        if key not in POLICY_BOOL_KEYS:
            raise ValueError(f"不支持的群策略开关：{key}")
        fallback = POLICY_DEFAULTS[key] if default is None else default
        return bool(self.get_policy().get(key, fallback))

    def set_flag(self, key: str, enabled: bool):
        """写入本群 policy 布尔开关。"""
        if key not in POLICY_BOOL_KEYS:
            raise ValueError(f"不支持的群策略开关：{key}")
        data = self._load()
        data["policy"][key] = bool(enabled)
        self._save(data)

    # ---------- 本群总开关 ----------
    def is_group_enabled(self) -> bool:
        """本群功能总开关；缺省为开启。"""
        return bool(self._load().get("enabled", True))

    def set_group_enabled(self, enabled: bool):
        """设置本群功能总开关；关闭后所有 OneBot 命令不再响应。"""
        data = self._load()
        data["enabled"] = bool(enabled)
        self._save(data)

    def is_last_online_enabled(self) -> bool:
        return self.get_flag("show_last_online")

    def set_last_online_enabled(self, enabled: bool):
        self.set_flag("show_last_online", enabled)

    # ---------- 昵称检测开关 ----------
    def is_nickname_check_enabled(self) -> bool:
        return self.get_flag("nickname_check_enabled")

    def set_nickname_check_enabled(self, enabled: bool):
        self.set_flag("nickname_check_enabled", enabled)

    # ---------- 绑定是否强制校验群名片 ----------
    def is_bind_require_nickname(self) -> bool:
        return self.get_flag("bind_require_nickname")

    def set_bind_require_nickname(self, enabled: bool):
        self.set_flag("bind_require_nickname", enabled)

    # ---------- 绑定群名片的匹配规则 ----------
    def get_bind_nickname_rule(self) -> str:
        """当前群的匹配方式；缺失或非法一律回落到 paren"""
        return normalize_rule(self.get_policy().get("bind_nickname_rule"))

    def set_bind_nickname_rule(self, rule: str):
        """设置当前群的匹配方式"""
        data = self._load()
        data["policy"]["bind_nickname_rule"] = normalize_rule(rule)
        self._save(data)

    def get_bind_nickname_template(self) -> str:
        """自定义模板（仅 template 模式使用）"""
        return str(self.get_policy().get("bind_nickname_template") or "")

    def set_bind_nickname_template(self, template: str):
        """设置自定义模板"""
        data = self._load()
        data["policy"]["bind_nickname_template"] = str(template or "")
        self._save(data)

    # ---------- 昵称检测：忽略名单 ----------
    def get_nickname_ignore(self) -> list:
        """昵称检测时需要忽略的 QQ 号列表（字符串）。"""
        return list(self._load()["nickname_ignore"])

    def set_nickname_ignore(self, ids: list):
        """设置昵称检测时需要忽略的 QQ 号列表。"""
        data = self._load()
        data["nickname_ignore"] = _normalize_ignore(ids)
        self._save(data)