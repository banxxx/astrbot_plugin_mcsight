import json
import os
from typing import List, Dict, Tuple, Optional

from ..utils.plugin_paths import sub_dir

# 每个群最多绑定的服务器数量（/mc add、/mc batchadd 时拦截）
MAX_SERVERS_PER_GROUP = 20

# 服务器名不能含方括号：/执行 用 [服名] 定界，名字里带括号会让解析错位
INVALID_NAME_CHARS = ("[", "]", "【", "】")


def validate_server_name(name: str) -> str:
    """返回拒绝原因；合法返回空串"""
    bad = [c for c in INVALID_NAME_CHARS if c in name]
    if bad:
        return "名称不能包含方括号（{}），/执行 要用 [服务器名] 定界".format(" ".join(bad))
    return ""


def group_config_path(group_id) -> str:
    """按群号拼出 servers_<群号>.json 的路径，只拼不建（WebUI 读取前要判存在性）"""
    return os.path.join(sub_dir('group_configs'), f'servers_{group_id}.json')


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
                    api_port=_UNSET, remark=_UNSET) -> Tuple[bool, str]:
        """一次写回一条服务器的名称/地址/模组端口/备注（WebUI 编辑弹窗用）。

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
        for pair in servers_str.split(","):
            pair = pair.strip()
            if not pair:
                continue
            parts = pair.split(":")
            if len(parts) < 2 or len(parts) > 3:
                failed.append(pair)
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
                    failed.append(f"{pair} (无效端口)")
                    continue
            if not name or not host:
                failed.append(pair)
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