/*
  役割：専門職の記録の画面。意思モデルの要素と、見える言明を、花びらと4つの視点の仮説つきで出す。
  4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
  守る原則：2（今の様子、本人の言葉、AIの推測の順）、3（出どころを分ける）、10（見える範囲はサーバーが決める）。

  花びらの名前と根拠は、専門職の画面にだけ出す。件数は出さない。「本人だけ」の記録の数が分かってしまうため。
*/

import { openTrace } from "./advocate.js";
import { api } from "./api.js";
import { el } from "./ui.js";

const PAGE = 30;
// 仕組みの言葉を、読む人の言葉にする
const UNIT_WORDS = { 月: "1か月のまとめ", 年: "1年のまとめ", 人生の段階: "長い期間のまとめ" };
const KIND_WORDS = { 価値: "大切にしていること", 判断の決まり: "決めるときの決まり", 優先順位: "優先すること", 変化: "変わったこと" };

function dateWords(iso) {
  const [year, month, day] = iso.split("T")[0].split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

function statementCard(item) {
  const detail = el("div", {});
  let opened = false;
  const kind = item.source === "本人の言葉" || item.source === "本人の訂正" ? "self" : item.source === "AIの推測" ? "guess" : item.source.startsWith("今の様子") ? "now" : "testimony";
  return el(
    "article",
    { class: `source-card ${kind}` },
    el("p", { class: "lead-in", text: `${dateWords(item.effective_date)}（${item.confirm_status}）` }),
    el("p", { class: "quote", text: item.body }),
    el(
      "div",
      { class: "source-meta" },
      el("span", { class: `source-label ${kind}`, text: item.source }),
      ...(item.petals || []).map((petal) => el("span", { class: "tag", text: petal })),
      el("button", {
        type: "button",
        class: "link",
        text: "AI の読み取りを見る",
        onclick: async () => {
          if (opened) {
            detail.replaceChildren();
            opened = false;
            return;
          }
          const { items } = await api(`/statements/${item.id}/interpretations`);
          // 本人の言葉の枠の中に見えないよう、二重線の別の枠に出す
          detail.replaceChildren(
            el(
              "div",
              { class: "ai-reading" },
              el("p", { class: "hint", text: "AI の読み取り（本人に確かめていません）" }),
              ...(items.length
                ? items.map((row) => el("p", { text: `${row.perspective}：${row.body}` }))
                : [el("p", { text: "読み取りはありません。" })])
            )
          );
          opened = true;
        },
      })
    ),
    detail
  );
}

function elementCard(item) {
  return el(
    "article",
    { class: "card" },
    el("p", { class: "hint", text: `${UNIT_WORDS[item.unit] || item.unit}／${KIND_WORDS[item.kind] || item.kind}／${dateWords(item.valid_from)}から／${item.confirmed ? "本人が確かめた記録から" : "本人がまだ確かめていない記録から"}` }),
    el("p", { text: item.body }),
    item.condition ? el("p", { class: "hint", text: `条件：${item.condition}` }) : "",
    el("div", { class: "row" }, ...(item.petals || []).map((petal) => el("span", { class: "tag", text: petal }))),
    el("button", { type: "button", class: "quiet", text: "根拠をたどる", onclick: () => openTrace(item.element_id) })
  );
}

function pager(draw) {
  const more = el("button", { type: "button", class: "wide", text: "もっと見る", hidden: true });
  const box = el("div", {});
  let all = [];
  let shown = 0;
  function showMore() {
    const next = all.slice(shown, shown + PAGE);
    box.append(...next.map(draw));
    shown += next.length;
    more.hidden = shown >= all.length;
  }
  more.addEventListener("click", showMore);
  return {
    nodes: [box, more],
    fill(items) {
      all = items;
      shown = 0;
      box.replaceChildren();
      showMore();
    },
  };
}

export function renderProRecords(container) {
  const nowBox = el("div", {});
  const records = pager(statementCard);
  const summaries = pager(elementCard);
  const message = el("p", { class: "error", role: "alert", hidden: true });
  container.append(
    el(
      "section",
      { class: "panel" },
      el("h1", { text: "記録" }),
      el("h2", { text: "今の様子" }),
      nowBox,
      el("h2", { text: "記録の一つひとつ" }),
      el("p", { class: "hint", text: "本人の言葉、証言、AIの推測の順に並びます。" }),
      ...records.nodes,
      el(
        "details",
        { class: "elements-fold" },
        el("summary", { text: "AI がまとめた文" }),
        el("p", { class: "hint", text: "本人の言葉ではなく、AI が記録をまとめた文です。根拠をたどって確かめてください。" }),
        ...summaries.nodes
      ),
      message
    )
  );
  Promise.all([api("/advocacy/elements"), api("/statements")])
    .then(([model, list]) => {
      const isNow = (item) => item.source.startsWith("今の様子");
      const now = list.items.filter(isNow);
      nowBox.replaceChildren(...(now.length ? now.slice(0, PAGE).map(statementCard) : [el("p", { text: "今の様子は、まだ入っていません。" })]));
      records.fill(list.items.filter((item) => !isNow(item)));
      summaries.fill(model.items);
    })
    .catch((error) => {
      message.textContent = error.message;
      message.hidden = false;
    });
}
