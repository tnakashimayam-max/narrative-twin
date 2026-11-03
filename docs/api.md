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
| 400 | 写真の形式や大きさ、名前など、値の中身が受け付けられない |
| 401 | ログインしていない。ログイン画面に戻す |
| 404 | ない、または見てよい範囲にない。どちらかは区別できない |
| 409 | すでにいる本人など、今の状態と合わない |
| 422 | 送った値の形が違う |
| 503 | AI の呼び出しがうまくいかなかった。もう一度送れる |

## 起動の種類とログイン

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/mode` | 起動の種類。ログイン不要 | `{"mine": 真偽, "label": "架空データ" か "自分の記録で試用中", "needs_self": 本人の登録が要るか}` |
| `GET /api/users` | ログイン画面に出す名前。はずした人と亡くなった方は出ない。ログイン不要 | `{"items": [LoginName, ...]}` |
| `POST /api/login` | `{"user_id": 番号}`。その人として使えるかを確かめる | `Person` |
| `GET /api/me` | ログイン中の人 | `Person` に `can_invite_professional`（専門職を招けるか）を足したもの |
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
| `PUT /api/people/{番号}` | 本人はすべての項目を直せる。本人以外は自分の `name`、`called_name`、`photo_id` だけ。`photo_id` に指定できるのは、操作する人が送ったプロフィールの写真だけ | `PersonDetail`（本人以外は `Person`） |
| `GET /api/people/{番号}/role-preview?role=家族` | 立場を変えると見えるようになるものを言葉で返す | `{"sees": [文], "stops": [文], "same": [文]}` |
| `POST /api/people/{番号}/remove` | はずす。消さない。過去の発言は残る。本人ははずせない | 204 |

次の2つは本人以外も使えます。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/people/choices` | 部屋の相手を選ぶ一覧。ログインできる人だけ。自分は入らない | `{"items": [Person, ...]}` |
| `POST /api/people/professional` | 専門職を招く。`name`、`called_name`、`relation`。本人と、立場が家族の託された人だけ。架空用の起動では2036年1月1日以降だけ。それ以外は 404 | 201、`Person` |

架空用の起動では、2036年1月1日より前の日付のとき、専門職はログイン一覧と相手の一覧に出ず、ログインもできません。自分用の起動では日付で切り替えません。

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
| `POST /api/rooms` | 部屋を作る。`{"kind": "1対1" か "グループ", "member_ids": [番号], "name": "名前"}`。作った人は必ず入る | 201、`Room` |
| `GET /api/rooms/{番号}/messages?after=数` | 部屋の発言。`after` より後だけを返す。部屋の人以外は 404 | `RoomView` |
| `POST /api/rooms/{番号}/messages` | 発言する。`{"text": 文, "photo_id": 番号, "client_key": 文}` | `{"message": Message, "replies": [Message, ...]}` |
| `POST /api/rooms/{番号}/close` | ツインとの部屋で「今日はここまで」。本人だけ | `{"closed": 真偽, "replies": [Message, ...], "conversation_id": 番号}` |
| `POST /api/rooms/{番号}/twin` | グループにツインを入れる、はずす。`{"present": 真偽}`。部屋にいる本人と、立場が家族の託された人だけ。本人がいない部屋は 400 | `RoomView` |
| `POST /api/rooms/{番号}/members/{番号}/remove` | グループから人をはずす。作った人と、部屋にいる本人だけ。本人はほかの人にはずされない | 204 |
| `POST /api/rooms/{番号}/leave` | 自分でグループを出る。本人が出たらツインもはずれる | 204 |
| `POST /api/rooms/{番号}/read` | 読んだ位置を進める。`{"seq": 数}`。画面が見えているときに送る。ツインとの部屋は 400 | 204 |
| `POST /api/questions/{番号}/rest` | 問いの話題をお休みにする。本人だけ | 204 |
| `GET /api/resting-themes` | お休みにしている話題。本人だけ | `{"items": [テーマ, ...]}` |
| `POST /api/resting-themes/resume` | 話題のお休みをやめる。`{"theme": テーマ}`。本人だけ | 204 |

- `client_key` は画面が作る文字列です。同じ `client_key` で送り直しても、発言は増えません。発言が保存されたあとに返事だけが失敗したときは、送り直すと返事だけを作り直します。
- ツインとの部屋で AI が失敗すると 503 を返します。そのときも本人の発言は保存されています。

`Room`：`id`、`kind`、`name`、`members`（`Person` の配列）、`twin_present`、`last_text`（最後の発言の冒頭）、`last_at`、`can_manage_twin`（ツインを操作できるか）、`can_remove_members`（人をはずせるか）、`unread_count`（未読の数。ツインとの部屋にはない）

`RoomView`：`room`（`Room`）、`items`（`Message` の配列）、`people`（`Person` の配列）、`last_seq`、`thinking`（ツインが考えているか）、`shareable_conversation_id`（共有できる直前の会話の番号。なければ `null`）、`first_unread_seq`（最初の未読の順番。なければ `null`）、`others_read_seqs`（部屋にいるほかの人の読んだ位置を小さい順に並べた配列。名前は返さない）。最後の2つはツインとの部屋にはない

`Message`

