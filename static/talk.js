/*
  役割：トークの一覧と部屋の画面。本人とツイン、人どうし、グループのやり取りを出す。
  4つの極：1. 書く・話す（ツインとの部屋）、2. 分かち合う（人どうしの部屋）。
  守る原則：7（催促や責める言葉を出さない。「今日はここまで」はいつも見える）、
            12（ツインには姿と「ツイン（AI）」を必ず出す）、
            15（ツインが読むかどうかを部屋の上に出す）。

  1対1とグループには未読の数と既読を出す。通知は作らず、読んだ人の名前も出さない。
  画面が見えているときにだけ、読んだ位置を送る。開いたままの別のウィンドウで既読にしないため。

  PC版は一覧、部屋、この部屋のことを横に並べ、スマホ版は1画面ずつ出す。部品は同じで、並べ方だけが違う（static/device.js）。
  電話、音声の入力、通知、読んだ人の顔は作らない（CLAUDE.md の作らないもの）。

  使い心地のために次のようにしている。
  - 送ると自分の言葉がすぐ出る。失敗しても、書いた文と写真を消さず「もう一度送る」で送り直せる。
  - 同じ client_key で送り直すので、発言は増えない。送っている間はボタンを止めるので、二重に送れない。
  - 日本語の変換を決める Enter で送らないよう、Enter は改行のままにする。Alt+Enter か Ctrl+Enter でも送れる（static/ui.js の sendWithKeys）。
  - 開いている部屋は3秒ごとに新しい発言を取りに行く。途中で閉じても、開き直すと続きが見える。
  - 書きかけの文と写真は、人と部屋ごとに覚える。PC版とスマホ版を切り替えても、部屋を移っても消えないようにするため。
*/

import { api, uploadPhoto } from "./api.js";
import { addToGroup, bareName, breakGroup, dateDivider, newDateTracker, newGroupTracker, stampPicker, TWIN_NAME } from "./bubbles.js";
import { hasRoomForThreePanes, isPc } from "./device.js";
import { shrinkPhoto } from "./photo.js";
import { BANNER_NO_TWIN, BANNER_TWIN, createRoomInfo, groupIcon, roleWords, roomTitle } from "./room_info.js";
import { stampOnlyClass, stampText, withoutCutStamp } from "./stamps.js";
import { openShareSheet } from "./share.js";
import { NEEDS_KEY_WORDS, avatar, dateKey, el, openSheet, sendWithKeys, svg, timeLabel, twinFigure } from "./ui.js";

const POLL_MS = 3000;
// サーバーの上限（app/rooms.py の text）と同じにする
const MAX_MESSAGE_CHARS = 4000;
const GUESS_ENDING = "合っていますか？";
const GUESS_ANSWERS = ["そうです", "ちがいます", "今は話したくない"];
// 推測の問いへの答えのボタンの吹き出し（static/tips.js）。答えを足すときは、ここにも足す
const GUESS_TIPS = {
  そうです: "ツインの推測が合っていると、すぐに答えます",
  ちがいます: "ツインの推測がちがうと、すぐに答えます",
  今は話したくない: "この話は今はしたくないと、すぐに答えます",
};
const DISMISSED_SHARE = "nt_dismissed_share";
const ICON_PHOTO = "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9.5v.5";
const ICON_SEND = "M4 12l16-8-6 16-3-6z";

// 人ごとに、開いていた部屋を覚える。デモでは同じウィンドウで人を切り替えるので、混ざらないようにする
const lastRooms = new Map();
// 書きかけは「人:部屋」ごとに覚える
const drafts = new Map();
// PC版の右のペインを開いているか。部屋を移っても同じままにする。null は、まだ決めていないとき
let infoOpen = null;

function newKey() {
  return crypto.randomUUID();
}

