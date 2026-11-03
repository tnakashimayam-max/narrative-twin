"""デモの写真から、撮影の位置や日時などの情報を消す。

役割：開発者が用意した元の写真を読み直し、情報を落とした JPEG だけを出力する。
4つの極：特定の極に属さない土台（架空データの道具）。
守る原則：8（デモの写真から実在の場所が分からないようにする）。

写真を AI に送らない。読み直しは app/images.py の normalize と同じ処理を使う。アプリの保存と同じ決まりで
情報を落とすため。書き出したあとに開き直し、情報が残っていないことを確かめてから成功にする。

写真に写っているもの（看板、表札、車の番号、人）は、プログラムでは確かめられない。
手順は data/README.md にある。

使い方：.venv/Scripts/python -m fiction.prepare_photos 元の写真のフォルダ
出力先：data/demo_photos/（Git に入らない）
関連するテスト：tests/test_prepare_photos.py
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import ExifTags, Image, UnidentifiedImageError

from app import config, images

DEFAULT_OUTPUT = config.DATA_DIR / "demo_photos"
_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
# カメラの元の写真は大きいことがあるので、アプリの受け付けの上限より広くする。
_MAX_SOURCE_BYTES = 200 * 1024 * 1024
_INFO_KEYS = {"exif", "comment", "xmp", "photoshop", "icc_profile", "dpi", "jfif", "adobe"}


@dataclass(frozen=True)
class PreparedPhoto:
    """1枚の結果。problem があれば、出力していない。"""

    name: str
    had_location: bool = False
    had_exif: bool = False
    problem: str | None = None


def _inspect(raw: bytes) -> tuple[bool, bool]:
    """元の写真に、撮影の情報と位置があるかを返す。"""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            exif = image.getexif()
            return bool(exif.get_ifd(ExifTags.IFD.GPSInfo)), bool(exif)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return False, False


def _is_clean(data: bytes) -> bool:
    """書き出した写真に情報が残っていないかを確かめる。"""
    with Image.open(io.BytesIO(data)) as image:
        kept = _INFO_KEYS - {"jfif", "dpi"}
        return not image.getexif() and not (kept & image.info.keys())


def prepare_one(source: Path, output: Path) -> PreparedPhoto:
    raw = source.read_bytes()
    had_location, had_exif = _inspect(raw)
    try:
        cleaned = images.normalize(raw, max_bytes=_MAX_SOURCE_BYTES)
    except images.PhotoError as error:
        return PreparedPhoto(source.name, had_location, had_exif, str(error))
    if not _is_clean(cleaned):
        return PreparedPhoto(source.name, had_location, had_exif, "情報が消えていないので出力しません。")
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{source.stem}.jpg").write_bytes(cleaned)
    return PreparedPhoto(source.name, had_location, had_exif)


def prepare_folder(source: Path, output: Path = DEFAULT_OUTPUT) -> list[PreparedPhoto]:
    """フォルダの写真をすべて整える。フォルダの中のフォルダは読まない。"""
    files = sorted(path for path in source.iterdir() if path.is_file() and path.suffix.lower() in _PHOTO_SUFFIXES)
    return [prepare_one(path, output) for path in files]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="デモの写真から撮影の情報を消します。")
    parser.add_argument("source", type=Path, help="元の写真のフォルダ")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="出力先のフォルダ")
    args = parser.parse_args(argv)
    results = prepare_folder(args.source, args.output)
    failed = 0
    for result in results:
        if result.problem:
            failed += 1
            print(f"だめ　{result.name}：{result.problem}")
        else:
            location = "位置の情報あり→消した" if result.had_location else "位置の情報なし"
            exif = "撮影の情報あり→消した" if result.had_exif else "撮影の情報なし"
            print(f"できた {result.name}：{location}、{exif}")
    print(f"{len(results) - failed}枚を {args.output} に出力しました。{failed}枚は出力していません。")
    print("写っているものは人の目で確かめてください（data/README.md）。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
