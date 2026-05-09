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
MAX_MEMORY_CACHE = 200          # 内存中最多缓存多少个头像
DISK_CACHE_EXPIRE_DAYS = 7      # 磁盘缓存过期天数
MAX_RETRIES = 3                 # 下载失败最大重试次数
RETRY_DELAY = 1.0               # 重试间隔（秒）

# ================= 内存缓存（LRU） =================
class LRUAvatarCache:
    """基于 OrderedDict 的 LRU 头像内存缓存"""
    def __init__(self, max_size: int = MAX_MEMORY_CACHE):
        self.cache = OrderedDict()
        self.max_size = max_size

    def get(self, key: str) -> Optional[Image.Image]:
        if key in self.cache:
            # 移到最后表示最近使用
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def put(self, key: str, img: Image.Image):
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.max_size:
                # 删除最久未使用的项
                self.cache.popitem(last=False)
            self.cache[key] = img

    def remove(self, key: str):
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        self.cache.clear()

# 全局内存缓存实例
_memory_cache = LRUAvatarCache()

# ================= 辅助函数 =================
def _is_cache_expired(file_path: str) -> bool:
    """检查磁盘缓存文件是否过期（超过 DISK_CACHE_EXPIRE_DAYS 天）"""
    if not os.path.exists(file_path):
        return True
    mtime = os.path.getmtime(file_path)
    age_days = (time.time() - mtime) / (24 * 3600)
    return age_days > DISK_CACHE_EXPIRE_DAYS

def _clean_expired_cache(file_path: str):
    """删除过期的磁盘缓存文件"""
    try:
        os.remove(file_path)
        logger.debug(f"已删除过期缓存: {file_path}")
    except Exception as e:
        logger.warning(f"删除过期缓存失败 {file_path}: {e}")

async def _download_with_retry(session: aiohttp.ClientSession, url: str, username: str) -> Optional[bytes]:
    """带重试机制的下载函数，返回二进制数据"""
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(10)) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.warning(f"下载头像 {username} 失败，HTTP {resp.status} (尝试 {attempt}/{MAX_RETRIES})")
        except Exception as e:
            last_exception = e
            logger.warning(f"下载头像 {username} 网络错误 (尝试 {attempt}/{MAX_RETRIES}): {e}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    logger.error(f"下载头像 {username} 最终失败，已重试 {MAX_RETRIES} 次。最后错误: {last_exception}")
    return None

# ================= 核心下载函数 =================
async def download_avatar(session: aiohttp.ClientSession, username: str, size: int = 36) -> Image.Image:
    """
    获取玩家头像（优先内存缓存 -> 磁盘缓存 -> 网络下载）。
    
    Args:
        session: aiohttp 会话
        username: Minecraft 玩家名
        size: 头像尺寸（默认 36px）
    
    Returns:
        RGBA 格式的 PIL Image 对象。下载失败时返回灰色默认头像。
    """
    cache_key = f"{username}_{size}"
    
    # 1. 内存缓存
    cached_img = _memory_cache.get(cache_key)
    if cached_img is not None:
        return cached_img.copy()  # 返回副本，防止外部修改影响缓存
    
    # 2. 磁盘缓存
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{username}.png")
    
    if os.path.exists(cache_path) and not _is_cache_expired(cache_path):
        try:
            img = Image.open(cache_path).convert("RGBA")
            # 如果需要特定尺寸，缩放一下（下载时指定了 size，但可能之前下载的是别的尺寸）
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            _memory_cache.put(cache_key, img.copy())
            return img
        except Exception as e:
            logger.warning(f"读取磁盘缓存失败 {cache_path}: {e}")
            _clean_expired_cache(cache_path)
    elif os.path.exists(cache_path):
        # 过期则删除
        _clean_expired_cache(cache_path)
    
    # 3. 网络下载（带重试）
    url = f"https://minotar.net/avatar/{username}/{size}"
    data = await _download_with_retry(session, url, username)
    
    if data is not None:
        try:
            # 保存到磁盘
            with open(cache_path, "wb") as f:
                f.write(data)
            img = Image.open(cache_path).convert("RGBA")
            _memory_cache.put(cache_key, img.copy())
            return img
        except Exception as e:
            logger.error(f"处理下载的头像数据失败 {username}: {e}")
            # 如果保存或打开失败，删除可能损坏的文件
            if os.path.exists(cache_path):
                os.remove(cache_path)
    
    # 4. 全部失败，返回灰色默认头像
    logger.warning(f"无法获取 {username} 的头像，使用默认灰色头像。")
    default_img = Image.new("RGBA", (size, size), (128, 128, 128, 255))
    # 默认头像也缓存到内存？没必要，每次新建
    return default_img

# ================= 可选：手动清理接口 =================
def clear_memory_cache():
    """清空内存缓存（调试用）"""
    _memory_cache.clear()
    logger.info("头像内存缓存已清空")

def clear_disk_cache():
    """清空所有磁盘缓存（危险，谨慎使用）"""
    if os.path.exists(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith('.png'):
                try:
                    os.remove(os.path.join(CACHE_DIR, f))
                except Exception as e:
                    logger.warning(f"删除磁盘缓存文件失败 {f}: {e}")
    logger.info("头像磁盘缓存已清空")