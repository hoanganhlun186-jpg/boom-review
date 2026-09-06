from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "marketing_assets"


def u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


TITLE = "AutoRecapPro V2"
TAGLINE = u("PH\\u1ea6N M\\u1ec0M AI REVIEW PHIM T\\u1ef0 \\u0110\\u1ed8NG")
SUBTITLE = u("T\\u1ea1o recap phim \\u2022 Vi\\u1ebft k\\u1ecbch b\\u1ea3n \\u2022 L\\u1ed3ng ti\\u1ebfng AI \\u2022 Render video")
CTA = u("D\\u00e0nh cho creator YouTube / Facebook mu\\u1ed1n ra video nhanh, \\u0111\\u1ec1u v\\u00e0 chuy\\u00ean nghi\\u1ec7p")
BADGES = [
    u("B\\u00e1m c\\u1ea3nh"),
    u("Gi\\u1ecdng \\u0111\\u1ecdc m\\u01b0\\u1ee3t"),
    u("K\\u1ecbch b\\u1ea3n cu\\u1ed1n"),
    u("Xu\\u1ea5t video nhanh"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def draw_glow(img: Image.Image, draw: ImageDraw.ImageDraw, xy, text: str, fnt, fill, glow, blur: int = 10):
    x, y = xy
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((x, y), text, font=fnt, fill=glow)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(layer)
    draw.text((x, y), text, font=fnt, fill=fill)


def background(w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w, h), "#07111f")
    pix = img.load()
    for y in range(h):
        for x in range(w):
            nx = x / max(w - 1, 1)
            ny = y / max(h - 1, 1)
            r = int(5 + 25 * nx + 12 * math.sin(ny * math.pi))
            g = int(12 + 30 * ny + 18 * math.sin(nx * math.pi))
            b = int(28 + 58 * (1 - nx) + 28 * ny)
            pix[x, y] = (r, g, b, 255)
    draw = ImageDraw.Draw(img, "RGBA")
    for cx, cy, color, scale in [
        (int(w * 0.12), int(h * 0.20), (30, 144, 255, 105), 0.42),
        (int(w * 0.83), int(h * 0.24), (255, 42, 125, 95), 0.34),
        (int(w * 0.67), int(h * 0.80), (20, 215, 190, 80), 0.36),
    ]:
        r = int(min(w, h) * scale)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    draw = ImageDraw.Draw(img, "RGBA")
    for i in range(18):
        x = int(w * (0.08 + i * 0.055))
        draw.line((x, int(h * 0.10), x + int(w * 0.18), int(h * 0.92)), fill=(255, 255, 255, 14), width=max(1, w // 420))
    return img


def draw_phone_mockup(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int):
    rounded_rect(draw, (x, y, x + w, y + h), max(18, w // 18), (10, 16, 31, 235), (70, 130, 255, 180), max(2, w // 180))
    pad = max(12, w // 24)
    sx0, sy0, sx1, sy1 = x + pad, y + pad * 2, x + w - pad, y + h - pad
    rounded_rect(draw, (sx0, sy0, sx1, sy1), max(10, w // 30), (17, 28, 49, 255))
    bar_h = max(18, h // 15)
    draw.rectangle((sx0, sy0, sx1, sy0 + bar_h), fill=(255, 48, 102, 255))
    draw.rectangle((sx0, sy1 - bar_h, sx1, sy1), fill=(0, 180, 230, 255))
    for i in range(4):
        yy = sy0 + bar_h + pad + i * max(35, h // 9)
        rounded_rect(draw, (sx0 + pad, yy, sx1 - pad, yy + max(20, h // 22)), 8, (32, 48, 77, 255))
        draw.rectangle((sx0 + pad, yy, sx0 + pad + int((sx1 - sx0 - pad * 2) * (0.45 + i * 0.1)), yy + max(20, h // 22)), fill=(105, 67, 255, 255))
    play_r = max(28, w // 9)
    cx, cy = x + w // 2, y + h // 2
    draw.ellipse((cx - play_r, cy - play_r, cx + play_r, cy + play_r), fill=(255, 255, 255, 35), outline=(255, 255, 255, 120), width=3)
    tri = [(cx - play_r // 3, cy - play_r // 2), (cx - play_r // 3, cy + play_r // 2), (cx + play_r // 2, cy)]
    draw.polygon(tri, fill=(255, 255, 255, 220))


def draw_banner(size: tuple[int, int], filename: str, compact: bool = False):
    w, h = size
    img = background(w, h)
    draw = ImageDraw.Draw(img, "RGBA")

    margin = int(w * (0.07 if not compact else 0.045))
    title_font = font(int(h * (0.082 if not compact else 0.115)), bold=True)
    tag_font = font(int(h * (0.043 if not compact else 0.06)), bold=True)
    sub_font = font(int(h * (0.027 if not compact else 0.038)))
    cta_font = font(int(h * (0.022 if not compact else 0.032)), bold=True)
    badge_font = font(int(h * (0.020 if not compact else 0.028)), bold=True)

    panel_x0 = margin
    panel_y0 = int(h * (0.20 if not compact else 0.14))
    panel_x1 = int(w * (0.68 if not compact else 0.72))
    panel_y1 = int(h * (0.77 if not compact else 0.82))
    rounded_rect(draw, (panel_x0, panel_y0, panel_x1, panel_y1), int(h * 0.035), (5, 9, 20, 140), (255, 255, 255, 45), 2)

    x = panel_x0 + int(w * 0.035)
    y = panel_y0 + int(h * 0.07)
    draw_glow(img, draw, (x, y), TITLE, title_font, (255, 255, 255, 255), (0, 190, 255, 190), blur=max(8, h // 90))
    y += int(h * (0.105 if not compact else 0.14))
    draw.text((x, y), TAGLINE, font=tag_font, fill=(255, 225, 80, 255))
    y += int(h * (0.072 if not compact else 0.095))
    draw.text((x, y), SUBTITLE, font=sub_font, fill=(218, 234, 255, 245))
    y += int(h * (0.080 if not compact else 0.11))

    bx = x
    for badge in BADGES:
        tw, th = text_size(draw, badge, badge_font)
        bw = tw + int(w * 0.026)
        bh = th + int(h * 0.022)
        rounded_rect(draw, (bx, y, bx + bw, y + bh), bh // 2, (0, 175, 220, 215), (255, 255, 255, 65), 1)
        draw.text((bx + int(w * 0.013), y + int(h * 0.010)), badge, font=badge_font, fill=(255, 255, 255, 255))
        bx += bw + int(w * 0.012)
        if bx > panel_x1 - int(w * 0.12):
            bx = x
            y += bh + int(h * 0.018)

    y = panel_y1 - int(h * 0.105)
    draw.text((x, y), CTA, font=cta_font, fill=(180, 235, 255, 255))

    phone_w = int(w * (0.22 if not compact else 0.18))
    phone_h = int(h * (0.52 if not compact else 0.56))
    phone_x = int(w * (0.72 if not compact else 0.76))
    phone_y = int(h * (0.25 if not compact else 0.20))
    draw_phone_mockup(draw, phone_x, phone_y, phone_w, phone_h)

    # Safe-area hint for YouTube channel art, subtle enough to keep the banner clean.
    if not compact and w == 2560 and h == 1440:
        safe_w, safe_h = 1546, 423
        sx0, sy0 = (w - safe_w) // 2, (h - safe_h) // 2
        draw.rounded_rectangle((sx0, sy0, sx0 + safe_w, sy0 + safe_h), radius=18, outline=(255, 255, 255, 35), width=2)

    img = img.convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / filename, quality=95, optimize=True)


def main():
    draw_banner((2560, 1440), "autorecappro_market_youtube_2560x1440.jpg")
    draw_banner((1640, 924), "autorecappro_market_facebook_1640x924.jpg")
    draw_banner((820, 312), "autorecappro_market_facebook_cover_820x312.jpg", compact=True)
    print(OUT)


if __name__ == "__main__":
    main()
