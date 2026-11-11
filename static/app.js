/*
  作り方：このファイルは Claude Code（Anthropic の CLI）で書いた。何を作るかと守る約束は tyumajinさんが決めた（README の「Claude Code で作りました」）。
  役割：画面の入口。起動の種類を読み、ログインと、下のタブの切り替えを行う。
  4つの極：1. 書く・話す、2. 分かち合う。
  守る原則：7（催促や連続の数の表示を作らない）、8（画面の隅の表示）、10（見える範囲はサーバーが決める）。

  ログインは名前か顔を押すだけで、切り替えも一度押すだけにする。だれとして使っているかはウィンドウごとに覚える。
  入口のツインは色がそろった固定の絵。ログインの前は、だれの記録の色か決まらないため。
  タブは本人に4つ、本人以外に3つ。「記録」は本人だけが開けるので、本人以外には出さない。
  トークのタブには、人どうしの新しい発言の数を出す。画面の中だけの表示で、通知はしない。
  PC版とスマホ版の切り替えは static/device.js。作り直すのはトークと記録だけで、ほかの画面は CSS だけで変わる。入力の途中の言葉を消さないため。
  トークは書きかけを覚えていて、記録には入力がないので、作り直しても言葉は消えない。
  デモと新規のデータは「ログイン画面」のボタンで行き来する。自分用の起動には出さない。
*/

import { dateWords, renderAdvocate } from "./advocate.js";
import { ApiError, api, currentData, currentUserId, forgetData, forgetUser, rememberData, rememberUser, uploadPhoto } from "./api.js";
import { startDevice } from "./device.js";
import { currentTheme, startTheme } from "./theme.js";
import { renderHome } from "./home.js";
import { showLetterNotice, showReceivedLetters } from "./letter_notice.js";
import { shrinkPhoto } from "./photo.js";
import { renderProRecords } from "./prorecords.js";
import { renderRecords } from "./records.js";
import { renderSelfPetals } from "./self_petals.js";
import { currentRoomId, renderTalk } from "./talk.js";
import { renderMeeting } from "./meeting.js";
import { renderDocuments } from "./documents.js";
import { renderTimeline } from "./timeline.js";
import { renderTools } from "./tools.js";
import { avatar, el, photoPicker, setTwinLevels, tabIcon, twinDrawing } from "./ui.js";

const view = document.getElementById("view");
const tabs = document.getElementById("tabs");
const plateTitle = document.getElementById("plate-title");
const ENTRANCE_TWIN_LEVELS = ["こい", "こい", "こい", "こい", "こい"];

let me = null;
let modeLabel = "";
let modeKind = "";
let needsKey = false;
let leaveCurrent = null;
let currentTab = "home";
let currentOptions = {}; // 今の画面を開いたときの options（PC版とスマホ版が変わったときに開き直すため）
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

/* 見出しの札。 */
function setPlate(title) {
  plateTitle.textContent = title;
}

const PLATES = {
  home: "ホーム",
  talk: "トーク",
  timeline: "タイムライン",
  records: "記録",
  advocate: "ツインに聞く",
  prorecords: "記録",
  meeting: "ケア会議",
  document: "資料",
  documents: "資料",
  petals: "5つの花びら",
};

// タブに出さない画面で、下のタブのどれを今いる所として示すか。資料の画面と資料の部屋は「ツール」の続き
// 本人の「5つの花びら」（static/self_petals.js）は、ホームの「5つの花びら」から開くので「ホーム」の続き
const TAB_PARENT = { document: "tools", documents: "tools", petals: "home" };

/* タブの吹き出し（static/tips.js）。押すと開く画面で何ができるかを短く伝える。
   ホームは立場で中身が違うので、本人とそれ以外で分ける。タブを足すときは、ここにも1行足す。 */
const TAB_TIPS = {
  tools: "ツインにたずねる、資料のチェック、手紙などの道具を開きます",
  talk: "家族や専門職、ツインとの話の部屋を開きます",
  timeline: "本人が共有した便りを、新しい順に読めます",
  records: "これまでに話したことの記録を、日付ごとに見られます",
  advocate: "ツインが本人の立場で答える画面を開きます",
  prorecords: "本人の記録と、ツインの読みを見る画面を開きます",
  meeting: "みんなで見るケア会議の画面を開きます",
  document: "受け取った資料を開きます",
  documents: "受け取った資料を開きます",
  petals: "ツイン（AI）が読んだ、5つの花びらを見られます",
};

