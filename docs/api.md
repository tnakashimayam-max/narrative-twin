# 画面とサーバーの取り決め

画面はすべて、この API を通して記録を受け取ります。だれに何を見せるかはサーバーが決めるので、画面の側では絞り込みません。

## 共通

- 送受信は JSON です。写真を送るときと受け取るときだけ、中身そのものです。項目の名前は英語、決めた言葉の値は日本語です。
- ログイン中の人は、ヘッダー `X-NT-User` の番号で判断します。画面はウィンドウごとに `sessionStorage` に番号を持ちます。同じブラウザの2つのウィンドウで別の人として使えるようにするためです。
- これは本物の認証ではありません。パスワードのない簡易ログインで、サーバーは 127.0.0.1 だけで待ち受けます。
- エラーの応答は `{"detail": "日本語の説明"}` の形です。`detail` はそのまま画面に出せます。
- 言明、便り、部屋、写真などの番号は乱数の整数です（1〜2^53−1）。順番を表さないので、並べ替えに使わず、画面にも見せません。

| 番号 | 意味 |
|---|---|
| 400 | 写真の形式や大きさ、値の中身が受け付けられない |
| 401 | ログインしていない。ログイン画面に戻す |
| 404 | ない、または見てよい範囲にない。どちらかは区別できない |
| 409 | もういる人の名前、すでにいる本人など、今の状態と合わない |
| 422 | 送った値の形が違う |
| 503 | AI の呼び出しがうまくいかなかった。もう一度送れる |

## 起動の種類とログイン

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/mode` | 起動の種類。ログイン不要 | `{"mine": 真偽, "label": "架空データ" か "自分の記録で試用中", "needs_self": 本人の登録が要るか}` |
| `GET /api/users` | ログイン画面に出す名前。はずした人と亡くなった方は出ない。ログイン不要 | `{"items": [LoginName, ...]}` |
| `POST /api/login` | `{"user_id": 番号}`。その人として使えるかを確かめる | `Person` |
| `GET /api/me` | ログイン中の人 | `Person` |
| `POST /api/self` | 本人の登録。本人がまだいないときだけ。ログイン不要 | 201、`Person` |

`LoginName`：`id`、`name`、`called_name`、`role`

`Person`（本人以外に返す形）：`id`、`name`、`called_name`（呼ばれ方）、`role`、`photo_id`

`PersonDetail`（本人だけに返す形）：`Person` に、`relation`（続柄）、`residence`（同居／近く／遠く）、`birth_year`、`gender`（男性／女性／不明）、`is_deceased`、`memo` を足したもの

## 人の登録と管理

登録、立場の変更、はずすことができるのは本人だけです。本人以外には 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/people` | はずしていない人の一覧（本人を含む） | `{"items": [PersonDetail, ...]}` |
| `POST /api/people` | 人を登録する。`name`、`called_name`、`role`、`relation`、`residence`、`birth_year`、`gender`、`is_deceased`、`memo`、`photo_id` | 201、`PersonDetail` |
| `PUT /api/people/{番号}` | 本人はすべての項目を直せる。本人以外は自分の `name`、`called_name`、`photo_id` だけ | `PersonDetail`（本人以外は `Person`） |
| `GET /api/people/{番号}/role-preview?role=家族` | 立場を変えると見えるようになるものを言葉で返す | `{"sees": [文], "stops": [文]}` |
| `POST /api/people/{番号}/remove` | はずす。消さない。過去の発言は残る。本人ははずせない | 204 |

