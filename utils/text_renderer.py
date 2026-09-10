"""
纯 Pillow 混合文本渲染工具
支持中英文与彩色 Emoji 混合渲染，完全本地化，不依赖网络
"""

import os
import re
from PIL import Image, ImageDraw, ImageFont
from astrbot.api import logger


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

# Emoji 字体缓存：'font' 存字体对象，'native_size' 存原生像素尺寸
_emoji_font_cache = {}


def _get_emoji_font():
    """
    加载 NotoColorEmoji.ttf。自动探测该字体支持的原生位图尺寸。
    返回 (font, native_size) 或 (None, None)。
    """
    if 'font' in _emoji_font_cache:
        return _emoji_font_cache['font'], _emoji_font_cache.get('native_size')
    if 'failed' in _emoji_font_cache:
        return None, None

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    font_path = os.path.join(base, 'resources', 'fonts', 'NotoColorEmoji.ttf')

    if not os.path.exists(font_path):
        logger.warning(f"❌ NotoColorEmoji.ttf 不存在: {font_path}")
        _emoji_font_cache['failed'] = True
        _emoji_font_cache['font'] = None
        return None, None

    # 逐个尝试候选尺寸
    candidates = [109, 128, 136, 96, 112, 160, 64, 32]
    for size in candidates:
        try:
            font = ImageFont.truetype(font_path, size)
            _emoji_font_cache['font'] = font
            _emoji_font_cache['native_size'] = size
            logger.info(f"✅ NotoColorEmoji 加载成功，原生尺寸 = {size}")
            return font, size
        except Exception:
            continue

    logger.warning(f"❌ NotoColorEmoji.ttf 加载失败：所有候选尺寸 {candidates} 都不支持")
    _emoji_font_cache['failed'] = True
    _emoji_font_cache['font'] = None
    return None, None


def _render_emoji_image(emoji: str, target_size: int):
    """
    把单个 emoji 渲染为指定大小的 RGBA 图片。
    - 使用字体原生位图尺寸绘制到带 padding 的画布
    - 不做 getbbox 裁剪，避免切掉 emoji 边缘
    - 整体缩放到目标大小
    失败返回 None。
    """
    font, native_size = _get_emoji_font()
    if font is None or native_size is None:
        return None

    try:
        padding = 12
        canvas_size = native_size + padding * 2
        tmp = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)
        tmp_draw.text((padding, padding), emoji, font=font, embedded_color=True)

        if tmp.getbbox() is None:
            # 回退：不启用 embedded_color
            tmp = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
            tmp_draw = ImageDraw.Draw(tmp)
            tmp_draw.text((padding, padding), emoji, font=font, fill=(0, 0, 0, 255))
            if tmp.getbbox() is None:
                logger.warning(f"⚠️ emoji '{emoji}' 渲染后为空")
                return None

        scaled = tmp.resize((target_size, target_size), Image.LANCZOS)
        return scaled
    except Exception as e:
        logger.warning(f"渲染 emoji '{emoji}' 失败: {e}")
        return None


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
            if emoji_img is not None:
                img.paste(emoji_img, (int(x), int(y + emoji_offset_y)), emoji_img)
            x += emoji_size
        else:
            draw.text((x, y), segment, font=font, fill=fill)
            x += draw.textlength(segment, font=font)

    return x