function tabTip(key, person) {
  if (key === "home") {
    return person.role === "本人" ? "ツインと話す入口や、確かめることのある画面を開きます" : "自分の立場の確かめや、人の切り替えができる画面を開きます";
  }
  return TAB_TIPS[key];
}

const TAB_LABELS = [
  ["tools", "ツール", renderTools],
  ["home", "ホーム", renderHome],
  ["talk", "トーク", renderTalk],
  ["timeline", "タイムライン", renderTimeline],
  ["records", "記録", renderRecords],
  ["advocate", "ツインに聞く", renderAdvocate],
  ["prorecords", "記録", renderProRecords],
  ["meeting", "ケア会議", renderMeeting],
  // 1件の資料を開いた資料の画面。前の呼び方 openTab("document", { documentId }) も、資料の画面でその資料を選んで開く
  ["document", "資料", renderDocuments],
  // 資料の画面（static/documents.js）。ツールの「資料を渡してチェックする」から openTab("documents") で開く
  ["documents", "資料", renderDocuments],
  // 本人の「5つの花びら」。ホームの「5つの花びら」から openTab("petals") で開く（2026-11-07）
  ["petals", "5つの花びら", renderSelfPetals],
];

/* 立場ごとのタブ。託された専門職はツールを先にし、託された家族には「ツインに聞く」を足す。 */
function tabsFor(person) {
  if (person.role === "本人") {
    return ["home", "talk", "timeline", "records"];
  }
  if (person.role === "専門職") {
    // 託されていない専門職に見えるのは、入っている部屋の話だけ
    if (!person.can_use_supporter_screen) {
      return ["home", "talk"];
    }
    // 託された専門職には、いつもツールのタブを出す。サーバーのツールがない職種（主治医など）も、ツールの中にケア会議がある（2026-11-06）
    return ["tools", "advocate", "prorecords", "talk", "home"];
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
  // PC版の札が読む。タブが出ている間はアプリの名前を、タブがない画面は画面の名前を出す（style.css）
  document.body.dataset.tabs = visible ? "on" : "off";
}

/* PC版とスマホ版が変わったとき。トークは同じ部屋を続けて出し、記録は日付の並べ方を変える。 */
function onDeviceChange() {
  if (!me || document.body.dataset.screen === "login") {
    return;
  }
  if (currentTab === "talk") {
    const roomId = currentRoomId(me);
    openTab("talk", roomId === null ? {} : { roomId });
  } else if (currentTab === "records") {
    openTab("records");
  } else if (currentTab === "document" || currentTab === "documents") {
    // 資料の画面は、同じ資料のまま並べ方を変える
    openTab("document", currentOptions);
  }
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
          "aria-current": key === (TAB_PARENT[currentTab] || currentTab) ? "page" : null,
          tip: tabTip(key, me),
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
    // 新規のデータでキーがないとき。ツインと話す入口の近くに案内を出す
    needsKey,
    modeKind,
    refreshMe: async () => {
      me = await api("/me");
      await afterLogin();
      return me;
    },
  };
}

