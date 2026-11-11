/*
  役割：ご本人と託されたご家族のホームに、確かめ待ちの手紙の知らせを出す部品。押すと、手紙を読んで直す画面を開く。
  4つの極：4. 10年後に代弁する。
  守る原則：1（返すか出さないかは、ご本人とご家族が決める）、7（催促や数の表示を作らない。知らせは静かな枠1つ）、
            12（必ず「ツイン（AI）」と出す）。

  ご本人かご家族のどちらかが返せば、専門職に届く（app/advocacy_letter.py）。
  確かめられる人かはサーバーが決める。確かめられない人には一覧が返らないので、何も出さない。
*/

import { api } from "./api.js";
import { dateWords } from "./letter_paper.js";
import { avatar, el } from "./ui.js";

const HONORIFIC = /(さん|くん|ちゃん|先生|様)$/;
const bare = (name) => String(name || "").trim().replace(HONORIFIC, "");

/* 手紙にかかわる人（届いた手紙の送り主、下書きを頼んだ人）を、入口の人の一覧から探す。
   番号が返っていれば番号で、なければ呼び名で探す。見つからないときは、名前の頭文字の丸になる（avatar）。
   入口の一覧は、自分用の起動では写真の番号を返さないので、そのときも頭文字の丸になる。 */
async function peopleFinder() {
  let users = [];
  try {
    ({ items: users } = await api("/users"));
  } catch {
    users = [];
  }
  return (id, name) =>
    users.find((user) => id && user.id === id) ||
    users.find((user) => [user.called_name, user.name].some((known) => known && bare(known) === bare(name))) || { name: bare(name) };
}

/* 行の頭に小さなアイコン、続けて日付と手紙の名前。アイコンは飾りなので、読み上げは文字だけにする（avatar が aria-hidden） */
function whoLine(person, text) {
  return el("p", { class: "letter-notice-what" }, avatar(person, "small"), el("span", { text }));
}

/* 確かめ待ちの手紙があれば、ホームのいちばん上に枠を出す。still は、読み終えたときにまだホームを開いているか。 */
export async function showLetterNotice(view, still) {
  let items;
  try {
    ({ items } = await api("/letters/pending"));
  } catch {
    return;
  }
  if (!items.length || !still()) {
    return;
  }
  const find = await peopleFinder();
  if (!still()) {
    return;
  }
  const box = el(
    "section",
    { class: "card letter-notice" },
    el("h2", { text: "手紙の下書きが届いています" }),
    el("p", { text: "ツイン（AI）が、ご本人に代わって書きました。読んで、違うところを修正してから返してください。" }),
    ...items.map((item) =>
      el(
        "div",
        { class: "letter-notice-row" },
        // 何通あっても見分けられるよう、頼まれた日と目的を出す
        whoLine(find(item.requester_id, item.recipient), `${dateWords(item.created_at)}　${item.recipient}への手紙（${item.purpose}）`),
        el("button", { type: "button", class: "primary", text: "読んで修正する", tip: "新しいタブで下書きを開き、修正して返せます", onclick: () => window.open(`letter_check.html?id=${item.id}`) })
      )
    ),
    el("p", { class: "hint", text: "ご本人かご家族のどちらかが返すと、届きます。返すまでは届きません。" })
  );
  // ホームを続けて開き直したとき、前の読み込みの枠と二重にならないようにする
  // 届いた手紙の枠（同じ letter-notice の形）は消さない
  view.querySelector(".letter-notice:not(.received-letters)")?.remove();
  const panel = view.querySelector(".panel");
  // 並びは、届いた手紙が上、下書きがその下。どちらが先に読み終わっても同じ並びにする
  const received = view.querySelector(".received-letters");
  if (received) {
    received.after(box);
  } else {
    (panel || view).prepend(box);
  }
}

/* ご本人から届いた手紙を、受け取った人のホームに知らせる（app/self_letters.py）。押すと、読むだけの手紙のページを開く。
   新しい3通までを出す。未読や数の表示は出さない（原則7）。 */
export async function showReceivedLetters(view, still) {
  let items;
  try {
    ({ items } = await api("/self-letters/inbox"));
  } catch {
    return;
  }
  if (!items.length || !still()) {
    return;
  }
  const find = await peopleFinder();
  if (!still()) {
    return;
  }
  const box = el(
    "section",
    { class: "card letter-notice received-letters" },
    el("h2", { text: "手紙が届いています" }),
    ...items.slice(0, 3).map((item) =>
      el(
        "div",
        { class: "letter-notice-row" },
        whoLine(
          find(item.author_id, item.author),
          `${dateWords(item.sent_at)}　${HONORIFIC.test(item.author) ? item.author : `${item.author}さん`}からの手紙`
        ),
        el("button", { type: "button", class: "primary", text: "読む", tip: "届いた手紙を、新しいタブで読めます", onclick: () => window.open(`my_letter.html?id=${item.id}`) })
      )
    ),
    items.length > 3 ? el("button", { type: "button", class: "quiet", text: "届いた手紙をすべて見る", tip: "これまでに届いた手紙の一覧を、新しいタブで開きます", onclick: () => window.open("my_letter.html") }) : ""
  );
  view.querySelector(".received-letters")?.remove();
  const panel = view.querySelector(".panel");
  // いちばん上に置く（下書きの知らせがあれば、その上）
  (panel || view).prepend(box);
}
