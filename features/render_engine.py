"""Pure drawing primitives shared by existing and official statistics cards.

Only bundled fonts are read while rendering. No framework imports, configuration,
avatars, caches, credential files or runtime initialization are involved.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


SCALE, WIDTH, PAD = 2, 1080, 36
BG, DASH = "#F9F9F8", "#efefec"
_fonts = {}


def _font(size):
    size = int(size + 0.5)
    if size not in _fonts:
        font_file = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "msyh.ttf"
        try:
            _fonts[size] = ImageFont.truetype(str(font_file), size)
        except OSError:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def smart_resize(image, size):
    if image.size == (size, size):
        return image
    return image.resize((size, size), Image.Resampling.NEAREST if size >= image.width else Image.Resampling.LANCZOS)


def new_ops():
    return {"shadow": [], "bg0": [], "bg1": [], "fg": []}


def text_w(draw, value, size):
    return draw.textlength(value, font=_font(size))


def add_text(ops, x, y, value, size, color):
    ops["fg"].append(("text", x, y, value, size, color))


def add_rect(ops, layer, x, y, width, height, radius, fill, outline=None, line_w=1):
    ops[layer].append(("rect", x, y, width, height, radius, fill, outline, line_w))


def add_ellipse(ops, layer, x, y, diameter, fill):
    ops[layer].append(("ellipse", x, y, diameter, fill))


def render_ops(draw, ops, scale=SCALE, img=None, extra_handlers=None):
    def scaled(value):
        return value * scale
    for layer in ("shadow", "bg0", "bg1", "fg"):
        for op in ops[layer]:
            kind = op[0]
            if extra_handlers and kind in extra_handlers:
                extra_handlers[kind](op, img)
                continue
            if kind == "shadow":
                _, x, y, width, height, radius, blur, dy, color = op
                pad = blur * 2 + 2
                tile = Image.new("RGBA", (int((width + pad * 2) * scale), int((height + pad * 2) * scale)), (*color[:3], 0))
                ImageDraw.Draw(tile).rounded_rectangle(
                    [pad * scale, pad * scale, (pad + width) * scale, (pad + height) * scale],
                    radius=radius * scale, fill=color)
                tile = tile.filter(ImageFilter.GaussianBlur(blur * scale))
                img.paste(tile, (int((x - pad) * scale), int((y - pad + dy) * scale)), tile)
            elif kind == "rect":
                _, x, y, width, height, radius, fill, outline, line_w = op
                draw.rounded_rectangle([scaled(x), scaled(y), scaled(x + width), scaled(y + height)],
                    radius=scaled(radius), fill=fill, outline=outline, width=max(1, int(scaled(line_w))))
            elif kind == "ellipse":
                _, x, y, diameter, fill = op
                draw.ellipse([scaled(x), scaled(y), scaled(x + diameter), scaled(y + diameter)], fill=fill)
            elif kind == "text":
                _, x, y, value, size, color = op
                draw.text((scaled(x), scaled(y)), value, font=_font(size * scale), fill=color)
            elif kind == "dash":
                _, x0, x1, y = op
                current = scaled(x0)
                while current < scaled(x1):
                    draw.line([(current, scaled(y)), (min(current + scaled(4), scaled(x1)), scaled(y))],
                              fill=DASH, width=max(1, int(scale / 2)))
                    current += scaled(8)


def build_image(builder, doc, extra_handlers=None):
    scratch = Image.new("RGB", (8, 8), BG)
    ops = new_ops()
    total = builder(ImageDraw.Draw(scratch), ops, doc, PAD)
    image = Image.new("RGB", (WIDTH * SCALE, (int(total) + PAD) * SCALE), BG)
    render_ops(ImageDraw.Draw(image), ops, img=image, extra_handlers=extra_handlers)
    return image
