import asyncio
import json
import ssl
import os
import threading
import pymysql
from dbutils.pooled_db import PooledDB
from astrbot.api import logger
from ..config.whitelist_config import WhitelistManager
from ..utils import mod_http

_pool = None
_keepalive = {"thread": None, "stop": None}

# 单条 SQL 的读写预算（秒）。中心库在境外，链路一旦进入半死状态，语句会挂在
# TCP 重传上且没有上界（实测出现过 22 秒），这里给它一个封顶。取值与模组 HTTP
# 层的 TOTAL_TIMEOUT 对齐，两个外部依赖的失败节奏保持一致。
DB_SQL_TIMEOUT = 5.0
# 连接保活间隔。实测跨境链路在 120~240 秒空闲后开始退化，取更小的值留余量。
DB_KEEPALIVE_INTERVAL = 30.0
# 保活失败后的退避阶梯，用尽后停在最后一档持续重试
DB_KEEPALIVE_BACKOFF = (10.0, 30.0, 120.0)

async def call_mod_api(host: str, port: int, token: str, endpoint: str, method: str = "POST", data: dict = None):
    """
    调用模组 HTTP API（实现见 utils/mod_http，全局共用一个连接池）
    :return: (是否成功, 响应内容)
             成功时返回 (True, 完整JSON响应字典)
             失败时返回 (False, 面向用户的错误消息字符串)
    """
    return await mod_http.request(host, port, endpoint, method=method, data=data, token=token)

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
            read_timeout=DB_SQL_TIMEOUT,
            write_timeout=DB_SQL_TIMEOUT,
            ssl=ssl_context
        )
        logger.info(f"同步数据库连接池已初始化，连接至 {wm.db_host}:{wm.db_port}/{wm.db_name}")
    return _pool


def _ping_pool():
    """借出一条连接做一次最轻的查询，既验证可用也让链路保持活跃"""
    pool = init_db_pool()
    conn = pool.connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
        finally:
            cursor.close()
    finally:
        conn.close()


def _keepalive_loop(stop_event):
    # 建池要串行做两次跨境全握手（实测 4 秒以上），必须放在线程里；
    # 放在插件 __init__ 会把整个事件循环卡住
    step = 0
    while not stop_event.is_set():
        try:
            _ping_pool()
            step = 0
            wait = DB_KEEPALIVE_INTERVAL
        except Exception as e:
            # 数据库配置未填时不刷屏，直接退出保活
            if "数据库配置不完整" in str(e) or isinstance(e, FileNotFoundError):
                logger.info(f"[db] 未配置数据库或证书缺失，保活线程退出: {e}")
                return
            wait = DB_KEEPALIVE_BACKOFF[min(step, len(DB_KEEPALIVE_BACKOFF) - 1)]
            step += 1
            logger.warning(f"[db] 连接保活失败，{wait:.0f} 秒后重试: {e}")
        stop_event.wait(wait)


def start_db_keepalive():
    """启动预热+保活守护线程（幂等）"""
    wm = WhitelistManager()
    if not (wm.db_host and wm.db_user and wm.db_password):
        return
    if _keepalive["thread"] is not None and _keepalive["thread"].is_alive():
        return
    stop_event = threading.Event()
    thread = threading.Thread(target=_keepalive_loop, args=(stop_event,),
                              name="MCWatcher-DbKeepAlive", daemon=True)
    _keepalive["stop"] = stop_event
    _keepalive["thread"] = thread
    thread.start()
    logger.info("[db] 连接池预热与保活已启动")


def stop_db_keepalive():
    stop_event = _keepalive["stop"]
    if stop_event is not None:
        stop_event.set()


def execute_sync_update(sql, params):
    """同步执行更新操作（INSERT / DELETE）"""
    pool = init_db_pool()
    conn = pool.connection()
    try:
        cursor = conn.cursor()
        try:
            # 写操作关掉 DBUtils 的透明重试：超时时语句可能已经到达服务端，
            # 重放会撞上唯一键，把成功的绑定误报成「已被绑定」
            with cursor.no_failover():
                rows = cursor.execute(sql, params)
            return rows
        finally:
            cursor.close()
    finally:
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