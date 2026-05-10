import os
import time
import asyncio
import logging
from collections import OrderedDict
from typing import Optional

import aiohttp
from PIL import Image

logger = logging.getLogger("astrbot_plugin_mcwatcher")

# ================= 配置常量 =================
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'avatar_cache')
MAX_MEMORY_CACHE = 200          # 内存最多缓存 200 个头像
DISK_CACHE_EXPIRE_DAYS = 7      # 磁盘缓存过期天数
# 下载参数
DOWNLOAD_TIMEOUT = 5.0          # 单个头像下载超时（秒）
MAX_RETRIES = 1                 # 失败不重试，直接使用默认头像（可以改为 1 重试一次）
# 头像 API 源（国内加速）
AVATAR_API_BASE = "https://cravatar.cn/helm"

# ================= 内存缓存（LRU） =================
class LRUAvatarCache:
    def __init__(self, max_size: int = MAX_MEMORY_CACHE):
        self.cache = OrderedDict()
        self.max_size = max_size

    def get(self, key: str) -> Optional[Image.Image]:
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def put(self, key: str, img: Image.Image):
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = img

    def remove(self, key: str):
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        self.cache.clear()

_memory_cache = LRUAvatarCache()

# ================= 辅助函数 =================
def _is_cache_expired(file_path: str) -> bool:
    if not os.path.exists(file_path):
        return True
    mtime = os.path.getmtime(file_path)
    age_days = (time.time() - mtime) / (24 * 3600)
    return age_days > DISK_CACHE_EXPIRE_DAYS

def _clean_expired_cache(file_path: str):
    try:
        os.remove(file_path)
        logger.debug(f"已删除过期缓存: {file_path}")
    except Exception as e:
        logger.warning(f"删除过期缓存失败 {file_path}: {e}")

async def _download_avatar_data(session: aiohttp.ClientSession, username: str, size: int) -> Optional[bytes]:
    """下载头像数据（单次尝试，超时 5 秒）"""
    url = f"{AVATAR_API_BASE}/{username}/{size}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(DOWNLOAD_TIMEOUT)) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                logger.debug(f"下载头像 {username} 失败，HTTP {resp.status}")
    except Exception as e:
        logger.debug(f"下载头像 {username} 网络错误: {e}")
    return None

# ================= 核心下载函数 =================
async def download_avatar(session: aiohttp.ClientSession, username: str, size: int = 36) -> Image.Image:
    """
    获取玩家头像（优先内存 → 磁盘 → 网络，网络超时后直接使用默认头像）。
    """
    cache_key = f"{username}_{size}"
    
    # 1. 内存缓存
    cached = _memory_cache.get(cache_key)
    if cached is not None:
        return cached.copy()
    
    # 2. 磁盘缓存
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{username}.png")
    
    if os.path.exists(cache_path) and not _is_cache_expired(cache_path):
        try:
            img = Image.open(cache_path).convert("RGBA")
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            _memory_cache.put(cache_key, img.copy())
            return img
        except Exception as e:
            logger.warning(f"读取磁盘缓存失败 {cache_path}: {e}")
            _clean_expired_cache(cache_path)
    elif os.path.exists(cache_path):
        _clean_expired_cache(cache_path)
    
    # 3. 网络下载（无重试，超时即失败）
    data = await _download_avatar_data(session, username, size)
    if data is not None:
        try:
            with open(cache_path, "wb") as f:
                f.write(data)
            img = Image.open(cache_path).convert("RGBA")
            _memory_cache.put(cache_key, img.copy())
            return img
        except Exception as e:
            logger.error(f"处理头像数据失败 {username}: {e}")
            if os.path.exists(cache_path):
                os.remove(cache_path)
    
    # 4. 返回默认灰色头像
    default_img = Image.new("RGBA", (size, size), (128, 128, 128, 255))
    return default_img