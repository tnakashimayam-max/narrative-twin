/*
  役割：専門職の記録の画面。意思モデルの要素と、見える言明を、花びらと4つの視点の仮説つきで出す。
  4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
  守る原則：2（今の様子、本人の言葉、AIの推測の順）、3（出どころを分ける）、10（見える範囲はサーバーが決める）。

  花びらの名前と根拠は、専門職の画面にだけ出す。件数は出さない。「本人だけ」の記録の数が分かってしまうため。
*/

import { api } from "./api.js";
import { el } from "./ui.js";

const PAGE = 30;

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
        text: "4つの視点の仮説",
        onclick: async () => {
          if (opened) {
            detail.replaceChildren();
            opened = false;
            return;
          }
          const { items } = await api(`/statements/${item.id}/interpretations`);
          detail.replaceChildren(
            ...(items.length
              ? items.map((row) => el("p", { class: "hint", text: `${row.perspective}（${row.status}）：${row.body}` }))
              : [el("p", { class: "hint", text: "仮説はありません。" })])
          );
          opened = true;
        },
      })
    ),
    detail
  );
}

export function renderProRecords(container) {
  const elements = el("div", {});
  const statements = el("div", {});
  const more = el("button", { type: "button", class: "wide", text: "もっと見る", hidden: true });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  container.append(
    el(
      "section",
      { class: "panel" },
      el("h1", { text: "記録" }),
      el("h2", { text: "意思モデルの要素" }),
      el("p", { class: "hint", text: "AI の読み取りです。根拠の言明をたどって確かめてください。" }),
      elements,
      el("h2", { text: "言明" }),
      el("p", { class: "hint", text: "今の様子、本人の言葉、証言、AIの推測の順に並びます。" }),
      statements,
      more,
      message
    )
  );
  let all = [];
  let shown = 0;
  function showMore() {
    const next = all.slice(shown, shown + PAGE);
    statements.append(...next.map(statementCard));
    shown += next.length;
    more.hidden = shown >= all.length;
  }
  more.addEventListener("click", showMore);
  Promise.all([api("/advocacy/elements"), api("/statements")])
    .then(([model, records]) => {
      elements.replaceChildren(
        ...(model.items.length
          ? model.items.slice(0, PAGE).map((item) =>
              el(
                "article",
                { class: "card" },
                el("p", { class: "hint", text: `${item.unit}／${item.kind}／${dateWords(item.valid_from)}から／${item.confirmed ? "本人が確かめた" : "AIの見立て"}` }),
                el("p", { text: item.body }),
                item.condition ? el("p", { class: "hint", text: `条件：${item.condition}` }) : "",
                el("div", { class: "row" }, ...(item.petals || []).map((petal) => el("span", { class: "tag", text: petal })))
              )
            )
          : [el("p", { text: "見える要素は、まだありません。" })])
      );
      all = records.items;
      showMore();
    })
    .catch((error) => {
      message.textContent = error.message;
      message.hidden = false;
    });
}
