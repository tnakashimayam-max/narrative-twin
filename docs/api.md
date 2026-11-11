# 画面とサーバーの取り決め

画面はすべて、この API を通して記録を受け取ります。だれに何を見せるかはサーバーが決めるので、画面の側では絞り込みません。

## 共通

- 送受信は JSON です。写真を送るときと受け取るときだけ、中身そのものです。項目の名前は英語、決めた言葉の値は日本語です。
- ログイン中の人は、ヘッダー `X-NT-User` の番号で判断します。画面はウィンドウごとに `sessionStorage` に番号を持ちます。同じブラウザの2つのウィンドウで別の人として使えるようにするためです。
- 新規のデータを使っている間は、ヘッダー `X-NT-Data` にその新規の番号を付けます（下の「新規のデータ」）。画面はウィンドウごとに `sessionStorage` の `nt_data` に持ちます。写真の読み込みにも付けます。
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
| 503 | AI の呼び出しがうまくいかなかった。もう一度送れる。ツインとの部屋で API キーがないときの `detail` は「ツインと話すには API キーが要ります。」で、発言は保存される（自分用の起動の文は変えない） |

## 新規のデータ

デモの10年分とは別のデータを、入口から何度でも作れます。1つのデータに本人は1人です。作ったデータは消えません。`kind` が `mine` の起動には、この節の道もボタンもありません。

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/fresh` | 新規で始める。本人がまだいない新規があれば、それを返す。なければ作る。ログイン不要。`X-NT-Data` は見ない | 201、`{"id": 新規の番号}` |
| `GET /api/fresh` | 本人を登録した新規の一覧。新しい順。ログイン不要。`X-NT-Data` は見ない | `{"items": [{"id": 新規の番号, "name": 本人の名前, "created_at": 作った日時}, ...]}` |

新規の番号は `20261105-171500-123456` の形の文字列です。`/api/fresh` のほかの道に `X-NT-Data` を付けると、その新規のデータへ届きます。形が違う値と、ない番号は 404 です。

画面の決まりです。

| 場面 | すること |
|---|---|
| `kind` が `demo` か `fresh` の間 | どの画面にも「ログイン画面」のボタンを出す。「はじめまして」にも出す |
| 「ログイン画面」を押す | `nt_user_id` と `nt_data` を両方消し、最初からやり直す。デモの入口に戻る |
| 入口（`kind` が `demo`） | 「新規で始める」と、`GET /api/fresh` の一覧を出す |
| 「新規で始める」を押す | `POST /api/fresh` の `id` を `nt_data` に入れ、最初からやり直す。`needs_self` が真なので「はじめまして」が出る |
| 一覧の新規を押す | `id` を `nt_data` に入れ、最初からやり直す。その新規の名前の一覧が出る |
| `nt_data` を付けた `GET /api/mode` が 404 | `nt_data` を消し、最初からやり直す |
| `kind` が `fresh` で `ai_ready` が偽 | 「ツインと話すには API キーが要ります」と出す。エラーではなく案内として出す。登録とホームは使える |

`kind` が `fresh` のときも、画面の隅の表示は `label` をそのまま出します。

## ツインにだけ話した言葉

`GET /api/statements`、`GET /api/statements/{番号}`、その解釈、`GET /api/advocacy/elements/{番号}` の根拠は、本人以外には、ツインにだけ話した本人の言葉を返しません。代弁の中で引いた言葉だけが、本文のまま見えます。

セルフがあるとき、代弁の理由の `elements` はピースを指します。`element_id` は `null` で、`kind` は区画の名前です。

## スタンプ

発言の `text` に「（スタンプ：太陽）」の形が入ることがあります。種類は太陽、星、花、月です。保存するのはこの文字のままで、画面が絵にします。送るボタンはツインのいない部屋にだけ出します。

## 起動の種類とログイン

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/mode` | 起動の種類。ログイン不要 | `{"mine": 自分用の起動か, "kind": "demo" か "fresh" か "mine", "label": "架空データ" か "新規のデータ" か "自分の記録で試用中", "needs_self": 本人の登録が要るか, "ai_ready": API キーが見つかるか}`。`ai_ready` はキーの有無だけで、通信はしない |
| `GET /api/users` | ログイン画面に出す名前。はずした人と亡くなった方は出ない。ログイン不要 | `{"items": [LoginName, ...]}` |
| `POST /api/login` | `{"user_id": 番号}`。その人として使えるかを確かめる | `Person` |
| `GET /api/me` | ログイン中の人 | `Person` に `can_invite_professional`（専門職を招けるか）、`can_use_supporter_screen`（「ツインに聞く」を使えるか）、`can_check_letters`（手紙を確かめられるか。本人と託された家族。画面はこれが真の人だけ `GET /api/letters/pending` を呼ぶ）、`tools`（使えるツール。`Tool` の配列）を足したもの。`tools` は、託された専門職で、職種が使えるものだけ。美穂さんと、託されていない専門職は空 |
| `POST /api/self` | 本人の登録。本人がまだいないときだけ。ログイン不要 | 201、`Person` |

`LoginName`：`id`、`name`、`called_name`、`role`、`photo_id`（入口の札に出すアイコン。架空用の起動だけ。自分用の起動ではいつも `null`）。`profile_photo_id` は返しません

`Person`（本人以外に返す形）：`id`、`name`、`called_name`（呼ばれ方）、`role`、`profession`（職種。専門職で職種がある人だけ。ケアマネジャー／主治医／歯科医／ヘルパー／訪問看護／包括支援センター／商店。ほかは `null`）、`photo_id`（丸く出すアイコン）、`profile_photo_id`（大きく出すプロフィール写真）。写真がなければ `null`。はずした人はどちらも `null`

`Tool`：`id`（`voice`／`plan`／`sheet`）、`name`（ツインの考え／ケアプラン案／1枚の紙）。

`LoginName` にも `profession` が付く。入口で専門職を職種つきで分けて出すため。

1枚の紙（`/api/advocacy/sheet`）とケアプラン案（`/api/advocacy/plans` の全部）は、`tools` にある人だけが使える。それ以外の人には 404 を返す。ツインの考え（`/api/advocacy/turns/{番号}/advocate`）、`/api/advocacy/ask`、`/api/advocacy/chat` は、託された家族と託された専門職のだれでも使える（`can_use_supporter_screen` が true の人）。チャットのふだんの問いには、ツインの考え（代弁）で答える。紙と案の言葉は、ツールを使えない人にはふつうの問いとして答える。託された家族の代弁は、理由ごとの `evidence` に、もとにしたピースを支える本人の言葉をプログラムが1件足す（ツインにだけ話した言葉も使う）。

