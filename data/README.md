# 架空データ

人物と記録はすべて架空です。実在の人に似せていません。写真の扱いは下の「デモの写真」にあります。

## 中身と量

| 場所 | 中身 | 量 | Git |
|---|---|---|---|
| `data/trial/` | 架空ログの小さな試しの結果（2026年の4月から12月、会話40回、一時のデータベース、測った結果） | 会話40回、言明195件、解釈551件。測った結果は `docs/tools.md` にある | 入れない |
| `data/story/log_YYYY.json` | 10年分の架空の会話（2026年4月〜2036年4月） | 387回 | 入れる |
| `data/story/split_YYYY.json` | 会話を言明に分けた AI の結果 | 443単位 | 入れる |
| `data/story/model_YYYY.json` | 固める処理の AI の結果。言明は鍵で指す | 月、年、人生の段階 | 入れる |
| `data/narrative_twin.db`、`data/photos/` | 架空用の起動の保存先。起動すると作られる | 利用者5人と、2026年11月3日より前の架空ログ | 入れない |
| `data/demo_photos/` | デモに映す写真（撮影の情報を消したもの） | 約10枚 | 入れない |
| `data/mine/` | 開発者が自分の記録で試すときの保存先 | 公開しない | 入れない |

## 10年分の架空ログの作り方

物語は `fiction/plan.py` にあります。乱数を使わないので、同じ予定が毎回できます。泊まりの話と病名は、物語に入れません。

```
python -m fiction.generate log-submit       # 会話をバッチで作る（Haiku 5.5）
python -m fiction.generate log-collect
python -m fiction.generate split-submit     # 言明に分ける（Haiku 5.5）
python -m fiction.generate split-collect
python -m fiction.consolidate_story         # 固める（月は Sonnet 5.5、年と人生の段階は Opus 5.5）
```

保存した JSON は、AI を呼ばずに読み込めます（`fiction/load.py`）。物語を差し替えるときは `fiction/plan.py` を直し、変わった会話の鍵を JSON から除いて、上を流し直します。プロンプトは `fiction/generate.py` と `app/prompts.py` にあります。

## 2026年分の小さな試しの作り方

| 手順 | 場所 |
|---|---|
| 会話の予定、プロンプト、読み込み、測定のコード | `scripts/trial_log.py` |
| 言明に分けるプロンプト | `app/prompts.py` |
| 読み込みの決まり（`ledger` を通す） | `app/ledger.py`、`app/splitter.py` |
| 検証に使う、人が写っていない画像 | `scripts/make_verify_images.py`（出力は `data/trial/verify/images/`） |

動かし方は `scripts/trial_log.py` の先頭にあります。データは必ず `ledger` を通して入れるので、決まりに合わない行は入らず、数が記録されます。

## デモの写真

記録と人物は架空です。画面に映る約10枚の写真だけ、開発者が用意した実物を使います。人が写っておらず、場所が分からないものに限ります。写真はリポジトリに入れません。ほかの回は説明文だけの記録にします。

1. 元の写真をリポジトリの外のフォルダに置きます。ファイル名は写真の番号にします（例：`01_engawa.jpg`）。形式は JPEG、PNG、WEBP です。
2. 写っているものを人の目で確かめます。プログラムでは確かめられません。
   - 人の顔や姿（窓やガラスに映ったものも含む）
   - 看板、表札、住所、郵便物、車の番号、建物の名前
   - 手書きの文字、画面に映った内容、事業所の掲示
3. 撮影の情報を消します。

   ```
   .venv/Scripts/python -m fiction.prepare_photos 元の写真のフォルダ
   ```

   撮影の位置、日時、カメラの情報、コメントを消し、長い辺を1568px以下にした JPEG を `data/demo_photos/` に出力します。書き出したあとに開き直し、情報が残っていないことを確かめます。読めない写真と、確かめに通らない写真は出力しません。

4. 出力した写真を開き、向きと写っているものをもう一度確かめます。アプリには、出力した写真だけを入れます。
