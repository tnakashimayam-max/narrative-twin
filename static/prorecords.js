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

const PERSPECTIVE_WORDS = {
  表の言葉: "言葉どおりの内容",
  本当のニーズ: "奥にあるかもしれない願い",
  本人の真意: "奥にあるかもしれない思い",
  気持ち: "気持ちの見立て",
};
const CONFIRM_WORDS = { 確認済み: "本人が確かめた", 訂正済み: "本人が直した", 未確認: "まだ確かめていない" };
const FILTERS = [
  ["本人の言葉", (item) => item.source === "本人の言葉" || item.source === "本人の訂正"],
  ["家族と縁のある人の証言", (item) => item.source.endsWith("証言")],
  ["AIの推測", (item) => item.source === "AIの推測"],
];

function kindOf(item) {
  if (item.source === "本人の言葉" || item.source === "本人の訂正") {
    return "self";
  }
  if (item.source === "AIの推測") {
    return "guess";
  }
  return item.source.startsWith("今の様子") ? "now" : "testimony";
}

function statementCard(item, names) {
  const detail = el("div", {});
  let opened = false;
  const kind = kindOf(item);
  const speaker = names.get(item.speaker_user_id);
  const lead = kind === "now" || kind === "testimony" ? `${dateWords(item.effective_date)}、${speaker || "はずした人"}の記録です` : dateWords(item.effective_date);
  const card = el(
    "article",
    { class: `source-card ${kind}` },
    el("p", { class: "lead-in", text: lead }),
    el("p", { class: "quote", text: item.body }),
    el(
      "div",
      { class: "source-meta" },
      el("span", { class: `source-label ${kind}`, text: item.source }),
      kind === "now" ? "" : el("span", { class: "hint", text: CONFIRM_WORDS[item.confirm_status] || item.confirm_status }),
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
          // 本人の言葉の枠の外に、二重線の別の枠で出す
          detail.replaceChildren(
            el(
              "div",
              { class: "ai-reading" },
              el("p", { class: "hint", text: "AI の読み取り（本人に確かめていません）" }),
              ...(items.length
                ? items.map((row) => el("p", { text: `${PERSPECTIVE_WORDS[row.perspective] || row.perspective}：${row.body}` }))
                : [el("p", { text: "読み取りはありません。" })])
            )
          );
          opened = true;
        },
      })
    )
  );
  return el("div", {}, card, detail);
}

function elementCard(item) {
  return el(
    "article",
    { class: "card" },
    el("p", { class: "hint", text: `${UNIT_WORDS[item.unit] || item.unit}／${KIND_WORDS[item.kind] || item.kind}／${item.valid_from.slice(0, 4)}年ごろから／${item.confirmed ? "本人が確かめた記録を含む" : "本人がまだ確かめていない記録から"}` }),
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
  let names = new Map();
  const records = pager((item) => statementCard(item, names));
  const summaries = pager(elementCard);
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const filterRow = el("div", { class: "row" });
  const search = el("input", { type: "search", "aria-label": "言葉で絞る", placeholder: "言葉で絞る（例：物忘れ）", maxLength: 40 });
  let all = [];
  let current = FILTERS[0];
  function apply() {
    const words = search.value.trim();
    records.fill(all.filter((item) => current[1](item) && (!words || item.body.includes(words))));
    [...filterRow.children].forEach((button, index) => button.setAttribute("aria-pressed", String(FILTERS[index] === current)));
  }
  filterRow.append(
    ...FILTERS.map((filter) =>
      el("button", {
        type: "button",
        class: "quiet",
        text: filter[0],
        onclick: () => {
          current = filter;
          apply();
        },
      })
    )
  );
  search.addEventListener("input", apply);
  container.append(
    el(
      "section",
      { class: "panel" },
      el("h1", { text: "記録" }),
      el("h2", { text: "今の様子" }),
      nowBox,
      el("h2", { text: "記録の一つひとつ" }),
      filterRow,
      search,
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
  Promise.all([api("/advocacy/elements"), api("/statements"), api("/users")])
    .then(([model, list, users]) => {
      names = new Map(users.items.map((user) => [user.id, user.name]));
      const isNow = (item) => item.source.startsWith("今の様子");
      const now = list.items.filter(isNow);
      nowBox.replaceChildren(...(now.length ? now.slice(0, PAGE).map((item) => statementCard(item, names)) : [el("p", { text: "今の様子は、まだ入っていません。" })]));
      all = list.items.filter((item) => !isNow(item));
      apply();
      summaries.fill(model.items);
    })
    .catch((error) => {
      message.textContent = error.message;
      message.hidden = false;
    });
}
