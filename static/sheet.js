/*
  役割：1枚の紙を組み立てる（仮置き）。呼び方が決まるまでの最小の形。
  4つの極：4. 10年後に代弁する。
  守る原則：3（ツインの文と記録を分ける）、9（本人の言葉は記録の本文のまま）。

  本人の言葉の本文と会議の言葉は、サーバーが記録から入れたものだけを出す。ここで文を作らない。
  作った紙は「削除」で消せる。取り消せないので2回押す形にする（app/made_documents.py）。
*/

import { api } from "./api.js";
import { el, startWaiting, twoStepButton } from "./ui.js";

const sheet = document.getElementById("sheet");
const status = document.getElementById("status");
// 紙に載せる根拠の言葉の数。1枚に収めるため。全部は画面の「根拠をたどる」で読める
const QUOTES_PER_TOPIC = 1;

function dateWords(iso) {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

/* 2件目からは印刷しない。A4 1枚に収めるため。画面ではすべて出す。 */
function quote(item, index = 0) {
  return el("div", { class: index > 0 ? "quote extra" : "quote" }, el("small", { text: `${dateWords(item.date)}／${item.source}／${item.speaker}` }), `「${item.body}」`);
}

// 今出している紙の番号。削除のボタンが消す紙を決める
let shownId = null;
let removeButton = null;

/* 削除のボタンを作り直す。2回押す形の「押した」状態を、紙が変わるたびに戻すため */
function placeRemove() {
  removeButton?.remove();
  removeButton = null;
  if (shownId === null) {
    return;
  }
  removeButton = twoStepButton("削除", "本当に削除する", async () => {
    try {
      await api(`/advocacy/made/sheet/${shownId}`, { method: "DELETE" });
    } catch (error) {
      status.textContent = error.message;
      return;
    }
    shownId = null;
    sheet.replaceChildren();
    placeRemove();
    status.textContent = "紙を削除しました。";
  }, "quiet", "いま出ている1枚の紙を消します（取り消せません）");
  document.querySelector(".actions").append(removeButton);
}

function draw(data) {
  shownId = data.id;
  placeRemove();
  const parts = [
    el("header", {}, el("h1", { text: `${data.self_name}の1枚の紙` }), el("span", { class: "tag", text: `${dateWords(data.made_on)}　${data.deployment_label}` })),
    data.now.length ? el("p", { class: "now", text: `今の様子（${dateWords(data.now[0].date)}、${data.now[0].speaker_label || data.now[0].speaker}）：${data.now[0].body}` }) : "",
    el("h2", { text: "ツイン（AI）の代弁" }),
    el("p", { class: "claim", text: data.claim }),
    el("p", { class: "label", text: data.label }),
    data.reading ? el("p", { class: "label", text: data.reading }) : "",
    el(
      "div",
      { class: "topics" },
      ...data.topics.map((topic) =>
        el("section", { class: "topic" }, el("h3", { text: topic.title }), el("p", { text: topic.text }), ...topic.evidence.slice(0, QUOTES_PER_TOPIC).map(quote))
      )
    ),
  ];
  if (data.change) {
    parts.push(el("h2", { text: "10年の変わり方（ツイン（AI）の読み）" }), el("p", { text: data.change.text }), ...data.change.evidence.map(quote));
  }
  const left = el(
    "div",
    {},
    el("h2", { text: "ケアプラン原案（ツイン（AI）の案）" }),
    el(
      "dl",
      { class: "plan" },
      el("dt", { text: "本人の言葉" }),
      el("dd", {}, ...(data.plan.own_words.length ? data.plan.own_words.map((item) => quote(item)) : [el("span", { class: "empty", text: "案の根拠に本人の言葉はありません。" })])),
      el("dt", { text: "本人の意向（ツイン（AI）の案）" }),
      el("dd", { text: data.plan.self_intent }),
      el("dt", { text: "長期目標" }),
      el("dd", { text: data.plan.long_term_goal }),
      el("dt", { text: "根拠" }),
      el("dd", { text: data.plan.verdict })
    )
  );
  if (data.requests.length) {
    left.append(el("h2", { text: "ツイン（AI）からのお願い" }), ...data.requests.map((text) => el("p", { text })));
  }
  if (data.ask_back) {
    left.append(el("h2", { text: "ツイン（AI）から聞きたいこと" }), el("p", { text: data.ask_back }));
  }
  const right = el(
    "div",
    {},
    el("h2", { text: "大切なもの" }),
    data.treasure ? el("p", { class: "treasure", text: `「${data.treasure.text}」` }) : el("p", { class: "empty", text: "会議でご本人にうかがいます。" }),
    el("h2", { text: "会議でご本人が言った言葉" }),
    ...(data.meeting.length
      ? data.meeting.map((pair) =>
          el(
            "div",
            { class: "pair" },
            pair.asked ? el("div", { class: "asked", text: `${pair.asked.speaker}：${pair.asked.text}` }) : "",
            el("div", { class: "answer", text: `「${pair.answer.text}」` })
          )
        )
      : [el("p", { class: "empty", text: "まだ会議をしていません。" })])
  );
  parts.push(el("div", { class: "lower" }, left, right));
  sheet.replaceChildren(...parts);
}

async function make(again) {
  startWaiting(status, "ツイン（AI）が10年の記録を読んで書いています。数分かかることがあります。");
  try {
    const { sheet: data } = await api("/advocacy/sheet", { method: "POST", json: { again } });
    draw(data);
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
}

// ?print を付けると、印刷の見た目を画面で確かめられる
if (new URLSearchParams(location.search).has("print")) {
  document.getElementById("print-style").media = "all";
}

document.getElementById("make").addEventListener("click", () => make(false));
document.getElementById("again").addEventListener("click", () => make(true));
document.getElementById("print").addEventListener("click", () => window.print());

api("/advocacy/sheet")
  .then(({ sheet: data }) => {
    if (data) {
      draw(data);
    } else {
      status.textContent = "まだ紙がありません。「紙を作る」を押してください。";
    }
  })
  .catch((error) => {
    // 使えない人が URL を直接開いたとき。押せないボタンを残さない
    if (error.status === 404) {
      document.querySelector(".actions").remove();
      sheet.remove();
      status.replaceChildren("1枚の紙は、今は使えません。", " ", el("a", { href: "./", text: "もとの画面へ戻る", tip: "アプリのもとの画面を開きます" }));
      return;
    }
    status.textContent = error.message;
  });
