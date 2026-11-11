/*
  役割：タイムライン。便りのカードを新しい順に並べ、下にスレッドを付ける。
  4つの極：2. 分かち合う。
  守る原則：10（見える便りはサーバーが絞る）、14（いいねの数、通知、既読、おすすめ表示を作らない）。

  カードは囲まず白い面を積み、コメントは山吹の線でスレッドにする。
  PC版は横の余りに「月から探す」を置く。読み込んだ便りの月を並べるだけで、新しい受け口は増やさない。
  便りは新しいほうから約30件ずつ読み、「もっと前を読む」で下に足す。記録が増えても、最初に読む量を変えないため。
  開いている間は、いちばん新しいページと、読み足したページのうち1つを読み直して、新しいコメントを取りに行く。
  読み足したページを毎回全部読むと、読み足すほど通信が増えるため。
*/

import { api } from "./api.js";
import { personName, stampPicker, threadComment } from "./bubbles.js";
import { stampText } from "./stamps.js";
import { avatar, dateLabel, el, photoImage, sendWithKeys } from "./ui.js";

const POLL_MS = 5000;

export function renderTimeline(container, ctx) {
  let alive = true;
  let timer = null;
  // 次に読み足す位置。5秒ごとの読み込みでは動かさない。いちばん新しいページの切れ目は便りが増えると動くので、
  // それで上書きすると、まだ出していない便りを飛ばしたり、同じページを読み続けたりするため。
  let nextBefore = null;
  // 読み足したときの位置。新しいコメントを取りに行くとき、順に1つずつ読み直す。
  const olderPages = [];
  let refreshTurn = 0;
  const cards = new Map();
  const people = new Map([[ctx.me.id, ctx.me]]);
  const list = el("div", {});
  const emptyNote = el("p", { class: "hint", hidden: true });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  // 長い一覧のいちばん下で押すので、失敗の文もボタンのそばに出す
  const moreError = el("p", { class: "error", role: "alert", hidden: true });
  const more = el("button", { type: "button", class: "wide", text: "もっと前を読む", tip: "さらに前の便りを読み込んで、下に足します", hidden: true });
  const months = el("div", { class: "month-list" });
  const monthNote = el("p", { class: "hint", text: "読み込んだ分の月です。一覧の下の「もっと前を読む」で増えます。", hidden: true });
  const monthAside = el("aside", { class: "feed-aside card", "aria-label": "月から探す", hidden: true }, el("h2", { text: "月から探す" }), months, monthNote);
  let drawnMonths = "";
  container.append(el("div", { class: "feed-layout" }, el("section", { class: "panel feed" }, emptyNote, message, list, moreError, more), monthAside));

  /* 出ている便りの月を並べる。押すと、その月のいちばん新しい便りへ移る。 */
  function drawMonths() {
    const found = new Set([...list.children].map((card) => card.dataset.month));
    const signature = [...found].join();
    monthNote.hidden = more.hidden;
    if (signature === drawnMonths) {
      return;
    }
    drawnMonths = signature;
    monthAside.hidden = found.size === 0;
    months.replaceChildren(
      ...[...found].map((month) =>
        el("button", {
          type: "button",
          class: "quiet month-button",
          text: month,
          tip: `${month}のいちばん新しい便りまで移ります`,
          onclick: () => list.querySelector(`[data-month="${month}"]`)?.scrollIntoView({ block: "start" }),
        })
      )
    );
  }

  function drawCard(letter, selfId) {
    const author = people.get(selfId);
    const thread = el("div", { class: "letter-thread" });
    const replyBox = el("textarea", { rows: 1, placeholder: "コメントを書く", "aria-label": "コメント" });
    const replyStatus = el("p", { class: "error", role: "alert", hidden: true });
    const sendButton = el("button", { type: "button", text: "コメントする", tip: "書いたコメントを、この便りの下に付けます" });
    // 書いてあるときだけ主ボタンの朱にする。便りごとに朱が並ぶと、便りより先にボタンへ目が行くため
    const markReady = () => sendButton.classList.toggle("primary", Boolean(replyBox.value.trim()));
    replyBox.addEventListener("input", markReady);
    let pendingKey = null;
    /* コメントを送る。スタンプも1つのコメントとして同じ道で送る。 */
    async function post(text, key, onSent) {
      sendButton.disabled = true;
      stamps.setBusy(true);
      replyStatus.hidden = true;
      try {
        const comment = await api(`/letters/${letter.id}/comments`, { method: "POST", json: { text, client_key: key } });
        onSent?.();
        // 返ってきたコメントをそのまま足す。読み足したページの便りは、次の読み込みに入らないことがあるため。
        updateThread(entry, { thread: [comment] });
        await load();
      } catch (error) {
        // 失敗しても書いた文を消さない。同じ印で送り直すので、コメントは増えない。
        replyStatus.textContent = error.message;
        replyStatus.hidden = false;
      } finally {
        sendButton.disabled = false;
        stamps.setBusy(false);
      }
    }
    // 書きかけの文は残す。文と混ざらないよう、印も別にする
    const stamps = stampPicker((name) => post(stampText(name), crypto.randomUUID()));
    sendButton.addEventListener("click", () => {
      const text = replyBox.value.trim();
      if (!text || sendButton.disabled) {
        return;
      }
      pendingKey = pendingKey || crypto.randomUUID();
      post(text, pendingKey, () => {
        replyBox.value = "";
        markReady();
        pendingKey = null;
      });
    });
    // Enter だけなら改行。Alt+Enter か Ctrl+Enter で「コメントする」を押したのと同じにする
    sendWithKeys(replyBox, sendButton);
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
          el("div", { class: "hint letter-scope", text: `見せる相手：${letter.share_with}` })
        ),
        letter.photo_id ? photoImage(letter.photo_id, "letter-photo", "便りの写真") : null,
        el("div", { class: "letter-body", text: letter.body }),
        el("p", { class: "hint letter-note", text: `ツイン（AI）の下書きを、${personName(author)}が確かめて共有しました。` }),
        thread,
        replyStatus,
        el("div", { class: "letter-reply" }, replyBox, stamps.button, sendButton),
        stamps.choices
      )
    );
    const shared = new Date(letter.shared_at);
    card.dataset.month = `${shared.getFullYear()}年${shared.getMonth() + 1}月`;
    const entry = { card, thread, shown: new Set() };
    return entry;
  }

  /* スレッドに、まだ出していないコメントを足す。送った直後に足すと、先に届いていた人のコメントがあとから来るので、順番の位置に入れる。 */
  function updateThread(entry, letter) {
    for (const item of letter.thread) {
      if (!entry.shown.has(item.id)) {
        entry.shown.add(item.id);
        const node = threadComment(item, { people });
        node.dataset.seq = item.seq;
        const later = [...entry.thread.children].find((child) => Number(child.dataset.seq) > item.seq);
        entry.thread.insertBefore(node, later || null);
      }
    }
  }

  function addPeople(data) {
    for (const person of data.people) {
      people.set(person.id, person);
    }
  }

  /* 便りのカードを出す。すでにあればスレッドだけを新しくする。place は、新しいカードを置く場所を決める。 */
  function showLetter(letter, selfId, place) {
    let entry = cards.get(letter.id);
    if (!entry) {
      entry = drawCard(letter, selfId);
      cards.set(letter.id, entry);
      place(entry.card);
    }
    updateThread(entry, letter);
  }

  /* いちばん新しいページを読む。 */
  async function load() {
    try {
      const data = await api("/timeline");
      if (!alive) {
        return;
      }
      message.hidden = true;
      addPeople(data);
      // 出しているカードとひとつもつながらないのは、記録がまとめて増えたとき（10年後へ進むなど）。
      // 間の便りを読めなくしないよう、最初から並べ直す。
      if (cards.size > 0 && data.items.length > 0 && !data.items.some((letter) => cards.has(letter.id))) {
        cards.clear();
        list.replaceChildren();
        olderPages.length = 0;
      }
      // 続きの位置を受け取るのは、まだカードがないときだけ
      if (cards.size === 0) {
        nextBefore = data.next_before;
        more.hidden = !nextBefore;
      }
      emptyNote.hidden = data.items.length > 0;
      emptyNote.textContent = ctx.me.role === "本人" ? "まだ便りはありません。ツインとの話のあとで共有できます。" : "見られる便りは、まだありません。";
      data.items.forEach((letter, index) => {
        showLetter(letter, data.self_id, (card) => list.insertBefore(card, list.children[index] || null));
      });
      drawMonths();
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  }

  /* 読み足したページを1つだけ読み直す。スレッドのツインの問いは裏で書かれるので、読み足した便りにも取りに行く。 */
  async function refreshOlderPage() {
    if (olderPages.length === 0) {
      return;
    }
    const before = olderPages[refreshTurn % olderPages.length];
    refreshTurn += 1;
    try {
      const data = await api(`/timeline?before=${before}`);
      if (!alive) {
        return;
      }
      addPeople(data);
      for (const letter of data.items) {
        const entry = cards.get(letter.id);
        if (entry) {
          updateThread(entry, letter);
        }
      }
    } catch {
      // 通信の一時的な失敗では止めない。次の順番で読み直す。
    }
  }

  more.addEventListener("click", async () => {
    more.disabled = true;
    moreError.hidden = true;
    try {
      const before = nextBefore;
      const data = await api(`/timeline?before=${before}`);
      if (!alive) {
        return;
      }
      addPeople(data);
      for (const letter of data.items) {
        showLetter(letter, data.self_id, (card) => list.append(card));
      }
      olderPages.push(before);
      nextBefore = data.next_before;
      more.hidden = !nextBefore;
      drawMonths();
    } catch (error) {
      moreError.textContent = error.message;
      moreError.hidden = false;
    } finally {
      more.disabled = false;
    }
  });

  async function loop() {
    await load();
    await refreshOlderPage();
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
