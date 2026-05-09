import os
import aiohttp
from PIL import Image

# 获取插件根目录（当前文件位于 utils/ 下，上一级就是根目录）
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PLUGIN_ROOT, "avatar_cache")

# 确保缓存目录存在
os.makedirs(CACHE_DIR, exist_ok=True)

async def download_avatar(session: aiohttp.ClientSession, username: str, size: int = 36) -> Image.Image:
    """从 minotar 下载头像并缓存到本地，返回 RGBA Image"""
    cache_path = os.path.join(CACHE_DIR, f"{username}.png")
    
    # 如果已缓存，直接读取
    if os.path.exists(cache_path):
        return Image.open(cache_path).convert("RGBA")
    
    url = f"https://minotar.net/avatar/{username}/{size}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(10)) as resp:
            if resp.status == 200:
                data = await resp.read()
                with open(cache_path, "wb") as f:
                    f.write(data)
                return Image.open(cache_path).convert("RGBA")
    except Exception:
        # 下载失败（网络问题、玩家不存在等），返回灰色默认头像
        pass
    
    # 返回灰色默认头像
    return Image.new("RGBA", (size, size), (128, 128, 128, 255))