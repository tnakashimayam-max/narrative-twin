/*
  役割：トークの一覧と部屋の画面。本人とツイン、人どうし、グループのやり取りを出す。
  4つの極：1. 書く・話す（ツインとの部屋）、2. 分かち合う（人どうしの部屋）。
  守る原則：7（催促や責める言葉を出さない。「今日はここまで」はいつも見える）、
            12（ツインには姿と「ツイン（AI）」を必ず出す）、
            15（ツインが読むかどうかを部屋の上に出す）。

  1対1とグループには未読の数と既読を出す。通知は作らず、読んだ人の名前も出さない。
  画面が見えているときにだけ、読んだ位置を送る。開いたままの別のウィンドウで既読にしないため。

  使い心地のために次のようにしている。
  - 送ると自分の言葉がすぐ出る。失敗しても、書いた文と写真を消さず「もう一度送る」で送り直せる。
  - 同じ client_key で送り直すので、発言は増えない。送っている間はボタンを止めるので、二重に送れない。
  - 送るのはボタンだけ。日本語の変換を決める Enter で送らないよう、Enter は改行のままにする。
  - 開いている部屋は3秒ごとに新しい発言を取りに行く。途中で閉じても、開き直すと続きが見える。
*/

import { api, uploadPhoto } from "./api.js";
import { bubble, dateDivider, newDateTracker, personName, TWIN_NAME } from "./bubbles.js";
import { shrinkPhoto } from "./photo.js";
import { openShareSheet } from "./share.js";
import { avatar, el, openSheet, twinFigure } from "./ui.js";

const POLL_MS = 3000;
// サーバーの上限（app/rooms.py の text）と同じにする
const MAX_MESSAGE_CHARS = 4000;
const BANNER_NO_TWIN = "ツイン（AI）はこの部屋を読みません";
const BANNER_TWIN = "ツイン（AI）がいます。ここでの話を読みます";
const GUESS_ENDING = "合っていますか？";
const GUESS_ANSWERS = ["そうです", "ちがいます", "今は話したくない"];
const DISMISSED_SHARE = "nt_dismissed_share";

function newKey() {
  return crypto.randomUUID();
}

