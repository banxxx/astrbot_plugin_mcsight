import asyncio
import aiohttp

async def call_mod_api(host: str, port: int, token: str, endpoint: str, method: str = "POST", data: dict = None):
    """
    调用模组 HTTP API
    :return: (是否成功, 响应内容)
             成功时返回 (True, 完整JSON响应字典)
             失败时返回 (False, 错误消息字符串)
    """
    if not host or host == "self":
        return False, "无效的服务器地址"
    if ":" in host:
        ip, _ = host.split(":", 1)
    else:
        ip = host
    url = f"http://{ip}:{port}{endpoint}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession() as session:
            if method.upper() == "POST":
                async with session.post(url, json=data, headers=headers, timeout=5.0) as resp:
                    result = await resp.json()
                    if resp.status == 200 and result.get("success") is True:
                        return True, result
                    else:
                        return False, result.get("message", f"API 返回错误 (HTTP {resp.status})")
            elif method.upper() == "GET":
                async with session.get(url, params=data, headers=headers, timeout=5.0) as resp:
                    result = await resp.json()
                    if resp.status == 200 and result.get("success") is True:
                        return True, result
                    else:
                        return False, result.get("message", f"请求失败 (HTTP {resp.status})")
    except aiohttp.ClientError as e:
        return False, f"连接失败: {e}"
    except asyncio.TimeoutError:
        return False, "请求超时，请检查服务器是否在线"
    except Exception as e:
        return False, f"未知错误: {e}"