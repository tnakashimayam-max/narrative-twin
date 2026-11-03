/*
  役割：トークとスレッドで同じ形の吹き出しを出す部品。
  4つの極：1. 書く・話す、2. 分かち合う。
  守る原則：3（だれの言葉かを分ける）、12（ツインには姿と「ツイン（AI）」を必ず出す）、
            14（タイムラインには、いいねの数、既読、通知を作らない）。

  トークの自分の発言には、既読の印の場所を持つ。数を入れるのは static/talk.js で、タイムラインでは空のまま。
  LINE のように、相手を左（アイコンと名前つき）、自分を右に出す。
  人の言葉は手書き風の文字にし、ツインの言葉はふつうの文字にする。
  ツインの問いは枠を太くして、問いだと分かるようにする。
*/

import { avatar, dateKey, dateLabel, el, photoImage, timeLabel, twinFigure } from "./ui.js";

export const TWIN_NAME = "ツイン（AI）";

export function personName(person) {
  return person?.called_name || person?.name || "はずした人";
}

/* これまでの日付を覚えておく入れ物。 */
export function newDateTracker() {
  return { last: null };
}

/* 日付が変わったときだけ区切りを返す。 */
export function dateDivider(tracker, iso) {
  const key = dateKey(iso);
  if (tracker.last === key) {
    return null;
  }
  tracker.last = key;
  return el("div", { class: "date-divider" }, el("span", { text: dateLabel(iso) }));
}

/* 1行の吹き出しを作る。message は docs/api.md の Message の形。 */
export function bubble(message, { viewerId, people, onRest } = {}) {
  if (message.kind === "お知らせ") {
    return el("p", { class: "room-notice", text: message.text });
  }
  const isTwin = message.is_twin;
  const mine = !isTwin && message.sender_user_id === viewerId;
  const person = isTwin ? null : people?.get(message.sender_user_id);
  const body = el("div", { class: "bubble-body" });
  if (!mine) {
    body.append(el("span", { class: "bubble-name", text: isTwin ? TWIN_NAME : personName(person) }));
  }
  if (message.photo_id) {
    body.append(el("div", { class: "bubble-line" }, photoImage(message.photo_id)));
  }
  const time = el("span", { class: "bubble-time", text: timeLabel(message.sent_at) });
  if (mine) {
    const mark = el("span", { class: "read-mark" });
    mark.dataset.seq = message.seq;
    time.prepend(mark);
  }
  if (message.text) {
    const classes = ["bubble"];
    if (!isTwin) {
      classes.push("person");
    }
    if (message.is_question) {
      classes.push("question");
    }
    // AI の推測は「推測」と表示する（原則4）
    if (isTwin && message.text.endsWith("合っていますか？")) {
      body.append(el("span", { class: "guess-tag", text: "推測" }));
    }
    body.append(el("div", { class: "bubble-line" }, el("div", { class: classes.join(" "), text: message.text }), time));
  } else {
    body.append(time);
  }
  if (message.question_id && isTwin && onRest) {
    body.append(
      el(
        "div",
        { class: "bubble-actions" },
        message.question_rested
          ? el("span", { class: "hint", text: "この話はお休みにしました。" })
          : el("button", {
              type: "button",
              class: "link",
              text: "この話はお休みにする",
              onclick: (event) => onRest(message, event.currentTarget),
            })
      )
    );
  }
  const row = el("div", { class: `bubble-row${mine ? " mine" : ""}` });
  if (!mine) {
    row.append(isTwin ? twinFigure() : avatar(person));
  }
  row.append(body);
  return row;
}