託されていない専門職に見えるのは、入っている部屋の話だけ。タブはホームとトークの2つ。

`/api/people` と地図の人（`/api/map`）にも `profession` が付く。

`PersonDetail`（本人だけに返す形）：`Person` に、`relation`（続柄）、`residence`（同居／近く／遠く）、`birth_year`、`gender`（男性／女性／不明）、`is_deceased`、`memo` を足したもの

## 人の登録と管理

登録、立場の変更、はずすことができるのは本人だけです。本人以外には 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/people` | はずしていない人の一覧（本人を含む） | `{"items": [PersonDetail, ...]}` |
| `POST /api/people` | 人を登録する。`name`、`called_name`、`role`、`relation`、`residence`、`birth_year`、`gender`、`is_deceased`、`memo`、`photo_id`、`profile_photo_id` | 201、`PersonDetail` |
| `PUT /api/people/{番号}` | 本人はすべての項目を直せる。本人以外は自分の `name`、`called_name`、`photo_id`、`profile_photo_id` だけ。2つの枠に指定できるのは、操作する人が送ったプロフィールの写真だけ（それ以外は 404）。同じ写真の行を両方の枠に指すと 400 | `PersonDetail`（本人以外は `Person`） |
| `GET /api/people/{番号}/role-preview?role=家族` | 立場を変えると見えるようになるものを言葉で返す | `{"sees": [文], "stops": [文], "same": [文]}` |
| `POST /api/people/{番号}/remove` | はずす。消さない。過去の発言は残る。本人ははずせない | 204 |

次の2つは本人以外も使えます。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/people/choices` | 部屋の相手を選ぶ一覧。ログインできる人だけ。自分は入らない | `{"items": [Person, ...]}` |
| `GET /api/people/professions` | 招く画面の職種の選択肢。ログインが要る | `{"items": [{"name": "ケアマネジャー", "tools": [Tool, ...]}, {"name": "主治医", "tools": []}, ...]}`。主治医のあとに、歯科医、ヘルパー、訪問看護、包括支援センター、商店が同じ形（`tools` は空）で続く。ツールを使えるのはケアマネジャーだけで、ほかの職種は託されたらケア会議を使える |
| `POST /api/people/professional` | 専門職を招く。`name`、`called_name`、`relation`（職種。`/api/people/professions` の `name` を送る）。もう登録してある、託されていない専門職と同じ名前なら、新しく作らず託す。本人と、立場が家族の託された人だけ。架空用の起動では2036年1月1日以降だけ。それ以外は 404 | 201、`Person` |

架空用の起動では、2036年1月1日より前の日付のとき、専門職はログイン一覧と相手の一覧に出ず、ログインもできません。自分用の起動では日付で切り替えません。

## 写真

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/photos?kind=会話` | 本文に写真そのものを送る。`kind` は 会話／部屋／便り／プロフィール。会話と便りは本人だけ | 201、`{"id": 番号}` |
| `GET /api/photos/{番号}` | 写真の中身（JPEG）。種類、持ち主、説明は返さない。ログインなしで読めるのは「入口の顔」だけ。それ以外（プロフィール写真の枠を含む）をログインなしで読むと 401 | 写真 |

- 画面で長い辺を1568pxほどに縮めてから送ります。サーバーでも開き直し、1568px以下の JPEG にして、撮影の情報を落とします。10MB までです。
- 見てよい人は、持ち主、その写真を指す部屋の人、その写真を指す便りの共有範囲の人、そのアイコンかプロフィール写真の人がはずされていないときのログイン中の人です。それ以外は 404 です。
- 「入口の顔」は、架空用の起動で、種類がプロフィールで、入口に名前が出る人のアイコンです。プロフィール写真の枠、はずした人や亡くなった方の写真、自分用の起動の写真は入りません。
- AI に送るのは、ツインとの部屋に本人が送った「会話」の写真だけです。

## トーク

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/rooms` | 見える部屋の一覧。本人にはいちばん上にツインとの部屋 | `{"items": [Room, ...]}` |
| `POST /api/rooms` | 部屋を作る。`{"kind": "1対1" か "グループ", "member_ids": [番号], "name": "名前"}`。作った人は必ず入る | 201、`Room` |
| `GET /api/rooms/{番号}/messages?after=数` | 部屋の発言。下の「発言の読み方」のとおりに返す。部屋の人以外は 404 | `RoomView` |
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

発言の読み方（ツインとの部屋は1万行を超えるので、部屋を開くたびに全部を返しません）

| 指定 | 返す発言 |
|---|---|
| `after` なし、または `after=0` | 新しいほうの300件まで。300件以下なら全部 |
| `after=数`（1以上） | その順番より後を、件数を限らずすべて。画面の3秒ごとの読み込みで使う |
| `before=順番&limit=件数` | その順番より前の、新しいほうから `limit` 件を古い順に。`limit` は1〜300で、省くと300。範囲の外は 422 |

- `before` と `after` を両方送ると、`before` を使います。`limit` は `after` が1以上のときは使いません。`after=0` に付けると、新しいほうからその件数だけを返します。
- `seq` は、範囲を限っても部屋の中の本当の順番のままです。前の分を読むときは、いま持っている最初の `seq` を `before` に渡します。
- `POST /api/rooms/{番号}/twin` の `RoomView` は、`after=0` と同じ範囲を返します。
- `last_seq`、`first_unread_seq`、`others_read_seqs`、`unread_count` は、返す範囲に関係なく部屋の全部の行から決めます。だから `first_unread_seq` が、返した最初の `seq` より前のことがあります。

`Room`：`id`、`kind`、`name`、`members`（`Person` の配列）、`twin_present`、`last_text`（最後の発言の冒頭）、`last_at`、`can_manage_twin`（ツインを操作できるか）、`can_remove_members`（人をはずせるか）、`unread_count`（未読の数。ツインとの部屋にはない）

`RoomView`：`room`（`Room`）、`items`（`Message` の配列）、`people`（`Person` の配列）、`last_seq`、`has_earlier`（返した範囲より前に発言があるか）、`thinking`（ツインが考えているか）、`shareable_conversation_id`（共有できる直前の会話の番号。なければ `null`）、`first_unread_seq`（最初の未読の順番。なければ `null`）、`others_read_seqs`（部屋にいるほかの人の読んだ位置を小さい順に並べた配列。名前は返さない）。最後の2つはツインとの部屋にはない

