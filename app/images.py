"""写真を開き直して整える（決まった手順で動く層）。

役割：送られた写真を読み直し、向きを直し、長い辺を1568px以下にし、撮影の情報を落として JPEG にする。
4つの極：1. 書く・話す。
守る原則：8（提出物とデモの写真は架空）、10（撮影の位置などを見せる範囲を広げない）。

画面の側でも縮めて情報を落とすが、ここでもう一度行う。画面の処理を通らずに届いた写真でも、
撮影の位置を保存しないようにするため。すべて JPEG にそろえるのは、保存と AI への受け渡しを
1つの形で済ませるため。透明な部分は白にする。
関連するテスト：tests/test_photos.py
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_LONG_EDGE = 1568
# 受け付ける大きさの上限。画面の側で縮めてから届くので、これで足りる。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_READABLE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
_JPEG_QUALITY = 88

UNREADABLE_MESSAGE = "この形式の写真は読めません。JPEG か PNG に変えてから、お送りください。"
TOO_LARGE_MESSAGE = "写真が大きすぎます。10MB までの写真をお使いください。"
EMPTY_MESSAGE = "写真が届いていません。もう一度お試しください。"


class PhotoError(ValueError):
    """写真を受け取れないとき。メッセージはそのまま画面に出す。"""


def normalize(raw: bytes) -> bytes:
    """写真を読み直し、保存してよい JPEG の中身を返す。読めなければ PhotoError にする。"""
    if not raw:
        raise PhotoError(EMPTY_MESSAGE)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PhotoError(TOO_LARGE_MESSAGE)
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in _READABLE_FORMATS:
                raise PhotoError(UNREADABLE_MESSAGE)
            # 撮影時の向きを画素に写してから情報を落とす。落としたあとでは向きが分からなくなるため。
            upright = ImageOps.exif_transpose(opened)
            upright.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.Resampling.LANCZOS)
            flat = _on_white(upright)
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        # 壊れた PNG は SyntaxError になることがある
        raise PhotoError(UNREADABLE_MESSAGE) from error
    output = io.BytesIO()
    # exif を渡さず info も空にするので、撮影の位置、日時、コメント欄は書き込まれない
    flat.info = {}
    flat.save(output, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return output.getvalue()


def _on_white(image: Image.Image) -> Image.Image:
    """透明な部分を白にして、JPEG で保存できる形にする。"""
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")