/* トークの一覧 */
export function renderTalk(container, ctx, options = {}) {
  if (options.roomId) {
    return openRoom(container, ctx, options.roomId);
  }
  let alive = true;
  const list = el("ul", { class: "room-list" });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const panel = el("section", {}, list, message);
  container.append(panel);

  async function load() {
    try {
      const { items } = await api("/rooms");
      if (!alive) {
        return;
      }
      list.replaceChildren(...items.map((room) => el("li", {}, roomRow(room, ctx))));
      if (items.length === 0) {
        list.append(el("li", { class: "panel" }, el("p", { class: "hint", text: "入っている部屋はまだありません。" })));
      }
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  }
  panel.append(
    el(
      "div",
      { class: "panel" },
      el("p", { class: "hint", text: "家族や知人と話す部屋です。ツイン（AI）は招かれた部屋だけを読みます。" }),
      el("button", {
        type: "button",
        class: "wide",
        text: "新しい部屋を作る",
        onclick: () => openNewRoomSheet(ctx),
      })
    )
  );
  load();
  // 開いている間に届いた発言が分かるよう、一覧を読み直す
  const timer = setInterval(load, POLL_MS);
  return () => {
    alive = false;
    clearInterval(timer);
  };
}

function roomRow(room, ctx) {
  const pinned = room.kind === "ツイン";
  const icon = pinned ? twinFigure() : room.kind === "1対1" ? avatar(room.members.find((m) => m.id !== ctx.me.id)) : groupIcon();
  return el(
    "button",
    {
      type: "button",
      class: `room-item${pinned ? " pinned" : ""}`,
      onclick: () => ctx.openTab("talk", { roomId: room.id }),
    },
    icon,
    el(
      "span",
      { class: "room-text" },
      el("span", { class: "room-name", text: room.name }),
      el("span", { class: "room-preview", text: room.last_text || (pinned ? "写真やひとことを送ってみてください" : "") })
    ),
    room.unread_count
      ? el("span", { class: "unread-count", "aria-label": `新しい発言 ${room.unread_count}件`, text: String(room.unread_count) })
      : ""
  );
}

function groupIcon() {
  return el("span", { class: "avatar", "aria-hidden": "true" }, "組");
}

/* 新しい部屋を作る */
async function openNewRoomSheet(ctx) {
  let people;
  try {
    people = (await api("/people/choices")).items;
  } catch (error) {
    return openSheet((inner, close) => inner.append(el("p", { class: "error", text: error.message }), el("button", { type: "button", text: "閉じる", onclick: close })));
  }
  return openSheet((inner, close) => {
    const checks = people.map((person) => ({
      person,
      input: el("input", { type: "checkbox" }),
    }));
    const nameInput = el("input", { type: "text", maxLength: 40, placeholder: "例：家族の部屋" });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    inner.append(
      el("h2", { text: "新しい部屋を作る" }),
      el("p", { class: "hint", text: "話したい人を選んでください。1人なら二人の部屋、2人以上ならグループになります。" }),
      ...checks.map(({ person, input }) => el("label", { class: "check" }, input, `${personName(person)}（${person.role}）`)),
      el("label", { class: "field" }, "部屋の名前（グループのとき）", nameInput),
      message,
      el(
        "div",
        { class: "row" },
        el("button", {
          type: "button",
          class: "primary",
          text: "部屋を作る",
          onclick: async () => {
            const chosen = checks.filter(({ input }) => input.checked).map(({ person }) => person.id);
            const name = nameInput.value.trim();
            if (chosen.length === 0) {
              message.textContent = "部屋に入る人を選んでください。";
              message.hidden = false;
              return;
            }
            try {
              const room = await api("/rooms", {
                method: "POST",
                json: { kind: chosen.length === 1 && !name ? "1対1" : "グループ", member_ids: chosen, name: name || null },
              });
              close();
              ctx.openTab("talk", { roomId: room.id });
            } catch (error) {
              message.textContent = error.message;
              message.hidden = false;
            }
          },
        }),
        el("button", { type: "button", class: "quiet", text: "やめる", onclick: close })
      )
    );
  });
}

/* 部屋の画面 */
export function openRoom(container, ctx, roomId) {
  container.className = "view talk-view";
  ctx.showTabs(false);
  let alive = true;
  let timer = null;
  let lastSeq = 0;
  let room = null;
  let sending = false;
  let attached = null; // 送る前の写真 { blob, url }
  let shownShare = null;
  let firstUnreadSeq = null;
  let firstLoad = true;
  let readSent = 0;
  const people = new Map();
  const tracker = newDateTracker();
  const seen = new Set();
  const pendings = new Map();

  const title = el("div", { class: "room-title" });
  const headerIcon = el("span", {});
  const closeDayButton = el("button", { type: "button", class: "close-day", text: "今日はここまで", hidden: true });
  const twinToggle = el("button", { type: "button", class: "quiet", hidden: true });
  const removeButton = el("button", { type: "button", class: "quiet", text: "人をはずす", hidden: true });
  const leaveButton = el("button", { type: "button", class: "quiet", text: "部屋を出る", hidden: true });
  const bannerText = el("div", {});
  const banner = el(
    "div",
    { class: "room-banner", hidden: true },
    bannerText,
    el("div", { class: "row room-actions" }, twinToggle, removeButton, leaveButton)
  );
  const emptyNote = el("p", { class: "room-notice", text: "写真やひとことを送ってみてください。", hidden: true });
  const messagesInner = el("div", { class: "messages-inner" }, emptyNote);
  const pendingArea = el("div", {});
  const thinkingArea = el("div", {});
  const shareArea = el("div", {});
  const guessArea = el("div", {});
  const messagesBox = el("div", { class: "messages" }, messagesInner, guessArea, pendingArea, thinkingArea, shareArea);
  const statusLine = el("p", { class: "error", role: "alert", hidden: true });

  const textBox = el("textarea", { rows: 1, placeholder: "ひとこと", "aria-label": "送る言葉" });
  const fileInput = el("input", { type: "file", accept: "image/*", class: "visually-hidden", "aria-label": "写真を選ぶ" });
  const photoButton = el("label", { class: "button photo-button" }, "写真", fileInput);
  const sendButton = el("button", { type: "button", class: "primary send-button", text: "送る" });
  const previewArea = el("div", {});
  const composer = el(
    "div",
    { class: "composer" },
    el("div", { class: "composer-inner" }, statusLine, previewArea, el("div", { class: "composer-row" }, photoButton, textBox, sendButton))
  );

  container.append(
    el(
      "div",
      { class: "room-header" },
      el("button", {
        type: "button",
        class: "quiet",
        text: "一覧へ",
        onclick: () => ctx.openTab("talk"),
      }),
      headerIcon,
      title,
      closeDayButton
    ),
    banner,
    messagesBox,
    composer
  );

  // いちばん下を見ている間は、新しい吹き出しや写真が出ても下についていく
  let stick = true;

  function nearBottom() {
    return messagesBox.scrollHeight - messagesBox.scrollTop - messagesBox.clientHeight < 120;
  }

  function scrollDown() {
    messagesBox.scrollTop = messagesBox.scrollHeight;
    stick = true;
  }

  messagesBox.addEventListener("scroll", () => {
    stick = nearBottom();
  });
  // 写真は読み終わると高さが変わるので、読み終わったときにも下へ動かす
  messagesBox.addEventListener(
    "load",
    () => {
      if (stick) {
        scrollDown();
      }
    },
    true
  );

  function showStatus(text) {
    statusLine.textContent = text || "";
    statusLine.hidden = !text;
  }

  function setRoom(info) {
    room = info;
    title.textContent = room.name;
    // 幅の狭い画面では帯に入りきらないので、見出しの札に部屋の名前を出す
    ctx.setPlate("TALK", room.name);
    if (room.kind === "ツイン") {
      headerIcon.replaceChildren(twinFigure());
      closeDayButton.hidden = false;
      banner.hidden = true;
    } else {
      headerIcon.replaceChildren(room.kind === "1対1" ? avatar(room.members.find((m) => m.id !== ctx.me.id)) : groupIcon());
      banner.hidden = false;
      const others = room.members.filter((member) => member.id !== ctx.me.id).map((member) => member.called_name);
      bannerText.replaceChildren(
        el("div", { text: room.twin_present ? BANNER_TWIN : BANNER_NO_TWIN }),
        room.twin_present ? el("div", { text: "「@ツイン」と書くと、ツイン（AI）が答えます" }) : "",
        room.kind === "グループ" ? el("div", { class: "hint", text: `この部屋の人：${others.join("、")}` }) : ""
      );
      twinToggle.hidden = !room.can_manage_twin;
      twinToggle.textContent = room.twin_present ? "ツイン（AI）をはずす" : "ツイン（AI）を入れる";
      removeButton.hidden = !room.can_remove_members;
      leaveButton.hidden = room.kind !== "グループ";
    }
  }

  /* 自分の発言の既読の印を、ほかの人の読んだ位置から作る。名前は出さない。 */
  function drawReadMarks(positions) {
    for (const mark of messagesInner.querySelectorAll(".read-mark")) {
      const count = positions.filter((seq) => seq >= Number(mark.dataset.seq)).length;
      if (count === 0) {
        mark.textContent = "";
      } else {
        mark.textContent = room?.kind === "1対1" ? "既読 " : `既読 ${count}人 `;
      }
    }
  }

  /* 画面が見えているときにだけ、読んだ位置を送る。 */
  async function sendRead() {
    if (!room || room.kind === "ツイン" || document.visibilityState !== "visible" || lastSeq <= readSent) {
      return;
    }
    const seq = lastSeq;
    try {
      await api(`/rooms/${roomId}/read`, { method: "POST", json: { seq } });
      readSent = Math.max(readSent, seq);
    } catch {
      // 送れなくても次の読み込みで送り直す
    }
  }

  function clearGuessAnswers() {
    guessArea.replaceChildren();
  }

  /* 推測の問いへの答えのボタン。押すと、その言葉を本人の発言として送る（原則4）。 */
  function showGuessAnswers(item) {
    clearGuessAnswers();
    guessArea.append(
      el(
        "div",
        { class: "row guess-answers" },
        GUESS_ANSWERS.map((word) =>
          el("button", {
            type: "button",
            text: word,
            onclick: () => {
              textBox.value = word;
              send();
            },
          })
        )
      )
    );
  }

  function addMessages(items) {
    const following = stick;
    for (const item of items) {
      if (seen.has(item.id)) {
        continue;
      }
      seen.add(item.id);
      if (item.client_key && pendings.has(item.client_key)) {
        dropPending(item.client_key);
      }
      if (firstUnreadSeq !== null && item.seq === firstUnreadSeq) {
        messagesInner.append(el("div", { class: "date-divider new-divider" }, el("span", { text: "ここから新しい発言" })));
      }
      const divider = dateDivider(tracker, item.sent_at);
      if (divider) {
        messagesInner.append(divider);
      }
      messagesInner.append(bubble(item, { viewerId: ctx.me.id, people, onRest: ctx.me.role === "本人" ? restQuestion : null }));
      // いちばん新しい行が本人への推測の問いのときだけ、答えのボタンを出す
      clearGuessAnswers();
      if (room?.kind === "ツイン" && item.is_twin && item.is_question && item.text?.endsWith(GUESS_ENDING)) {
        showGuessAnswers(item);
      }
    }
    if (following) {
      scrollDown();
    }
  }

  async function restQuestion(message, button) {
    button.disabled = true;
    try {
      await api(`/questions/${message.question_id}/rest`, { method: "POST" });
      button.replaceWith(el("span", { class: "hint", text: "この話はお休みにしました。" }));
    } catch (error) {
      button.disabled = false;
      showStatus(error.message);
    }
  }

  function setThinking(on) {
    if (room?.kind === "ツイン") {
      headerIcon.replaceChildren(twinFigure({ thinking: on }));
    }
    thinkingArea.replaceChildren();
    if (on) {
      thinkingArea.append(
        el(
          "div",
          { class: "bubble-row" },
          twinFigure({ thinking: true }),
          el("div", { class: "bubble-body" }, el("span", { class: "bubble-name", text: TWIN_NAME }), el("span", { class: "hint", role: "status", text: "考えています" }))
        )
      );
      scrollDown();
    }
  }

  function shareDismissed(conversationId) {
    return sessionStorage.getItem(DISMISSED_SHARE) === String(conversationId);
  }

  function showShare(conversationId) {
    if (shownShare === conversationId) {
      return;
    }
    shownShare = conversationId;
    shareArea.replaceChildren();
    if (!conversationId || shareDismissed(conversationId)) {
      return;
    }
    const following = stick;
    shareArea.append(
      el(
        "div",
        { class: "card" },
        el("p", { text: "今日の話を、家族や縁のある人に見せることもできます。" }),
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            text: "この話を共有する",
            onclick: () => openShareSheet(conversationId, () => shareArea.replaceChildren()),
          }),
          el("button", {
            type: "button",
            class: "quiet",
            text: "今はしない",
            onclick: () => {
              sessionStorage.setItem(DISMISSED_SHARE, String(conversationId));
              shareArea.replaceChildren();
            },
          })
        )
      )
    );
    if (following) {
      scrollDown();
    }
  }

  async function poll() {
    try {
      const data = await api(`/rooms/${roomId}/messages?after=${lastSeq}`);
      if (!alive) {
        return;
      }
      for (const person of data.people) {
        people.set(person.id, person);
      }
      setRoom(data.room);
      // 線を引くのは開いたときの1回だけ。開いている間に届いた発言には引かない
      if (firstLoad) {
        firstUnreadSeq = data.first_unread_seq ?? null;
        firstLoad = false;
      }
      addMessages(data.items);
      // 発言がないと入力欄しか見えず、何をする場所か分からないので案内を出す
      emptyNote.hidden = seen.size > 0 || pendings.size > 0;
      lastSeq = Math.max(lastSeq, data.last_seq);
      if (data.others_read_seqs) {
        drawReadMarks(data.others_read_seqs);
      }
      sendRead();
      if (!sending) {
        setThinking(data.thinking);
      }
      showShare(pendings.size === 0 ? data.shareable_conversation_id : null);
    } catch (error) {
      if (error.status === 404 || error.status === 401) {
        showStatus("この部屋は開けません。");
        alive = false;
        return;
      }
      // 通信の一時的な失敗では止めない。次の読み込みでつながる。
    }
    if (alive) {
      timer = setTimeout(poll, POLL_MS);
    }
  }

  /* 送る前の写真の表示 */
  function drawPreview() {
    previewArea.replaceChildren();
    if (!attached) {
      return;
    }
    previewArea.append(
      el(
        "div",
        { class: "composer-preview" },
        el("img", { src: attached.url, alt: "送る写真" }),
        el("button", {
          type: "button",
          class: "quiet",
          text: "写真をやめる",
          onclick: () => {
            attached = null;
            drawPreview();
          },
        })
      )
    );
  }

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    fileInput.value = "";
    if (!file) {
      return;
    }
    showStatus("");
    try {
      const blob = await shrinkPhoto(file);
      attached = { blob, url: URL.createObjectURL(blob) };
      drawPreview();
    } catch (error) {
      showStatus(error.message);
    }
  });

  function drawPending(pending) {
    const body = el("div", { class: "bubble-body" });
    if (pending.url) {
      body.append(el("div", { class: "bubble-line" }, el("img", { class: "bubble-photo", src: pending.url, alt: "送る写真" })));
    }
    if (pending.text) {
      body.append(el("div", { class: "bubble-line" }, el("div", { class: "bubble person", text: pending.text })));
    }
    if (pending.failed) {
      body.append(
        el("p", { class: "bubble-state error", text: pending.failed }),
        el("button", {
          type: "button",
          text: "もう一度送る",
          onclick: () => deliver(pending),
        })
      );
    } else {
      body.append(el("span", { class: "bubble-time", text: "送っています" }));
    }
    const row = el("div", { class: "bubble-row mine" }, body);
    if (pending.node) {
      pending.node.replaceWith(row);
    } else {
      pendingArea.append(row);
    }
    pending.node = row;
  }

  function dropPending(key) {
    const pending = pendings.get(key);
    if (pending?.node) {
      pending.node.remove();
    }
    pendings.delete(key);
  }

  async function deliver(pending) {
    if (sending) {
      return;
    }
    sending = true;
    sendButton.disabled = true;
    pending.failed = null;
    drawPending(pending);
    scrollDown();
    const isTwinRoom = room?.kind === "ツイン";
    if (isTwinRoom) {
      setThinking(true);
    }
    try {
      if (pending.blob && !pending.photoId) {
        const uploaded = await uploadPhoto(pending.blob, isTwinRoom ? "会話" : "部屋");
        pending.photoId = uploaded.id;
      }
      const result = await api(`/rooms/${roomId}/messages`, {
        method: "POST",
        json: { text: pending.text || null, photo_id: pending.photoId || null, client_key: pending.key },
      });
      dropPending(pending.key);
      // 順番は読み込みの last_seq でだけ進める。ここで進めると、先に届いていた相手の発言を飛ばすため。
      addMessages([result.message, ...result.replies]);
      scrollDown();
    } catch (error) {
      pending.failed = error.message;
      drawPending(pending);
    } finally {
      sending = false;
      sendButton.disabled = false;
      setThinking(false);
    }
  }

  async function send() {
    emptyNote.hidden = true;
    const text = textBox.value.trim();
    if (sending || (!text && !attached)) {
      return;
    }
    // 長すぎる言葉は送っても受け付けられず、「もう一度送る」でも通らない。消さずに残して、短くしてもらう
    if (text.length > MAX_MESSAGE_CHARS) {
      showStatus(`長すぎて送れません。${MAX_MESSAGE_CHARS}字までにしてください（今は${text.length}字です）。`);
      return;
    }
    showStatus("");
    const pending = { key: newKey(), text, blob: attached?.blob, url: attached?.url };
    pendings.set(pending.key, pending);
    clearGuessAnswers();
    shownShare = null;
    shareArea.replaceChildren();
    textBox.value = "";
    textBox.style.height = "";
    attached = null;
    drawPreview();
    await deliver(pending);
  }

  sendButton.addEventListener("click", send);
  textBox.addEventListener("input", () => {
    textBox.style.height = "auto";
    textBox.style.height = `${Math.min(textBox.scrollHeight + 2, 140)}px`;
  });

  closeDayButton.addEventListener("click", async () => {
    if (sending) {
      return;
    }
    sending = true;
    closeDayButton.disabled = true;
    setThinking(true);
    try {
      const result = await api(`/rooms/${roomId}/close`, { method: "POST" });
      addMessages(result.replies);
      if (result.closed) {
        showShare(result.conversation_id);
        scrollDown();
      } else {
        showStatus("");
        shareArea.replaceChildren(el("p", { class: "hint", text: "今は開いている話がありません。" }));
      }
    } catch (error) {
      showStatus(error.message);
    } finally {
      sending = false;
      closeDayButton.disabled = false;
      setThinking(false);
    }
  });

  document.addEventListener("visibilitychange", sendRead);

  leaveButton.addEventListener("click", () =>
    openSheet((inner, close) => {
      const message = el("p", { class: "error", role: "alert", hidden: true });
      inner.append(
        el("h2", { text: "この部屋を出ますか？" }),
        el("p", { text: "出ると、この部屋の話は読めなくなります。話したことは記録に残ります。" }),
        ctx.me.role === "本人" && room?.twin_present ? el("p", { text: "ツイン（AI）も一緒にはずれます。" }) : "",
        message,
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "部屋を出る",
            onclick: async () => {
              try {
                await api(`/rooms/${roomId}/leave`, { method: "POST" });
                close();
                ctx.openTab("talk");
              } catch (error) {
                message.textContent = error.message;
                message.hidden = false;
              }
            },
          }),
          el("button", { type: "button", class: "quiet", text: "やめる", onclick: close })
        )
      );
    })
  );

  removeButton.addEventListener("click", () =>
    openSheet((inner, close) => {
      const message = el("p", { class: "error", role: "alert", hidden: true });
      const others = room.members.filter((member) => member.id !== ctx.me.id && member.role !== "本人");
      inner.append(
        el("h2", { text: "この部屋からはずす人" }),
        el("p", { class: "hint", text: "はずれた人は、この部屋の話を読めなくなります。部屋に表示します。" }),
        ...others.map((member) =>
          el(
            "div",
            { class: "person-row" },
            el("div", { class: "person-text", text: personName(member) }),
            el("button", {
              type: "button",
              class: "quiet",
              text: "はずす",
              onclick: async () => {
                try {
                  await api(`/rooms/${roomId}/members/${member.id}/remove`, { method: "POST" });
                  close();
                } catch (error) {
                  message.textContent = error.message;
                  message.hidden = false;
                }
              },
            })
          )
        ),
        others.length === 0 ? el("p", { text: "はずせる人はいません。" }) : "",
        message,
        el("button", { type: "button", class: "quiet", text: "閉じる", onclick: close })
      );
    })
  );

  twinToggle.addEventListener("click", async () => {
    twinToggle.disabled = true;
    try {
      const data = await api(`/rooms/${roomId}/twin`, { method: "POST", json: { present: !room.twin_present } });
      setRoom(data.room);
      addMessages(data.items);
      // 発言がないと入力欄しか見えず、何をする場所か分からないので案内を出す
      emptyNote.hidden = seen.size > 0 || pendings.size > 0;
      lastSeq = Math.max(lastSeq, data.last_seq);
    } catch (error) {
      showStatus(error.message);
    } finally {
      twinToggle.disabled = false;
    }
  });

  poll();
  return () => {
    alive = false;
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", sendRead);
    ctx.showTabs(true);
    ctx.setPlate("TALK", "トーク");
  };
}
