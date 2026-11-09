"""玩家头像：官方纹理链路为主、第三方兜底。
脸图按纹理 hash 落盘（内容寻址，换皮即换文件），映射层带 TTL，兜底图只进内存。
"""
import os
import io
import re
import time
import json
import base64
import hashlib
from urllib.parse import quote
from collections import OrderedDict
from typing import Optional, Tuple

import aiohttp
from PIL import Image, ImageDraw
from astrbot.api import logger

from .plugin_paths import sub_dir


# ================= 配置常量 =================
CACHE_DIR = sub_dir('avatar_cache')
LOCAL_STEVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'resources', 'images', 'steve-avatar.png')

MAX_MEMORY_CACHE = 200          # 内存最多缓存 200 个头像
NAME_UUID_TTL = 7 * 24 * 3600   # 名字→UUID 复查周期
PROFILE_TTL = 6 * 3600          # UUID→纹理 hash 复查周期，即换皮最迟多久被发现
FALLBACK_TTL = 3600             # 第三方兜底图在内存里的存活时间
REQUEST_TIMEOUT = 5.0           # 单次请求超时（秒）
MAX_FACE_FILES = 1000           # 脸图永久保存，超过这个数量按 mtime 淘汰最旧的

FACE_DIR = os.path.join(CACHE_DIR, 'face')
NAME_DIR = os.path.join(CACHE_DIR, 'name2uuid')
PROFILE_DIR = os.path.join(CACHE_DIR, 'profile')
for _d in (FACE_DIR, NAME_DIR, PROFILE_DIR):
    os.makedirs(_d, exist_ok=True)

MOJANG_NAME2UUID = "https://api.mojang.com/users/profiles/minecraft/{name}"
MOJANG_PROFILE = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"

# 兜底源：crafthead 在前（实测它对个别名字比 minotar 准）
FALLBACK_TEMPLATES = [
    "https://crafthead.net/avatar/{identifier}/{size}",
    "https://minotar.net/avatar/{identifier}/{size}",
]

_OLD_NAME_RE = re.compile(r'^[0-9a-f]{40}\.png$')


def _purge_legacy_cache():
    """旧版把脸图直接放在 avatar_cache 根下、键里不含尺寸，改版后不会再被读到"""
    try:
        for fn in os.listdir(CACHE_DIR):
            if _OLD_NAME_RE.match(fn):
                os.remove(os.path.join(CACHE_DIR, fn))
    except Exception as e:
        logger.warning(f"清理旧版头像缓存失败: {e}")


_purge_legacy_cache()


# ================= 内存缓存（LRU + TTL） =================
class LRUAvatarCache:
    def __init__(self, max_size: int = MAX_MEMORY_CACHE):
        self.cache: "OrderedDict[str, Tuple[Image.Image, float]]" = OrderedDict()
        self.max_size = max_size

    def get(self, key: str) -> Optional[Image.Image]:
        item = self.cache.get(key)
        if item is None:
            return None
        img, expire_at = item
        if time.time() >= expire_at:
            del self.cache[key]
            return None
        self.cache.move_to_end(key)
        return img

    def put(self, key: str, img: Image.Image, ttl: float):
        if key in self.cache:
            self.cache.move_to_end(key)
        elif len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)
        self.cache[key] = (img, time.time() + ttl)

    def clear(self):
        self.cache.clear()


_memory_cache = LRUAvatarCache()


# ================= 通用工具 =================
def _smart_resize(img: Image.Image, size: int) -> Image.Image:
    """像素风头像：放大用 NEAREST 保持硬边缘，缩小才用 LANCZOS"""
    if img.size == (size, size):
        return img
    resample = Image.NEAREST if size >= img.size[0] else Image.LANCZOS
    return img.resize((size, size), resample)


def _disk_key(text: str) -> str:
    return hashlib.sha1(str(text).encode('utf-8')).hexdigest()


