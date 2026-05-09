import json
import os
from typing import List, Dict, Tuple

class ConfigManager:
    def __init__(self, session_id: str, base_path: str = None):
        if base_path is None:
            base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
        self.config_dir = os.path.join(base_path, 'group_configs')
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_file = os.path.join(self.config_dir, f'servers_{session_id}.json')
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

    def add_server(self, name: str, host: str) -> bool:
        data = self._load()
        if any(s["name"] == name for s in data["servers"]):
            return False
        data["servers"].append({"name": name, "host": host})
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

    def batch_add(self, servers_str: str) -> Tuple[int, List[str]]:
        data = self._load()
        existing = {s["name"] for s in data["servers"]}
        success = 0
        failed = []
        for pair in servers_str.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                failed.append(pair)
                continue
            name, host = pair.split(":", 1)
            name, host = name.strip(), host.strip()
            if not name or not host:
                failed.append(pair)
                continue
            if name in existing:
                failed.append(f"{name} (已存在)")
                continue
            data["servers"].append({"name": name, "host": host})
            existing.add(name)
            success += 1
        self._save(data)
        return success, failed

    def batch_remove(self, names_str: str) -> Tuple[int, List[str]]:
        data = self._load()
        to_remove = [n.strip() for n in names_str.split(",") if n.strip()]
        removed = 0
        failed = []
        new_servers = []
        for s in data["servers"]:
            if s["name"] in to_remove:
                removed += 1
            else:
                new_servers.append(s)
        found_names = {s["name"] for s in new_servers}
        for name in to_remove:
            if name not in found_names and name not in failed:
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