function lineIcon(path) {
  return svg(
    "svg",
    { class: "line-icon", viewBox: "0 0 24 24", "aria-hidden": "true" },
    svg("path", { d: path, fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" })
  );
}

/* いま開いている部屋の番号。PC版とスマホ版を切り替えても同じ部屋を続けるために app.js が読む。 */
export function currentRoomId(me) {
  return lastRooms.get(me.id) ?? null;
}

/* トークの入口。PC版は3つのペイン、スマホ版は一覧か部屋を1画面で出す。 */
export function renderTalk(container, ctx, options = {}) {
  if (isPc()) {
    return renderPanes(container, ctx, options);
  }
  if (options.roomId) {
    return openRoom(container, ctx, options.roomId);
  }
  container.className = "view talk-view";
  const list = roomListPane(ctx, { onOpen: (roomId) => ctx.openTab("talk", { roomId }) });
  container.append(list.node);
  return list.stop;
}

/* PC版の3つのペイン。部屋を移るときは真ん中と右だけを入れ替え、一覧は作り直さない。 */
function renderPanes(container, ctx, options) {
  // 幅が足りない画面では閉じて始め、会話の幅を先に取る
  infoOpen ??= hasRoomForThreePanes();
  container.className = "view talk-panes";
  // 部屋を開くまでは、中身のない右のペインを出さない
  container.dataset.info = "closed";
  const noRoom = el("p", { class: "room-notice" });
  const roomHost = el("section", { class: "pane-room", "aria-label": "部屋" }, noRoom);
  const infoHost = el("aside", { class: "pane-info", "aria-label": "この部屋のこと" });
  let leaveRoom = null;
  let shownId = null;

  function show(roomId) {
    if (roomId === shownId) {
      return;
    }
    leaveRoom?.();
    roomHost.replaceChildren();
    shownId = roomId;
    container.dataset.info = infoOpen ? "open" : "closed";
    list.setCurrent(roomId);
    leaveRoom = openRoom(roomHost, ctx, roomId, {
      infoHost,
      panes: container,
      onGone: () => {
        // 開けなくなった部屋を覚えたままにしない。次は一覧のいちばん上を開く
        lastRooms.delete(ctx.me.id);
        ctx.openTab("talk");
      },
    });
  }

  const list = roomListPane(ctx, {
    onOpen: show,
    onLoaded: (rooms) => {
      if (shownId === null && rooms.length > 0) {
        // 開くと相手に既読が付く部屋を、選ぶ前に開かない。ツインとの部屋には既読がないので開いてよい
        if (rooms[0].kind === "ツイン") {
          show(rooms[0].id);
        } else {
          noRoom.textContent = "左の一覧から、話す部屋を選んでください。";
        }
      } else if (shownId === null) {
        noRoom.textContent = "「新しい部屋」から部屋を作れます。";
      }
    },
  });
  container.append(list.node, roomHost, infoHost);
  const wanted = options.roomId ?? lastRooms.get(ctx.me.id) ?? null;
  if (wanted !== null) {
    show(wanted);
  }
  return () => {
    list.stop();
    leaveRoom?.();
    delete container.dataset.info;
  };
}

/* 部屋の一覧。開いている間に届いた発言が分かるよう、読み直す。 */
function roomListPane(ctx, { onOpen, onLoaded }) {
  let alive = true;
  let rooms = [];
  let currentId = null;
  let word = "";
  let drawn = null;
  const list = el("ul", { class: "room-list" });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const search = el("input", {
    class: "search-box",
    type: "search",
    placeholder: "部屋を探す",
    "aria-label": "部屋を探す",
    oninput: () => {
      word = search.value.trim();
      draw();
    },
  });
  const node = el(
    "section",
    { class: "pane-list", "aria-label": "部屋の一覧" },
    el(
      "div",
      { class: "list-head" },
      el("h2", { text: "トーク" }),
      // PC版は見出しの下に、スマホ版は「新しい部屋」の横に並べる（style.css）
      el("div", { class: "list-search" }, search),
      el("button", { type: "button", class: "quiet", text: "新しい部屋", tip: "話したい人を選んで、新しい部屋を作ります", onclick: () => openNewRoomSheet(ctx) })
    ),
    list,
    message,
    el("p", { class: "list-note hint", text: "家族や知人と話す部屋です。ツイン（AI）は招かれた部屋だけを読みます。" })
  );

  function draw() {
    const shown = rooms.filter((room) => !word || roomTitle(room, ctx.me.id).includes(word));
    // 同じ中身なら作り直さない。押そうとした行が動いたり、写真が読み直されたりしないようにするため
    const signature = JSON.stringify([shown, currentId]);
    if (signature === drawn) {
      return;
    }
    drawn = signature;
    const focused = document.activeElement?.dataset?.roomId;
    list.replaceChildren(...shown.map((room) => el("li", {}, roomRow(room, ctx, room.id === currentId, onOpen))));
    if (shown.length === 0) {
      list.append(el("li", { class: "room-list-empty hint", text: rooms.length ? "見つかりませんでした。" : "入っている部屋はまだありません。" }));
    }
    if (focused) {
      list.querySelector(`[data-room-id="${focused}"]`)?.focus();
    }
  }

  async function load() {
    try {
      const { items } = await api("/rooms");
      if (!alive) {
        return;
      }
      rooms = items;
      message.hidden = true;
      draw();
      onLoaded?.(items);
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  }
  load();
  const timer = setInterval(load, POLL_MS);
  return {
    node,
    setCurrent(id) {
      currentId = id;
      draw();
    },
    stop() {
      alive = false;
      clearInterval(timer);
    },
  };
}

/* 一覧の時刻。今日なら時刻、それ以外は月と日。 */
function whenWords(iso) {
  const day = new Date(iso);
  return dateKey(iso) === dateKey(new Date().toISOString()) ? timeLabel(iso) : `${day.getMonth() + 1}/${day.getDate()}`;
}

function roomRow(room, ctx, current, onOpen) {
  const pinned = room.kind === "ツイン";
  const icon = pinned ? twinFigure() : room.kind === "1対1" ? avatar(room.members.find((m) => m.id !== ctx.me.id)) : groupIcon(room, ctx.me.id);
  return el(
    "button",
    {
      type: "button",
      class: `room-item${pinned ? " pinned" : ""}${room.unread_count ? " has-unread" : ""}`,
      "aria-current": current ? "true" : null,
      "data-room-id": room.id,
      // PC版の部屋の一覧は左の縦の列なので、吹き出しを右に出して、上下の部屋を隠さない（static/tips.js）
      "data-tip-side": "right",
      tip: pinned ? "ツインとの部屋を開いて、続きを話せます" : `「${roomTitle(room, ctx.me.id)}」の部屋を開いて、話を読めます`,
      onclick: () => onOpen(room.id),
    },
    icon,
    el(
      "span",
      { class: "room-text" },
      el("span", { class: "room-line" }, el("span", { class: "room-name", text: roomTitle(room, ctx.me.id) }), room.last_at ? el("span", { class: "room-when", text: whenWords(room.last_at) }) : ""),
      el(
        "span",
        { class: "room-line" },
        el("span", { class: "room-preview", text: withoutCutStamp(room.last_text || (pinned ? "写真やひとことを送ってみてください" : "")) }),
        room.unread_count
          ? el("span", { class: "unread-count", "aria-label": `新しい発言 ${room.unread_count}件`, text: String(room.unread_count) })
          : ""
      )
    )
  );
}

/* 新しい部屋を作る */
async function openNewRoomSheet(ctx) {
  let people;
  try {
    people = (await api("/people/choices")).items;
  } catch (error) {
    return openSheet((inner, close) => inner.append(el("p", { class: "error", text: error.message }), el("button", { type: "button", text: "閉じる", tip: "この窓を閉じます", onclick: close })));
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
      ...checks.map(({ person, input }) => el("label", { class: "check" }, input, `${bareName(person)}（${person.profession ? `${person.role}・${person.profession}` : person.role}）`)),
      el("label", { class: "field" }, "部屋の名前（グループのとき）", nameInput),
      message,
      el(
        "div",
        { class: "row" },
        el("button", {
          type: "button",
          class: "primary",
          text: "部屋を作る",
          tip: "選んだ人との部屋を作って、すぐに開きます",
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
        el("button", { type: "button", class: "quiet", text: "やめる", tip: "部屋を作らずに、この窓を閉じます", onclick: close })
      )
    );
  });
}

/*
  部屋の画面。infoHost を渡されたら PC版で、右のペインへ「この部屋のこと」を出す。
  渡されなければスマホ版で、同じ中身を部屋の中の別の画面として開く。
*/
export function openRoom(container, ctx, roomId, { infoHost = null, panes = null, onGone = null } = {}) {
  const pc = Boolean(infoHost);
  if (!pc) {
    container.className = "view talk-view";
    ctx.showTabs(false);
    // スマホ版の部屋では見出しの帯を隠して、会話の面を広くする（style.css）
    document.body.dataset.inRoom = "on";
  }
  lastRooms.set(ctx.me.id, roomId);
  const draftKey = `${ctx.me.id}:${roomId}`;
  const draft = drafts.get(draftKey) ?? { text: "", attached: null }; // attached は送る前の写真 { blob, url }
  drafts.set(draftKey, draft);

  let alive = true;
  let timer = null;
  let lastSeq = 0;
  let room = null;
  let drawnRoom = null;
  let sending = false;
  let shownShare = null;
  let firstUnreadSeq = null;
  let firstLoad = true;
  let readSent = 0;
  // 部屋の発言は新しい分だけを先に読む。古い分は「もっと前を読む」で足す（docs/api.md）
  let hasEarlier = false;
  let earliestSeq = null;
  let loadingEarlier = false;
  // 未読の線の位置で開いた間は、写真が読み終わって高さが変わっても、線の位置を保つ
  let holdUnreadLine = false;
  const people = new Map();
  const tracker = newDateTracker();
  const groups = newGroupTracker();
  const seen = new Set();
  const pendings = new Map();
  const allMessages = [];

  const title = el("div", { class: "room-title" });
  const subtitle = el("div", { class: "room-subtitle" });
  const headerIcon = el("span", {});
  const closeDayButton = el("button", { type: "button", class: "close-day", text: "今日はここまで", tip: "今日のツインとの話を区切ります。便りにして見せることもできます" });
  const closeDayRow = el("div", { class: "composer-actions", hidden: true }, closeDayButton);
  const infoButton = el("button", { type: "button", class: "quiet room-info-button", text: "くわしく", tip: "この部屋にいる人や写真、話をさがす所を開きます" });
  const banner = el("div", { class: "room-banner", hidden: true });
  const emptyNote = el("p", { class: "room-notice", text: "写真やひとことを送ってみてください。", hidden: true });
  const earlierNote = el("span", { class: "hint" });
  const earlierButton = el("button", { type: "button", class: "quiet", text: "もっと前を読む", tip: "この部屋の前の発言を読み込んで、上に足します", onclick: () => loadEarlier() });
  const earlierBar = el("div", { class: "earlier-bar", hidden: true }, earlierNote, earlierButton);
  const messagesInner = el("div", { class: "messages-inner" }, emptyNote);
  const pendingArea = el("div", {});
  const thinkingArea = el("div", {});
  const shareArea = el("div", {});
  const guessArea = el("div", {});
  const messagesBox = el("div", { class: "messages" }, earlierBar, messagesInner, guessArea, pendingArea, thinkingArea, shareArea);
  // 帯を隠しても「架空データ」の表示は消さない（原則8）。帯の札と同じ言葉を、スマホでは部屋の見出しに出す
  // （会話の面の隅に重ねると、吹き出しの文字に重なっていた。2026-11-07 の見直し）
  const messagesArea = pc ? messagesBox : el("div", { class: "messages-area" }, messagesBox);
  const modeTag = pc ? null : el("span", { class: "tag room-mode-tag", text: document.getElementById("mode-label").textContent });
  const statusLine = el("p", { class: "error", role: "alert", hidden: true });

  const textBox = el("textarea", { rows: 1, placeholder: "ひとこと", "aria-label": "送る言葉" });
  const fileInput = el("input", { type: "file", accept: "image/*", class: "visually-hidden", "aria-label": "写真を選ぶ" });
  const photoButton = el("label", { class: "button photo-button", tip: "送る写真を選びます。選んだあと「送る」で届きます" }, lineIcon(ICON_PHOTO), "写真", fileInput);
  const sendButton = el("button", { type: "button", class: "primary send-button", tip: "書いた言葉や選んだ写真を、この部屋に送ります" }, lineIcon(ICON_SEND), "送る");
  const previewArea = el("div", {});
  const stamps = stampPicker(sendStamp, () => {
    if (stick) {
      scrollDown();
    }
  });
  const composerRow = el("div", { class: "composer-row" }, photoButton, stamps.button, textBox, sendButton);
  const composer = el("div", { class: "composer" }, el("div", { class: "composer-inner" }, statusLine, closeDayRow, previewArea, stamps.choices, composerRow));

  const roomBody = el(
    "div",
    { class: "room-body" },
    el(
      "div",
      { class: "room-header" },
      pc
        ? null
        : el("button", {
            type: "button",
            class: "quiet",
            text: "一覧へ",
            tip: "部屋を閉じて、部屋の一覧に戻ります",
            onclick: () => ctx.openTab("talk"),
          }),
      headerIcon,
      // 「架空データ」の札は名前の下の行に置く（名前の横だと、名前が短く切れていた）
      el("div", { class: "room-head-text" }, title, modeTag ? el("div", { class: "room-head-sub" }, subtitle, modeTag) : subtitle),
      infoButton
    ),
    banner,
    messagesArea,
    composer
  );
  container.append(roomBody);

  // スマホ版の「この部屋のこと」。部屋を閉じずに重ねて開く。読み込みを止めず、戻ったときに続きが見えるようにするため
  const infoPane = infoHost ?? el("section", { class: "pane-info room-info-screen", "aria-label": "この部屋のこと", hidden: true });
  if (!pc) {
    container.append(infoPane);
  }
  let scrollBeforeInfo = 0;

  function setInfoOpen(open) {
    if (pc) {
      infoOpen = open;
      panes.dataset.info = open ? "open" : "closed";
      infoButton.setAttribute("aria-expanded", String(open));
      return;
    }
    if (open) {
      scrollBeforeInfo = messagesBox.scrollTop;
      ctx.setPlate(room ? roomTitle(room, ctx.me.id) : "トーク");
    } else {
      ctx.setPlate("トーク");
    }
    // 「この部屋のこと」を開いている間は会話の面がないので、帯を戻す
    document.body.dataset.inRoom = open ? "off" : "on";
    roomBody.hidden = open;
    infoPane.hidden = !open;
    if (!open) {
      messagesBox.scrollTop = scrollBeforeInfo;
      infoButton.focus();
    }
  }

  /* 探した発言や写真の発言へ移り、少しの間だけ印を付ける。 */
  function jumpTo(messageId) {
    if (!pc) {
      setInfoOpen(false);
    }
    const line = messagesInner.querySelector(`[data-msg="${messageId}"]`);
    if (!line) {
      return;
    }
    line.scrollIntoView({ block: "center" });
    line.classList.add("msg-flash");
    setTimeout(() => line.classList.remove("msg-flash"), 1800);
  }

  const info = createRoomInfo(infoPane, ctx, {
    closeLabel: pc ? "閉じる" : "部屋へ",
    onClose: () => setInfoOpen(false),
    onJump: jumpTo,
    onLoadEarlier: () => loadEarlier(),
    onRoomData: applyRoomView,
    onLeft: () => {
      lastRooms.delete(ctx.me.id);
      ctx.openTab("talk");
    },
  });
  infoButton.addEventListener("click", () => setInfoOpen(pc ? !infoOpen : true));
  if (pc) {
    infoButton.setAttribute("aria-expanded", String(infoOpen));
  }

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
      if (holdUnreadLine) {
        showUnreadLine();
      } else if (stick) {
        scrollDown();
      }
    },
    true
  );
  for (const kind of ["wheel", "touchstart", "keydown", "mousedown"]) {
    messagesBox.addEventListener(
      kind,
      () => {
        holdUnreadLine = false;
      },
      { passive: true }
    );
  }

  /* 未読の線を上に出して開く。いちばん下で開くと、読んでいない発言を読んだことになるため。 */
  function showUnreadLine() {
    const line = messagesInner.querySelector(".new-divider");
    if (line) {
      line.scrollIntoView({ block: "start" });
    } else {
      // 線が読み込んだ範囲より前にあるときは、いちばん上で「もっと前を読む」を見せる
      messagesBox.scrollTop = 0;
    }
    stick = nearBottom();
  }

  function showStatus(text) {
    statusLine.textContent = text || "";
    statusLine.hidden = !text;
  }

  function setRoom(data) {
    room = data;
    info.setRoom(room);
    // 中身が変わったときだけ作り直す。読み込みのたびにアイコンを作り直さないため
    const signature = JSON.stringify([room.name, room.kind, room.twin_present, room.members]);
    if (signature === drawnRoom) {
      return;
    }
    drawnRoom = signature;
    title.textContent = roomTitle(room, ctx.me.id);
    if (room.kind === "ツイン") {
      headerIcon.replaceChildren(twinFigure());
      subtitle.textContent = "話を聞く相棒";
      closeDayRow.hidden = false;
      // キーがないときだけ出す。送った言葉は残るので、エラーの形にしない
      banner.hidden = !ctx.needsKey;
      banner.replaceChildren(ctx.needsKey ? el("div", { text: `${NEEDS_KEY_WORDS}話した言葉は残ります。` }) : "");
    } else {
      const other = room.kind === "1対1" ? room.members.find((m) => m.id !== ctx.me.id) : null;
      headerIcon.replaceChildren(other ? avatar(other) : groupIcon(room, ctx.me.id));
      subtitle.textContent = other ? roleWords(other) : `${room.members.length}人の部屋`;
      banner.hidden = false;
      banner.replaceChildren(
        el("div", { text: room.twin_present ? BANNER_TWIN : BANNER_NO_TWIN }),
        room.twin_present ? el("div", { text: ctx.needsKey ? NEEDS_KEY_WORDS : "「@ツイン」と書くと、ツイン（AI）が答えます" }) : ""
      );
    }
  }

  /* 既読は、読まれた発言のうちいちばん新しい所にだけ出す。読んだ人の位置から数え、名前も顔も出さない。 */
  function drawReadMarks(positions) {
    let shown = false;
    for (const mark of [...messagesInner.querySelectorAll(".read-mark")].reverse()) {
      const count = positions.filter((seq) => seq >= Number(mark.dataset.seq)).length;
      if (!shown && count > 0) {
        mark.textContent = room?.kind === "1対1" ? "既読 " : `既読 ${count}人 `;
        shown = true;
      } else {
        mark.textContent = "";
      }
    }
  }

  /* 画面が見えているときにだけ、読んだ位置を送る。 */
  async function sendRead() {
    if (!room || room.kind === "ツイン" || document.visibilityState !== "visible" || lastSeq <= readSent) {
      return;
    }
    // いちばん下を見るまでは送らない。相手には読んだと出るので、読んでいない発言を読んだことにしない
    if (!stick) {
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
            tip: GUESS_TIPS[word],
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
    let added = false;
    let twinSpoke = false;
    for (const item of items) {
      if (seen.has(item.id)) {
        continue;
      }
      seen.add(item.id);
      added = true;
      earliestSeq = earliestSeq === null ? item.seq : Math.min(earliestSeq, item.seq);
      twinSpoke = twinSpoke || item.is_twin;
      allMessages.push(item);
      if (item.client_key && pendings.has(item.client_key)) {
        dropPending(item.client_key);
      }
      if (firstUnreadSeq !== null && item.seq === firstUnreadSeq) {
        breakGroup(groups);
        messagesInner.append(el("div", { class: "date-divider new-divider" }, el("span", { text: "ここから新しい発言" })));
      }
      const divider = dateDivider(tracker, item.sent_at);
      if (divider) {
        breakGroup(groups);
        messagesInner.append(divider);
      }
      addToGroup(messagesInner, groups, item, {
        viewerId: ctx.me.id,
        people,
        onRest: ctx.me.role === "本人" ? restQuestion : null,
        // 二人の部屋では、吹き出しの上に相手の名前を出さない（ご本人の決定、2026-11-06）
        hideNames: room?.kind === "1対1",
      });
      // いちばん新しい行が本人への推測の問いのときだけ、答えのボタンを出す
      clearGuessAnswers();
      if (room?.kind === "ツイン" && item.is_twin && item.is_question && item.text?.endsWith(GUESS_ENDING)) {
        showGuessAnswers(item);
      }
    }
    if (added) {
      info.setMessages(allMessages, people);
    }
    if (twinSpoke) {
      info.reviewChanged();
    }
    if (following) {
      scrollDown();
    }
  }

  function drawEarlierBar() {
    earlierBar.hidden = !hasEarlier;
    const older = firstUnreadSeq !== null && earliestSeq !== null && firstUnreadSeq < earliestSeq;
    earlierNote.textContent = older ? "この前にも新しい発言があります。" : "";
    info.setHasEarlier(hasEarlier);
  }

  /* 古い発言を上に足す。読んでいる位置が動かないよう、増えた高さの分だけ下げる。 */
  async function loadEarlier() {
    if (loadingEarlier || !hasEarlier || earliestSeq === null) {
      return;
    }
    loadingEarlier = true;
    earlierButton.disabled = true;
    holdUnreadLine = false;
    try {
      const data = await api(`/rooms/${roomId}/messages?before=${earliestSeq}`);
      if (!alive) {
        return;
      }
      for (const person of data.people) {
        people.set(person.id, person);
      }
      const items = data.items.filter((item) => !seen.has(item.id));
      const chunk = el("div", { class: "earlier-chunk" });
      const chunkDates = newDateTracker();
      const chunkGroups = newGroupTracker();
      for (const item of items) {
        seen.add(item.id);
        if (firstUnreadSeq !== null && item.seq === firstUnreadSeq) {
          breakGroup(chunkGroups);
          chunk.append(el("div", { class: "date-divider new-divider" }, el("span", { text: "ここから新しい発言" })));
        }
        const divider = dateDivider(chunkDates, item.sent_at);
        if (divider) {
          breakGroup(chunkGroups);
          chunk.append(divider);
        }
        addToGroup(chunk, chunkGroups, item, {
          viewerId: ctx.me.id,
          people,
          onRest: ctx.me.role === "本人" ? restQuestion : null,
          hideNames: room?.kind === "1対1",
        });
      }
      // 同じ日の区切りが続けて2回出ないようにする
      const next = messagesInner.querySelector(".date-divider:not(.new-divider)");
      const dividers = chunk.querySelectorAll(".date-divider:not(.new-divider)");
      const lastDivider = dividers[dividers.length - 1];
      if (next && lastDivider?.textContent === next.textContent) {
        next.remove();
      }
      const height = messagesBox.scrollHeight;
      const top = messagesBox.scrollTop;
      messagesInner.prepend(chunk);
      messagesBox.scrollTop = top + (messagesBox.scrollHeight - height);
      allMessages.unshift(...items);
      if (items.length > 0) {
        earliestSeq = items[0].seq;
      }
      hasEarlier = data.has_earlier;
      info.setMessages(allMessages, people);
      drawEarlierBar();
    } catch (error) {
      showStatus(error.message);
    } finally {
      loadingEarlier = false;
      earlierButton.disabled = false;
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
          { class: "msg-group" },
          el("div", { class: "msg-who", text: TWIN_NAME }),
          el(
            "div",
            { class: "msg-line" },
            el("span", { class: "msg-slot" }, twinFigure({ thinking: true })),
            el(
              "div",
              { class: "bubble", role: "status" },
              "考えています",
              el("span", { class: "thinking-dots", "aria-hidden": "true" }, el("i"), el("i"), el("i"))
            )
          )
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
            tip: "今日の話から便りの下書きを作り、見せる相手を選べます",
            onclick: () => openShareSheet(conversationId, () => shareArea.replaceChildren()),
          }),
          el("button", {
            type: "button",
            class: "quiet",
            text: "今はしない",
            tip: "共有しないで、この案内を閉じます。だれにも届きません",
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

  /* サーバーが返した RoomView を画面へ反映する。ツインを入れる、はずすの答えもここを通る。 */
  function applyRoomView(data) {
    setRoom(data.room);
    addMessages(data.items);
    // 発言がないと入力欄しか見えず、何をする場所か分からないので案内を出す
    emptyNote.hidden = seen.size > 0 || pendings.size > 0;
    lastSeq = Math.max(lastSeq, data.last_seq);
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
      // 線を引くのは開いたときの1回だけ。開いている間に届いた発言には引かない
      const opening = firstLoad;
      if (firstLoad) {
        firstUnreadSeq = data.first_unread_seq ?? null;
        // 2回目からの読み込みは続きだけなので、古い分があるかは最初の答えで決める
        hasEarlier = Boolean(data.has_earlier);
        firstLoad = false;
      }
      applyRoomView(data);
      if (opening) {
        drawEarlierBar();
        if (firstUnreadSeq !== null) {
          holdUnreadLine = true;
          showUnreadLine();
        }
      }
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
        onGone?.();
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
    if (!draft.attached) {
      return;
    }
    previewArea.append(
      el(
        "div",
        { class: "composer-preview" },
        el("img", { src: draft.attached.url, alt: "送る写真" }),
        el("button", {
          type: "button",
          class: "quiet",
          text: "写真をやめる",
          tip: "選んだ写真を送らずに外します",
          onclick: () => {
            draft.attached = null;
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
      draft.attached = { blob, url: URL.createObjectURL(blob) };
      drawPreview();
    } catch (error) {
      showStatus(error.message);
    }
  });

  function drawPending(pending) {
    const group = el("div", { class: "msg-group mine" });
    if (pending.url) {
      group.append(el("div", { class: "msg-line" }, el("img", { class: "bubble-photo", src: pending.url, alt: "送る写真" })));
    }
    if (pending.text) {
      group.append(el("div", { class: "msg-line" }, el("div", { class: `bubble person${stampOnlyClass(pending.text)}`, text: pending.text })));
    }
    if (pending.failed) {
      group.append(
        el("p", { class: "bubble-state error", text: pending.failed }),
        el("button", {
          type: "button",
          text: "もう一度送る",
          tip: "届かなかった言葉や写真を、もう一度送ります",
          onclick: () => deliver(pending),
        })
      );
    } else {
      // 時刻と同じように、いちばん下の吹き出しの横に出す
      group.lastElementChild?.append(el("div", { class: "msg-foot" }, el("span", { class: "bubble-time", text: "送っています" })));
    }
    if (pending.node) {
      pending.node.replaceWith(group);
    } else {
      pendingArea.append(group);
    }
    pending.node = group;
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
    stamps.setBusy(true);
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
      stamps.setBusy(false);
      setThinking(false);
    }
  }

  async function send() {
    emptyNote.hidden = true;
    const text = textBox.value.trim();
    if (sending || (!text && !draft.attached)) {
      return;
    }
    // 長すぎる言葉は送っても受け付けられず、「もう一度送る」でも通らない。消さずに残して、短くしてもらう
    if (text.length > MAX_MESSAGE_CHARS) {
      showStatus(`長すぎて送れません。${MAX_MESSAGE_CHARS}字までにしてください（今は${text.length}字です）。`);
      return;
    }
    showStatus("");
    const pending = { key: newKey(), text, blob: draft.attached?.blob, url: draft.attached?.url };
    pendings.set(pending.key, pending);
    clearGuessAnswers();
    shownShare = null;
    shareArea.replaceChildren();
    textBox.value = "";
    textBox.style.height = "";
    draft.text = "";
    draft.attached = null;
    drawPreview();
    await deliver(pending);
  }

  /* スタンプは押した時点で送る。書きかけの文と写真は消さない。 */
  async function sendStamp(name) {
    if (sending) {
      return;
    }
    emptyNote.hidden = true;
    showStatus("");
    const pending = { key: newKey(), text: stampText(name) };
    pendings.set(pending.key, pending);
    clearGuessAnswers();
    shownShare = null;
    shareArea.replaceChildren();
    await deliver(pending);
  }

  function sizeTextBox() {
    textBox.style.height = "auto";
    textBox.style.height = `${Math.min(textBox.scrollHeight + 2, 140)}px`;
  }

  sendButton.addEventListener("click", send);
  textBox.addEventListener("input", () => {
    draft.text = textBox.value;
    sizeTextBox();
  });
  // 変換を決める Enter では送らない。Alt+Enter か Ctrl+Enter で送る
  sendWithKeys(textBox, sendButton);

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

  // 切り替えの前に書いていた文と写真を戻す
  textBox.value = draft.text;
  if (draft.text) {
    sizeTextBox();
  }
  drawPreview();

  poll();
  return () => {
    alive = false;
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", sendRead);
    info.destroy();
    if (!pc) {
      ctx.showTabs(true);
      ctx.setPlate("トーク");
      delete document.body.dataset.inRoom;
    }
  };
}
