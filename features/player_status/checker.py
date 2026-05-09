import asyncio
from mcstatus import JavaServer
from typing import List, Dict, Any

async def query_one(server_info: dict) -> Dict[str, Any]:
    host = server_info["host"]
    name = server_info["name"]
    try:
        server = await asyncio.to_thread(JavaServer.lookup, host)
        status = await asyncio.to_thread(server.status)
        players = []
        if status.players.sample:
            players = [p.name for p in status.players.sample]
        return {
            "name": name,
            "host": host,
            "online": status.players.online,
            "max": status.players.max,
            "players": players,
            "error": None
        }
    except Exception as e:
        return {
            "name": name,
            "host": host,
            "online": 0,
            "max": 0,
            "players": [],
            "error": str(e)
        }

async def query_all_servers(servers: List[dict]) -> List[Dict[str, Any]]:
    tasks = [query_one(s) for s in servers]
    return await asyncio.gather(*tasks)