| 項目 | 内容 |
|---|---|
| `id` | 番号 |
| `seq` | 部屋の中の順番。`after` に渡す |
| `is_question` | ツインの問いか |
| `kind` | 発言／お知らせ |
| `sender_user_id` | 話した人。ツインのときは `null` |
| `is_twin` | ツインが話したか |
| `text` | 本文。写真だけのときは `null` |
| `photo_id` | 写真の番号。なければ `null` |
| `question_id` | ツインの問いのとき、問いの記録の番号。本人の応答にだけ入る |
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
| `GET /api/timeline` | 見える便りを新しい順に。スレッドつき | `{"items": [TimelineLetter, ...], "people": [Person, ...], "self_id": 本人の番号}` |
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
| `GET /api/genogram` | 家系図の中身。`links` は図に出る関係、`all_links` は登録した関係のすべて | `{"people": [PersonDetail と generation], "links": [FamilyLink], "all_links": [FamilyLink], "self_id": 番号}` |
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

## 確認と託す設定

本人だけが使えます。本人以外には 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/review` | 今日うかがったこと。まだ確かめていない本人の言葉と AI の推測を、推測を先にして8件まで | `{"items": [ReviewItem, ...]}` |
| `POST /api/statements/{番号}/confirm` | 「そうです」。確かめた行を足す | `ReviewItem` |
| `POST /api/statements/{番号}/correct` | 「ちがいます」。`{"text": 文}`。本人の訂正の行を足す。元の行は履歴に残る | `ReviewItem` |
| `POST /api/statements/{番号}/rest` | 「今は話したくない」。範囲を「本人だけ」に戻した行を足す | `ReviewItem` |
| `GET /api/handover` | 託す設定 | `Handover` |
| `PUT /api/handover` | `{"delegate_ids": [番号], "themes": [テーマ]}`。託せるのは家族だけ。選んだテーマの今の言葉に、範囲を広げた行を足す。はずしたテーマの言葉は「本人だけ」に戻す | `Handover` |

すでに置き換えられた行を指すと 409 を返します。

`ReviewItem`：`id`、`body`、`source`、`effective_date`、`is_guess`

`Handover`：`themes`、`all_themes`、`delegate_ids`、`family`（`id`、`name`）、`decided_on`

## 意思モデルとツインの色

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/model/consolidate` | 固める処理を流す。言明が変わった月と、その年と人生の段階だけを動かす。本人だけ | `{"months": [文], "elements": 数}` |
| `GET /api/twin/appearance` | 今のツインの色。花びらの順に、濃さの言葉（なし／うすい／中くらい／こい）を返す。名前と数は返さない | `{"levels": [文, ...]}` |
| `GET /api/twin/growth` | 1年ごとのツインの色 | `{"items": [{"year": 数, "levels": [文, ...]}]}` |

## 支援者の画面

招かれた専門職と、立場が家族の託された人が使えます。それ以外には 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/advocacy/ask` | ツインに聞く。`{"question": 文, "client_key": 文}`。2000字まで。同じ `client_key` で送り直しても答えは増えない | `{"turn": Turn}` |
| `GET /api/advocacy/turns` | この人の問いと答え。ほかの人のやり取りは返さない | `{"items": [Turn, ...]}` |
| `GET /api/advocacy/overview` | 今の様子と、本人が範囲を決めた日 | `{"self_name": 文, "now": [Now, ...], "decided_on": 日付, "themes": [テーマ]}` |
| `GET /api/advocacy/elements` | 見える意思モデルの要素。花びらは専門職にだけ返す | `{"items": [Element, ...]}` |
| `GET /api/advocacy/elements/{番号}` | 要素の根拠を、言明と発言の原文までたどる。見えない要素は 404 | `{"element_id": 番号, "body": 文, "kind": 文, "evidence": [Quote に utterances を足したもの]}` |
| `POST /api/observations` | 今の様子を入れる。`{"text": 文, "theme": テーマ}` | 201、`{"statement_id": 番号}` |
| `GET /api/statements/{番号}/interpretations` | 言明の4つの視点の仮説。専門職だけ | `{"items": [{"perspective", "body", "confidence", "caution", "status"}]}` |

`Turn`：`id`、`created_at`、`question`、`notice`（原則に触れる頼みへの決まった1文。なければ `null`）、`lead`（ツインの短い案内。なければ `null`）、`now`、`self_words`、`testimony`、`guesses`（それぞれ `Quote` の配列）、`not_yet`（本人の言葉がないときの「まだ聞けていません。」。あれば `null`）、`estimate`（`Estimate`。なければ `null`）、`elements`

`Quote`：`statement_id`、`body`（記録の本文のまま）、`lead_in`（プログラムが付ける前置き）、`date`、`source`、`speaker`、`confirm`

`Estimate`：`text`、`evidence`、`counter`（`Quote` の配列）、`confidence`（とても低い／低い／中くらい）、`how_to_confirm`

## 10年後へ進む（架空用の起動だけ）

自分用の起動では 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/dev/story` | 状態。本人だけ | `{"future_loaded": 真偽, "ready": 真偽, "working": 真偽, "today": 日付}` |
| `POST /api/dev/advance` | 架空ログを読み込み、2036年5月1日へ進む。本人だけ。託す範囲が決まっていなければ 400。2回押しても2回読み込まない | 上と同じ |