## 写真

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/photos?kind=会話` | 本文に写真そのものを送る。`kind` は 会話／部屋／便り／プロフィール。会話と便りは本人だけ | 201、`{"id": 番号}` |
| `GET /api/photos/{番号}` | 写真の中身（JPEG）。種類、持ち主、説明は返さない | 写真 |

- 画面で長い辺を1568pxほどに縮めてから送ります。サーバーでも開き直し、1568px以下の JPEG にして、撮影の情報を落とします。10MB までです。
- 見てよい人は、持ち主、その写真を指す部屋の人、その写真を指す便りの共有範囲の人、そのプロフィール写真の人がはずされていないときのログイン中の人です。それ以外は 404 です。
- AI に送るのは、ツインとの部屋に本人が送った「会話」の写真だけです。

## トーク

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/rooms` | 見える部屋の一覧。本人にはいちばん上にツインとの部屋 | `{"items": [Room, ...]}` |
| `POST /api/rooms` | 部屋を作る。`{"kind": "1対1" か "グループ", "member_ids": [番号], "name": "名前"}`。本人だけ | 201、`Room` |
| `GET /api/rooms/{番号}/messages?after=数` | 部屋の発言。`after` より後だけを返す。部屋の人以外は 404 | `RoomView` |
| `POST /api/rooms/{番号}/messages` | 発言する。`{"text": 文, "photo_id": 番号, "client_key": 文}` | `{"message": Message, "replies": [Message, ...]}` |
| `POST /api/rooms/{番号}/close` | ツインとの部屋で「今日はここまで」。本人だけ | `{"closed": 真偽, "replies": [Message, ...], "conversation_id": 番号}` |
| `POST /api/rooms/{番号}/twin` | グループにツインを入れる、はずす。`{"present": 真偽}`。本人だけ | `RoomView` |
| `POST /api/questions/{番号}/rest` | 問いの話題をお休みにする。本人だけ | 204 |

- `client_key` は画面が作る文字列です。同じ `client_key` で送り直しても、発言は増えません。発言が保存されたあとに返事だけが失敗したときは、送り直すと返事だけを作り直します。
- ツインとの部屋で AI が失敗すると 503 を返します。そのときも本人の発言は保存されています。

`Room`：`id`、`kind`、`name`、`members`（`Person` の配列）、`twin_present`、`last_text`（最後の発言の冒頭）、`last_at`。未読の数は持ちません。

`RoomView`：`room`（`Room`）、`items`（`Message` の配列）、`people`（`Person` の配列）、`last_seq`、`thinking`（ツインが考えているか）、`shareable_conversation_id`（共有できる直前の会話の番号。なければ `null`）

`Message`

| 項目 | 内容 |
|---|---|
| `id` | 番号 |
| `seq` | 部屋の中の順番。`after` に渡す |
| `kind` | 発言／お知らせ |
| `sender_user_id` | 話した人。ツインのときは `null` |
| `is_twin` | ツインが話したか |
| `text` | 本文。写真だけのときは `null` |
| `photo_id` | 写真の番号。なければ `null` |
| `question_id` | ツインの問いのとき、問いの記録の番号 |
| `question_rested` | その問いをお休みにしたか |
| `sent_at` | 日時 |
| `client_key` | 送ったときの `client_key`。本人の発言だけ |

## これまでの記録

本人だけが開けます。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/archive?before=日付` | 日付ごとの会話、写真、便りを新しい順に。1回に30日分 | `{"days": [{"date": 日付, "conversations": [{"id": 番号, "items": [Message, ...]}], "letters": [Letter, ...]}], "next_before": 日付か null}` |

## 便りとタイムライン

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/conversations/{番号}/letter-draft` | 便りの下書きを AI が作る。保存しない。本人だけ | `{"body": 文, "photo_ids": [番号]}` |
| `POST /api/letters` | 「この内容で共有する」。`conversation_id`、`body`、`photo_id`、`share_with`（家族／縁のある人まで）。本人だけ | 201、`TimelineLetter` |
| `GET /api/timeline` | 見える便りを新しい順に。スレッドつき | `{"items": [TimelineLetter, ...], "people": [Person, ...]}` |
| `POST /api/letters/{番号}/comments` | コメントする。`{"text": 文, "client_key": 文}`。便りを見られない人は 404 | 201、`Message` |
| `GET /api/letters` | 見える便りの一覧（スレッドなし） | `{"items": [Letter, ...]}` |

`TimelineLetter`：`Letter` の項目に、`thread`（`Message` の配列。吹き出しはトークと同じ形）を足したもの

