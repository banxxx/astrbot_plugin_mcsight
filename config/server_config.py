import json
import os
import re
import unicodedata
from typing import List, Dict, Optional, Tuple

from ..utils.plugin_paths import sub_dir

# 每个群最多绑定的服务器数量（/mc add、/mc batchadd 时拦截）
MAX_SERVERS_PER_GROUP = 20

# 服务器名不能含方括号：/执行 用 [服名] 定界，名字里带括号会让解析错位
INVALID_NAME_CHARS = ("[", "]", "【", "】")

# 长度上限按显示宽度算：汉字与全角标点记 2，所以 20 字符＝10 个汉字
MAX_NAME_WIDTH = 20
MAX_REMARK_WIDTH = 50

# 备注取值来源：手填（默认）/ 服务端 MOTD
REMARK_SRC_MANUAL = "manual"
REMARK_SRC_MOTD = "motd"
REMARK_SOURCES = (REMARK_SRC_MANUAL, REMARK_SRC_MOTD)

# §色码：卡片字体不渲染它，留在备注里是一串乱码
MOTD_COLOR_CODE = re.compile(r"\u00a7[0-9a-zA-Z]")


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


def group_config_path(group_id) -> str:
    """按群号拼出 servers_<群号>.json 的路径，只拼不建（WebUI 读取前要判存在性）"""
    return os.path.join(sub_dir('group_configs'), f'servers_{group_id}.json')


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
    """MOTD 写成 JSON 文本组件时（部分模组/插件会这样）把所有 text/extra 拼成一行；解析不了返回 None"""
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
    # 文本组件在游戏里是直接拼接的，不能按段落补空格：
    # {"text":"A","extra":[{"text":"B"}]} 应显示 AB，而不是 A B
    return " ".join("".join(parts).split())


def clean_motd(raw) -> str:
    """把服务端 MOTD 收成能进备注的一行：展平 JSON、剥 §色码、换行折成空格、按备注宽度上限截断。"""
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else str(raw)
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        flattened = _flatten_json_text(text)
        if flattened is not None:
            text = flattened
    text = MOTD_COLOR_CODE.sub("", text).replace("\\n", " ")
    return truncate_by_width(" ".join(text.split()), MAX_REMARK_WIDTH)


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
        self.config_dir = sub_dir('group_configs')
        # 优先使用 group_id，如果为 None 则使用 session_id（私聊场景）
        if group_id is not None:
            self.config_file = group_config_path(group_id)
        elif session_id is not None:
            self.config_file = os.path.join(self.config_dir, f'servers_{session_id}.json')
        else:
            raise ValueError("必须提供 group_id 或 session_id")

        if not os.path.exists(self.config_file):
            self._save({"servers": []})

    def _load(self) -> dict:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"servers": []}

    def _save(self, data: dict):
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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

    def set_remark_motd(self, name: str, text: str, captured_at: int) -> bool:
        """写回采集到的 MOTD。/在线 每次都会走到这里，值没变就不落盘，免得配置文件白挨写。

        时间戳跟着值一起更新，所以面板上那个时间是「这份备注什么时候采到的」，
        不是「最近一次查询的时间」——MOTD 不变时它就该停在那一刻。
        """
        text = (text or "").strip()
        if not text:
            return False
        data = self._load()
        for s in data["servers"]:
            if s["name"] != name:
                continue
            if s.get("remark_motd") == text:
                return False
            s["remark_motd"] = text
            s["remark_motd_at"] = captured_at
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

    # ---------- 上次在线显示开关 ----------
    def is_last_online_enabled(self) -> bool:
        """当前群组是否开启‘上次在线’显示"""
        return bool(self._load().get("last_online_enabled", False))

    def set_last_online_enabled(self, enabled: bool):
        """设置当前群组的‘上次在线’显示开关"""
        data = self._load()
        data["last_online_enabled"] = enabled
        self._save(data)

    # ---------- 昵称检测开关 ----------
    def is_nickname_check_enabled(self) -> bool:
        """当前群组是否开启‘昵称检测’功能"""
        return bool(self._load().get("nickname_check_enabled", False))

    def set_nickname_check_enabled(self, enabled: bool):
        """设置当前群组的‘昵称检测’功能开关"""
        data = self._load()
        data["nickname_check_enabled"] = enabled
        self._save(data)

    # ---------- 昵称检测：忽略的 QQ 列表 ----------
    def get_nickname_check_ignore_ids(self) -> list:
        """获取昵称检测时需要忽略的 QQ 号列表（字符串）"""
        data = self._load()
        lst = data.get("nickname_check_ignore_ids", [])
        if not isinstance(lst, list):
            return []
        return [str(x).strip() for x in lst if str(x).strip()]

    def set_nickname_check_ignore_ids(self, ids: list):
        """设置昵称检测时需要忽略的 QQ 号列表"""
        data = self._load()
        data["nickname_check_ignore_ids"] = [str(x).strip() for x in ids if str(x).strip()]
        self._save(data)