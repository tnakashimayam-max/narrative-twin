/*
  役割：画面の入口。起動の種類を読み、ログインと、下のタブの切り替えを行う。
  4つの極：1. 書く・話す、2. 分かち合う。
  守る原則：7（催促や連続の数の表示を作らない）、8（画面の隅の表示）、10（見える範囲はサーバーが決める）。

  ログインは名前か顔を押すだけで、切り替えも一度押すだけにする。だれとして使っているかはウィンドウごとに覚える。
  入口のツインは色がそろった固定の絵。ログインの前は、だれの記録の色か決まらないため。
  タブは本人に4つ、本人以外に3つ。「記録」は本人だけが開けるので、本人以外には出さない。
  トークのタブには、人どうしの新しい発言の数を出す。画面の中だけの表示で、通知はしない。
*/

import { renderAdvocate } from "./advocate.js";
import { ApiError, api, currentUserId, forgetUser, rememberUser, uploadPhoto } from "./api.js";
import { renderHome } from "./home.js";
import { shrinkPhoto } from "./photo.js";
import { renderProRecords } from "./prorecords.js";
import { renderRecords } from "./records.js";
import { renderTalk } from "./talk.js";
import { renderMeeting } from "./meeting.js";
import { renderTimeline } from "./timeline.js";
import { avatar, el, setTwinLevels, tabIcon, twinDrawing } from "./ui.js";

const view = document.getElementById("view");
const tabs = document.getElementById("tabs");
const plateLabel = document.getElementById("plate-label");
const plateTitle = document.getElementById("plate-title");
const ENTRANCE_TWIN_LEVELS = ["こい", "こい", "こい", "こい", "こい"];

let me = null;
let modeLabel = "";
let leaveCurrent = null;
let currentTab = "home";
let unreadTotal = 0;
const UNREAD_POLL_MS = 5000;

function clearView() {
  if (leaveCurrent) {
    leaveCurrent();
    leaveCurrent = null;
  }
  view.className = "view";
  view.replaceChildren();
}

/* 見出しの札。英字は飾りなので読み上げない。 */
function setPlate(label, title) {
  plateLabel.textContent = label;
  plateLabel.hidden = !label;
  plateTitle.textContent = title;
}

const PLATES = {
  home: ["HOME", "ホーム"],
  talk: ["TALK", "トーク"],
  timeline: ["TIMELINE", "タイムライン"],
  records: ["RECORDS", "これまでの記録"],
  advocate: ["ASK", "ツインに聞く"],
  prorecords: ["RECORDS", "記録"],
  meeting: ["MEETING", "ケア会議"],
};

const TAB_LABELS = [
  ["home", "ホーム", renderHome],
  ["talk", "トーク", renderTalk],
  ["timeline", "タイムライン", renderTimeline],
  ["records", "記録", renderRecords],
  ["advocate", "ツインに聞く", renderAdvocate],
  ["prorecords", "記録", renderProRecords],
  ["meeting", "ケア会議", renderMeeting],
];

/* 立場ごとのタブ。専門職は「ツインと話す」を先にし、託された家族には足す。 */
function tabsFor(person) {
  if (person.role === "本人") {
    return ["home", "talk", "timeline", "records"];
  }
  if (person.role === "専門職") {
    return ["advocate", "prorecords", "talk", "home"];
  }
  return person.can_use_supporter_screen ? ["home", "talk", "timeline", "advocate"] : ["home", "talk", "timeline"];
}

/* 専門職の画面は白を基調にする。今のツインの色も読む。 */
async function afterLogin() {
  document.body.dataset.look = me.role === "専門職" ? "modern" : "";
  try {
    setTwinLevels((await api("/twin/appearance")).levels);
  } catch {
    setTwinLevels(null);
  }
}

function showTabs(visible) {
  tabs.hidden = !visible;
}

function drawTabs() {
  const usable = tabsFor(me).map((key) => TAB_LABELS.find(([name]) => name === key));
  tabs.className = "tabs";
  tabs.replaceChildren(
    ...usable.map(([key, label]) =>
      el(
        "button",
        {
          type: "button",
          "aria-current": key === currentTab ? "page" : null,
          onclick: () => openTab(key),
        },
        tabIcon(key),
        label,
        key === "talk" && unreadTotal
          ? el("span", { class: "unread-count", "aria-label": `新しい発言 ${unreadTotal}件`, text: String(unreadTotal) })
          : ""
      )
    )
  );
}

/* トークのタブの数を読み直す。ログインしていないときと、読めないときは何もしない。 */
async function refreshUnread() {
  if (me && !tabs.hidden) {
    try {
      const { items } = await api("/rooms");
      const total = items.reduce((sum, room) => sum + (room.unread_count || 0), 0);
      if (total !== unreadTotal) {
        unreadTotal = total;
        drawTabs();
      }
    } catch {
      // 次の読み込みで直る
    }
  }
  setTimeout(refreshUnread, UNREAD_POLL_MS);
}

/* 部品に渡す入れ物。タブの切り替えや、別のタブを開く操作を持たせる。 */
function context() {
  return {
    me,
    view,
    showTabs,
    openTab,
    switchUser: logIn,
    setPlate,
    refreshMe: async () => {
      me = await api("/me");
      await afterLogin();
      return me;
    },
  };
}

function openTab(key, options = {}) {
  currentTab = key;
  clearView();
  document.body.dataset.screen = "";
  setPlate(...PLATES[key]);
  // 会議の画面はみんなで見るので、下のタブを出さず、今の様子とツインを大きくする
  showTabs(key !== "meeting");
  drawTabs();
  const entry = TAB_LABELS.find(([name]) => name === key);
  leaveCurrent = entry[2](view, context(), options) || null;
}

