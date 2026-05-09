import json
import os
from typing import List, Dict, Tuple

CONFIG_FILE = os.path.join(os.path.dirname(__file__), '..', 'servers.json')

def _load() -> dict:
    if not os.path.exists(CONFIG_FILE):
        _save({"servers": []})
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _save(data: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_all_servers() -> List[Dict[str, str]]:
    return _load().get("servers", [])

def add_server(name: str, host: str) -> bool:
    data = _load()
    if any(s["name"] == name for s in data["servers"]):
        return False
    data["servers"].append({"name": name, "host": host})
    _save(data)
    return True

def remove_server(name: str) -> bool:
    data = _load()
    new_servers = [s for s in data["servers"] if s["name"] != name]
    if len(new_servers) == len(data["servers"]):
        return False
    data["servers"] = new_servers
    _save(data)
    return True

def edit_server(name: str, new_host: str) -> bool:
    data = _load()
    for s in data["servers"]:
        if s["name"] == name:
            s["host"] = new_host
            _save(data)
            return True
    return False

def batch_add(servers_str: str) -> Tuple[int, List[str]]:
    data = _load()
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
    _save(data)
    return success, failed

def batch_remove(names_str: str) -> Tuple[int, List[str]]:
    data = _load()
    to_remove = [n.strip() for n in names_str.split(",") if n.strip()]
    removed = 0
    failed = []
    new_servers = []
    for s in data["servers"]:
        if s["name"] in to_remove:
            removed += 1
        else:
            new_servers.append(s)
    # 找出未匹配的名字
    found_names = {s["name"] for s in new_servers}
    for name in to_remove:
        if name not in found_names and name not in failed:
            failed.append(name)
    data["servers"] = new_servers
    _save(data)
    return removed, failed