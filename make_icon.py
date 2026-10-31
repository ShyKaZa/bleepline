"""Draws the Voice Guard icon: a shield holding a sound wave with a censor bar across it.

    venv/Scripts/python make_icon.py  ->  assets/voiceguard.ico (16-256 px) + assets/voiceguard-*.png
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
VIOLET, CYAN = (124, 58, 237), (34, 211, 238)
CENSOR = (12, 10, 24)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def bezier(p0, p1, p2, p3, steps=48):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / steps for i in range(steps + 1))]


def shield(s):
    """Shield outline as a polygon for a canvas of size s."""
    left, right, top, r = 0.13 * s, 0.87 * s, 0.10 * s, 0.10 * s
    pts = bezier((left, top + r), (left, top + r * 0.45), (left + r * 0.45, top), (left + r, top))
    pts += bezier((right - r, top), (right - r * 0.45, top), (right, top + r * 0.45), (right, top + r))
    pts += bezier((right, 0.50 * s), (right, 0.74 * s), (0.66 * s, 0.86 * s), (0.50 * s, 0.95 * s))
    pts += bezier((0.50 * s, 0.95 * s), (0.34 * s, 0.86 * s), (left, 0.74 * s), (left, 0.50 * s))
    return pts


def render(size: int) -> Image.Image:
    s = size * 8  # draw big, scale down: smooth edges at every size
    gradient = Image.new("RGB", (s, s))
    px = gradient.load()
    for y in range(s):
        for x in range(0, s, 4):  # diagonal violet -> cyan
            t = min(1.0, max(0.0, (x + y) / (2 * s) * 1.25 - 0.1))
            c = tuple(int(a + (b - a) * t) for a, b in zip(VIOLET, CYAN))
            for dx in range(4):
                if x + dx < s:
                    px[x + dx, y] = c
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).polygon(shield(s), fill=255)
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    if size >= 48:  # subtle inner rim, kept inside the shield edge; too fine to read at small sizes
        rim = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(rim).line(shield(s) + [shield(s)[0]], fill=(255, 255, 255, 90), width=max(4, s // 45),
                                 joint="curve")
        rim.putalpha(ImageChops.multiply(rim.getchannel("A"), mask))
        icon = Image.alpha_composite(icon, rim)
    d = ImageDraw.Draw(icon)

    heights = (0.20, 0.36, 0.50, 0.36, 0.20) if size >= 32 else (0.26, 0.42, 0.54, 0.42, 0.26)
    bar_w = 0.075 * s if size >= 32 else 0.10 * s
    gap = 0.135 * s if size >= 32 else 0.15 * s
    cx, cy = 0.50 * s, 0.47 * s
    for i, h in enumerate(heights):
        x = cx + (i - 2) * gap
        d.rounded_rectangle((x - bar_w / 2, cy - h * s / 2, x + bar_w / 2, cy + h * s / 2),
                            radius=bar_w / 2, fill=(255, 255, 255, 255))

    bar_h = 0.13 * s if size >= 32 else 0.17 * s
    half = 0.30 * s if size >= 32 else 0.34 * s
    d.rounded_rectangle((cx - half, cy - bar_h / 2, cx + half, cy + bar_h / 2), radius=bar_h * 0.28,
                        fill=CENSOR + (255,), outline=(255, 255, 255, 230) if size >= 24 else None,
                        width=max(2, s // 110))
    return icon.resize((size, size), Image.LANCZOS)


def main():
    ASSETS.mkdir(exist_ok=True)
    images = {size: render(size) for size in ICO_SIZES}
    images[256].save(ASSETS / "voiceguard.ico", sizes=[(n, n) for n in ICO_SIZES],
                     append_images=[images[n] for n in ICO_SIZES if n != 256])
    for n in (32, 64, 256):
        images[n].save(ASSETS / f"voiceguard-{n}.png")
    preview = Image.new("RGBA", (256 + 16 + 128 + 16 + 64 + 16 + 48 + 16 + 32 + 16 + 16, 256), (32, 33, 40, 255))
    x = 0
    for n in (256, 128, 64, 48, 32, 16):
        preview.paste(images[n], (x, (256 - n) // 2), images[n])
        x += n + 16
    preview.save(ASSETS / "icon-preview.png")
    print("wrote", ", ".join(p.name for p in sorted(ASSETS.iterdir())))


if __name__ == "__main__":
    main()