`Message`

| 項目 | 内容 |
|---|---|
| `id` | 番号 |
| `seq` | 部屋の中の順番。`after` と `before` に渡す |
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
| `GET /api/archive?month=YYYY-MM` | その月の、記録のある日をすべて新しい順に。中身は上と同じ形。本人だけ | `{"days": [...], "next_before": null}`。`2036-5` のように形が違えば 422、`2031-13` のようにない月なら 400 |
| `GET /api/archive/months` | 記録（ツインとの会話か便り）のある月を新しい順に。年月の選び場所に使う。本人だけ | `{"months": ["2036-05", ...]}` |

## 便りとタイムライン

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/conversations/{番号}/letter-draft` | 便りの下書きを AI が作る。保存しない。本人だけ | `{"body": 文, "photo_ids": [番号]}` |
| `POST /api/letters` | 「この内容で共有する」。`conversation_id`、`body`、`photo_id`、`share_with`（家族／縁のある人まで）。本人だけ | 201、`TimelineLetter` |
| `GET /api/timeline?before=日付` | 見える便りを新しい順に、スレッドつきで約30件。`before` より前の日の分を返す。省くといちばん新しい分から | `{"items": [TimelineLetter, ...], "people": [Person, ...], "self_id": 本人の番号, "next_before": 日付か null}` |
| `POST /api/letters/{番号}/comments` | コメントする。`{"text": 文, "client_key": 文}`。便りを見られない人は 404 | 201、`Message` |
| `GET /api/letters` | 見える便りの一覧（スレッドなし） | `{"items": [Letter, ...]}` |

- 続きは `next_before` をそのまま `before` に渡して読みます。続きがなければ `null` です。`before` の日そのものは含みません。
- 同じ日の便りはページで分けないので、1回の件数は30件を超えることがあります。
- 数えて切るのは見える便りだけです。ページの切れ目と `next_before` から、見えない便りの有無は分かりません。`people` は、本人と、返したページでコメントした人です。

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
| `POST /api/dev/date` | `{"date": "2026-11-05"}` で、サーバーの今日をその日にする。`{"at": "2026-11-05T09:30:00+09:00"}` で時刻まで動かし、その時刻から進む。両方あれば `at` を使う。`{"date": null}` で戻す | `{"today": 日付}` |

日を変えた会話を確かめるための道です。自分用の起動では 404 を返します。

## 確認と託す設定

本人だけが使えます。本人以外には 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/review` | ツイン（AI）からの確かめ。まだ確かめていない本人の言葉と AI の推測を、今日の話、推測の順に8件まで | `{"items": [ReviewItem, ...]}` |
| `POST /api/statements/{番号}/confirm` | 「そうです」。確かめた行を足す | `ReviewItem` |
| `POST /api/statements/{番号}/correct` | 「ちがいます」。`{"text": 文}`。本人の訂正の行を足す。元の行は履歴に残る | `ReviewItem` |
| `POST /api/statements/{番号}/rest` | 「今は話したくない」。範囲を「本人だけ」に戻した行を足す | `ReviewItem` |
| `GET /api/handover` | 託す設定 | `Handover` |
| `PUT /api/handover` | `{"delegate_ids": [番号], "themes": [テーマ]}`。託せるのは家族だけ。選んだテーマの今の言葉に、範囲を広げた行を足す。はずしたテーマの言葉は「本人だけ」に戻す | `Handover` |

すでに置き換えられた行を指すと 409 を返します。

`ReviewItem`：`id`、`body`、`origin`（推測のもとの発言。推測でなければ `null`）、`origin_is_self`（もとの発言を話したのが本人か）、`origin_speaker`（もとの発言を話したのが本人以外のとき、その人の呼び名。本人のときと推測でないときは `null`。画面は「○○さんの言葉」と出す）、`source`、`effective_date`、`is_guess`

`Handover`：`themes`、`all_themes`、`delegate_ids`、`family`（`id`、`name`）、`professionals`（招かれた専門職の名前）、`decided_on`

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
| `POST /api/advocacy/chat` | チャットの言葉で処理を呼び分ける。`{"question": 文, "client_key": 文}`。語の一覧で見分け、当てはまらなければふだんの問いになる。呼び先はボタンと同じで、作ってあるものは AI を呼ばない | `{"kind": 種類, ...}`。下の `ChatReply` |
| `GET /api/advocacy/turns` | この人の問いと答え。ほかの人のやり取りは返さない | `{"items": [Turn, ...]}` |
| `POST /api/advocacy/turns/{番号}/advocate` | その答えに続けて、ツインの主張を作る。`{"again": 作り直すか}`。作ってあれば AI を呼ばずに返す。作り直しても前の行は残る。自分の問いにだけ呼べる。見える材料がまったくないときは作らず `voice` を null で返す。健康や病気の問いでも作る（診断と服薬の指示はしない。CLAUDE.md の原則17）。ケア会議の画面も、問いのあとにこれを呼んで代弁の枠に出す（読み上げない） | `{"voice": Voice \| null}` |
| `POST /api/advocacy/sheet` | 1枚の紙を作る。`{"again": 作り直すか, "meeting_room_id": 部屋の番号}`。部屋を省くと、入っている「ケア会議」の新しいほうを読む。作ってあれば AI を呼ばずに返す | `{"sheet": Sheet}` |
| `GET /api/advocacy/sheet` | この人のいちばん新しい紙。なければ `null`。今の様子と会議の言葉は読むたびに取り直す。AI は呼ばない | `{"sheet": Sheet}` |
| `POST /api/advocacy/plans` | ケアプランの第1版を作る。`{"client_key": 文, "text": 案の文}`。`text` があれば AI を呼ばずそのまま使う。省くと本人の言葉を読まずに AI が一般的な案を作る。同じ `client_key` で送り直しても増えない | `{"plan": Plan}` |
| `POST /api/advocacy/plans/{番号}/objection` | 第1版にツインが異議を唱える。`{"again": 作り直すか}`。作ってあれば AI を呼ばない。自分の案にだけ呼べる | `{"plan": Plan}` |
| `POST /api/advocacy/plans/{番号}/revise` | 第1版と異議から第2版を作る。`{"again": 作り直すか}`。異議の前は 400。作ってあれば AI を呼ばない。原案の表にも残す | `{"plan": Plan}` |
| `POST /api/advocacy/plans/{番号}/choose` | どの版を使うかを人が選ぶ。`{"version": "第1版" か "第2版" か "どちらも使わない"}`。AI は呼ばない。第2版がないのに第2版を選ぶと 400。選び直しても前の選択は残す | `{"plan": Plan}` |
| `GET /api/advocacy/plans` | この人のいちばん新しい案を、版を並べて返す。なければ `null`。AI は呼ばない | `{"plan": Plan}` |
| `GET /api/advocacy/overview` | 今の様子と、本人が範囲を決めた日 | `{"self_name": 文, "now": [Now, ...], "decided_on": 日付, "themes": [テーマ]}` |
| `GET /api/advocacy/elements` | 見える意思モデルの要素。花びらは専門職にだけ返す | `{"items": [Element, ...]}` |
| `GET /api/advocacy/elements/{番号}` | 要素の根拠を、言明と発言の原文までたどる。見えない要素は 404 | `{"element_id": 番号, "body": 文, "kind": 文, "evidence": [Quote に utterances を足したもの]}` |
| `POST /api/observations` | 今の様子を入れる。`{"text": 文, "theme": テーマ, "at_meeting": 会議の画面から入れたか}`。`at_meeting` が真なら発言の場所を「会議」にする | 201、`{"statement_id": 番号}` |
| `GET /api/statements/{番号}/interpretations` | 言明の AI の読み（仮説）。専門職だけ | `{"items": [{"body", "confidence", "caution", "status"}]}` |

