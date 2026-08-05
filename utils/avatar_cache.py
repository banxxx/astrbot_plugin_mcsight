import os
import time
import asyncio
from collections import OrderedDict
from typing import Optional
from astrbot.api import logger

import aiohttp
from PIL import Image


# ================= 配置常量 =================
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'avatar_cache')
LOCAL_STEVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'resources', 'images', 'steve-avatar.png')
MAX_MEMORY_CACHE = 200          # 内存最多缓存 200 个头像
DISK_CACHE_EXPIRE_DAYS = 7      # 磁盘缓存过期天数
DOWNLOAD_TIMEOUT = 5.0          # 单个头像下载超时（秒）

# 头像 API 源（按优先级依次尝试）
AVATAR_API_TEMPLATES = [
    "https://minotar.net/avatar/{identifier}/{size}",    # 首选
    "https://crafthead.net/avatar/{identifier}/{size}",  # 备选
]

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

async def _download_avatar_data(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    """
    下载指定 URL 的头像数据（单次尝试，超时 DOWNLOAD_TIMEOUT 秒）
    """
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(DOWNLOAD_TIMEOUT)) as resp:
            if resp.status == 200:
                return await resp.read()
            else:
                logger.debug(f"下载头像失败，HTTP {resp.status}: {url}")
    except aiohttp.ClientError as e:
        logger.error(f"客户端错误: {url} - {e}")
    except Exception as e:
        logger.error(f"下载头像异常: {url} - {e}")
    raise  # 重新抛出以便上层处理

def get_default_avatar(size: int = 36) -> Image.Image:
    """
    返回默认 Steve 头像。
    优先从本地 steve-avatar.png 加载，若失败则生成一个带灰色背景和 "S" 字母的占位图。
    """
    # 方案1：尝试加载本地图片
    if os.path.exists(LOCAL_STEVE_PATH):
        try:
            img = Image.open(LOCAL_STEVE_PATH).convert("RGBA")
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            return img
        except Exception as e:
            logger.warning(f"加载本地 Steve 头像失败: {e}")

    # 方案2：生成一个灰色背景带白色 "S" 字母的占位头像
    img = Image.new("RGBA", (size, size), (128, 128, 128, 255))
    draw = ImageDraw.Draw(img)
    # 尝试加载字体，若失败则使用默认
    try:
        from .image_generator import _load_font
        font = _load_font(int(size * 0.7))
    except Exception:
        font = None
    draw.text(
        (size // 2, size // 2),
        "S",
        fill=(255, 255, 255, 255),
        font=font,
        anchor="mm"
    )
    return img

# ================= 核心下载函数 =================
async def download_avatar(session: aiohttp.ClientSession, 
                          username: str, 
                          size: int = 36, 
                          is_premium: Optional[bool] = None, 
                          uuid: Optional[str] = None) -> Image.Image:
    """
    获取玩家头像（优先内存 → 磁盘 → 网络多源回退，全部失败则返回默认灰色头像）
    如果 is_premium 为 False，直接返回默认灰色头像（Steve），跳过网络请求。
    如果 is_premium 为 True 或 None，走正常缓存+网络逻辑。
    """
    # 如果是明确离线玩家，直接返回默认头像
    logger.warning(f"头像is_premium {is_premium} ")
    if is_premium is False:
        return get_default_avatar(size)  # 需要实现此函数

    # 确定查询标识符（优先 UUID）
    identifier = uuid if uuid else username
    cache_key = f"{identifier}_{size}"
    logger.warning(f"头像identifier {identifier} ")

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

    # 3. 网络下载，依次尝试所有配置的 API 模板
    data = None
    for template in AVATAR_API_TEMPLATES:
        url = template.format(identifier=identifier, size=size)
        data = await _download_avatar_data(session, url)
        if data is not None:
            break   # 成功即停止尝试

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

    # 4. 所有 API 都失败，返回默认灰色头像
    if os.path.exists(LOCAL_STEVE_PATH):
        try:
            img = Image.open(LOCAL_STEVE_PATH).convert("RGBA")
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            return img
        except Exception as e:
            logger.error(f"加载本地 Steve 头像失败: {e}")

    # 5. 本地 Steve 头像也不可用，最后返回纯灰色方块
    default_img = Image.new("RGBA", (size, size), (128, 128, 128, 255))
    return default_img