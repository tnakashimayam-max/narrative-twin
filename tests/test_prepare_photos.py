"""デモの写真を整える道具のテスト。

役割：撮影の位置や日時などの情報が、出力のファイルに残らないことを確かめる。
4つの極：特定の極に属さない土台（架空データの道具）。
守る原則：8（デモの写真から実在の場所が分からないようにする）。

写真はこのファイルの中で Pillow で作る。実在の写真は使わない。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from fiction.prepare_photos import prepare_folder

SECRET = "目印の撮影情報"


def _jpeg_with_location(path: Path, *, size=(800, 600)) -> None:
    exif = Image.Exif()
    exif[0x010F] = "目印のカメラ".encode("utf-8").decode("latin-1")
    exif[0x0132] = "2026:04:01 09:00:00"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (33.0, 1.0, 12.0)
    Image.new("RGB", size, (120, 160, 90)).save(path, format="JPEG", exif=exif, comment=SECRET.encode("utf-8"))


def _png_with_text(path: Path) -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Location", SECRET)
    info.add_text("XML:com.adobe.xmp", f"<x>{SECRET}</x>")
    Image.new("RGB", (400, 300), (10, 20, 30)).save(path, format="PNG", pnginfo=info)


@pytest.fixture
def folders(tmp_path):
    source = tmp_path / "元"
    output = tmp_path / "出力"
    source.mkdir()
    return source, output


def test_撮影の位置と日時とカメラの情報を消す(folders):
    source, output = folders
    _jpeg_with_location(source / "01_engawa.jpg")
    assert SECRET.encode("utf-8") in (source / "01_engawa.jpg").read_bytes()

    results = prepare_folder(source, output)

    assert results[0].had_location is True
    raw = (output / "01_engawa.jpg").read_bytes()
    assert SECRET.encode("utf-8") not in raw
    with Image.open(io.BytesIO(raw)) as image:
        assert not image.getexif()
        assert "comment" not in image.info
        assert "xmp" not in image.info


def test_PNGの文字の情報も消してJPEGにする(folders):
    source, output = folders
    _png_with_text(source / "02_garden.png")
    assert SECRET.encode("utf-8") in (source / "02_garden.png").read_bytes()

    prepare_folder(source, output)

    raw = (output / "02_garden.jpg").read_bytes()
    assert SECRET.encode("utf-8") not in raw
    assert Image.open(io.BytesIO(raw)).format == "JPEG"


def test_長い辺は1568px以下になる(folders):
    source, output = folders
    _jpeg_with_location(source / "03_big.jpg", size=(4000, 3000))

    prepare_folder(source, output)

    assert max(Image.open(output / "03_big.jpg").size) == 1568


def test_読めないファイルは出力せず_理由を返す(folders):
    source, output = folders
    (source / "04_bad.jpg").write_bytes(b"not an image")

    results = prepare_folder(source, output)

    assert results[0].problem
    assert not (output / "04_bad.jpg").exists()


def test_写真でないファイルは読まない(folders):
    source, output = folders
    (source / "メモ.txt").write_text("x", encoding="utf-8")

    assert prepare_folder(source, output) == []


def test_10MBを超える元の写真も整えられる(folders):
    source, output = folders
    path = source / "05_large.jpg"
    _jpeg_with_location(path)
    with path.open("ab") as file:
        file.write(b"\0" * (11 * 1024 * 1024))

    results = prepare_folder(source, output)

    assert results[0].problem is None
    assert (output / "05_large.jpg").exists()


def test_デモの写真の出力先はGitに入らない():
    root = Path(__file__).resolve().parent.parent
    assert "data/demo_photos/" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
