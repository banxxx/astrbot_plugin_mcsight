import json
import os
from typing import List, Dict, Tuple, Optional

class ConfigManager:
    def __init__(self, group_id: Optional[int] = None, session_id: Optional[str] = None, base_path: str = None):
        if base_path is None:
            base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
        self.config_dir = os.path.join(base_path, 'group_configs')
        os.makedirs(self.config_dir, exist_ok=True)
        # 优先使用 group_id，如果为 None 则使用 session_id（私聊场景）
        if group_id is not None:
            self.config_file = os.path.join(self.config_dir, f'servers_{group_id}.json')
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

    # ---------- 修改：batch_add 支持端口 ----------
    def batch_add(self, servers_str: str) -> Tuple[int, List[str]]:
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
            if name in existing:
                failed.append(f"{name} (已存在)")
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