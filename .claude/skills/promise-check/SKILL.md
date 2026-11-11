---
name: promise-check
description: ツインの約束の点検。本人の言葉の扱い、共有範囲の絞り込み、出どころの区別に関わるコードを直したあとや、コミットの前に、テストと定められた確認を決まった順に流して結果を報告する。
---

# ツインの約束の点検

目的：本人の言葉と同意に関わる決まりが壊れていないことを、毎回同じ順で確かめる。

## 手順

1. 約束のテストを流す。
   ```
   PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest -q tests/test_ledger.py tests/test_consent.py tests/test_ids.py tests/test_verbatim.py tests/test_splitter.py
   ```
2. 全部のテストを流す。
   ```
   PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest -q
   ```
3. 次をコードで確かめる。
   - 言明と発言の表を書き換える処理がない（`UPDATE`、`DELETE`、`REPLACE` が `app/` にない）
   - 本人以外に返す項目が `app/consent.py` の `_HIDDEN_FIELDS` のとおりである
   - `anthropic` を読み込むのが `app/ai.py` だけである
4. 変更したファイルに、CLAUDE.md の「作らないもの」にあたるものが入っていないかを見る。

## 報告の形

- テストの件数と結果
- 手順3の3項目の結果
- 問題があれば、ファイルと行、何が違うか、どう直すとよいか

確かめられなかった項目は、確かめられなかったと書く。