`Turn`：`id`、`created_at`、`question`、`notice`（原則に触れる頼みへの決まった1文。なければ `null`）、`lead`（ツインの短い案内。なければ `null`）、`now`（`Now` の配列）、`self_words`、`testimony`、`guesses`（それぞれ `Quote` の配列。`Quote` に `group` が付く。同じ `group` の行は同じ発言から分かれた文で、画面は1枚にまとめる。本文は1行ずつ記録のまま）、`not_yet`（本人の言葉がないときの「まだ聞けていません。」。あれば `null`）、`changed`（届けた言葉が時期によって変わっているか、証言と食い違っているか）、`estimate`（`Estimate`。なければ `null`）、`elements`（届けた言葉を根拠に持つ要素。`element_id`、`body`、`kind`、`unit`、`valid_from`、`confirmed`）

`Now`：`statement_id`、`body`、`date`、`speaker`、`speaker_label`（続柄つきの呼び方。なければ `null`）、`source`

`Quote`：`statement_id`、`body`（記録の本文のまま）、`lead_in`（プログラムが付ける前置き）、`date`、`source`、`speaker`、`confirm`、`top`（先に見せる言葉の印。問いに近い言葉と、いちばん新しい言葉）

`Estimate`：`text`、`evidence`、`counter`（`Quote` の配列）、`confidence`（低い／中くらい）、`how_to_confirm`

`Sheet`：`id`、`created_at`、`made_on`、`deployment_label`、`label`、`self_name`、`claim`（ツインの代弁）、`topics`（`title`、`text`、`evidence`（`Quote` の配列）の配列）、`change`（`text`、`evidence`。なければ `null`）、`plan`（`self_intent`、`long_term_goal`、`evidence`、`verdict`）、`requests`、`ask_back`、`now`（`Now` の配列）、`meeting`（`asked`（`speaker`、`text`。なければ `null`）と `answer`（`text`、`sent_at`）の組の配列。部屋の発言のまま）、`treasure`（「大切なもの」を聞いた問いへの本人の返事。なければ `null`）

`ChatReply`：`kind` が「問い」なら `turn`、「ツインの考え」なら `voice`、「問いとツインの考え」なら `turn` と `voice`、「1枚の紙」なら `sheet`、「ケアプラン案」「第2版」「版の選択」なら `plan`、「伝える」なら `tell`（`Tell`）、「手紙」なら `letter`（専門職に見せる `Letter`）。「伝えます」「報告します」などの語は伝える処理へ、「手紙を書」などの語は手紙を頼む処理へ渡す

`Plan`：`id`、`created_at`、`first`（`source`、`summary`、`items`（`aim`、`support` の配列））、`objection`（`label`、`claim`、`points`（`target`、`objection`、`evidence`（`Quote` の配列）の配列）、`requests`、`ask_back`、`verdict`。まだなければ `null`）、`second`（`label`、`summary`、`items`（`aim`、`support`、`why`、`evidence` の配列）、`plan`（`self_intent`、`long_term_goal`、`evidence`、`verdict`）。まだなければ `null`）、`choice`（人が選んだ版。`version`、`chooser`、`created_at`。まだなければ `null`）

`Voice`：`id`、`turn_id`、`created_at`、`label`（決まった1文）、`claim`（ツインの主張）、`reasons`（`text` と `evidence`（`Quote` の配列）の配列）、`objections`（`quote`（`Quote`）と `answer` の配列）、`requests`（文の配列）、`ask_back`（相手に聞きたいこと。なければ `null`）、`off_record`（同じ問いで、問いに合う本人の言葉が記録になかったときの決まった1文「この話は、記録にはありません。ここからはツインの推測です。」。あれば `null`。画面は `claim` より前に出す。11/7 より前に作った考えには項目がない）。`claim`、`text`、`answer`、`requests`、`ask_back` はツインの文で、`Quote` の `body` だけが記録の本文

## 専門職がツインに伝えること（チャットと資料）

