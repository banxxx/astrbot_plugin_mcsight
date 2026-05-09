import json
import os
from astrbot.api import logger

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "servers.json")

def load_servers() -> dict:
    """加载服务器列表，如果文件不存在则创建默认空列表"""
    if not os.path.exists(CONFIG_FILE):
        default = {"servers": []}
        save_servers(default)
        return default
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取服务器配置失败: {e}")
        return {"servers": []}

def save_servers(data: dict):
    """保存服务器配置"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存服务器配置失败: {e}")

def add_server(name: str, host: str) -> bool:
    """添加一个服务器，如果同名已存在则返回 False"""
    data = load_servers()
    for s in data["servers"]:
        if s["name"] == name:
            return False
    data["servers"].append({"name": name, "host": host})
    save_servers(data)
    return True

def remove_server(name: str) -> bool:
    """删除指定名称的服务器，返回是否成功"""
    data = load_servers()
    original_len = len(data["servers"])
    data["servers"] = [s for s in data["servers"] if s["name"] != name]
    if len(data["servers"]) == original_len:
        return False
    save_servers(data)
    return True

def edit_server(name: str, new_host: str) -> bool:
    """修改指定服务器的 IP 地址，返回是否成功"""
    data = load_servers()
    for s in data["servers"]:
        if s["name"] == name:
            s["host"] = new_host
            save_servers(data)
            return True
    return False

def batch_add(servers_str: str) -> tuple[int, list]:
    """批量添加，格式: name1:ip1,name2:ip2,... 返回(成功数, 失败列表)"""
    data = load_servers()
    existing_names = {s["name"] for s in data["servers"]}
    success = 0
    failed = []
    pairs = servers_str.split(",")
    for pair in pairs:
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
        if name in existing_names:
            failed.append(f"{name} (已存在)")
            continue
        data["servers"].append({"name": name, "host": host})
        existing_names.add(name)
        success += 1
    save_servers(data)
    return success, failed

def batch_remove(names_str: str) -> tuple[int, list]:
    """批量删除，格式: name1,name2,... 返回(成功数, 失败列表)"""
    data = load_servers()
    to_remove = [n.strip() for n in names_str.split(",") if n.strip()]
    success = 0
    failed = []
    new_list = []
    for s in data["servers"]:
        if s["name"] in to_remove:
            success += 1
        else:
            new_list.append(s)
    data["servers"] = new_list
    save_servers(data)
    # 检查哪些名字不存在
    existing_names = {s["name"] for s in new_list}
    for name in to_remove:
        if name not in existing_names:
            failed.append(name)
            success -= 1  # 因为之前假定成功
    return success, failed

def get_all_servers() -> list:
    """返回服务器列表"""
    return load_servers()["servers"]