def _read_json_cache(path: str, ttl: float):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if time.time() - payload['ts'] < ttl:
            return payload['value']
    except Exception:
        pass
    return None


def _write_json_cache(path: str, value):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'ts': time.time(), 'value': value}, f)
    except Exception as e:
        logger.warning(f"写入头像缓存失败 {path}: {e}")


async def _fetch(session: aiohttp.ClientSession, url: str) -> Tuple[int, Optional[bytes]]:
    """返回 (HTTP 状态码, body)；网络异常记为 0，便于区分「明确没有」和「问不到」"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(REQUEST_TIMEOUT)) as resp:
            if resp.status == 200:
                return 200, await resp.read()
            return resp.status, None
    except Exception as e:
        logger.debug(f"头像请求失败 {url}: {e}")
        return 0, None


class _NoSuchAccount(Exception):
    """官方明确答复这个标识符不存在（404），不是网络故障"""


def get_default_avatar(size: int = 36) -> Image.Image:
    """默认 Steve 头像；本地图片不可用时退回灰底占位"""
    if os.path.exists(LOCAL_STEVE_PATH):
        try:
            img = Image.open(LOCAL_STEVE_PATH).convert("RGBA")
            return _smart_resize(img, size)
        except Exception as e:
            logger.warning(f"加载本地 Steve 头像失败: {e}")

    img = Image.new("RGBA", (size, size), (128, 128, 128, 255))
    draw = ImageDraw.Draw(img)
    try:
        from .image_generator import _load_font
        font = _load_font(int(size * 0.7))
    except Exception:
        font = None
    draw.text((size // 2, size // 2), "S", fill=(255, 255, 255, 255), font=font, anchor="mm")
    return img


# ================= 官方纹理链路 =================
async def _name_to_uuid(session: aiohttp.ClientSession, username: str) -> Optional[str]:
    path = os.path.join(NAME_DIR, f"{_disk_key(username)}.json")
    cached = _read_json_cache(path, NAME_UUID_TTL)
    if cached:
        return cached

    url = MOJANG_NAME2UUID.format(name=quote(str(username), safe=''))
    status, data = await _fetch(session, url)
    if status == 404:
        raise _NoSuchAccount(username)
    if status != 200 or not data:
        return None
    try:
        uuid = json.loads(data)['id']
    except Exception as e:
        logger.warning(f"解析 Mojang 名字查询结果失败 {username}: {e}")
        return None
    _write_json_cache(path, uuid)
    return uuid


async def _profile_texture(session: aiohttp.ClientSession, uuid: str) -> Optional[str]:
    """皮肤纹理 URL，末段即内容 hash；换皮必然换 hash，旧脸图不可能被误命中"""
    path = os.path.join(PROFILE_DIR, f"{_disk_key(uuid)}.json")
    cached = _read_json_cache(path, PROFILE_TTL)
    if cached:
        return cached

    url = MOJANG_PROFILE.format(uuid=quote(uuid, safe=''))
    status, data = await _fetch(session, url)
    if status == 404:
        raise _NoSuchAccount(uuid)
    if status != 200 or not data:
        return None
    try:
        profile = json.loads(data)
        prop = json.loads(base64.b64decode(profile['properties'][0]['value']).decode())
        tex_url = prop['textures']['SKIN']['url']
        # Mojang 下发的是 http://，同一资源 https 可达（实测字节一致），不走明文
        if tex_url.startswith('http://'):
            tex_url = 'https://' + tex_url[len('http://'):]
    except Exception as e:
        logger.warning(f"解析 Mojang profile 失败 {uuid}: {e}")
        return None
    _write_json_cache(path, tex_url)
    return tex_url


def _render_face(skin_data: bytes, size: int) -> Image.Image:
    # 只裁脸、不叠帽子层：第三方 /avatar 端点就是只给脸，叠了会改变现有出图观感
    skin = Image.open(io.BytesIO(skin_data)).convert("RGBA")
    face = skin.crop((8, 8, 16, 16))
    return face.resize((size, size), Image.NEAREST)


def _prune_face_dir():
    try:
        entries = [(os.path.getmtime(os.path.join(FACE_DIR, f)), f)
                   for f in os.listdir(FACE_DIR) if f.endswith('.png')]
        if len(entries) <= MAX_FACE_FILES:
            return
        entries.sort()
        for _, fn in entries[:len(entries) - MAX_FACE_FILES]:
            os.remove(os.path.join(FACE_DIR, fn))
    except Exception as e:
        logger.debug(f"淘汰旧脸图失败: {e}")


async def _official_face(session: aiohttp.ClientSession, username: str,
                         uuid: Optional[str], size: int) -> Optional[Image.Image]:
    clean_uuid = uuid.replace('-', '') if uuid else None
    if not clean_uuid:
        if not username:
            return None
        clean_uuid = await _name_to_uuid(session, username)
    if not clean_uuid:
        return None

    tex_url = await _profile_texture(session, clean_uuid)
    if not tex_url:
        return None
    tex_hash = tex_url.rsplit('/', 1)[-1]

    face_path = os.path.join(FACE_DIR, f"{tex_hash}_{size}.png")
    if os.path.exists(face_path):
        try:
            return Image.open(face_path).convert("RGBA")
        except Exception as e:
            logger.warning(f"读取脸图缓存失败 {face_path}: {e}")

    status, data = await _fetch(session, tex_url)
    if status != 200 or not data:
        return None
    try:
        img = _render_face(data, size)
    except Exception as e:
        logger.warning(f"裁剪皮肤失败 {tex_url}: {e}")
        return None
    try:
        img.save(face_path, 'PNG')
        _prune_face_dir()
    except Exception as e:
        logger.warning(f"写入脸图缓存失败 {face_path}: {e}")
    return img


# ================= 第三方兜底 =================
async def _fallback_face(session: aiohttp.ClientSession, identifiers, size: int) -> Optional[Image.Image]:
    for identifier in identifiers:
        for template in FALLBACK_TEMPLATES:
            url = template.format(identifier=quote(str(identifier), safe=''), size=size)
            status, data = await _fetch(session, url)
            if status != 200 or not data:
                continue
            try:
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return _smart_resize(img, size)
            except Exception as e:
                logger.warning(f"解析兜底头像失败 {url}: {e}")
    return None


# ================= 对外入口 =================
async def download_avatar(session: aiohttp.ClientSession,
                          username: str,
                          size: int = 36,
                          is_premium: Optional[bool] = None,
                          uuid: Optional[str] = None) -> Image.Image:
    """取玩家头像：内存 → 官方纹理链路 → 第三方兜底 → 默认 Steve。

    is_premium 明确为 False、或官方答复 404 时直接用默认头像且不缓存。
    第三方兜底图只进内存短 TTL、不落盘——它可能返回 200 配一张假脸。
    """
    if is_premium is False:
        return get_default_avatar(size)

    identifiers = []
    if username:
        identifiers.append(username)
    if uuid:
        identifiers.append(uuid.replace('-', ''))
    if not identifiers:
        return get_default_avatar(size)

    for identifier in identifiers:
        cached = _memory_cache.get(f"{identifier}_{size}")
        if cached is not None:
            return cached.copy()

    try:
        img = await _official_face(session, username, uuid, size)
        if img is not None:
            for identifier in identifiers:
                _memory_cache.put(f"{identifier}_{size}", img.copy(), PROFILE_TTL)
            return img
    except _NoSuchAccount:
        return get_default_avatar(size)
    except Exception as e:
        logger.warning(f"官方头像链路异常 {username}: {e}")

    img = await _fallback_face(session, identifiers, size)
    if img is not None:
        for identifier in identifiers:
            _memory_cache.put(f"{identifier}_{size}", img.copy(), FALLBACK_TTL)
        return img

    return get_default_avatar(size)