伝えたことは、ツインが項目に分けて「確かめ待ち」にします。「残す」を押した項目だけが、今の様子（観察）の言明として台帳に入ります。チャットの伝えとケアプラン、担当者会議の要点から残した記録は「意思決定支援用」、アセスメント、支援経過、事業所の報告、そのほかから残した記録は「専門職だけ」の範囲になり、託された家族には見えません。資料の中の「本人：〜」は専門職の聞き取りで、本人の言葉として台帳に入れません。処理は `app/professional_records.py` にあります。

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/advocacy/tell` | チャットで伝える。`{"text": 文, "client_key": 文}`。2000字まで。託された人が使える。同じ `client_key` なら AI を呼ばずに返す | `{"tell": Tell}` |
| `GET /api/advocacy/tells` | この人が伝えたこと。ほかの人の伝えは返さない | `{"items": [Tell, ...]}` |
| `POST /api/advocacy/documents?client_key=文&file_name=文` | 資料（PDF）を取り込む。本文に PDF そのものを送る。10MB、30ページまで。ケアプランの道具を使える人だけ。自分用の起動では 400。資料の利用者の名前が本人と合わなければ「名前の確かめ待ち」にし、項目を返さない | 201、`{"document": Document}` |
| `GET /api/advocacy/documents` | 取り込んだ資料。取り下げたものは返さない。1件ごとに `chat_count`（この人がその資料についてツインと話した数）を足す | `{"items": [Document, ...]}` |
| `GET /api/advocacy/documents/{番号}` | 1件の資料 | `{"document": Document}` |
| `GET /api/advocacy/documents/{番号}/file` | 元の PDF | `application/pdf` |
| `POST /api/advocacy/documents/{番号}/person-ok` | 名前が合わなかった資料を「この人の資料です」と確かめる | `{"document": Document}` |
| `GET /api/advocacy/documents/{番号}/erase-preview` | 消去で消えるものを数えて返す。AI は呼ばない | `{"preview": {"can_erase", "reason", "erase_until", "counts": {資料, 残した記録, ツインの考え, ケアプランのチェック, 手紙, 1枚の紙}, "returned_letters"}}` |
| `POST /api/advocacy/documents/{番号}/erase` | 別の人の資料だったときの消去。資料、ファイル、読んだ記録、項目、残した記録（言明とその連なり、発言、会話、解釈）、この資料のチェック、残したあとに作られたツインの考え、手紙、1枚の紙を消し、取り込む前の時点に戻す。取り込んでから72時間を過ぎると 400。台帳の決まりの例外 | `{"erased": 数, "returned_letters": 数}` |
| `POST /api/advocacy/documents/{番号}/check` | 取り込んだケアプランを第1版として入れ、ツインが異議を作る。ケアプランでなければ 400。2回押しても案は増えない | `{"plan": Plan}` |
| `POST /api/advocacy/documents/{番号}/review` | ツイン（AI、Opus）が資料を本人のセルフと照らしてレビューし、文章で返す（要点、合っているところ、気になるところと資料の場所、本人に確かめたいこと）。作ってあれば AI を呼ばない。名前の確かめ待ちは 400。資料を渡したとき、画面は記録の候補より先にこれを出す | `{"review": {"overview", "fits", "concerns": [{"text", "where"}], "asks", "created_at"}}` |
| `GET /api/advocacy/documents/{番号}/chat` | この資料について、この人が聞いた問いと答えを古い順に。AI は呼ばない。ほかの人のやり取りは返さない | `{"items": [DocumentTurn, ...]}` |
| `POST /api/advocacy/documents/{番号}/chat` | 資料について問う。`{"question": 文（1000字まで）, "client_key": 文}`。ツイン（AI）が、資料の要点、ケアプランの中身、読んだ項目、レビューと、同じ資料のこの人の直前のやり取り（3件まで）を材料に、本人として代弁で答える（「ツインに聞く」と同じ作り方。「ツインに聞く」の履歴とトークには入らない）。同じ `client_key` なら AI を呼ばない。名前の確かめ待ちの資料は 400、資料を扱えない人は 404、AI が答えられないときは 503 | `{"turn": DocumentTurn}` |

`DocumentTurn`：`id`、`question`、`created_at`、`voice`（`Voice` と同じ形。`label` に「ツイン（AI）」を含む）。

### 資料の画面（2036年の専門職の道具。11/6 に作り直した）

tyumajinさんの依頼で、資料は「ツインに聞く」から切り離し、資料ごとにレビューとチャットを持つ画面にする。

**入口**
- ツールの「資料を渡してチェックする」を押すと、資料の画面（`openTab("documents")`）を開く。「ツインに聞く」へは行かない。ファイルを選ぶ窓は自動で開かない。
- 「ツインに聞く」の画面には、資料の画面へ移る「資料の画面を開く」のボタンを1つだけ置く（tyumajinさんの依頼）。資料はそこで登録し、「ツインに聞く」の中では登録しない。

**資料の画面の作り**（PC は左に一覧、右に選んだ資料。スマホは一覧 → 資料の順に切り替える）
1. 上に「資料を登録する」のボタン。押したときだけファイルを選ぶ窓を開き、`POST /documents` で登録する。待つ間はくるくるの印。読み取れたら、その資料を右に開く。
2. 左に、これまで登録した資料の一覧（`GET /documents`、新しい順）。1行に、登録した日、種類、題、書いた人、レビューの有無（`review`）、話した数（`chat_count`）。名前の確かめ待ちの資料には印。
3. 右に、選んだ資料。見出し（種類、題、書いた人、資料の日付）の下を「レビュー」「記録の候補」の2つで切り替え、その横か下に、その資料についてのチャットを置く。見出しには「元の資料を開く」のボタンを置く。
   - レビュー：`review` があれば出す。なければ `POST /documents/{番号}/review` で作る（待つ印）。
   - 元の資料：「元の資料を開く」を押すと、モーダル画面（画面の上に重ねる窓）で PDF を大きく見せる（11/6 tyumajinさんの依頼。レビューやチャットを読みながら、必要なときだけ原本を開くため）。`GET /documents/{番号}/file`（`X-NT-User` と `X-NT-Data` を付けて読み、Blob の URL で見せる）。窓には「閉じる」と「別のタブで開く」を置き、Esc と背景を押しても閉じる。閉じたらボタンに戻る。
   - 記録の候補：`items` を出し、`POST /items/{番号}/decide` で残すかを決める。ケアプランなら `POST /documents/{番号}/check` でチェックを頼める。消去（`can_erase`）もここ。
   - チャット：`GET /documents/{番号}/chat` で、その資料のこの人のやり取りを古い順に出し、`POST` で問う。答えは代弁の枠（`static/voice.js`）で、「ツイン（AI）」と推測を含むことが分かる形にする。
4. 「今の様子」は資料の画面には出さない（資料の中身とレビューを読む画面にするため）。
5. 名前の確かめ待ちの資料は、右の見出しの下に確かめ（`POST /documents/{番号}/person-ok`）だけを出し、レビュー、記録の候補、チャットは確かめるまで出さない。
| `POST /api/advocacy/items/{番号}/decide` | 項目を残すか決める。`{"decision": "残す" か "残さない", "text": 直した文}`。決めた項目は決め直せない。チャットの項目は伝えた人だけが決められる | `{"item": Item}` |

`Tell`：`id`、`text`、`created_at`、`items`（`Item` の配列）

`Item`：`id`、`kind`（観察／本人の思いの聞き取り／家族の思いの聞き取り）、`text`、`page`（資料のページ。AI が返したもの）、`section`（資料のどの場面から読んだか）、`quote`（原文の短い抜き出し。チャットの項目では空）、`about`（だれについての項目か。分からなければ `null`）、`status`（確かめ待ち／残した／残さない）、`statement_id`、`document_id`、`turn_id`

`Document`：`made_by_ai`（ツイン（AI）や AI が作った資料か。真なら項目にしない）、`review`（ツインのレビュー。まだなければ `null`）、`id`、`family_can_see`（残した記録がご家族にも見えるか。ケアプランと担当者会議の要点だけ真）、`can_erase`、`erase_until`、`kind`、`title`、`author`、`document_date`、`person_name`、`pages`、`status`（名前の確かめ待ち／確かめ待ち／確かめ済み）、`summary`、`care_plan`（`self_intent`、`family_intent`、`policy`、`needs`（`need`、`long_term_goal`、`short_term_goal`、`services`）。ケアプランでなければ `null`）、`has_medical`、`uploader`、`created_at`、`items`

## 花びらの読み（専門職の「記録」の上）

tyumajinさんの依頼（11/6）。専門職の「記録」の画面のいちばん上の真ん中に、ツイン（AI）の3Dの姿を立たせ、5つの花びら（キットウッドの5つの心の求め）のボタンを置く。押すと、その花びらについて、誠一さんにとって何で満たされ、何で損なわれ、今どうかを、ツインの読みとして答える。花びらの名前と根拠を出すので、専門職の画面だけに置く（原則13）。11/7 から、本人もホームの「5つの花びら」（`static/self_petals.js`）で同じ読みを見られる（ご本人の依頼）。家族には出さない。

| 呼び方 | 中身 | 返すもの |
|---|---|---|
| `GET /api/twin/petals` | 5つの花びらの読み。セルフのいちばん新しい再生の「A 花びら」の15か所（5つ×3つの面）を、デモの今日の日付までの版で返す。AI は呼ばない。本人と、託されている専門職だけ（家族とほかの人には 404）。根拠の言葉と読みのかぎかっこの文は、見る人に見える記録で確かめたものだけ | `{"as_of": "2036-05-06", "label": "ツイン（AI）の読み（推測を含みます）", "petals": [Petal]}`。セルフがまだないときは `petals` が空 |

`Petal`：`order`（0〜4。ツインの姿の花びらの番号と同じ。0 が上で、時計回り）、`name`（くつろぎ／結びつき／共にあること／たずさわること／自分らしさ）、`faces`（`Face` の配列。満たされる、損なわれる、今の順）

`Face`：`face`（何で満たされるか／何で損なわれるか／今どうか）、`status`（推測／仮説／確からしい／本人が確かめた／揺れている／変わった／空き）、`readings`（ツインの読みの文。1〜3文。AI がセルフを作ったときに書いて保存したもの。見る人の「記録」の一覧にある本文で確かめられないかぎかっこの文は、サーバーが落とす。人だけの部屋の言葉を引かないため（原則15）。セルフが「／」でつないだ段落は、分けて返す）、`words`（根拠の本人の言葉。新しい順に2つまで。`statement_id`、`body`（記録の本文のまま）、`effective_date`、`source`）、`latest_evidence_date`（その面を支える根拠のうち、いちばん新しい日。なければ `null`。「今どうか」の根拠が数年前のこともあるので、画面で「いちばん新しい根拠：2029年」のように添える）

- 確からしさの数は返さない（原則13。数字を出さない）。
- `words` は、見る人に見える、いま有効な言明だけ（`/api/statements` と同じ絞り込み）。「本人だけ」の言葉は件数も出ない。
- 読みは推測なので、画面では「ツイン（AI）の読み」と出し、読み上げない。`status` は言葉と枠の線（本人が確かめた＝実線、それ以外＝点線）で分ける。
- 花びらを選んだときの3Dの動きは、動きと形だけで示し、花びらの色は変えない（原則13）。

**画面の作り**（PC）
1. 「記録」の画面のいちばん上の真ん中に、3Dのツインを光の台座に立たせる（`twinStage`）。「ツインに聞く」の右の窓の3Dは、ここへ移す（ケア会議の3Dはそのまま）。
2. 5つの花びらのボタンを、姿のまわりに、花びらと同じ向き（上から時計回り）に並べる。スマホは姿の下に横に並べる。
3. ボタンを押すと、その花びらの読みを出す。見出しは花びらの名前、続けて「満たされる」「損なわれる」「今」の3つ。それぞれに `status` の札、読みの文、根拠の本人の言葉（日付と出どころつき、手書き風の字）。期間は `as_of` から「2036年5月までの記録から」と出す。
4. 矢印キーで花びらを移り、Esc で選ぶのをやめる。ボタンは高さ44px以上、文字16px以上。動きを減らす設定では動かさない。

## 本人の今を代弁する手紙

ツインが本人として、今の暮らしと今の気持ちを A4 1枚に書きます。本人か、託された家族が確かめて返すまで、専門職には中身を返しません。処理は `app/advocacy_letter.py` にあります。

| 道 | 内容 | 応答 |
|---|---|---|
| `POST /api/advocacy/letters` | 手紙を頼む。`{"purpose": "今月の様子" か "会議の前に" か "新しい人が来る前に" か "そのほか", "client_key": 文}`。手紙の道具を使える人だけ。同じ `client_key` なら AI を呼ばない | 201、`{"letter": Letter}` |
| `GET /api/advocacy/letters` | この人が頼んだ手紙 | `{"items": [Letter, ...]}` |
| `GET /api/advocacy/letters/{番号}` | 1通。頼んだ人だけ | `{"letter": Letter}` |
| `GET /api/letters/pending` | 確かめ待ちの手紙。本人と託された家族だけ（ほかは 404）。本人の画面のお知らせに使う。`requester_id`（頼んだ人の番号）で、画面が知らせの行の頭に顔を出す | `{"items": [{"id", "recipient", "requester_id", "purpose", "created_at"}]}` |
| `GET /api/letters/{番号}/check` | 確かめる画面の中身。札は、今確かめている人を入れた形で返す | `{"letter": Paper}` |
| `POST /api/letters/{番号}/edit` | 直した手紙を新しい版として足す。`{"body": 文, "requests": [文, ...]}`。鉤括弧は 400 | `{"letter": Paper}` |
| `POST /api/letters/{番号}/return` | 専門職へ返す。直して足された文を、直した人の言葉として台帳に足す（本人は本人の言葉で、返したときに確認済み。家族は家族の証言） | `{"letter": Paper}` |
| `POST /api/letters/{番号}/decline` | 出さない。あとから返せない | `{"letter": Paper}` |

`Letter`（専門職に見せる形）：`id`、`purpose`、`status`（確かめ待ち／返した／出さない）、`created_at`、`returned_at`。返したときだけ `paper`（`Paper`）、`pieces`（もとにしたピース。画面だけで紙には載せない）、`reading` が付く

`Paper`：`id`、`purpose`、`status`、`recipient`、`self_name`、`written_on`、`body`、`requests`、`quote`（引いた本人の言葉。`Quote`。なければ `null`）、`illustration`（`src`、`title`。絵のファイルがなければ `null`）、`label`（紙の札。「ツイン（AI）が◯◯さんに代わって書きました。」に、確かめた人で「◯◯さんも」「ご家族も」「◯◯さんとご家族も確認されました。」が続く）、`edited`、`checker_role`（確かめる画面だけ）

挿絵の一覧は `static/letter_art.json`、絵は `static/letter_art/` に置きます。

## 専門職が入力するアセスメントなど

専門職が、アセスメントシート、フェイスシート、ライフサポートプランを書式どおりに入力します。本人と家族には見せず、台帳（言明）にも入れません。資料のレビュー、ケアプランのチェック、生活まとめプランの材料になります。ケアプランの道具を使える専門職だけが使えます（ほかは 404）。処理は `app/care_form_records.py`、書式の定義は `forms/`、画面は `static/care_form.html` です。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/advocacy/care-forms/forms` | 入力できる書式の定義（`forms/assessment.json` など） | `{"items": [Form, ...]}` |
| `GET /api/advocacy/care-forms/records?form_id=` | 入力した記録の一覧（直された前の版と、削除した記録は除く） | `{"items": [Record（中身なし）, ...]}` |
| `GET /api/advocacy/care-forms/records/{番号}` | 1件の中身。前の版も開ける。`earlier` に前の版の一覧（新しい順、中身なし）、`latest_id` にいちばん新しい版の番号。削除した記録は、前の版も 404 | `{"record": Record}` |
| `POST /api/advocacy/care-forms/records` | 保存する。`{"form_id", "record_date": 日付, "values": {"まとまり／欄": 値}, "details": {"欄": 文}, "replaces_id": 直す前の番号, "copied_from": コピーのもとの番号, "client_key": 文}`。書式にない欄、選択肢にない値は 400。直すと新しい行を足し、前の版は残す（コピーのもとは引き継ぐ）。コピーは別の日付の新しい記録になる。`replaces_id` と `copied_from` を一緒に送る、もとが見つからない、書式が違う、削除した記録を指すと 400 | 201、`{"record": Record}` |
| `DELETE /api/advocacy/care-forms/records/{番号}` | 削除する。行は消さず、いちばん新しい版に印（`care_form_deletions`、追記だけ）を足し、前の版もまとめて一覧、中身、AI の材料から外す。前の版を指すと 400。ケアプランの道具を使える専門職なら消せる | `{"deleted": 番号}` |

