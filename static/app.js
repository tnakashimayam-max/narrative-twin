/*
  役割：画面の入口。起動の種類を読み、ログインと、下のタブの切り替えを行う。
  4つの極：1. 書く・話す、2. 分かち合う。
  守る原則：7（催促や数の表示を作らない）、8（画面の隅の表示）、10（見える範囲はサーバーが決める）。

  ログインは名前を押すだけで、切り替えも一度押すだけにする。だれとして使っているかはウィンドウごとに覚える。
  タブは本人に4つ、本人以外に3つ。「記録」は本人だけが開けるので、本人以外には出さない。
*/

import { ApiError, api, currentUserId, forgetUser, rememberUser, uploadPhoto } from "./api.js";
import { renderHome } from "./home.js";
import { shrinkPhoto } from "./photo.js";
import { renderRecords } from "./records.js";
import { renderTalk } from "./talk.js";
import { renderTimeline } from "./timeline.js";
import { avatar, el, tabIcon } from "./ui.js";

const view = document.getElementById("view");
const tabs = document.getElementById("tabs");
const loginNote = document.getElementById("login-note");

let me = null;
let leaveCurrent = null;
let currentTab = "home";

function clearView() {
  if (leaveCurrent) {
    leaveCurrent();
    leaveCurrent = null;
  }
  view.className = "view";
  view.replaceChildren();
}

const TAB_LABELS = [
  ["home", "ホーム", renderHome],
  ["talk", "トーク", renderTalk],
  ["timeline", "タイムライン", renderTimeline],
  ["records", "記録", renderRecords],
];

function showTabs(visible) {
  tabs.hidden = !visible;
}

function drawTabs() {
  const usable = TAB_LABELS.filter(([key]) => key !== "records" || me.role === "本人");
  tabs.className = usable.length === 3 ? "tabs three" : "tabs";
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
        label
      )
    )
  );
}

/* 部品に渡す入れ物。タブの切り替えや、別のタブを開く操作を持たせる。 */
function context() {
  return {
    me,
    view,
    showTabs,
    openTab,
    switchUser: logIn,
    refreshMe: async () => {
      me = await api("/me");
      return me;
    },
  };
}

function openTab(key, options = {}) {
  currentTab = key;
  clearView();
  showTabs(true);
  drawTabs();
  const entry = TAB_LABELS.find(([name]) => name === key);
  leaveCurrent = entry[2](view, context(), options) || null;
}

async function logIn(userId) {
  try {
    me = await api("/login", { method: "POST", json: { user_id: userId } });
  } catch (error) {
    showLogin(error.message);
    return;
  }
  rememberUser(me.id);
  loginNote.hidden = true;
  openTab("home");
}

async function showLogin(message) {
  clearView();
  showTabs(false);
  loginNote.hidden = false;
  forgetUser();
  const list = el("div", { class: "user-list" });
  const panel = el(
    "section",
    { class: "panel" },
    el("h1", { text: "どなたですか？" }),
    el("p", { text: "お名前を押すと、その人の画面に切り替わります。パスワードはいりません。" }),
    list,
    message ? el("p", { class: "error", role: "alert", text: message }) : null
  );
  view.append(panel);
  try {
    const { items } = await api("/users");
    list.replaceChildren(
      ...items.map((user) =>
        el(
          "button",
          { type: "button", class: "user-button", onclick: () => logIn(user.id) },
          avatar(user),
          `${user.name}（${user.role}）`
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
  document.getElementById("mode-label").textContent = mode.label;
  if (mode.needs_self) {
    showSelfRegistration();
    return;
  }
  if (currentUserId()) {
    try {
      me = await api("/me");
      loginNote.hidden = true;
      openTab("home");
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
