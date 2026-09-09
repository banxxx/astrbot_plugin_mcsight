import asyncio
import aiohttp
import json
import ssl
import os
import pymysql
from dbutils.pooled_db import PooledDB
from astrbot.api import logger
from ..config.whitelist_config import WhitelistManager

_pool = None

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

def init_db_pool():
    """初始化同步数据库连接池"""
    global _pool
    if _pool is None:
        wm = WhitelistManager()
        if not wm.db_host or not wm.db_user or not wm.db_password:
            raise Exception("数据库配置不完整，请检查 db_host, db_user, db_password")

        # 证书路径
        ca_cert_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "isrgrootx1.pem")
        if not os.path.exists(ca_cert_path):
            ca_cert_path = "isrgrootx1.pem"
            if not os.path.exists(ca_cert_path):
                if os.path.exists("/etc/ssl/cert.pem"):
                    ca_cert_path = "/etc/ssl/cert.pem"
                elif os.path.exists("/etc/ssl/certs/ca-certificates.crt"):
                    ca_cert_path = "/etc/ssl/certs/ca-certificates.crt"
                else:
                    raise FileNotFoundError(f"CA 证书文件未找到: {ca_cert_path}")

        # 创建 SSL 上下文（与测试脚本一致）
        ssl_context = ssl.create_default_context(cafile=ca_cert_path)
        ssl_context.check_hostname = True

        # 创建连接池
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=10,
            mincached=2,
            maxcached=10,
            blocking=True,
            host=wm.db_host,
            port=wm.db_port,
            user=wm.db_user,
            password=wm.db_password,
            database=wm.db_name,
            charset='utf8mb4',
            autocommit=True,
            connect_timeout=10,
            ssl=ssl_context
        )
        logger.info(f"同步数据库连接池已初始化，连接至 {wm.db_host}:{wm.db_port}/{wm.db_name}")
    return _pool


def execute_sync_update(sql, params):
    """同步执行更新操作（INSERT / DELETE）"""
    pool = init_db_pool()
    conn = pool.connection()
    try:
        cursor = conn.cursor()
        rows = cursor.execute(sql, params)
        return rows
    finally:
        cursor.close()
        conn.close()


def execute_sync_query(sql, params, fetch_one=False, fetch_all=False):
    """同步执行查询操作"""
    pool = init_db_pool()
    conn = pool.connection()
    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(sql, params)
        if fetch_one:
            return cursor.fetchone()
        elif fetch_all:
            return cursor.fetchall()
        return None
    finally:
        cursor.close()
        conn.close()


async def execute_db_update(sql, params):
    """异步包装同步更新（供 bind/unbind 调用）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, execute_sync_update, sql, params)


async def execute_db_query(sql, params, fetch_one=False, fetch_all=False):
    """异步包装同步查询（供 bind/unbind 调用）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, execute_sync_query, sql, params, fetch_one, fetch_all)