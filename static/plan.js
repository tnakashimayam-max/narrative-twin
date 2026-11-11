/*
  役割：ケアプラン案のページを組み立てる（仮置き）。呼び方が決まるまでの最小の形。
  4つの極：4. 10年後に代弁する。
  守る原則：3（ツインの文と記録を分ける）、5（前の版を残す）、9（本人の言葉は記録の本文のまま）。

  本人の言葉の本文は、サーバーが記録から入れたものだけを出す。ここで文を作らない。
  案は「この案を削除」で消せる。取り消せないので2回押す形にする（app/made_documents.py）。
*/

import { api } from "./api.js";
import { el, startWaiting, twoStepButton } from "./ui.js";

const area = document.getElementById("plan");
const status = document.getElementById("status");
const textBox = document.getElementById("text");

function dateWords(iso) {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

function quote(item) {
  return el("div", { class: "quote" }, el("small", { text: `${dateWords(item.date)}／${item.source}／${item.speaker}` }), `「${item.body}」`);
}

async function step(path, json, message) {
  status.className = "";
  startWaiting(status, message);
  try {
    const { plan } = await api(path, { method: "POST", json });
    draw(plan);
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
    status.className = "error";
  }
}

function firstCard(first) {
  return el(
    "section",
    {},
    el("h2", { text: "第1版" }),
    el("p", { class: "hint", text: first.source }),
    el("p", { text: first.summary }),
    ...first.items.map((item) => el("div", { class: "item" }, el("strong", { text: item.aim }), item.support))
  );
}

function secondCard(second) {
  return el(
    "section",
    {},
    el("h2", { text: "第2版" }),
    el("p", { class: "hint", text: second.label }),
    el("p", { text: second.summary }),
    ...second.items.map((item) => el("div", { class: "item" }, el("strong", { text: item.aim }), item.support, el("p", { class: "hint", text: `なぜ：${item.why}` }), ...item.evidence.map(quote))),
    el("h3", { text: "本人の言葉" }),
    ...second.plan.own_words.map((item) => el("p", { class: "own-word" }, el("small", { text: `${item.date}／${item.source}` }), el("br", {}), item.body)),
    el("h3", { text: "本人の意向（ツイン（AI）の案）" }),
    el("p", { text: second.plan.self_intent }),
    el("h3", { text: "長期目標（案）" }),
    el("p", { text: second.plan.long_term_goal })
  );
}

function objectionCard(objection) {
  return el(
    "section",
    {},
    el("h2", { text: "ツイン（AI）の異議" }),
    el("p", { class: "hint", text: `${objection.label}　根拠：${objection.verdict}` }),
    el("p", { class: "claim", text: objection.claim }),
    objection.reading ? el("p", { class: "hint", text: objection.reading }) : "",
    ...objection.points.map((point) => el("div", { class: "item" }, el("strong", { text: point.target }), point.objection, ...point.evidence.map(quote))),
    ...(objection.requests.length ? [el("h3", { text: "ツインからのお願い" }), ...objection.requests.map((text) => el("p", { text }))] : []),
    objection.ask_back ? el("p", {}, el("strong", { text: "ツインから聞きたいこと：" }), objection.ask_back) : ""
  );
}

/* どの版を使うかは人が選ぶ。ツイン（AI）は選ばない。 */
function choiceCard(plan) {
  const chosen = plan.choice;
  return el(
    "section",
    {},
    el("h2", { text: "どの版を使いますか" }),
    el("p", { class: "hint", text: "ツイン（AI）は選びません。" }),
    chosen ? el("p", { class: "chosen", role: "status", text: `${chosen.chooser}が「${chosen.version}」を選びました。` }) : "",
    ...["第1版", "第2版", "どちらも使わない"].map((version) =>
      el("button", {
        type: "button",
        class: chosen?.version === version ? "primary" : "",
        "aria-pressed": String(chosen?.version === version),
        text: version === "どちらも使わない" ? version : `${version}を使う`,
        tip: version === "どちらも使わない" ? "どちらの版も使わないと、人が決めたことを残します" : `${version}を使うと、人が決めたことを残します`,
        onclick: () => step(`/advocacy/plans/${plan.id}/choose`, { version }, "記録しています。"),
      })
    )
  );
}

function draw(plan) {
  const nextButton = !plan.objection
    ? el("button", { type: "button", class: "primary", text: "ツインの異議を聞く", tip: "ツイン（AI）が本人の記録を読み、この案への異議を出します", onclick: () => step(`/advocacy/plans/${plan.id}/objection`, {}, "ツイン（AI）が10年の記録を読んで考えています。") })
    : !plan.second
      ? el("button", { type: "button", class: "primary", text: "異議をもとに第2版を作る", tip: "ツイン（AI）が異議をもとに修正した第2版を書きます", onclick: () => step(`/advocacy/plans/${plan.id}/revise`, {}, "ツイン（AI）が第2版を書いています。") })
      : "";
  area.replaceChildren(
    el("div", { class: "compare" }, firstCard(plan.first), plan.second ? secondCard(plan.second) : el("section", {}, el("h2", { text: "第2版" }), el("p", { class: "hint", text: "まだありません。" }))),
    plan.objection ? objectionCard(plan.objection) : "",
    plan.second ? choiceCard(plan) : "",
    nextButton,
    el(
      "p",
      {},
      twoStepButton("この案を削除", "本当に削除する", async () => {
        try {
          await api(`/advocacy/made/plan/${plan.id}`, { method: "DELETE" });
        } catch (error) {
          status.textContent = error.message;
          status.className = "error";
          return;
        }
        area.replaceChildren();
        status.textContent = "案を削除しました。";
        status.className = "";
      }, "quiet", "この案を、異議と第2版ごと消します（取り消せません）")
    )
  );
}

function startPlan(text) {
  step("/advocacy/plans", { client_key: crypto.randomUUID(), ...(text ? { text } : {}) }, text ? "入れています。" : "AI が一般的な案を作っています。");
}

document.getElementById("make").addEventListener("click", () => startPlan(textBox.value.trim()));

// 物語の2034年に前のケアマネジャーが勧めた案。人が入れる案なので AI を呼ばず、毎回同じ第1版から始められる
const EXAMPLE_PLAN = "週2回のデイサービスと配食弁当";
document.getElementById("example").addEventListener("click", () => {
  textBox.value = EXAMPLE_PLAN;
  startPlan(EXAMPLE_PLAN);
});

api("/advocacy/plans")
  .then(({ plan }) => {
    if (plan) {
      draw(plan);
    }
  })
  .catch((error) => {
    // 使えない人が URL を直接開いたとき。入れられない欄を残さない
    if (error.status === 404) {
      document.getElementById("start").hidden = true;
      status.replaceChildren("ケアプラン案は、今は使えません。", " ", el("a", { href: "./", text: "もとの画面へ戻る", tip: "アプリのもとの画面を開きます" }));
      return;
    }
    status.textContent = error.message;
    status.className = "error";
  });
