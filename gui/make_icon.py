#!/usr/bin/env python3
"""앱 아이콘(assets/romwriter.ico, assets/romwriter_256.png)을 그리는 스크립트. Pillow 가 필요하고, 한 번만 실행하면 됩니다.

디자인: 파스텔 라벤더-핑크 그라데이션 바탕 위에 DIP 칩(노치와 1번 핀 표시, 양쪽 핀) 모양.
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")
os.makedirs(OUT, exist_ok=True)

S = 1024                                  # 크게 그린 뒤 줄여서 계단 현상을 없앤다


def gradient(size, top, bottom):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (0.65 * y + 0.35 * x) / size          # 대각선 방향 그라데이션
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img


def make():
    base = gradient(S, (196, 181, 244), (255, 196, 214))          # 라벤더 -> 핑크
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.23), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(base, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # 칩 몸체
    bx0, by0, bx1, by1 = int(S * 0.29), int(S * 0.17), int(S * 0.71), int(S * 0.83)
    # 핀 (양쪽 5개)
    pin_w, pin_h = int(S * 0.12), int(S * 0.045)
    n = 5
    gap = (by1 - by0 - int(S * 0.12)) / (n - 1)
    for i in range(n):
        cy = by0 + int(S * 0.06) + i * gap
        d.rounded_rectangle((bx0 - pin_w + 8, cy - pin_h / 2, bx0 + 12, cy + pin_h / 2), radius=10, fill=(255, 226, 196, 255))
        d.rounded_rectangle((bx1 - 12, cy - pin_h / 2, bx1 + pin_w - 8, cy + pin_h / 2), radius=10, fill=(255, 226, 196, 255))
    # 몸체 그림자와 본체
    d.rounded_rectangle((bx0 + 10, by0 + 14, bx1 + 10, by1 + 14), radius=int(S * 0.04), fill=(120, 96, 190, 70))
    d.rounded_rectangle((bx0, by0, bx1, by1), radius=int(S * 0.04), fill=(255, 255, 255, 255))
    # 노치 (위쪽 반원)
    r = int(S * 0.055)
    cx = (bx0 + bx1) // 2
    d.pieslice((cx - r, by0 - r, cx + r, by0 + r), 0, 180, fill=(214, 202, 247, 255))
    # 1번 핀 표시
    d.ellipse((bx0 + 40, by0 + 44, bx0 + 40 + 36, by0 + 44 + 36), fill=(160, 222, 196, 255))

    # 가운데 글자
    font = None
    for name in ("malgunbd.ttf", "arialbd.ttf", "segoeuib.ttf"):
        try:
            font = ImageFont.truetype(name, int(S * 0.15))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    for text, y, color in (("ROM", 0.33, (139, 111, 209, 255)), ("28C64", 0.50, (255, 156, 120, 255))):
        f = font if text == "ROM" else ImageFont.truetype(font.path, int(S * 0.105)) if hasattr(font, "path") else font
        w = d.textlength(text, font=f)
        d.text(((S - w) / 2, S * y), text, font=f, fill=color)
    # 아래 데이터 점 (이진수 느낌)
    for i, on in enumerate((1, 0, 1, 1, 0, 1)):
        x = bx0 + int(S * 0.045) + i * int(S * 0.062)
        y = int(S * 0.70)
        d.rounded_rectangle((x, y, x + int(S * 0.04), y + int(S * 0.07)), radius=8,
                            fill=(139, 111, 209, 255) if on else (226, 220, 244, 255))
    return img


if __name__ == "__main__":
    big = make()
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "romwriter_256.png"))
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, "romwriter_64.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "romwriter.ico"), sizes=[(s, s) for s in sizes])
    print("saved", os.listdir(OUT))
