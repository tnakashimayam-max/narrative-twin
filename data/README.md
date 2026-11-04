# 架空データ

人物と記録はすべて架空です。実在の人に似せていません。写真の扱いは下の「デモの写真」にあります。

## 中身と量

| 場所 | 中身 | 量 | Git |
|---|---|---|---|
| `data/trial/` | 架空ログの小さな試しの結果（2026年の4月から12月、会話40回、一時のデータベース、測った結果） | 会話40回、言明195件、解釈551件。測った結果は `docs/tools.md` にある | 入れない |
| `data/story/log_YYYY.json` | 10年分の架空の会話（2026年4月〜2036年4月） | 387回 | 入れる |
| `data/story/split_YYYY.json` | 会話を言明に分けた AI の結果 | 443単位 | 入れる |
| `data/story/model_YYYY.json` | 固める処理の AI の結果。言明は鍵で指す | 月、年、人生の段階 | 入れる |
| `data/narrative_twin.db`、`data/photos/` | 架空用の起動の保存先。起動すると作られる | 利用者5人、2026年11月3日より前の架空ログ、4人分の顔（8行） | 入れない |
| `data/narrative_twin.db.before_v2` | 表の変更の前に自動で取った控え | 1ファイル | 入れない |
| `data/demo_photos/` | 投稿の写真（AI で作った架空の写真を、撮影の情報を消して整えたもの） | 917枚の予定 | 入れない |
| `data/demo_photos/profile/` | プロフィールの顔（下の「プロフィールの顔」） | 7枚、約1.6MB | 入れない |
| `data/trial/snapshots/` | 架空用のデータベースと写真の控え（下の「「10年後へ進む」の前へ戻す」） | 1回あたり約3MB | 入れない |
| `data/trial/replaced/` | 控えへ戻したときに、戻す前のデータベースと写真を移した先 | 1回あたり約3MB | 入れない |
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

保存した JSON は、AI を呼ばずに読み込めます（`fiction/load.py`）。AI が作った文に英語が1か所混ざっていたので、`log_2036.json` と `split_2036.json` の同じ文を手で直しました（`tests/test_fiction.py` で確かめます）。物語を差し替えるときは `fiction/plan.py` を直し、変わった会話の鍵を JSON から除いて、上を流し直します。プロンプトは `fiction/generate.py` と `app/prompts.py` にあります。

## script.jsonl から記録を作る

決まりは [docs/script-format.md](../docs/script-format.md)、道具は `fiction/run_script.py` です。今ある API を順に呼ぶので、記録は画面から入れたものと同じ処理を通ります。結果は `--folder` の下に入り、`data/narrative_twin.db` と `data/story/` には触れません。

```
python -m fiction.run_script script.jsonl --check
python -m fiction.run_script script.jsonl --folder data/trial/script_run --budget 10
```

試しのファイルは `fiction/sample_script.jsonl`（24行）です。1回流すと約 $0.10 かかります（固める処理を含みます）。

チャットの行は、本物のツインの返事を見ずに書いたものです。流すと、ツインの返事は本物の AI が書きます。ツインの問いに本人の答えが要る場面では、AI が本人の短い受け答えを足します。**AI が足した文は画面の会話にだけ出て、本人の言葉として記録されません。** 言明にならないので、田中さんにも届きません。チャットの行は一字も変えません。

## 「10年後へ進む」の前へ戻す

デモを撮り直すための道具です（`fiction/snapshot.py`）。架空用の保存先（`data/narrative_twin.db` と `data/photos/`）だけを扱います。自分用の記録の置き場は写さず、変えません。場所を指定する引数はありません。

控えは次のときに自動で取ります。`data/trial/snapshots/` に増えていき、前の控えを上書きしません。

| いつ | 控えの種類 | 戻すと |
|---|---|---|
| 架空用のサーバーを初めて起動し、2026年11月3日より前の架空ログを入れた直後 | `first-start` | 誠一さんがまだ何も入れていない2026年の画面 |
| 「10年後へ進む」を押した直後、読み込みの前 | `before-advance` | 押す直前の2026年の画面（入れた言葉、訂正、託す設定が残っている） |
| `python -m fiction.snapshot save` を流したとき | `manual` | 流したときの画面 |