| `POST /api/advocacy/care-forms/read?form_id=&file_name=` | 本文に PDF そのものを送ると、ツイン（AI、Sonnet）が書式の欄に当てはめて読み、値を返す。保存はしない。書式にない欄と選択肢にない値は捨てて `skipped` に名前を返す。資料の利用者の名前が本人と合わなければ `name_matches` が false。PDF でない、10MB より大きい、30ページより多いと 400。自分用の起動では 400 | `{"read": {"values", "details", "record_date", "person_name", "skipped", "name_matches"}}` |

JSON のファイルは画面の中で読みます（`static/care_form.js`）。`{"form": 書式, "values": {...}, "details": {...}}` の形（`fiction/documents/assessment_2036-04.json` と同じ）か、値だけの形を受け付けます。読み込んだ中身は入力欄に入り、保存は2段（1回目で確かめの表示、2回目で書き込み）です。

`Record`：`id`、`form_id`、`title`、`record_date`、`author`、`replaces_id`、`version`（第何版か）、`copied_from`、`copied_from_date`（コピーのもとの記録の日付）、`created_at`、`values`、`details`

画面は資料の画面と同じ形です。左に「新しく入れる」とこれまでの記録（書式で絞れる）、右に選んだ記録の中身（入っている欄だけ）を出し、そこから「修正する」「コピーして別の日に使う」「削除」（2回押す）を選びます。直すときも記録の日付を変えられます。もとの日付の記録を残したまま別の日にも使うときは、コピーを使います。紙をスキャンしただけの PDF（文字の層がないもの）も、PDF をそのまま AI に渡すので、ページの画像から読みます。

