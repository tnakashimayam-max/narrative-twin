"""流し終えた物語に、あとから当てる直し。

役割：台本を流したあとで決まった3つの直しを、控えとデータベースに同じ内容で当てる。
      1. 重い話で終わった日の今日の一言を、部屋の行から外す（判断は AI、外すのはプログラム）。
      2. 家系図の関係を足す（AI を使わない）。
      3. 人ではない語と、登録済みの人の候補を「ちがう」にする（AI を使わない）。
      4. 井ノ口先生を専門職・主治医にする（AI を使わない）。話した当時の言明の札は変えない。
      5. 人ごとに、ほかの人の発言の最後の3件だけを未読に残す（AI を使わない）。
      6. 井ノ口先生に託す（AI を使わない）。田中さんを招いた日（2036年1月18日）に、本人が主治医にも託した物語にする。
         台本には、登録済みの専門職に託す行の形がないので、ここで託す設定の行を足す。2026年の控えには足さない。
4つの極：特定の極に属さない土台（デモの道具）。
守る原則：5（元の記録を残す）、8（提出物とデモは架空）。

全部を流し直さないのは、言明と要素と番号を変えないため。ここで書き換えるのは、下の3か所の行だけである。
一言の判断の結果は data/select/fixes/quiet_tips.json に、外した文ごと残す。同じ結果を複数の控えに当てて、
撮る画面とリポジトリを食い違わせないため。発言の台帳（utterances）の行は残し、画面に出す部屋の行だけを外す。
部屋の行は追記だけの決まりで守られているので、外すときだけ、その決まりのトリガーを外して戻す。

使い方：
  python -m fiction.story_fixes judge 判断に使うデータベース
  python -m fiction.story_fixes apply データベース [--no-family]

理由と数字：docs/decisions.md の「流したあとで当てた直し」
関連するテスト：tests/test_story_fixes.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from app import ai, config, db, memory, professions, prompts
from app.schemas import CandidateStatus, FamilyLinkKind, MessageKind, Role, RoomKind, ShareScope

DECISIONS = config.ROOT / "data" / "select" / "fixes" / "quiet_tips.json"
WORKERS = 8
# 親子は左が親。夫婦は順を問わない
FAMILY_LINKS = (
    ("誠一さん", "和子さん", FamilyLinkKind.SPOUSE),
    ("誠一さん", "美穂さん", FamilyLinkKind.PARENT_CHILD),
    ("和子さん", "美穂さん", FamilyLinkKind.PARENT_CHILD),
    ("美穂さん", "健太くん", FamilyLinkKind.PARENT_CHILD),
)
FAMILY_LINKS_AT = "2036-05-05T00:00:00+09:00"
DOCTOR_NAME = "井ノ口先生"
# 主治医に託した日。tyumajinさんの決定で、田中さんを招いた日にそろえた
DOCTOR_ENTRUSTED_ON = "2036-01-18"
# 流した物語では誰も部屋を開いていないので、読んだ位置を入れないと10年分がすべて未読に数えられる
UNREAD_LEFT = 3
# 人ではない語（井ノ口内科、小雪、亀、おる）と、もう登録してある人（健太、先生、たなか）
REJECTED_CANDIDATES = ("井ノ口内科", "小雪", "亀", "おる", "健太", "先生", "たなか")


def closing_rows(conn: sqlite3.Connection) -> list[dict]:
    """今日の一言の行を、ツインとの部屋の会話ごとに返す。

    「今日はここまで」のときは、受け止めと一言を同じ時刻に続けて書く。一言が出なかった会話は、
    最後の時刻のツインの行が1つだけになるので、ここには入らない。
    """
    room = conn.execute("SELECT id FROM rooms WHERE kind = ?", (RoomKind.TWIN.value,)).fetchone()
    if room is None:
        return []
    found = []
    numbers = conn.execute(
        "SELECT DISTINCT conversation_id FROM room_messages WHERE room_id = ? AND conversation_id IS NOT NULL", (room[0],)
    ).fetchall()
    for (conversation_id,) in numbers:
        rows = conn.execute(
            """SELECT m.id, m.sent_at, u.text AS body FROM room_messages m JOIN utterances u ON u.id = m.utterance_id
               WHERE m.conversation_id = ? AND m.is_twin = 1 ORDER BY m.rowid""",
            (conversation_id,),
        ).fetchall()
        if len(rows) >= 2 and rows[-1]["sent_at"] == rows[-2]["sent_at"]:
            found.append(
                {
                    "message_id": rows[-1]["id"],
                    "conversation_id": conversation_id,
                    "sent_at": rows[-1]["sent_at"],
                    "text": rows[-1]["body"],
                    "reply": rows[-2]["body"],
                }
            )
    return found


def _question(conn: sqlite3.Connection, row: dict, self_user) -> str:
    """流したときと同じ入力を作る。受け止めと一言は、まだ書かれていなかった文なので除く。"""
    lines = memory.conversation_lines(
        conn,
        row["conversation_id"],
        self_user,
        max_lines=memory.LIMITS["current_utterances"],
        max_chars=memory.LIMITS["current_chars"],
        with_photos=True,
        with_added=True,
    )
    written = (memory.speech(row["reply"]), memory.speech(row["text"]))
    heard = "\n".join(line for line in lines if not line.endswith(written))
    now = datetime.fromisoformat(row["sent_at"])
    return f"## 今日の日付\n{memory.date_label(now.date())}\n\n## 今の時刻\n{now.hour}時{now.minute}分\n\n## 今日の話\n{heard}"


def judge(conn: sqlite3.Connection, *, client=None) -> dict:
    """今の決まりで一言を書き直させ、空なら重い話と判断する。書き直した文は使わない。

    呼べなかった行は残す。判断できないものを外さないため。
    """
    self_user = db.self_user(conn)
    system = prompts.twin_closing_system(self_user.display_name)
    rows = closing_rows(conn)
    questions = [_question(conn, row, self_user) for row in rows]

    def ask(text: str):
        try:
            return ai.twin_closing(system=system, text=text, client=client)
        except (ai.AIResultError, ai.AIRefusedError, ai.AIUnavailableError):
            return None

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(ask, questions))
    answered = [result for result in results if result is not None]
    removed = [
        {key: row[key] for key in ("message_id", "conversation_id", "sent_at", "text")}
        for row, result in zip(rows, results)
        if result is not None and not result.value.tip
    ]
    return {
        "model": answered[0].model if answered else None,
        "judged": len(rows),
        "failed": len(rows) - len(answered),
        "removed": removed,
        "usd": round(sum(config.cost_usd(item.model, item.input_tokens, item.output_tokens) for item in answered), 3),
    }


def apply(conn: sqlite3.Connection, decisions: dict, *, family: bool = True) -> dict:
    """直しを当てる。何度当てても同じになる。全部入るか、全部入らないかにする。"""
    numbers = [item["message_id"] for item in decisions["removed"]]
    triggers = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'room_messages_no_delete'"
    ).fetchone()
    conn.execute("BEGIN")
    try:
        _keep_read_positions(conn, numbers)
        conn.execute("DROP TRIGGER IF EXISTS room_messages_no_delete")
        hidden = sum(conn.execute("DELETE FROM room_messages WHERE id = ?", (number,)).rowcount for number in numbers)
        if triggers is not None:
            conn.execute(triggers[0])
        linked = _add_family_links(conn) if family else 0
        rejected = conn.execute(
            f"""UPDATE person_candidates SET status = ?, decided_at = created_at
                WHERE status = ? AND name IN ({", ".join("?" for _ in REJECTED_CANDIDATES)})""",
            (CandidateStatus.REJECTED.value, CandidateStatus.PENDING.value, *REJECTED_CANDIDATES),
        ).rowcount
        doctor = conn.execute(
            "UPDATE users SET role = ?, relation = ? WHERE name = ? AND (role <> ? OR relation IS NOT ?)",
            (Role.PROFESSIONAL.value, professions.DOCTOR, DOCTOR_NAME, Role.PROFESSIONAL.value, professions.DOCTOR),
        ).rowcount
        reads = _settle_reads(conn)
        entrusted = _entrust_doctor(conn) if family else 0
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return {
        "hidden_tips": hidden,
        "family_links": linked,
        "rejected_candidates": rejected,
        "doctor": doctor,
        "read_positions": reads,
        "doctor_entrusted": entrusted,
    }


def _entrust_doctor(conn: sqlite3.Connection) -> int:
    """井ノ口先生に託す設定の行を足す。すでにあれば足さない。足した行の数を返す。

    託すと、意思決定支援用の記録が見え、支援者の画面と「ツインに聞く」（代弁）が使えるようになる。
    ツール（紙とケアプラン案）は職種で決まるので、主治医は使えないまま（app/professions.py）。
    関連するテスト：tests/test_story_fixes.py の test_井ノ口先生に託す行を1回だけ足す
    """
    row = conn.execute("SELECT id FROM users WHERE name = ? AND role = ?", (DOCTOR_NAME, Role.PROFESSIONAL.value)).fetchone()
    if row is None:
        return 0
    if conn.execute("SELECT 1 FROM delegations WHERE delegate_user_id = ? AND scope = ?", (row["id"], ShareScope.DECISION_SUPPORT.value)).fetchone():
        return 0
    db.insert_row(conn, "delegations", {"delegate_user_id": row["id"], "scope": ShareScope.DECISION_SUPPORT.value, "decided_on": DOCTOR_ENTRUSTED_ON})
    return 1


def _keep_read_positions(conn: sqlite3.Connection, numbers: list[int]) -> None:
    """読んだ位置は部屋の中の順番なので、外す行の分だけ前へずらす。読んだ行と未読の行を入れ替えないため。"""
    removed = set(numbers)
    for read in conn.execute("SELECT room_id, user_id, last_read_seq FROM room_reads").fetchall():
        ordered = conn.execute("SELECT id FROM room_messages WHERE room_id = ? ORDER BY rowid", (read["room_id"],)).fetchall()
        before = sum(1 for row in ordered[: read["last_read_seq"]] if row["id"] in removed)
        if before:
            conn.execute(
                "UPDATE room_reads SET last_read_seq = ? WHERE room_id = ? AND user_id = ?",
                (read["last_read_seq"] - before, read["room_id"], read["user_id"]),
            )


def _settle_reads(conn: sqlite3.Connection) -> int:
    """人ごとに、ほかの人の発言の最後の数件だけを未読に残し、それより前を既読にする。変えた行の数を返す。

    数え方は app/room_store.py の unread と同じで、自分の発言とお知らせは数えない。ツインとの部屋は画面が未読を
    数えないので除く。亡くなった方とはずした人は
    画面を開かないので、位置を作らない。読んだ位置は戻さない。すでに開いて読んだ部屋を未読へ戻さないため。
    関連するテスト：tests/test_story_fixes.py の test_ほかの人の発言の最後の3件だけを未読に残す
    """
    changed = 0
    users = conn.execute("SELECT id FROM users WHERE is_deceased = 0 AND removed_at IS NULL").fetchall()
    for (user_id,) in users:
        rooms = conn.execute(
            "SELECT m.room_id FROM room_members m JOIN rooms r ON r.id = m.room_id WHERE m.user_id = ? AND m.left_at IS NULL AND r.kind <> ?",
            (user_id, RoomKind.TWIN.value),
        ).fetchall()
        lengths: dict[int, list[str]] = {}
        others = []
        for (room_id,) in rooms:
            rows = conn.execute("SELECT rowid, sender_user_id, kind, sent_at FROM room_messages WHERE room_id = ? ORDER BY rowid", (room_id,)).fetchall()
            lengths[room_id] = [row["sent_at"] for row in rows]
            others += [
                (row["sent_at"], row["rowid"], room_id, seq)
                for seq, row in enumerate(rows, start=1)
                if row["kind"] != MessageKind.NOTICE.value and row["sender_user_id"] != user_id
            ]
        first_unread: dict[int, int] = {}
        for _, _, room_id, seq in sorted(others)[-UNREAD_LEFT:]:
            first_unread[room_id] = min(seq, first_unread.get(room_id, seq))
        for room_id, moments in lengths.items():
            position = first_unread.get(room_id, len(moments) + 1) - 1
            if position < 1:
                continue
            row = conn.execute("SELECT last_read_seq FROM room_reads WHERE room_id = ? AND user_id = ?", (room_id, user_id)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO room_reads (room_id, user_id, last_read_seq, updated_at) VALUES (?, ?, ?, ?)",
                    (room_id, user_id, position, moments[position - 1]),
                )
                changed += 1
            elif position > row["last_read_seq"]:
                conn.execute(
                    "UPDATE room_reads SET last_read_seq = ?, updated_at = ? WHERE room_id = ? AND user_id = ?",
                    (position, moments[position - 1], room_id, user_id),
                )
                changed += 1
    return changed


def _add_family_links(conn: sqlite3.Connection) -> int:
    ids = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM users")}
    added = 0
    for first, second, kind in FAMILY_LINKS:
        if first in ids and second in ids:
            added += conn.execute(
                "INSERT OR IGNORE INTO family_links (from_user_id, to_user_id, kind, created_at) VALUES (?, ?, ?, ?)",
                (ids[first], ids[second], kind.value, FAMILY_LINKS_AT),
            ).rowcount
    return added


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="流し終えた物語に、あとから当てる直し。")
    parser.add_argument("action", choices=("judge", "apply"))
    parser.add_argument("database", type=Path)
    parser.add_argument("--no-family", action="store_true", help="家系図の関係を足さない（2026年の控え）")
    args = parser.parse_args(argv)
    if "mine" in {part.lower() for part in args.database.resolve().parts}:
        print("自分用の記録は扱いません。", file=sys.stderr)
        return 1
    conn = db.connect(args.database)
    conn.isolation_level = None
    try:
        if args.action == "judge":
            result = judge(conn)
            DECISIONS.parent.mkdir(parents=True, exist_ok=True)
            DECISIONS.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            print(f"{result['judged']} 件を判断、外す {len(result['removed'])} 件、失敗 {result['failed']} 件、${result['usd']}")
        else:
            result = apply(conn, json.loads(DECISIONS.read_text(encoding="utf-8")), family=not args.no_family)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            print(json.dumps(result, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