function openTab(key, options = {}) {
  currentTab = key;
  currentOptions = options;
  clearView();
  document.body.dataset.screen = "";
  setPlate(PLATES[key]);
  // 会議の画面はみんなで見るので、下のタブを出さず、今の様子とツインを大きくする
  showTabs(key !== "meeting");
  drawTabs();
  const entry = TAB_LABELS.find(([name]) => name === key);
  leaveCurrent = entry[2](view, context(), options) || null;
  // 確かめ待ちの手紙は、ご本人と託されたご家族のホームに知らせる（static/letter_notice.js）。
  // 確かめられるかはサーバーが決める（/me の can_check_letters）。託されていない家族には問い合わせない
  if (key === "home" && me.can_check_letters) {
    showLetterNotice(view, () => currentTab === "home");
  }
  // ご本人から届いた手紙は、受け取った人のホームに知らせる（static/letter_notice.js、app/self_letters.py）
  if (key === "home" && me.role !== "本人") {
    showReceivedLetters(view, () => currentTab === "home");
  }
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

/* 入口のロゴ。暗い表示では背景の四角なしの版に切り替わる。
   表示はボタンでも選べるので、端末の設定ではなく今の表示（static/theme.js）で選ぶ。 */
const LOGO_LIGHT = "brand/narrativetwin-logo.svg";
const LOGO_DARK = "brand/narrativetwin-logo-on-dark.svg";

function wordmark() {
  const logo = el("img", { class: "brand-logo", src: currentTheme() === "dark" ? LOGO_DARK : LOGO_LIGHT, alt: "Narrative Twin", width: "263", height: "44" });
  return el("div", { class: "logo-band" }, logo);
}

window.addEventListener("nt-theme", (event) => {
  for (const logo of document.querySelectorAll(".brand-logo")) {
    logo.src = event.detail === "dark" ? LOGO_DARK : LOGO_LIGHT;
  }
});

/* 入口の藍の面。ツインは面の斜めの縁からのぞく。札の言葉は起動の種類をそのまま出す（原則8）。 */
function entranceHero() {
  return el(
    "div",
    { class: "hero-panel" },
    el("div", { class: "hero-tag" }, el("span", { class: "tag", text: modeLabel })),
    el("p", { class: "hero-title" }, "あなたの言葉を、", el("br"), "未来の自分へ", el("br"), "託す。"),
    el("p", { class: "hero-role" }, "話を聞く相棒", el("br"), el("b", { text: "ツイン（AI）" })),
    el("span", { class: "hero-sun", "aria-hidden": "true" }),
    el("span", { class: "hero-twin" }, twinDrawing(176, ENTRANCE_TWIN_LEVELS, { free: true }))
  );
}

/* 最初からやり直す。ページごと読み直して start() へ戻る。
   前のデータの物は me や見た目のほかに、トークの書きかけ、ツインの色、写真の控えと部品ごとにあり、
   途中の通信があとから札の見出しを書き換えることもある。読み直せば、1つも残らない。 */
function restart() {
  location.reload();
}

/* 「ログイン画面」を押したとき。だれとして、どのデータを使っているかを両方忘れ、デモの入口へ戻る。 */
function backToEntrance() {
  forgetUser();
  forgetData();
  restart();
}

function returnButton(className) {
  return el("button", { type: "button", class: className, text: "ログイン画面", tip: "入口に戻って、別の人を選び直せます。記録は消えません", onclick: backToEntrance });
}

/* 新規のデータを開く。前の人の番号は持ち込まない。 */
function openData(dataId) {
  forgetUser();
  rememberData(dataId);
  restart();
}

/* デモの入口の「新規で始める」と、前に作った新規の一覧。本人の登録がまだの新規は、サーバーが一覧に入れない。 */
function freshSection() {
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const earlier = el("div", { class: "fresh-earlier" });
  const section = el(
    "section",
    { class: "fresh-start" },
    el("p", { class: "hint", text: "デモとは別に、何もない状態から使えます。デモの10年分はそのまま残ります。" }),
    el("button", {
      type: "button",
      class: "wide",
      text: "新規で始める",
      tip: "何も入っていない新しいデータを作って始めます。デモは残ります",
      onclick: async () => {
        message.hidden = true;
        try {
          const { id } = await api("/fresh", { method: "POST" });
          openData(id);
        } catch (error) {
          message.textContent = error.message;
          message.hidden = false;
        }
      },
    }),
    message,
    earlier
  );
  api("/fresh")
    .then(({ items }) => {
      if (items.length) {
        earlier.append(
          el("h2", { class: "grid-head", text: "前に作った新規" }),
          ...items.map((item) =>
            el(
              "button",
              { type: "button", class: "wide fresh-row", tip: `前に作った「${item.name}」のデータを開き直します`, onclick: () => openData(item.id) },
              el("b", { text: item.name }),
              el("span", { class: "person-role", text: dateWords(item.created_at) })
            )
          )
        );
      }
    })
    .catch(() => {
      // 一覧が読めなくても、新規では始められる
    });
  return section;
}

async function showLogin(message) {
  clearView();
  showTabs(false);
  document.body.dataset.screen = "login";
  forgetUser();
  const list = el("div", { class: "person-grid" });
  // 初めて開く人への道案内。人の一覧を読んだあとで、デモの道筋の3人がそろっていれば出す
  const guide = el("p", { class: "login-guide", hidden: true });
  const panel = el(
    "section",
    { class: "login-choice" },
    el("h1", { text: "どなたですか？" }),
    el("p", { class: "hint" }, "お名前を押すと、その人の画面になります。", el("br"), "パスワードはいりません。"),
    guide,
    list,
    message ? el("p", { class: "error", role: "alert", text: message }) : null,
    // 新規のデータはデモではないが、入り方は同じなので「簡易ログイン」とだけ書く
    el("p", { class: "hint login-note", text: modeKind === "fresh" ? "簡易ログイン" : "デモ用の簡易ログイン" }),
    modeKind === "demo" ? freshSection() : null,
    // 入口には見出しの帯がないので、新規の名前の一覧ではここに置く
    modeKind === "fresh"
      ? el(
          "div",
          { class: "fresh-start" },
          el("p", { class: "hint", text: "デモの入口へ戻ります。このデータは消えません。" }),
          returnButton("wide")
        )
      : null
  );
  view.append(el("div", { class: "login-screen" }, wordmark(), entranceHero(), panel));
  try {
    const { items } = await api("/users");
    // 初めて開く人がどの人で入ればよいか分かるように、デモの道筋の3人に色を付ける（tyumajinさんの依頼、2026-11-07）。
    // 本人と担当のケアマネジャーは薄い赤、最初の家族（デモでは本人から託された長女）はクリーム（style.css）
    const firstFamily = items.find((user) => user.role === "家族");
    const toneOf = (user) => (user.role === "本人" || user.profession === "ケアマネジャー" ? "tone-red" : user === firstFamily ? "tone-cream" : "");
    // 入る順の番号。本人 → 託された家族 → ケアマネジャーの順に見ると、全体が分かる
    const path = [items.find((user) => user.role === "本人"), firstFamily, items.find((user) => user.profession === "ケアマネジャー")];
    const stepOf = (user) => (path.every(Boolean) ? path.indexOf(user) + 1 : 0);
    if (path.every(Boolean)) {
      const roleWords = ["本人", "家族", "ケアマネジャー"];
      guide.replaceChildren(
        "デモを確認したい方は、番号の順に入ってみてください。",
        el("br"),
        ...path.flatMap((user, index) => [
          index ? " → " : "",
          // 1人分（番号、名前、立場）は途中で折り返さない
          el("span", { class: "login-guide-item" }, el("span", { class: "login-guide-step", text: String(index + 1) }), `${user.name}（${roleWords[index]}）`),
        ])
      );
      guide.hidden = false;
    }
    const card = (user) =>
      el(
        "button",
        {
          type: "button",
          class: ["person-card", user.role === "本人" ? "first" : "", toneOf(user)].filter(Boolean).join(" "),
          tip: stepOf(user) ? `${stepOf(user)}番め。${user.name}の立場で、その人の画面に入ります` : `${user.name}の立場で、その人の画面に入ります`,
          onclick: () => logIn(user.id),
        },
        stepOf(user) ? el("span", { class: "person-step", "aria-hidden": "true", text: String(stepOf(user)) }) : null,
        avatar(user, "portrait"),
        el("b", { text: user.name }),
        el("span", { class: "person-role", text: user.profession || user.role })
      );
    // 専門職は見出しで分ける。札には職種を出す
    const professionals = items.filter((user) => user.role === "専門職");
    list.replaceChildren(
      ...items.filter((user) => user.role !== "専門職").map(card),
      ...(professionals.length ? [el("h2", { class: "grid-head", text: "専門職" }), ...professionals.map(card)] : [])
    );
  } catch (error) {
    panel.append(el("p", { class: "error", role: "alert", text: error.message }));
  }
}

/* 本人がまだいないデータの、本人の登録の画面。自分用の最初の起動と、「新規で始める」のあとに出す。 */
function showSelfRegistration() {
  clearView();
  showTabs(false);
  setPlate("はじめまして");
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
  const icon = el("input", { type: "file", accept: "image/*" });
  const profile = el("input", { type: "file", accept: "image/*" });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  form.append(
    el("label", { class: "field" }, "お名前", name),
    el("label", { class: "field" }, "呼ばれ方（ツインがこう呼びます。例：一郎さん）", called),
    el("label", { class: "field" }, "生まれた年（なくてもかまいません）", year),
    el("label", { class: "field" }, "性別（なくてもかまいません）", gender),
    el("label", { class: "field" }, "アイコン（丸く表示します。AI には送りません。なくてもかまいません）", photoPicker(icon)),
    el("label", { class: "field" }, "プロフィール写真（AI には送りません。なくてもかまいません）", photoPicker(profile)),
    message,
    el("button", { type: "submit", class: "primary wide", text: "はじめる", tip: "入れた内容でご本人を登録して、使い始めます" })
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
      // 枠ごとに送る。同じ画像を選んでも、写真の行は分かれる
      for (const [field, input] of [["photo_id", icon], ["profile_photo_id", profile]]) {
        if (input.files[0]) {
          const shrunk = await shrinkPhoto(input.files[0]);
          const uploaded = await uploadPhoto(shrunk, "プロフィール");
          await api(`/people/${created.id}`, { method: "PUT", json: { [field]: uploaded.id } });
        }
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
      el("p", { text: "ご本人のことを教えてください。あとから修正できます。" }),
      form
    )
  );
}

async function start() {
  let mode;
  try {
    mode = await api("/mode");
  } catch (error) {
    // 開いていた新規が見つからない。人の番号も別のデータのものなので一緒に忘れ、デモからやり直す
    if (error.status === 404 && currentData()) {
      forgetUser();
      forgetData();
      start();
      return;
    }
    view.append(el("section", { class: "panel" }, el("p", { class: "error", role: "alert", text: error.message })));
    return;
  }
  modeLabel = mode.label;
  modeKind = mode.kind;
  needsKey = mode.kind === "fresh" && !mode.ai_ready;
  document.getElementById("mode-label").textContent = mode.label;
  if (mode.kind === "demo" || mode.kind === "fresh") {
    document.body.dataset.returnButton = "on";
    document.querySelector(".site-header .tags").append(returnButton("login-return"));
  }
  if (mode.needs_self) {
    showSelfRegistration();
    return;
  }
  // 別のタブのページのロゴから来たとき（./#home）は、最初の画面をホームにする（static/page_nav.js）
  const wantHome = location.hash === "#home";
  if (wantHome) {
    history.replaceState(null, "", location.pathname + location.search);
  }
  if (currentUserId()) {
    try {
      me = await api("/me");
      await afterLogin();
      openTab(wantHome ? homeTab() : tabsFor(me)[0]);
      return;
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) {
        throw error;
      }
    }
  }
  showLogin();
}

