/*
  役割：これまでの記録。本人が、日付ごとに会話、写真、便りを読み返す。
  4つの極：1. 書く・話す。
  守る原則：5（元の記録を消さない）、10（本人だけが開ける。サーバーでも確かめる）。
*/

import { api } from "./api.js";
import { bubble } from "./bubbles.js";
import { dateLabel, el, photoImage } from "./ui.js";

export function renderRecords(container, ctx) {
  let alive = true;
  let nextBefore = null;
  const list = el("div", {});
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const more = el("button", { type: "button", class: "wide", text: "もっと前を見る", hidden: true });
  const empty = el("p", { class: "hint", text: "まだ記録はありません。ツインと話すと、ここに日付ごとに残ります。", hidden: true });
  container.append(
    el(
      "section",
      { class: "panel" },
      el("h1", { text: "これまでの記録" }),
      el("p", { class: "hint", text: "日付を押すと開きます。この一覧を開けるのはご本人だけです。便りは選んだ相手に見えます。託す人と範囲で選んだテーマの話は、託した人と招いた専門職にも見えます。" }),
      empty,
      message,
      list,
      more
    )
  );
  const people = new Map([[ctx.me.id, ctx.me]]);

  /* 日付だけを並べ、押すと開く。長い記録でも目当ての日を探せるようにするため。写真も開いたときに読む。 */
  function drawDay(day) {
    const body = el("div", {});
    const section = el("details", { class: "card day-fold" }, el("summary", { text: dateLabel(`${day.date}T12:00:00`) }), body);
    let drawn = false;
    section.addEventListener("toggle", () => {
      if (!section.open || drawn) {
        return;
      }
      drawn = true;
      for (const conversation of day.conversations) {
        const box = el("div", {});
        for (const item of conversation.items) {
          box.append(bubble(item, { viewerId: ctx.me.id, people }));
        }
        body.append(box);
      }
      for (const letter of day.letters) {
        body.append(
          el(
            "article",
            { class: "letter-card" },
            letter.photo_id ? photoImage(letter.photo_id, "letter-photo", "便りの写真") : null,
            el("div", { class: "letter-body", text: letter.body }),
            el("p", { class: "hint", style: "padding:0 14px 8px", text: `便り（${letter.share_with}に見せています）` })
          )
        );
      }
    });
    return section;
  }

  async function load() {
    const query = nextBefore ? `?before=${nextBefore}` : "";
    try {
      const data = await api(`/archive${query}`);
      if (!alive) {
        return;
      }
      const firstLoad = list.children.length === 0;
      list.append(...data.days.map(drawDay));
      // いちばん新しい日だけ、最初から開いておく
      if (firstLoad && list.children[0]) {
        list.children[0].open = true;
      }
      nextBefore = data.next_before;
      more.hidden = !nextBefore;
      empty.hidden = list.children.length > 0;
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  }
  more.addEventListener("click", load);
  load();
  return () => {
    alive = false;
  };
}
