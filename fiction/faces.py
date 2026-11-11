"""デモの人の顔の写真を、アイコンとプロフィール写真の枠に入れる。

役割：fiction/prepare_photos.py で整えた顔の写真を、対応する人の空いた枠に入れる。AI は呼ばない。
4つの極：特定の極に属さない土台（架空データの道具）。
守る原則：8（デモの顔は架空の人物。自分用の起動には入れない）。

なぜこう作ったか：
- 自分用の起動には入れない。開発者の本当の記録に架空の顔が混ざるのを防ぐため。同じ名前の人がいても入らない。
- 枠ごとに写真の行を分ける。同じ画像でも行を1つにすると、ログインの前に読めるアイコンと一緒に、
  プロフィール写真もログインの前に読めてしまう（app/photos.py の is_entrance_face）。
- 空いた枠にだけ入れる。起動のたびに呼ぶので、入れ替えた写真を上書きしないようにするため。
- 写真のフォルダは Git に入らないので、ないことがある。そのときは何も入れず、画面は頭文字の丸のまま動く。
- 写真の種類と置き場は投稿の写真と同じ道にする。控えと戻す道具（fiction/snapshot.py）をそのまま使うため。

人は番号ではなく名前で引く。田中さんのように、あとから登録される人の番号は乱数で決まっていないため。

手順：data/README.md の「プロフィールの顔」
関連するテスト：tests/test_profile_photo.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app import config, db, images, photos
from app.schemas import PhotoKind, User

# 人の名前と、整えた写真のファイルの名前の対応。
FACE_FILES = {
    "誠一さん": "01_seiichi.jpg",
    "美穂さん": "02_miho.jpg",
    "健太くん": "03_kenta.jpg",
    "正雄さん": "04_masao.jpg",
    "節子さん": "05_setsuko.jpg",
    "井ノ口先生": "06_inokuchi.jpg",
    "田中さん": "07_tanaka.jpg",
}

# 入れる枠。丸いアイコンと、大きく出すプロフィール写真。
_SLOTS = ("photo_id", "profile_photo_id")

# 写真の表の時刻には、顔を作った日を入れる。架空用の起動の日付はデモの場面で動くので、そちらに合わせない。
_MADE_AT = "2026-11-04T00:00:00+09:00"


def _face_file(person: User) -> Path | None:
    """この人の顔の写真のファイルを返す。顔が決まっていない人と、ファイルがない人は None。"""
    name = FACE_FILES.get(person.name) or FACE_FILES.get(person.display_name)
    if name is None:
        return None
    path = config.DEMO_FACES_DIR / name
    return path if path.is_file() else None


def fill_person(conn: sqlite3.Connection, deployment: config.Deployment, person: User) -> bool:
    """この人の空いた枠に顔の写真を入れる。入れたら True を返す。

    壊れた写真でも止めない。人の登録と起動を、デモの顔のために失敗させないため。
    """
    if deployment.mine:
        return False
    path = _face_file(person)
    if path is None:
        return False
    filled = False
    for slot in _SLOTS:
        if getattr(person, slot) is not None:
            continue
        try:
            photo = photos.store_photo(
                conn,
                deployment.photos_dir,
                path.read_bytes(),
                owner_user_id=person.id,
                kind=PhotoKind.PROFILE,
                moment=_MADE_AT,
            )
        except (images.PhotoError, OSError):
            return filled
        with conn:
            conn.execute(f"UPDATE users SET {slot} = ? WHERE id = ?", (photo.id, person.id))
        filled = True
    return filled


def fill_everyone(deployment: config.Deployment) -> list[str]:
    """はずしていない人の空いた枠に顔の写真を入れ、入れた人の名前を返す。"""
    if deployment.mine:
        return []
    connection = db.connect(deployment.db_path)
    try:
        return [person.name for person in db.list_users(connection) if fill_person(connection, deployment, person)]
    finally:
        connection.close()
