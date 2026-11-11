"""架空のケアプランの PDF を作る。

役割：fiction/documents/care_plan_2036-05.html から、文字の入った PDF と、スキャンした紙に近い画像だけの PDF を作る。
     資料の取り込み（app/professional_records.py）を、文字の読める資料とスキャンの両方で試すため。
守る原則：8（提出物とデモは架空）。

使い方：python -m fiction.documents.make_pdfs
Chrome（または Edge）の印刷の機能で PDF にし、画面の写しを Pillow で画像だけの PDF にする。AI は呼ばない。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "care_plan_2036-05.html"
TEXT_PDF = HERE / "care_plan_2036-05.pdf"
SCAN_PDF = HERE / "care_plan_2036-05_scan.pdf"
BROWSERS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)


def browser() -> Path:
    for path in BROWSERS:
        if path.exists():
            return path
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return Path(found)
    raise SystemExit("Chrome か Edge が見つかりません。")


def run(*args: str) -> None:
    subprocess.run([str(browser()), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", *args], check=True, timeout=120)


def main() -> None:
    url = SOURCE.as_uri()
    run(f"--print-to-pdf={TEXT_PDF}", url)
    with tempfile.TemporaryDirectory() as folder:
        shot = Path(folder) / "page.png"
        # A4 の縦横の比にした画面の写し。1ページ目（第1表）だけでなく全体が入る高さにする
        run(f"--screenshot={shot}", "--window-size=1240,2600", "--hide-scrollbars", url)
        gray = Image.open(shot).convert("L")
        # 画面の写しは改ページしないので、字のある所だけを1ページに切り出す（白い余白は少し残す）
        left, top, right, bottom = ImageOps.invert(gray).getbbox()
        page = gray.crop((max(left - 40, 0), max(top - 40, 0), min(right + 40, gray.width), min(bottom + 60, gray.height)))
        page.convert("RGB").save(SCAN_PDF, resolution=150)
    print(TEXT_PDF.name, TEXT_PDF.stat().st_size, "bytes")
    print(SCAN_PDF.name, SCAN_PDF.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
