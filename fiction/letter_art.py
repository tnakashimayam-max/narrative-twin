"""手紙の挿絵を取り込む。

役割：画像生成で作った水彩の挿絵（番号の名前、例：01.png）を、横1200px の WebP に変えて static/letter_art/ に置く。
     手紙（app/advocacy_letter.py）は static/letter_art.json の一覧から、AI が番号で絵を選ぶ。
守る原則：8（提出物とデモは架空）。挿絵は人も顔も描かない、物と景色だけの絵。

使い方：python -m fiction.letter_art <挿絵のフォルダー>
PNG のままだと1枚2MB を超え、100枚でリポジトリと画面が重くなるため、取り込むときに小さくする。AI は呼ばない。
挿絵は ChatGPT（01）と Codex の image_gen（02〜100）で作った（README の「挿絵」）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ART_DIR = ROOT / "static" / "letter_art"
ART_LIST = ROOT / "static" / "letter_art.json"
WIDTH = 1200
QUALITY = 80
SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def source_for(folder: Path, number: int) -> Path | None:
    """番号の絵のファイルを探す。01.png と 1.png のどちらでもよい。"""
    for stem in (f"{number:02d}", str(number)):
        for suffix in SUFFIXES:
            path = folder / f"{stem}{suffix}"
            if path.exists():
                return path
    return None


def main(folder: Path) -> None:
    arts = json.loads(ART_LIST.read_text(encoding="utf-8"))
    ART_DIR.mkdir(parents=True, exist_ok=True)
    done = []
    for number, art in enumerate(arts, start=1):
        source = source_for(folder, number)
        if source is None:
            continue
        image = Image.open(source).convert("RGB")
        if image.width > WIDTH:
            image = image.resize((WIDTH, round(image.height * WIDTH / image.width)), Image.LANCZOS)
        image.save(ART_DIR / art["file"], "WEBP", quality=QUALITY, method=6)
        done.append(number)
    print(f"取り込んだ挿絵：{len(done)}枚 {done}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("使い方：python -m fiction.letter_art <挿絵のフォルダー>")
    main(Path(sys.argv[1]))