10年後へ進んだあとの状態は控えにしません。

戻し方：

1. 架空用のサーバー（8000番）を、動かしている窓で Ctrl+C で止めます。動いていると、道具は戻さずに止まります。
2. 次の命令を流します。いちばん新しい控えへ戻ります。

   ```
   .venv/Scripts/python -m fiction.snapshot restore
   ```

   別の控えへ戻すときは、一覧で名前を確かめて指定します。

   ```
   .venv/Scripts/python -m fiction.snapshot list
   .venv/Scripts/python -m fiction.snapshot restore --from 控えの名前
   ```

3. サーバーを起動します。

   ```
   .venv/Scripts/python -m app
   ```

4. ブラウザを開き直します。田中さんでログインしたままなら、名前を選ぶ画面に戻ります。田中さんは戻した時点にはいないためです。

プロフィール写真の枠がないころの控えへ戻しても動きます。次の起動で表が変わり、空いた枠に顔が入ります。

戻す前のデータベースと写真は消さず、`data/trial/replaced/` へ移します。「10年後へ進む」をもう一度押すと、その場で入れた言葉の分だけ固める処理が本物の API で流れ、1回あたり約 $0.17 かかりました（`docs/tools.md`）。読み込みそのものは AI を呼びません。

## 2026年分の小さな試しの作り方

| 手順 | 場所 |
|---|---|
| 会話の予定、プロンプト、読み込み、測定のコード | `scripts/trial_log.py` |
| 言明に分けるプロンプト | `app/prompts.py` |
| 読み込みの決まり（`ledger` を通す） | `app/ledger.py`、`app/splitter.py` |
| 検証に使う、人が写っていない画像 | `scripts/make_verify_images.py`（出力は `data/trial/verify/images/`） |

動かし方は `scripts/trial_log.py` の先頭にあります。データは必ず `ledger` を通して入れるので、決まりに合わない行は入らず、数が記録されます。

## デモの写真

投稿の写真は、AI（Codex の image_gen）で作った架空の写真を使います。文章の指示だけで作り、実在の写真を参考にしません。人の顔、文字、看板、ロゴ、表札は写しません（将棋の駒の字を除く）。Git に入れません。プロフィールの7人の顔は、別の扱いです（下の「プロフィールの顔」）。

1. 元の写真をリポジトリの外のフォルダに置きます。ファイル名は写真の番号にします（例：`P20261001-1.jpg`）。形式は JPEG、PNG、WEBP です。
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

## プロフィールの顔

7人の顔は、AI（Codex の image_gen）で作った架空の人物です。作った日は2026年11月4日です。実在の人や写真を元にしていません。人の目で確かめ、リポジトリには入れません。

整え方は上の「デモの写真」と同じで、出力先だけが違います。

```
.venv/Scripts/python -m fiction.prepare_photos 元の写真のフォルダ --output data/demo_photos/profile
```

ファイルと人の対応は `fiction/faces.py` にあります。

| ファイル | 人 | 入るとき |
|---|---|---|
| `01_seiichi.jpg` | 誠一さん | 起動のとき |
| `02_miho.jpg` | 美穂さん | 起動のとき |
| `03_kenta.jpg` | 健太くん | 登録したとき |
| `04_masao.jpg` | 正雄さん | 起動のとき |
| `05_setsuko.jpg` | 節子さん | 起動のとき |
| `06_inokuchi.jpg` | 井ノ口先生 | 登録したとき |
| `07_tanaka.jpg` | 田中さん | 招いたとき |

- 1枚を、その人のアイコンとプロフィール写真の両方に入れます。写真の行とファイルは枠ごとに分けます。
- 入れるのは架空用の起動と、`fiction.run_script` の `--folder` のデータベースだけです。自分用の起動には、同じ名前の人がいても入れません。
- 新しいデータベースでは、最初の控え（`first-start`）より前に入れます。控えにも顔が入ります。
- 空いた枠にだけ入れます。入れ替えた写真は、起動し直しても上書きしません。
- 人は登録の名前か呼ばれ方で引きます。表の名前と同じでなければ入りません。
- `data/demo_photos/profile/` がなければ何も入れず、頭文字の丸のまま動きます。
