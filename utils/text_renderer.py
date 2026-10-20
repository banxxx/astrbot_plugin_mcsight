"""
纯 Pillow 混合文本渲染工具
中英文与彩色 Emoji 图标混排，图标取自 resources/emoji 下的 PNG 贴图，完全本地化，不依赖网络
"""

import os
import re
from PIL import Image


# Emoji 匹配正则（涵盖主流 Emoji 的 Unicode 范围）
_EMOJI_PATTERN = re.compile(
    "("
    "["
    "\U0001F300-\U0001FAFF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002300-\U000023FF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\uFE00-\uFE0F"
    "\U0001F3FB-\U0001F3FF"
    "\u200D"
    "]+"
    ")",
    re.UNICODE
)

# Emoji 图标贴图：只放实际用到的几个，缺位的 emoji 一律不画也不占位
_EMOJI_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'resources', 'emoji'
)
_tile_cache = {}


def _tile_for(run: str):
    """取该 emoji 段的贴图，没有对应图标返回 None（结果连同缺失一起缓存，不重复探盘）"""
    for ch in run:
        cp = ord(ch)
        if cp in (0xFE0E, 0xFE0F, 0x200D):   # 变体选择符与零宽连接符不单独定图标
            continue
        key = f"{cp:04X}"
        if key not in _tile_cache:
            try:
                img = Image.open(os.path.join(_EMOJI_DIR, key + '.png'))
                img.load()
            except (OSError, ValueError):
                img = None
            _tile_cache[key] = img
        return _tile_cache[key]
    return None


def _render_emoji_image(emoji: str, target_size: int):
    """
    把 emoji 段取成目标大小的 RGBA 图片。
    贴图按 133px 画布烘制（109 原生位图 + 12px 内边距 ×2），
    与原先从字体绘制再缩放的尺寸约定一致，因此缩放结果不变。
    没有对应图标时返回 None。
    """
    tile = _tile_for(emoji)
    if tile is None or target_size <= 0:
        return None
    return tile.resize((target_size, target_size), Image.LANCZOS)


def split_emoji_runs(text: str):
    """
    拆分文本为 runs。
    返回: [(is_emoji, segment), ...]
    """
    if not text:
        return []
    parts = _EMOJI_PATTERN.split(text)
    result = []
    for part in parts:
        if not part:
            continue
        if _EMOJI_PATTERN.fullmatch(part):
            result.append((True, part))
        else:
            result.append((False, part))
    return result


def measure_text_with_emoji(draw, text, font, emoji_scale=1.0):
    """测量包含 emoji 的文本宽度（不绘制）。"""
    if not text:
        return 0
    font_size = getattr(font, "size", 16)
    emoji_size = max(1, int(font_size * emoji_scale))
    total_w = 0.0
    for is_emoji, segment in split_emoji_runs(text):
        if is_emoji:
            if _tile_for(segment) is not None:
                total_w += emoji_size
        else:
            total_w += draw.textlength(segment, font=font)
    return total_w


def draw_text_with_emoji(img, draw, pos, text, font, fill,
                         emoji_scale=1.0, emoji_offset_y=None):
    """
    在 img 上绘制可能包含 emoji 的文本，返回绘制完成后的 x 坐标。
    """
    x, y = pos
    font_size = getattr(font, "size", 16)
    emoji_size = max(1, int(font_size * emoji_scale))

    if emoji_offset_y is None:
        # 让 emoji 垂直居中于文字行高
        emoji_offset_y = int((font_size - emoji_size) * 0.5)

    for is_emoji, segment in split_emoji_runs(text):
        if is_emoji:
            emoji_img = _render_emoji_image(segment, emoji_size)
            if emoji_img is None:
                continue    # 没有图标就整段跳过，不占位：避免昵称里留下空格洞
            img.paste(emoji_img, (int(x), int(y + emoji_offset_y)), emoji_img)
            x += emoji_size
        else:
            draw.text((x, y), segment, font=font, fill=fill)
            x += draw.textlength(segment, font=font)

    return x