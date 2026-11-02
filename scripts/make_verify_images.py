"""検証に使う、人が写っていない架空の画像を作る。

役割：縁側と庭、紫陽花の鉢、赤い急須と湯のみ、手書きのメモ、位置情報つきの大きな JPEG、
      PNG、読めない形式のファイルを Pillow で描く。実在の写真は使わない。
4つの極：1. 書く・話す（写真の検証）。
守る原則：8（提出物とデモの写真はすべて架空）。

出力先は data/trial/verify/images/ で、Git には入れない。
使い方：.venv\\Scripts\\python -m scripts.make_verify_images
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "trial" / "verify" / "images"
HAND_FONTS = [
    r"C:\Windows\Fonts\UDDigiKyokashoN-R.ttc",
    r"C:\Windows\Fonts\yumin.ttf",
    r"C:\Windows\Fonts\msgothic.ttc",
]


def _soften(image: Image.Image, radius: float = 1.2) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(radius))


def engawa() -> Image.Image:
    """木の縁側と庭のある場面。"""
    image = Image.new("RGB", (1600, 1200), (168, 205, 232))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 420, 1600, 820), fill=(92, 142, 78))
    for _ in range(90):
        x, y = random.randint(0, 1600), random.randint(430, 800)
        r = random.randint(18, 60)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(random.randint(50, 90), random.randint(110, 165), random.randint(50, 85)))
    for x, y in ((260, 470), (1240, 450)):
        draw.rectangle((x - 22, y, x + 22, y + 260), fill=(96, 68, 44))
        draw.ellipse((x - 190, y - 230, x + 190, y + 90), fill=(58, 118, 62))
    for x in (640, 720, 810, 900):
        draw.ellipse((x - 30, 700, x + 30, 735), fill=(170, 168, 160))
    image = _soften(image, 2.2)
    draw = ImageDraw.Draw(image)
    # 縁側の板と柱
    for index, y in enumerate(range(820, 1200, 54)):
        tone = 150 - index * 6
        draw.rectangle((0, y, 1600, y + 50), fill=(tone + 20, tone - 30, tone - 70))
        draw.line((0, y + 50, 1600, y + 50), fill=(70, 46, 28), width=4)
    draw.rectangle((90, 0, 150, 830), fill=(110, 76, 46))
    draw.rectangle((1450, 0, 1510, 830), fill=(110, 76, 46))
    draw.rectangle((0, 0, 1600, 70), fill=(92, 62, 38))
    return _soften(image, 0.8)


def hydrangea() -> Image.Image:
    """紫陽花の鉢。"""
    image = Image.new("RGB", (1200, 1400), (226, 222, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 1050, 1200, 1400), fill=(172, 160, 144))
    draw.polygon([(390, 880), (810, 880), (750, 1240), (450, 1240)], fill=(164, 84, 52))
    draw.rectangle((370, 850, 830, 910), fill=(182, 96, 60))
    for _ in range(26):
        x, y = random.randint(280, 920), random.randint(560, 860)
        draw.ellipse((x - 90, y - 44, x + 90, y + 44), fill=(52, random.randint(104, 140), 62))
    for cx, cy, r in ((600, 400, 240), (380, 520, 170), (830, 520, 170)):
        for _ in range(360):
            x = cx + random.randint(-r, r)
            y = cy + random.randint(-r, r)
            if (x - cx) ** 2 + (y - cy) ** 2 > r**2:
                continue
            size = random.randint(14, 24)
            color = random.choice([(92, 112, 200), (120, 104, 196), (150, 120, 206), (88, 140, 214)])
            draw.polygon([(x, y - size), (x + size, y), (x, y + size), (x - size, y)], fill=color)
    return _soften(image, 1.0)


def teapot() -> Image.Image:
    """赤い急須と湯のみ。"""
    image = Image.new("RGB", (1500, 1100), (210, 186, 150))
    draw = ImageDraw.Draw(image)
    for y in range(0, 1100, 90):
        draw.line((0, y, 1500, y + 20), fill=(186, 158, 118), width=5)
    red = (184, 42, 38)
    dark = (140, 28, 28)
    draw.ellipse((300, 380, 820, 860), fill=red)
    draw.ellipse((360, 430, 560, 600), fill=(214, 92, 84))
    draw.ellipse((430, 320, 690, 420), fill=dark)
    draw.ellipse((525, 280, 595, 340), fill=red)
    draw.polygon([(800, 560), (1000, 440), (1030, 490), (820, 680)], fill=red)
    draw.arc((130, 460, 370, 760), start=80, end=280, fill=dark, width=38)
    for x in (1090, 1290):
        draw.polygon([(x - 90, 650), (x + 90, 650), (x + 65, 860), (x - 65, 860)], fill=(238, 234, 222))
        draw.ellipse((x - 90, 625, x + 90, 680), fill=(120, 150, 92))
        draw.polygon([(x - 90, 650), (x + 90, 650), (x + 84, 700), (x - 84, 700)], fill=(52, 84, 132))
    return _soften(image, 1.0)


def memo() -> Image.Image:
    """文字が見える手書き風のメモ。"""
    image = Image.new("RGB", (1200, 1500), (196, 178, 150))
    paper = Image.new("RGB", (900, 1200), (250, 246, 232))
    draw = ImageDraw.Draw(paper)
    for y in range(150, 1200, 120):
        draw.line((50, y, 850, y), fill=(190, 200, 214), width=3)
    font = None
    for path in HAND_FONTS:
        try:
            font = ImageFont.truetype(path, 88)
            break
        except OSError:
            continue
    lines = ["今週のこと", "豆腐とねぎを買う", "紫陽花に肥料", "土曜は将棋の会", "節子さんに鉢を渡す"]
    for index, line in enumerate(lines):
        draw.text((70, 50 + index * 150), line, fill=(40, 44, 92), font=font)
    paper = paper.rotate(-3, expand=True, fillcolor=(196, 178, 150))
    image.paste(paper, (110, 90))
    return _soften(image, 0.5)


def big_with_location() -> tuple[Image.Image, Image.Exif]:
    """4000×3000 の JPEG に、撮影の位置情報を入れる。"""
    image = engawa().resize((4000, 3000))
    exif = Image.Exif()
    exif[0x010F] = "NarrativeTwinTest"
    exif[0x0132] = "2026:06:20 09:30:00"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (35.0, 40.0, 12.0)
    gps[3] = "E"
    gps[4] = (139.0, 45.0, 30.0)
    return image, exif


def main() -> None:
    random.seed(20261103)
    OUT.mkdir(parents=True, exist_ok=True)
    engawa().save(OUT / "engawa.jpg", quality=88)
    hydrangea().save(OUT / "hydrangea.jpg", quality=88)
    teapot().save(OUT / "teapot.jpg", quality=88)
    memo().save(OUT / "memo.jpg", quality=90)
    image, exif = big_with_location()
    image.save(OUT / "big_with_gps.jpg", quality=92, exif=exif)
    hydrangea().resize((800, 933)).save(OUT / "hydrangea.png")
    (OUT / "unreadable.jpg").write_bytes(b"This is not an image. " * 200)
    (OUT / "note.txt").write_text("写真ではありません。", encoding="utf-8")
    for path in sorted(OUT.iterdir()):
        print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
