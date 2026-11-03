/*
  役割：これまでの記録。本人が、日付ごとに会話、写真、便りを読み返す。
  4つの極：1. 書く・話す。
  守る原則：5（元の記録を消さない）、10（本人だけが開ける。サーバーでも確かめる）。
*/

import { api } from "./api.js";
import { bubble, personName } from "./bubbles.js";
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
      el("p", { class: "hint", text: "ツイン（AI）と話したことと便りを、日付ごとに残しています。日付を押すと開きます。" }),
      // 見える範囲の説明は消さない。長いので畳み、開けば全文を読めるようにする
      el(
        "details",
        { class: "fold" },
        el("summary", { text: "だれに見えるか" }),
        el("p", { class: "hint", text: "この一覧を開けるのはご本人だけです。便りは選んだ相手に見えます。託す人と範囲で選んだテーマの話は、託した人と招いた専門職にも見えます。" })
      ),
      empty,
      message,
      list,
      more
    )
  );
  const people = new Map([[ctx.me.id, ctx.me]]);
  let lastYear = "";

  /* 日付だけを並べ、押すと開く。長い記録でも目当ての日を探せるようにするため。写真も開いたときに読む。 */
  function drawDay(day) {
    const body = el("div", {});
    // 話題の題は記録にないので、日付と、何があった日かの札だけを出す
    const kinds = [
      day.conversations.length ? "会話" : "",
      day.conversations.some((conversation) => conversation.items.some((item) => item.photo_id)) || day.letters.some((letter) => letter.photo_id) ? "写真" : "",
      day.letters.length ? "便り" : "",
    ].filter(Boolean);
    const section = el(
      "details",
      { class: "day-fold rail-item" },
      el("summary", {}, el("span", { class: "rail-date", text: dateLabel(`${day.date}T12:00:00`) }), el("span", { class: "rail-kinds", text: kinds.join("・") })),
      body
    );
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
            el("p", { class: "hint letter-note", text: `ツイン（AI）の下書きを、${personName(ctx.me)}が確かめて共有した便りです。${letter.share_with}に見せています。` })
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
      for (const day of data.days) {
        const year = day.date.slice(0, 4);
        if (year !== lastYear) {
          lastYear = year;
          list.append(el("div", { class: "year-plate", text: year }));
        }
        list.append(drawDay(day));
      }
      // いちばん新しい日だけ、最初から開いておく
      const newest = list.querySelector(".rail-item");
      if (firstLoad && newest) {
        newest.open = true;
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