`Letter`：`id`、`conversation_id`、`body`、`photo_id`、`share_with`、`shared_at`

便りの相手を選ぶことと、言明の共有範囲を広げることは別のことです。言明はすべて「本人だけ」のままです。

## 言明

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/statements` | 見える、いま有効な言明の一覧 | `{"items": [Statement, ...]}` |
| `GET /api/statements/{番号}` | 番号で開く。本人は訂正前の行も開ける。本人以外は、いま有効な行だけ開ける | `Statement` |
| `POST /api/conversations/{番号}/statements` | 会話の、まだ分けていない発言を分ける。ふだんは裏で動く。本人だけ | `SplitReport` |
| `POST /api/letters/{番号}/statements` | スレッドの、まだ分けていない発言を分ける。ふだんは裏で動く。本人だけ | `SplitReport` |

一覧の並びは、今の様子 → 本人の言葉と本人の訂正 → 家族と縁のある人の証言 → AIの推測 の順です。同じ順位の中では、有効になった日が新しいものを先にします。同じ日の中では作成日時の順で、同じ時刻の中では追記の順です。一覧には合計や件数の項目がありません。

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

`SplitReport`：`statement_count`、`interpretation_count`、`question_count`、`dropped_question_count`、`dropped`（`body` と `reason` の配列）

## つながりの地図と家系図

本人だけが開けます。本人以外には 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/map` | 地図の中身 | `{"self": PersonDetail, "people": [{"person": PersonDetail, "link": MapLink か null}], "places": [{"place": MapPlace, "link": MapLink か null}]}` |
| `POST /api/map/places` | 病院などのつながりを足す。`{"name": 文, "kind": 文}` | 201、`MapPlace` |
| `POST /api/map/places/{番号}/remove` | つながりをはずす | 204 |
| `PUT /api/map/links` | 線を決める。`{"user_id": 番号}` か `{"place_id": 番号}` に、`strength`（強い／ふつう／弱い／ストレス）と `direction`（本人から／相手から／お互い／なし） | `MapLink` |
| `GET /api/genogram` | 家系図の中身 | `{"people": [PersonDetail と generation], "links": [FamilyLink], "self_id": 番号}` |
| `POST /api/family-links` | 関係を足す。`{"kind": "親子" か "夫婦", "from_user_id": 親か一方, "to_user_id": 子か他方}` | 201、`FamilyLink` |
| `DELETE /api/family-links/{番号}` | 関係を消す。間違えて足したときのため | 204 |
| `GET /api/people/candidates` | 話に出てきた人の候補 | `{"items": [Candidate, ...]}` |
| `POST /api/people/candidates/{番号}/decide` | `{"decision": "登録する" か "ちがう" か "今はいい", "role": 立場}` | `{"person": PersonDetail か null}` |

`Candidate`：`id`、`name`、`relation`、`relative_kind`、`related_to_name`、`is_deceased`、`gender`、`evidence`（根拠の発言の本文）、`evidence_date`

線の強さとストレスは AI から提案しません。人の仲を AI が決めて記録に残さないためです。

## 評価の質問と正解

`eval/cases.json` を読み書きします。入力の画面は `/eval.html` です。記録ではなく架空の評価の道具なので、ログインを求めません。

| 道 | 内容 | 成功したときの応答 |
|---|---|---|
| `GET /api/eval/cases` | 質問の一覧 | `{"items": [EvalCase, ...]}` |
| `POST /api/eval/cases` | 質問を足す | 201、`EvalCase` |
| `PUT /api/eval/cases/{番号}` | 質問と正解を直す | `EvalCase` |

`EvalCase`：`id`、`question`（質問）、`correct_answer`（正解。空でもよい）、`question_type`（今の考え／変化／知らないこと／一字一句）

## 検証のための道（架空用の起動だけ）

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/dev/date` | `{"date": "2026-11-05"}` で、サーバーの今日をその日にする。`{"date": null}` で戻す | `{"today": 日付}` |

日を変えた会話を確かめるための道です。自分用の起動では 404 を返します。
