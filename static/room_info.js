/*
  役割：トークの「この部屋のこと」。PC版では右のペイン、スマホ版では部屋から開く別の画面になる。
  4つの極：1. 書く・話す、2. 分かち合う、3. 読み解き、積み上げる（確かめ）。
  守る原則：4（推測に本人が答えられる）、5（直しても元の記録は消えない）、6（お休みの話題）、
            10（見える範囲はサーバーの答えのまま）、12（ツインは AI の相棒だと出す）、
            13（色に数字を出さない）、15（ツインが読むかどうかを出す）。

  ツインとの部屋を開くと「確かめる」を最初に出す。話した直後に、その場で本人が直せるようにするため。
  確かめの部品は、ホームと同じ reviewSection を使う。
  部屋の写真と話を探す先は、読み込み済みの発言から集める。受け口を増やさず、探す言葉をサーバーへ送らないため。
  3秒ごとの読み込みでは、中身が変わった所だけ作り直す。書いている途中の言葉や、探している言葉を消さないため。
*/

import { api } from "./api.js";
import { personName, TWIN_NAME } from "./bubbles.js";
import { reviewSection } from "./review.js";
import { avatar, dateLabel, el, openSheet, photoImage, profilePhoto, timeLabel, twinDrawing, twinFigure } from "./ui.js";

export const BANNER_NO_TWIN = "ツイン（AI）はこの部屋を読みません";
export const BANNER_TWIN = "ツイン（AI）がいます。ここでの話を読みます";

const TAB_REVIEW = "review";
const TAB_ROOM = "room";
const TAB_PHOTOS = "photos";

/* 色が変わったら、画面に出ているツインをすべて描き直す。大きさは twinFigure と同じ決まり。 */
function repaintTwins() {
  for (const figure of document.querySelectorAll(".twin-figure")) {
    const size = figure.classList.contains("large") ? 96 : figure.classList.contains("small") ? 36 : 44;
    figure.replaceChildren(twinDrawing(size));
  }
}

/* グループの部屋のアイコン。自分以外の人の顔を2人まで重ねる。1人なら丸1つ、誰もいなければ「組」の丸にする。 */
export function groupIcon(room, viewerId, size = "") {
  const faces = (room?.members || []).filter((member) => member.id !== viewerId).slice(0, 2);
  if (faces.length === 0) {
    return el("span", { class: ["avatar", size].filter(Boolean).join(" "), "aria-hidden": "true", text: "組" });
  }
  if (faces.length === 1) {
    return avatar(faces[0], size);
  }
  return el("span", { class: ["group-faces", size].filter(Boolean).join(" "), "aria-hidden": "true" }, ...faces.map((person) => avatar(person)));
}

/* 立場の言葉。続柄があれば先に出す。 */
export function roleWords(person) {
  return person.relation && person.relation !== person.role ? `${person.relation}・${person.role}` : person.role;
}

