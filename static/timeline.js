/*
  役割：タイムライン。便りのカードを新しい順に並べ、下にスレッドを付ける。
  4つの極：2. 分かち合う。
  守る原則：10（見える便りはサーバーが絞る）、14（いいねの数、通知、既読、おすすめ表示を作らない）。

  カードは囲まず白い面を積み、コメントは山吹の線でスレッドにする。開いている間は新しいコメントを取りに行く。
*/

import { api } from "./api.js";
import { personName, threadComment } from "./bubbles.js";
import { avatar, dateLabel, el, photoImage } from "./ui.js";

const POLL_MS = 5000;

export function renderTimeline(container, ctx) {
  let alive = true;
  let timer = null;
  const cards = new Map();
  const people = new Map();
  const list = el("div", {});
  const emptyNote = el("p", { class: "hint", hidden: true });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  container.append(el("section", { class: "panel feed" }, emptyNote, message, list));

  function drawCard(letter, selfId) {
    const author = people.get(selfId);
    const thread = el("div", { class: "letter-thread" });
    const replyBox = el("textarea", { rows: 1, placeholder: "コメントを書く", "aria-label": "コメント" });
    const replyStatus = el("p", { class: "error", role: "alert", hidden: true });
    const sendButton = el("button", { type: "button", class: "primary", text: "コメントする" });
    let pendingKey = null;
    sendButton.addEventListener("click", async () => {
      const text = replyBox.value.trim();
      if (!text || sendButton.disabled) {
        return;
      }
      pendingKey = pendingKey || crypto.randomUUID();
      sendButton.disabled = true;
      replyStatus.hidden = true;
      try {
        await api(`/letters/${letter.id}/comments`, { method: "POST", json: { text, client_key: pendingKey } });
        replyBox.value = "";
        pendingKey = null;
        await load();
      } catch (error) {
        // 失敗しても書いた文を消さない。同じ印で送り直すので、コメントは増えない。
        replyStatus.textContent = error.message;
        replyStatus.hidden = false;
      } finally {
        sendButton.disabled = false;
      }
    });
    const card = el(
      "article",
      { class: "letter-card" },
      avatar(author),
      el(
        "div",
        { class: "letter-main" },
        el(
          "div",
          { class: "letter-head" },
          el("strong", { text: personName(author) }),
          el("span", { class: "hint", text: dateLabel(letter.shared_at) }),
          el("div", { class: "hint letter-scope", text: `${letter.share_with}に見せています` })
        ),
        letter.photo_id ? photoImage(letter.photo_id, "letter-photo", "便りの写真") : null,
        el("div", { class: "letter-body", text: letter.body }),
        el("p", { class: "hint letter-note", text: `ツイン（AI）の下書きを、${personName(author)}が確かめて共有しました。` }),
        thread,
        replyStatus,
        el("div", { class: "letter-reply" }, replyBox, sendButton)
      )
    );
    return { card, thread, shown: new Set() };
  }

  function updateThread(entry, letter) {
    for (const item of letter.thread) {
      if (!entry.shown.has(item.id)) {
        entry.shown.add(item.id);
        entry.thread.append(threadComment(item, { people }));
      }
    }
  }

  async function load() {
    try {
      const data = await api("/timeline");
      if (!alive) {
        return;
      }
      message.hidden = true;
      for (const person of data.people) {
        people.set(person.id, person);
      }
      emptyNote.hidden = data.items.length > 0;
      emptyNote.textContent = ctx.me.role === "本人" ? "まだ便りはありません。ツインとの話のあとで共有できます。" : "見られる便りは、まだありません。";
      data.items.forEach((letter, index) => {
        let entry = cards.get(letter.id);
        if (!entry) {
          entry = drawCard(letter, data.self_id);
          cards.set(letter.id, entry);
          const next = list.children[index];
          if (next) {
            list.insertBefore(entry.card, next);
          } else {
            list.append(entry.card);
          }
        }
        updateThread(entry, letter);
      });
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  }

  async function loop() {
    await load();
    if (alive) {
      timer = setTimeout(loop, POLL_MS);
    }
  }
  loop();
  return () => {
    alive = false;
    clearTimeout(timer);
  };
}
