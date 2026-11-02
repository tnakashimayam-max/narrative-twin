# 画面とサーバーの取り決め

画面はすべて、この API を通して記録を受け取ります。だれに何を見せるかはサーバーが決めるので、画面の側では絞り込みません。

## 共通

- 送受信は JSON です。項目の名前は英語、決めた言葉の値は日本語です。
- ログイン中の人は cookie で判断します。cookie は JavaScript から読めません。
- エラーの応答は `{"detail": "日本語の説明"}` の形です。
- 言明、便りなどの番号は乱数の整数です（1〜2^53−1）。順番を表さないので、並べ替えに使わず、画面にも見せません。

| 番号 | 意味 |
|---|---|
| 401 | ログインしていない。ログイン画面に戻す |
| 404 | ない、見てよい範囲にない、または本人以外が訂正前の行を開いた。どれかは区別できない |
| 422 | 送った値の形が違う（文字の番号や、大きすぎる番号など） |

## ログイン

| 道 | 内容 | 成功したときの応答 |
|---|---|---|
| `GET /api/users` | ログイン画面に出す名前の一覧 | `{"items": [User, ...]}` |
| `POST /api/login` | `{"user_id": 番号}` を送ると、その人になる | `User` |
| `POST /api/logout` | ログインをやめる | 204（中身なし） |
| `GET /api/me` | ログイン中の人 | `User` |

`User`：`id`、`name`、`role`（本人／家族／縁のある人／専門職）

## 記録

| 道 | 内容 | 成功したときの応答 |
|---|---|---|
| `GET /api/statements` | 見える、いま有効な言明の一覧 | `{"items": [Statement, ...]}` |
| `GET /api/statements/{番号}` | 番号で開く。本人は訂正前の行も開ける。本人以外は、いま有効な行だけ開ける | `Statement` |
| `GET /api/letters` | 見える便りの一覧。新しい順 | `{"items": [Letter, ...]}` |

一覧の並びは、今の様子 → 本人の言葉と本人の訂正 → 家族と縁のある人の証言 → AIの推測 の順です。同じ順位の中では、有効になった日が新しいものを先にします。同じ日の中では作成日時の順で、同じ時刻の中では追記の順です。

一覧には合計や件数の項目がありません。

`Statement`

| 項目 | 内容 |
|---|---|
| `id` | 言明の番号 |
| `source_utterance_ids` | 根拠の発言の番号の配列。専門職の応答にだけ入る |
| `subject_user_id` | 誰についての記録か |
| `speaker_user_id` | 話した人。ツインのときは `null` |
| `speaker_is_twin` | ツインが話したか |
| `source` | 出どころ |
| `confirm_status` | 確認の状態 |
| `body` | 本文 |
| `period` | 時期 |
| `theme` | テーマ |
| `petals` | 花びらの配列（0〜2個）。専門職の応答にだけ入る |
| `share_scope` | 共有範囲 |
| `effective_date` | 有効になった日 |
| `replaces_id` | 置き換えた言明の番号。なければ `null`。本人の応答にだけ入る |
| `created_at` | 作成日時 |

## AI を使う処理

どれも本人だけが使えます。本人以外には 404 を返します。

| 道 | 内容 | 成功したときの応答 |
|---|---|---|
| `POST /api/photo-question` | 本文に写真そのものを送り、`Content-Type` に形式を書く（JPEG、PNG、GIF、WebP。5MB まで） | `{"question": "問いかける文"}` |
| `POST /api/conversations/{番号}/statements` | 会話の発言を言明に分ける。同じ会話は1回だけ | 下の `SplitReport` |
| `POST /api/letters/{番号}/statements` | スレッドの発言を言明に分ける。同じスレッドは1回だけ | 下の `SplitReport` |

エラーの `detail` は、そのまま画面に出せる文です。

| 番号 | 意味 |
|---|---|
| 400 | 写真の形式、大きさ、空 |
| 409 | もう言明に分けてある |
| 503 | AI の呼び出しがうまくいかなかった |

`SplitReport`

| 項目 | 内容 |
|---|---|
| `statement_count` | 保存した言明の数 |
| `interpretation_count` | 保存した解釈の数 |
| `question_count` | 本人に確かめる問いの候補の数 |
| `dropped_question_count` | 形が合わず残さなかった問いの候補の数 |
| `dropped` | 決まりに合わず入らなかった言明。`body`（本文）と `reason`（理由）の配列 |

分けた言明の共有範囲は、すべて「本人だけ」です。

## 評価の質問と正解

`eval/cases.json` を読み書きします。入力の画面は `/eval.html` です。記録ではなく架空の評価の道具なので、ログインを求めません。

| 道 | 内容 | 成功したときの応答 |
|---|---|---|
| `GET /api/eval/cases` | 質問の一覧 | `{"items": [EvalCase, ...]}` |
| `POST /api/eval/cases` | 質問を足す | 201、`EvalCase` |
| `PUT /api/eval/cases/{番号}` | 質問と正解を直す | `EvalCase` |

`EvalCase`：`id`、`question`（質問）、`correct_answer`（正解。空でもよい）、`question_type`（今の考え／変化／知らないこと／一字一句）

`Letter`

| 項目 | 内容 |
|---|---|
| `id` | 便りの番号 |
| `conversation_id` | 元の会話の番号。なければ `null` |
| `body` | 本文 |
| `photo_id` | 写真の番号。なければ `null` |
| `share_with` | 共有の相手（家族／縁のある人まで） |
| `shared_at` | 共有した日時 |