/* その人のホームのタブ。ホームのない人はいないが、念のため最初のタブにする */
function homeTab() {
  const keys = tabsFor(me);
  return keys.includes("home") ? "home" : keys[0];
}

/* 左上のロゴを押したとき。どの画面からでもホームへ戻る（tyumajinさんの依頼、2026-11-07）。
   ケア会議のようにタブを隠す画面からも戻れる。まだだれも選んでいないときは入口を開き直す。 */
function goHome() {
  if (!me || document.body.dataset.screen === "login") {
    location.href = "./";
    return;
  }
  openTab(homeTab());
  view.scrollTo?.({ top: 0 });
  window.scrollTo?.({ top: 0 });
}

// ブラウザが古い index.html を覚えていると、このリンクがない。そのときも画面全体を止めない（?.）
document.getElementById("home-link")?.addEventListener("click", (event) => {
  // 新しいタブで開く押し方（中ボタン、Ctrl など）はリンクのままにする
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return;
  }
  event.preventDefault();
  goHome();
});

// 別のタブで開いたページ（手紙、アセスメントの入力など）のロゴから「ホームへ」と頼まれたとき（static/page_nav.js）
window.addEventListener("message", (event) => {
  if (event.origin === location.origin && event.data?.type === "nt-home") {
    goHome();
  }
});

// 最初の画面を作る前に形を決める。切り替えのボタンは「架空データ」の隣に置く（原則8）
// 明るい・暗いの切り替えは、PC・スマホの切り替えの隣に置く（static/theme.js）
document.querySelector(".site-header .tags").prepend(startDevice(onDeviceChange), startTheme());
start();
refreshUnread();