## 生活まとめプラン

暮らしの場面ごとに、今まで、出来ること、出来ないこと、支援して欲しいこと、誰がを書く紙（A4 横）です。ツイン（AI）が、今のアセスメントと今の様子から出来ること・出来ないことを見立て、セルフと本人の資源、周りの支えをもとに Opus で下書きします。専門職が画面で直すと、新しい版として足します。ケアプランの道具を使える専門職だけが使えます（ほかは 404）。処理は `app/life_plan.py`、紙の形は `forms/life_plan.json`、画面は `static/life_plan.html` です。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/life-plan-form` | 白紙の紙の形（見出し、列、場面の行）。中身のない書式なので、ログインの前でも返す | `{"form": {...}, "label": 文}` |
| `POST /api/advocacy/life-plans` | 下書きを作る。`{"client_key": 文}`。同じ `client_key` なら AI を呼ばない | 201、`{"plan": Plan}` |
| `GET /api/advocacy/life-plans` | この人が作った生活まとめプラン | `{"items": [Plan, ...]}` |
| `DELETE /api/advocacy/made/{種類}/{番号}` | ツイン（AI）が作った書類を削除する。種類は `letter`（手紙）、`sheet`（1枚の紙）、`plan`（ケアプラン案）、`life_plan`（生活まとめプラン）。頼んだ専門職だけが消せる。行は消さず削除の印を足し、一覧と中身と確かめ待ちから外す。書類から台帳に入った言葉は消さない。ほかの人の書類と、消した書類には 404 | 204 |
| `GET /api/advocacy/life-plans/{番号}` | 1件。作った人だけ | `{"plan": Plan}` |
| `POST /api/advocacy/life-plans/{番号}/edit` | 直した中身を新しい版として足す。`{"rows": [Row, ...]}`。紙にない場面は捨て、書かれなかった場面は空の行にする | `{"plan": Plan}` |

`Plan`：`id`、`created_at`、`written_on`、`self_name`、`person_name`（紙のご利用者名。入力したフェイスシートかアセスメントの氏名を先に使う）、`author`（作成者。作った時点ではツイン（AI））、`editors`（直した専門職の名前）、`requester`（下書きを頼んだ専門職）、`rows`、`version`、`edited`、`label`（紙の札。「ツイン（AI）が記録から下書きしました。」に、直した人が続く）、`pieces`、`reading`

`Row`：`area`（場面）、`part`（食事の朝食、昼食、夕食、飲水。ほかは `null`）、`past`（今まで）、`can`（出来ること）、`cannot`（出来ないこと）、`support`（支援して欲しいこと。本人の願いとして書く）、`who`（誰が）

## 本人が送る手紙

本人のホームの「手紙を書く」から開くページ（`static/my_letter.html`）。ツイン（AI）が、選んだ期間に本人とツインが話したこと（日記と会話）と、送る相手との1対1の部屋のやり取りと、セルフをもとに、本人の人柄が伝わる標準語で下書きを書く（方言は人柄を読むためだけに使う）。ほかの人の発言と、ほかの部屋の話は渡さない（原則15）。本人は文面を直し、ツインに校正してもらい、前の版に戻し、送る。いらない下書きは捨てられる（印を足すだけ。`self_letter_discards`）。挿絵はツイン（AI、Sonnet）が手紙ごとに SVG で描く（`self_letter_arts`、追記だけ）。版は足すだけ（`self_letter_versions`）、送った印も足すだけ（`self_letter_sends`）。作る、直す、送るは本人だけ（ほかは 404）。送った手紙は相手も読める。処理は `app/self_letters.py`、プロンプトは `app/self_letter_prompts.py`。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/self-letters/recipients` | 送る相手の候補。本人のほかの、はずしていない人。亡くなった方も入る | `{"items": [{"id", "name", "relation", "role", "is_deceased"}, ...]}` |
| `GET /api/self-letters` | 本人が書いた手紙（下書きと送ったもの）を新しい順に | `{"items": [Summary, ...]}` |
| `GET /api/self-letters/inbox` | この人に届いた手紙（送ったものだけ）を新しい順に。`Summary` の `author_id`（書いた人の番号）で、画面が知らせの行の頭に顔を出す | `{"items": [Summary, ...]}` |
| `GET /api/self-letters/{番号}` | 手紙の中身。書いた本人か、送ったあとの相手だけ（ほかは 404） | `{"letter": Letter}` |
| `POST /api/self-letters` | 下書きを作る。`{"period": "今週"・"ひと月"・"期間", "start", "end"（期間のときだけ）, "recipient_id", "outline": あらすじ（なくてよい）, "client_key"}`。今週は今日（物語の日付）までの7日、ひと月は30日。期間は92日まで、今日まで。期間にツインと話した記録がなければ 400。同じ client_key なら AI を呼ばずに返す。ツイン（AI、Opus）が書く。挿絵は、描くまでは前もって作った水彩の絵（選ばれなければ通年の絵）を出す | 201、`{"letter": Letter}` |
| `POST /api/self-letters/{番号}/edit` | 本人が直した文面。`{"salutation", "body", "closing", "signature"}`。前と同じなら版を足さない | `{"letter": Letter}` |
| `POST /api/self-letters/{番号}/proofread` | ツイン（AI、Sonnet）が今の文面を校正し、版を足す。文を整えるだけで作り直さない（出来事は変えず、文も段落も消さない。残った方言は標準語に直す）。体に直す欄の4つの部分を渡すと、先に「直し」の版として残してから校正する。直す所がなければ版を足さず、`proofread_changed` が false。段落が減ったり本文が85%より短くなったりした校正は使わず、`proofread_dropped` が true | `{"letter": Letter}` |
| `POST /api/self-letters/{番号}/art` | ツイン（AI、Sonnet）が今の文面に合う挿絵を、これまでの水彩の挿絵を見本の画像にして SVG で描く。描き直すたびに足す。SVG は使ってよい要素と属性だけに組み直し（`app/svg_art.py`）、だめなものがあれば使わず 503（前の絵のまま） | `{"letter": Letter}` |
| `POST /api/self-letters/{番号}/revert` | `{"to_version": 版}` の文面に戻す。戻したことも新しい版として足すので、戻す前にもまた戻れる | `{"letter": Letter}` |
| `POST /api/self-letters/{番号}/send` | 送る。いちばん新しい版を送った版にする。送ったあとの直し、校正、戻す、送る、捨てるは 409 | `{"letter": Letter}` |
| `DELETE /api/self-letters/{番号}` | 送っていない下書きを捨てる。行も版も消さず、印を足して一覧と画面から外す（以後は 404） | `{"discarded": 番号}` |

`Letter`：`id`、`author`、`recipient`（`id`、`name`、`relation`、`is_deceased`）、`period_start`、`period_end`、`created_at`、`status`（下書き／送った）、`sent_at`、`illustration`（`src`、`title`、`drawn`。ツインが描いた絵は data: の URL）、`content`（`salutation`、`body`、`closing`、`signature`）、`version`、`kind`（下書き／直し／校正／戻す）、`notes`（校正で直したところ）、`label`（紙の札。「ツイン（AI）が下書きを手伝い、◯◯さんが確かめて送りました。」）。書いた本人には `outline` と `versions` も返す。

受け取った人のホームには「手紙が届いています」と出す（`static/letter_notice.js`）。送った手紙の文は、今は台帳に本人の言葉として入れない。

## 物語の今日（架空用の起動だけ）

自分用の起動では 404 を返します。

| 道 | 内容 | 応答 |
|---|---|---|
| `GET /api/dev/story` | 物語の今日の日付。本人だけ | `{"today": 日付}` |