export function createRoomInfo(host, ctx, { closeLabel, onClose, onJump, onRoomData, onLeft }) {
  let alive = true;
  let room = null;
  let messages = [];
  let people = new Map();
  let tab = null;
  let drawnTabs = null;
  let drawnRoom = null;
  let drawnPhotos = 0;
  let reviewSignature = null;

  const tabsBar = el("div", { class: "info-tabs", role: "group", "aria-label": "この部屋のことの切り替え" });
  const body = el("div", { class: "info-body" });
  const message = el("p", { class: "error", role: "alert", hidden: true });

  // 探す欄は作り直さない。読み込みのたびに言葉が消えないようにするため
  const results = el("div", { class: "search-results" });
  const searchInput = el("input", {
    class: "search-box",
    type: "search",
    placeholder: "言葉を入れてください",
    "aria-label": "この部屋の話を探す",
    oninput: drawResults,
  });
  const searchSection = el("section", { class: "info-section" }, el("h3", { text: "この部屋の話を探す" }), searchInput, results);

  host.append(
    el(
      "div",
      { class: "info-head" },
      el("h2", { text: "この部屋のこと" }),
      el("button", { type: "button", class: "quiet", text: closeLabel, onclick: onClose })
    ),
    tabsBar,
    body
  );

  function tabsFor() {
    const common = [
      [TAB_ROOM, "この部屋"],
      [TAB_PHOTOS, "写真"],
    ];
    // 確かめは本人だけが使える。本人以外には /api/review が 404 を返す
    return room.kind === "ツイン" && ctx.me.role === "本人" ? [[TAB_REVIEW, "確かめる"], ...common] : common;
  }

  function drawTabs() {
    const usable = tabsFor();
    if (!usable.some(([key]) => key === tab)) {
      tab = usable[0][0];
    }
    tabsBar.replaceChildren(
      ...usable.map(([key, text]) =>
        el("button", {
          type: "button",
          text,
          "aria-pressed": String(key === tab),
          onclick: () => {
            tab = key;
            drawTabs();
            drawBody();
          },
        })
      )
    );
  }

  function drawResults() {
    const word = searchInput.value.trim();
    results.replaceChildren();
    if (!word) {
      return;
    }
    const hits = messages.filter((item) => item.kind !== "お知らせ" && item.text?.includes(word));
    if (hits.length === 0) {
      results.append(el("p", { class: "hint", text: "見つかりませんでした。" }));
      return;
    }
    results.append(
      ...hits.map((item) => {
        const speaker = item.is_twin ? TWIN_NAME : personName(people.get(item.sender_user_id));
        const [before, ...after] = item.text.split(word);
        return el(
          "button",
          { type: "button", class: "result-button", onclick: () => onJump(item.id) },
          el("span", { class: "result-meta", text: `${dateLabel(item.sent_at)} ${timeLabel(item.sent_at)}・${speaker}` }),
          el("span", {}, before, el("mark", { text: word }), after.join(word))
        );
      })
    );
  }

  /* 確かめる。ホームと同じ部品で、答えると色がのる。 */
  function drawReview() {
    const empty = el("p", { class: "hint", text: "今、確かめることはありません。", hidden: true });
    const section = reviewSection(
      ctx,
      () => {
        repaintTwins();
        drawBody();
      },
      {
        onLoaded: (items) => {
          reviewSignature = JSON.stringify(items.map((item) => item.id));
          empty.hidden = items.length > 0;
        },
      }
    );
    body.replaceChildren(
      el("div", { class: "info-twin" }, twinFigure({ large: true }), el("p", { class: "hint", text: "確かめた分だけ、少しずつ色がのります。" })),
      section,
      empty
    );
  }

  /* お休みにしている話題。本人だけが読める。 */
  async function drawResting(section) {
    try {
      const { items } = await api("/resting-themes");
      if (!alive) {
        return;
      }
      section.replaceChildren(
        el("h3", { text: "お休みにしている話題" }),
        items.length ? el("p", { class: "hint", text: "ツイン（AI）はこの話題を聞きません。" }) : el("p", { class: "hint", text: "お休みにしている話題はありません。" }),
        ...items.map((theme) =>
          el(
            "div",
            { class: "person-row" },
            el("div", { class: "person-text", text: theme }),
            el("button", {
              type: "button",
              class: "quiet",
              text: "お休みをやめる",
              onclick: async () => {
                await api("/resting-themes/resume", { method: "POST", json: { theme } });
                drawResting(section);
              },
            })
          )
        )
      );
    } catch {
      section.hidden = true;
    }
  }

  function drawTwinRoomInfo() {
    const resting = el("section", { class: "info-section" });
    body.replaceChildren(
      el(
        "div",
        { class: "info-profile" },
        twinFigure({ large: true }),
        el("strong", { text: TWIN_NAME }),
        el("span", { class: "hint", text: "話を聞く相棒です。本人ではありません。" })
      ),
      el(
        "section",
        { class: "info-section" },
        el("h3", { text: "ツイン（AI）が読むもの" }),
        el("p", { text: "この部屋の話を読みます。招かれていない部屋は読みません。" })
      ),
      searchSection,
      resting
    );
    if (ctx.me.role === "本人") {
      drawResting(resting);
    }
  }

  async function setTwinPresent(button) {
    button.disabled = true;
    message.hidden = true;
    try {
      onRoomData(await api(`/rooms/${room.id}/twin`, { method: "POST", json: { present: !room.twin_present } }));
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
      button.disabled = false;
    }
  }

  function confirmRemove(member) {
    openSheet((inner, close) => {
      const failure = el("p", { class: "error", role: "alert", hidden: true });
      inner.append(
        el("h2", { text: `${personName(member)}をはずしますか？` }),
        el("p", { class: "hint", text: "はずれた人は、この部屋の話を読めなくなります。部屋に表示します。" }),
        failure,
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "はずす",
            onclick: async () => {
              try {
                await api(`/rooms/${room.id}/members/${member.id}/remove`, { method: "POST" });
                close();
              } catch (error) {
                failure.textContent = error.message;
                failure.hidden = false;
              }
            },
          }),
          el("button", { type: "button", class: "quiet", text: "やめる", onclick: close })
        )
      );
    });
  }

  function confirmLeave() {
    openSheet((inner, close) => {
      const failure = el("p", { class: "error", role: "alert", hidden: true });
      inner.append(
        el("h2", { text: "この部屋を出ますか？" }),
        el("p", { text: "出ると、この部屋の話は読めなくなります。話したことは記録に残ります。" }),
        ctx.me.role === "本人" && room.twin_present ? el("p", { text: "ツイン（AI）も一緒にはずれます。" }) : "",
        failure,
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "部屋を出る",
            onclick: async () => {
              try {
                await api(`/rooms/${room.id}/leave`, { method: "POST" });
                close();
                onLeft();
              } catch (error) {
                failure.textContent = error.message;
                failure.hidden = false;
              }
            },
          }),
          el("button", { type: "button", class: "quiet", text: "やめる", onclick: close })
        )
      );
    });
  }

  function drawPeopleRoomInfo() {
    const other = room.kind === "1対1" ? room.members.find((member) => member.id !== ctx.me.id) : null;
    const twinState = el(
      "div",
      { class: "twin-state" },
      el("div", { class: "twin-state-line" }, twinFigure({ small: true }), el("span", { text: room.twin_present ? BANNER_TWIN : BANNER_NO_TWIN }))
    );
    if (room.can_manage_twin) {
      twinState.append(
        el("button", {
          type: "button",
          text: room.twin_present ? "ツイン（AI）をはずす" : "ツイン（AI）を入れる",
          onclick: (event) => setTwinPresent(event.currentTarget),
        })
      );
    }
    body.replaceChildren(
      other
        ? // プロフィール写真がある人は写真を大きく、ない人は丸いアイコンを出す
          el("div", { class: "info-profile" }, profilePhoto(other) || avatar(other, "large"), el("strong", { text: personName(other) }), el("span", { class: "hint", text: roleWords(other) }))
        : el(
            "div",
            { class: "info-profile" },
            groupIcon(room, ctx.me.id, "large"),
            el("strong", { text: room.name }),
            el("span", { class: "hint", text: `${room.members.length}人の部屋` })
          ),
      el("section", { class: "info-section" }, el("h3", { text: TWIN_NAME }), twinState, message)
    );
    if (room.kind === "グループ") {
      body.append(
        el(
          "section",
          { class: "info-section" },
          el("h3", { text: "この部屋の人" }),
          ...room.members.map((member) =>
            el(
              "div",
              { class: "person-row" },
              avatar(member),
              el("div", { class: "person-text" }, el("b", { text: personName(member) }), el("div", { class: "hint", text: roleWords(member) })),
              // 本人は人にはずされない（docs/api.md）。自分は「部屋を出る」で出る
              room.can_remove_members && member.id !== ctx.me.id && member.role !== "本人"
                ? el("button", { type: "button", class: "quiet", text: "はずす", onclick: () => confirmRemove(member) })
                : ""
            )
          )
        )
      );
    }
    body.append(searchSection);
    if (room.kind === "グループ") {
      body.append(el("button", { type: "button", class: "wide", text: "部屋を出る", onclick: confirmLeave }));
    }
  }

  function drawPhotos() {
    const items = messages.filter((item) => item.photo_id);
    drawnPhotos = items.length;
    if (items.length === 0) {
      body.replaceChildren(el("p", { class: "hint", text: "この部屋の写真はまだありません。" }));
      return;
    }
    body.replaceChildren(
      el(
        "div",
        { class: "photo-grid" },
        ...items.map((item) =>
          el(
            "button",
            { type: "button", "aria-label": `${dateLabel(item.sent_at)}の写真の発言へ移る`, onclick: () => onJump(item.id) },
            photoImage(item.photo_id, "", "この部屋の写真")
          )
        )
      )
    );
  }

  function drawBody() {
    if (!alive || !room) {
      return;
    }
    if (tab === TAB_REVIEW) {
      drawReview();
    } else if (tab === TAB_PHOTOS) {
      drawPhotos();
    } else if (room.kind === "ツイン") {
      drawTwinRoomInfo();
    } else {
      drawPeopleRoomInfo();
    }
  }

  return {
    setRoom(next) {
      room = next;
      const tabsKey = JSON.stringify(tabsFor());
      const roomKey = JSON.stringify([room.name, room.twin_present, room.can_manage_twin, room.can_remove_members, room.members]);
      if (tabsKey !== drawnTabs) {
        drawnTabs = tabsKey;
        drawTabs();
        drawBody();
      } else if (roomKey !== drawnRoom && tab === TAB_ROOM) {
        drawBody();
      }
      drawnRoom = roomKey;
    },
    setMessages(items, knownPeople) {
      messages = items;
      people = knownPeople;
      drawResults();
      if (tab === TAB_PHOTOS && items.filter((item) => item.photo_id).length !== drawnPhotos) {
        drawBody();
      }
    },
    /* ツインが話したあとに呼ぶ。確かめが増えたら出し直す。直している途中の言葉は消さない。 */
    async reviewChanged() {
      if (tab !== TAB_REVIEW || body.querySelector("textarea")) {
        return;
      }
      try {
        const { items } = await api("/review");
        if (alive && tab === TAB_REVIEW && JSON.stringify(items.map((item) => item.id)) !== reviewSignature && !body.querySelector("textarea")) {
          drawBody();
        }
      } catch {
        // 読めなくても、次に話したときに読み直す
      }
    },
    destroy() {
      alive = false;
      host.replaceChildren();
    },
  };
}