async function logIn(userId) {
  try {
    await api("/login", { method: "POST", json: { user_id: userId } });
    rememberUser(userId);
    me = await api("/me");
    await afterLogin();
  } catch (error) {
    showLogin(error.message);
    return;
  }
  openTab(tabsFor(me)[0]);
}

/* 文字で組んだロゴ。1行で並べ、NT は小さくする。 */
function wordmark() {
  return el(
    "div",
    { class: "logo-band" },
    el(
      "span",
      { class: "wordmark", role: "img", "aria-label": "Narrative Twin" },
      el("span", { class: "wm-nt", "aria-hidden": "true" }, "N", el("span", { text: "T" })),
      el("span", { "aria-hidden": "true", text: "Narrative" }),
      el("span", { class: "wm-twin", "aria-hidden": "true", text: "Twin" })
    )
  );
}

/* 入口の藍の面。ツインは面の斜めの縁からのぞく。札の言葉は起動の種類をそのまま出す（原則8）。 */
function entranceHero() {
  return el(
    "div",
    { class: "hero-panel" },
    el("div", { class: "hero-tag" }, el("span", { class: "tag", text: modeLabel })),
    el("p", { class: "hero-title" }, "本人の言葉を、", el("br"), "本人が直しながら", el("br"), "残す。"),
    el("p", { class: "hero-role" }, "話を聞く相棒", el("br"), el("b", { text: "ツイン（AI）" })),
    el("span", { class: "hero-sun", "aria-hidden": "true" }),
    el("span", { class: "hero-twin" }, twinDrawing(176, ENTRANCE_TWIN_LEVELS, { free: true }))
  );
}

async function showLogin(message) {
  clearView();
  showTabs(false);
  document.body.dataset.screen = "login";
  forgetUser();
  const list = el("div", { class: "person-grid" });
  const panel = el(
    "section",
    { class: "login-choice" },
    el("h1", { text: "どなたですか？" }),
    el("p", { class: "hint" }, "お名前を押すと、その人の画面になります。", el("br"), "パスワードはいりません。"),
    list,
    message ? el("p", { class: "error", role: "alert", text: message }) : null,
    el("p", { class: "hint login-note", text: "デモ用の簡易ログイン" })
  );
  view.append(el("div", { class: "login-screen" }, wordmark(), entranceHero(), panel));
  try {
    const { items } = await api("/users");
    list.replaceChildren(
      ...items.map((user) =>
        el(
          "button",
          { type: "button", class: user.role === "本人" ? "person-card first" : "person-card", onclick: () => logIn(user.id) },
          avatar(user, "portrait"),
          el("b", { text: user.name }),
          el("span", { class: "person-role", text: user.role })
        )
      )
    );
  } catch (error) {
    panel.append(el("p", { class: "error", role: "alert", text: error.message }));
  }
}

/* 自分用の最初の起動の、本人の登録の画面。 */
function showSelfRegistration() {
  clearView();
  showTabs(false);
  setPlate("", "はじめまして");
  const form = el("form", { class: "stack" });
  const name = el("input", { type: "text", required: true, maxLength: 40, autocomplete: "off" });
  const called = el("input", { type: "text", maxLength: 40, autocomplete: "off" });
  const year = el("input", { type: "number", min: 1880, max: 2100 });
  const gender = el(
    "select",
    {},
    el("option", { value: "", text: "選ばない" }),
    el("option", { value: "男性", text: "男性" }),
    el("option", { value: "女性", text: "女性" }),
    el("option", { value: "不明", text: "答えない" })
  );
  const photo = el("input", { type: "file", accept: "image/*" });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  form.append(
    el("label", { class: "field" }, "お名前", name),
    el("label", { class: "field" }, "呼ばれ方（ツインがこう呼びます。例：一郎さん）", called),
    el("label", { class: "field" }, "生まれた年（なくてもかまいません）", year),
    el("label", { class: "field" }, "性別（なくてもかまいません）", gender),
    el("label", { class: "field" }, "プロフィール写真（なくてもかまいません）", photo),
    message,
    el("button", { type: "submit", class: "primary wide", text: "はじめる" })
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    message.hidden = true;
    try {
      const created = await api("/self", {
        method: "POST",
        json: {
          name: name.value,
          called_name: called.value || null,
          birth_year: year.value ? Number(year.value) : null,
          gender: gender.value || null,
        },
      });
      rememberUser(created.id);
      if (photo.files[0]) {
        const shrunk = await shrinkPhoto(photo.files[0]);
        const uploaded = await uploadPhoto(shrunk, "プロフィール");
        await api(`/people/${created.id}`, { method: "PUT", json: { photo_id: uploaded.id } });
      }
      await logIn(created.id);
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  });
  view.append(
    el(
      "section",
      { class: "panel" },
      el("h1", { text: "はじめまして" }),
      el("p", { text: "ご本人のことを教えてください。あとから直せます。" }),
      form
    )
  );
}

async function start() {
  let mode;
  try {
    mode = await api("/mode");
  } catch (error) {
    view.append(el("section", { class: "panel" }, el("p", { class: "error", role: "alert", text: error.message })));
    return;
  }
  modeLabel = mode.label;
  document.getElementById("mode-label").textContent = mode.label;
  if (mode.needs_self) {
    showSelfRegistration();
    return;
  }
  if (currentUserId()) {
    try {
      me = await api("/me");
      await afterLogin();
      openTab(tabsFor(me)[0]);
      return;
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) {
        throw error;
      }
    }
  }
  showLogin();
}

start();
refreshUnread();
