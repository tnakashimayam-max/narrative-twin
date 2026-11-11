/*
  役割：画面を組み立てる小さな部品。文字の入った要素、人のアイコン、ツインの姿、日付の表示など。
  4つの極：特定の極に属さない土台。
  守る原則：12（ツインは AI の相棒で、本人にも実在の人にも似せない）、13（色がのるのは花びらだけ。数字を出さない）。

  文字は textContent で入れる。人の言葉や AI の文を HTML として解釈させないため。
  ツインの姿は「花の子」。頭のまわりに5枚の花びらを開いた形で、性別や年齢が分からず、人間そのものに見えない。
  背景、体、顔の色はいつも出る。色がのるのは花びらだけで、本人が確かめた分だけ濃くなる（原則13）。
  色がのる前の花びらは、白い面に細い線で描く。
  ここに写真を入れる道は作らない。
*/

import { photoUrl } from "./api.js";
import { hasStamp, stampParts } from "./stamps.js";
import { installTips } from "./tips.js";

// ボタンの吹き出し（static/tips.js）。どのページもこのファイルを読み込むので、ここで1回だけ動かす。
// ページを足しても、ボタンを足しても、吹き出しはそのまま付いてくる
installTips();

/* 新規のデータでキーがないときの案内。エラーではないので、赤い文字にしない。 */
export const NEEDS_KEY_WORDS = "ツインと話すには API キーが要ります。";

/* 要素を作る。props の on で始まるものは出来事の受け取り、それ以外は属性にする。
   tip はボタンの吹き出しの言葉（押すと何が起きるか）。data-tip に入れ、static/tips.js が出す。
   ボタンを作るところに一緒に書くので、ボタンを直すときに吹き出しも一緒に直せる。 */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) {
      continue;
    }
    if (key.startsWith("on")) {
      node.addEventListener(key.slice(2), value);
    } else if (key === "tip") {
      node.dataset.tip = value;
    } else if (key === "class") {
      node.className = value;
    } else if (key === "text" && hasStamp(value)) {
      // 発言の中のスタンプは、ここで絵にする（static/stamps.js）。どの画面の言葉もここを通るため
      node.append(...stampParts(value));
    } else if (key === "text") {
      node.textContent = value;
    } else if (key in node && key !== "list" && key !== "form") {
      node[key] = value;
    } else {
      node.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of children.flat()) {
    if (hasStamp(child)) {
      node.append(...stampParts(child));
    } else if (child !== undefined && child !== null && child !== false) {
      node.append(child);
    }
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";

/* 親に子を足す。null や false を「null」という文字にしない。 */
export function add(parent, ...children) {
  for (const child of children.flat()) {
    if (child !== undefined && child !== null && child !== false) {
      parent.append(child);
    }
  }
  return parent;
}

/* 取り消せない操作のボタン。押し間違えないよう、2回押す形にする。ブラウザの確認の窓は使わない。
   tip は1回目の吹き出し。1回目を押したあとは、もう一度押すと決まることを吹き出しで伝える。 */
export function twoStepButton(label, confirmLabel, action, className = "quiet", tip = "") {
  let armed = false;
  const button = el("button", {
    type: "button",
    class: className,
    text: label,
    tip: tip || undefined,
    onclick: async () => {
      if (!armed) {
        armed = true;
        button.textContent = confirmLabel;
        button.dataset.tip = "もう一度押すと決まります。取り消せません";
        return;
      }
      await action();
    },
  });
  return button;
}

/* 書いた言葉をキーでも送れるようにする。Alt+Enter か Ctrl+Enter（Mac は ⌘+Enter）で、送るボタンを押したのと同じにする。
   Enter だけなら改行のまま。日本語の変換を決める Enter で送らないため。変換の途中のキーも見ない。
   ボタンを押す形にするので、送っている間（ボタンが止まっている間）に二重に送ることはない。
   マウスのある画面では、ボタンにマウスを重ねると、吹き出しの終わりにキーの案内が出る。 */
export function sendWithKeys(textBox, button) {
  if (window.matchMedia?.("(hover: hover)").matches && !button.dataset.tip?.includes("Alt+Enter")) {
    const tip = button.dataset.tip || "書いた言葉を送ります";
    button.dataset.tip = `${tip}（Alt+Enter でも送れます）`;
  }
  textBox.addEventListener("keydown", (event) => {
    const withKey = event.altKey || event.ctrlKey || event.metaKey;
    if (event.key !== "Enter" || !withKey || event.isComposing || event.keyCode === 229) {
      return;
    }
    event.preventDefault();
    button.click();
  });
}

/* SVG の要素を作る。 */
export function svg(tag, attributes = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null) {
      continue;
    }
    if (key.startsWith("on")) {
      node.addEventListener(key.slice(2), value);
    } else if (key === "tip") {
      // 押せる図形（エコマップの人など）の吹き出し。el() と同じ書き方にする
      node.setAttribute("data-tip", value);
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of children.flat()) {
    if (child !== undefined && child !== null && child !== false) {
      node.append(child);
    }
  }
  return node;
}

/* ツインの色。花びらの順に、濃さの言葉だけを受け取る。名前も数も出さない（原則13）。
   色は index.html の pw0〜pw4（藍、朱、山吹、若竹、藤）にある。 */
const LEVEL_OPACITY = { なし: 0, うすい: 0.26, 中くらい: 0.5, こい: 0.82 };
let twinLevels = null;

export function setTwinLevels(levels) {
  twinLevels = levels;
  twinIcons?.setTwinIconLevels(levels);
}

/* ツインのアイコンの3D（static/twin3d/icons.js）。最初のアイコンを作るときに1回だけ読み込む。
   読み込めない、描けないときは2Dのアイコンのまま残す */
let twinIcons = null;
let twinIconsLoading = null;
let twinIconsFailed = false;

function upgradeTwinIcon(holder) {
  if (twinIconsFailed) {
    return;
  }
  twinIconsLoading ??= import("./twin3d/icons.js")
    .then((module) => {
      twinIcons = module;
    })
    .catch(() => {
      twinIconsFailed = true;
    });
  twinIconsLoading.then(() => {
    if (!twinIcons || twinIconsFailed) {
      return;
    }
    try {
      twinIcons.attachTwinIcon(holder, twinLevels);
    } catch {
      twinIconsFailed = true;
    }
  });
}

/* 今の花びらごとの濃さの言葉。3Dの舞台（static/twin_stage.js）も同じ色にするため */
export function currentTwinLevels() {
  return twinLevels;
}

// 花びらの形。根元が (0,0)、先が上。専門職の「記録」の花びらのボタンの線画と、2Dの姿で選んだ花びらの輪郭にも使う
export const PETAL_SHAPE = "M0 0C-13-16-35-44-32-70C-29-92-9-102 3-100C25-97 37-80 33-60C29-38 11-16 0 0Z";
const PETAL_VEIN = "M0-6C-3-32-4-58-1-86M-1-40C-9-52-14-62-16-74M-1-46C7-56 12-66 15-76";
const PETAL_LIGHT = "M-20-62C-21-76-13-88-3-90C-12-82-17-72-20-62Z";
const TWIN_LINE = { stroke: "#6E5E50", "stroke-width": "1", "vector-effect": "non-scaling-stroke" };

/* 花びら1枚。表示の大きさで描き分ける。小さいときに細かい線を描くと、つぶれて読めないため。 */
function twinPetal(index, opacity, px) {
  const transform = `translate(50 46) rotate(${index * 72}) scale(0.46 0.41)`;
  const parts = [svg("path", { transform, d: PETAL_SHAPE, fill: "url(#pbase)" })];
  if (opacity > 0) {
    parts.push(
      svg(
        "g",
        { filter: px >= 64 ? "url(#wc)" : undefined },
        svg("path", { transform, d: PETAL_SHAPE, fill: `url(#pw${index})`, "fill-opacity": String(opacity) })
      )
    );
  }
  if (px >= 44) {
    parts.push(
      svg("path", {
        transform,
        d: PETAL_VEIN,
        fill: "none",
        ...TWIN_LINE,
        stroke: "#7A6A5A",
        opacity: opacity > 0 ? "0.22" : "0.3",
        "stroke-linecap": "round",
      })
    );
  }
  if (px >= 64) {
    parts.push(svg("path", { transform, d: PETAL_LIGHT, fill: "#fff", opacity: "0.55" }));
  }
  parts.push(
    svg("path", {
      transform,
      d: PETAL_SHAPE,
      fill: "none",
      ...TWIN_LINE,
      "stroke-width": px <= 32 ? "0.7" : "1",
      opacity: opacity >= 0.5 ? "0.5" : "0.8",
      "stroke-linejoin": "round",
    })
  );
  return parts;
}

/* 顔。小さいときは目と口を大きくし、28px以下では口を省く。 */
function twinFace(px) {
  const eyeScale = px <= 28 ? 1.4 : px <= 44 ? 1.18 : 1;
  const radius = 2.8 * eyeScale;
  const eye = (x) => [
    svg("ellipse", { cx: x, cy: 45, rx: radius, ry: radius * 1.14, fill: "#2A2730" }),
    svg("circle", { cx: x + radius * 0.32, cy: 45 - radius * 0.42, r: radius * 0.38, fill: "#fff" }),
  ];
  const cheekScale = px <= 28 ? 1.2 : 1;
  const cheek = (x) =>
    svg("ellipse", { cx: x, cy: 51.2, rx: 3.6 * cheekScale, ry: 2.3 * cheekScale, fill: "#F08A78", opacity: "0.55" });
  const parts = [eye(42.4), eye(57.6), cheek(37.4), cheek(62.6)];
  if (px > 28) {
    const mouthScale = px <= 44 ? 1.15 : 1;
    const half = 2.7 * mouthScale;
    parts.push(
      svg("path", {
        d: `M${50 - half} 51.2Q50 ${51.2 + half * 0.95} ${50 + half} 51.2`,
        fill: "none",
        stroke: "#6B4438",
        "stroke-width": String(1.15 * mouthScale),
        "stroke-linecap": "round",
      })
    );
  }
  return parts;
}

/* ツインの姿。戻り値は <svg> のまま。地図の図の中に入れるので、外を包まない。
   free は背景の丸なしの姿で、入口の主役に使う。 */
export function twinDrawing(size = 100, levels = twinLevels, { free = false } = {}) {
  const figure = [
    svg("path", { d: "M20 102C20 82 33 72 50 72S80 82 80 102Z", fill: "url(#leafG)", ...TWIN_LINE, "stroke-opacity": "0.5" }),
    [0, 1, 2, 3, 4].map((index) => twinPetal(index, LEVEL_OPACITY[levels?.[index]] ?? 0, size)),
    // data-part は、花びらの舞台（static/twin_stage.js）が、選んだ花びらの輪郭を頭の下に差し込む目印
    svg("circle", { cx: "50", cy: "46", r: "20.5", fill: "url(#skin)", ...TWIN_LINE, "stroke-opacity": "0.55", "data-part": "head" }),
    twinFace(size),
  ].flat(Infinity);
  const attributes = { viewBox: "0 0 100 100", width: String(size), height: String(size), role: "img", "aria-label": "ツイン（AI）の姿" };
  if (free) {
    return svg("svg", attributes, figure);
  }
  return svg(
    "svg",
    attributes,
    svg("circle", { cx: "50", cy: "50", r: "50", fill: "url(#twbg)" }),
    svg("g", { "clip-path": "url(#twc)" }, figure),
    svg("circle", { cx: "50", cy: "50", r: "49.4", fill: "none", stroke: "#fff", "stroke-width": size <= 32 ? "1" : "1.6", "vector-effect": "non-scaling-stroke" })
  );
}

export function twinFigure({ large = false, small = false, thinking = false } = {}) {
  const classes = ["twin-figure"];
  if (large) {
    classes.push("large");
  }
  if (small) {
    classes.push("small");
  }
  if (thinking) {
    classes.push("thinking");
  }
  // 表示の大きさを渡す。描き分けは表示の大きさで決まるため
  const holder = el("span", { class: classes.join(" ") }, twinDrawing(large ? 96 : small ? 36 : 44));
  // 2Dの絵の上に、ゆっくり動く3Dを重ねる（2026-11-06）。大きさは2Dと同じ
  upgradeTwinIcon(holder);
  return holder;
}

/* 人のアイコン。写真がなければ、名前の頭文字の丸にする。 */
export function avatar(person, size = "") {
  const label = person?.called_name || person?.name || "？";
  const initial = Array.from(label.trim())[0] || "？";
  const classes = ["avatar", size, person?.role === "本人" ? "me" : ""].filter(Boolean).join(" ");
  const node = el("span", { class: classes, "aria-hidden": "true" }, initial);
  if (person?.photo_id) {
    photoUrl(person.photo_id).then((url) => {
      if (url) {
        node.replaceChildren(el("img", { src: url, alt: "" }));
      }
    });
  }
  return node;
}

/* 大きく出すプロフィール写真。丸いアイコンとは別の枠で、写真がない人には何も出さない。
   顔を黒い面や濃い四角い枠で囲むと遺影に見えるので、暗い表示でも明るい面に角丸で置く（style.css の .profile-photo）。 */
export function profilePhoto(person) {
  if (!person?.profile_photo_id) {
    return null;
  }
  const frame = el("figure", { class: "profile-photo", hidden: true });
  photoUrl(person.profile_photo_id).then((url) => {
    if (url) {
      frame.append(el("img", { src: url, alt: `${person.called_name || person.name}のプロフィール写真` }));
      frame.hidden = false;
    }
  });
  return frame;
}

/* 自分の画面に出す小さなプロフィール写真。押すと、人の窓と同じ大きさで開く。写真がない人には何も出さない。
   自分の画面では、幅390でも「ツイン（AI）からの確かめ」を最初の画面に入れるため、大きな写真は開いたときだけ出す。 */
export function profilePhotoButton(person) {
  if (!person?.profile_photo_id) {
    return null;
  }
  const name = person.called_name || person.name;
  const image = el("img", { alt: "" });
  const button = el(
    "button",
    {
      type: "button",
      class: "profile-thumb",
      hidden: true,
      "aria-label": `${name}のプロフィール写真を大きく見る`,
      tip: "写真を、この画面の上に大きく開きます",
      onclick: () =>
        openSheet((inner, close) =>
          add(inner, profilePhoto(person), el("button", { type: "button", class: "wide", text: "閉じる", tip: "大きな写真を閉じて、もとの画面に戻ります", onclick: close }))
        ),
    },
    image,
    el("span", { text: "大きく見る" })
  );
  photoUrl(person.profile_photo_id).then((url) => {
    if (url) {
      image.src = url;
      button.hidden = false;
    }
  });
  return button;
}

/* 写真を選ぶ欄。ブラウザの英語の部品（Choose File / No file chosen）を見せず、「写真を選ぶ」と選んだファイル名を出す。
   入力は見えなくするだけで、ラベルの中に置くので、ラベルのどこを押しても選ぶ窓が開く。読み上げはラベルの言葉のまま */
export function photoPicker(input, words = "写真を選ぶ") {
  input.classList.add("visually-hidden");
  const name = el("span", { class: "photo-picker-name", text: "まだ選んでいません" });
  input.addEventListener("change", () => {
    name.textContent = input.files?.[0]?.name || "まだ選んでいません";
  });
  return el("span", { class: "photo-picker" }, input, el("span", { class: "photo-picker-button", "aria-hidden": "true", text: words }), name);
}

/* 写真の <img> を作る。見てよい写真だけが読める。 */
export function photoImage(photoId, className = "bubble-photo", alt = "送られた写真") {
  const image = el("img", { class: className, alt });
  photoUrl(photoId).then((url) => {
    if (url) {
      image.src = url;
    }
  });
  return image;
}

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

export function dateLabel(iso) {
  const day = new Date(iso);
  return `${day.getFullYear()}年${day.getMonth() + 1}月${day.getDate()}日（${WEEKDAYS[day.getDay()]}）`;
}

export function dateKey(iso) {
  const day = new Date(iso);
  return `${day.getFullYear()}-${day.getMonth() + 1}-${day.getDate()}`;
}

export function timeLabel(iso) {
  const day = new Date(iso);
  return `${day.getHours()}:${String(day.getMinutes()).padStart(2, "0")}`;
}

/*
  AI を待つあいだの印。薄く色を敷いた枠に、くるくる回る輪（藍と陽だまり）、待つ文、待ち始めてからの時間を並べる。
  1分ほど待つ画面で、止まっていないことが分かるようにするため。
  あとで node.textContent などで中身を書き換えると、枠も輪も時計もすぐ消える。node を隠したときも時計は止まる。
  スタイルはここで一度だけ入れる。style.css を読まない別のページ（生活まとめプラン、手紙など）でも同じに出すため。
  色はそのページの決まり（--accent か --ai、--sun）を使い、ないときは同じ色を直に使う。
  時間は読み上げない。毎秒読み上げると、待つ文が聞き取れないため。
*/
const WAIT_STYLE = `
.ai-waiting {
  display: flex;
  align-items: center;
  gap: 0.6em;
  box-sizing: border-box;
  width: fit-content;
  max-width: 100%;
  padding: 0.5em 1em 0.5em 0.7em;
  border: 1px solid color-mix(in srgb, var(--accent, var(--ai, #24477a)) 24%, transparent);
  border-radius: 14px;
  background: color-mix(in srgb, var(--accent, var(--ai, #24477a)) 9%, transparent);
}
.ai-waiting[hidden] {
  display: none !important;
}
.ai-wait-ring {
  flex: none;
  box-sizing: border-box;
  width: 1.5em;
  height: 1.5em;
  border: 0.22em solid color-mix(in srgb, currentColor 16%, transparent);
  border-top-color: var(--accent, var(--ai, #24477a));
  border-right-color: var(--sun, #f0b73a);
  border-radius: 50%;
  animation: ai-wait-spin 1s linear infinite;
}
.ai-wait-text {
  min-width: 0;
}
.ai-wait-clock {
  flex: none;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  opacity: 0.85;
}
.ai-wait-clock:empty {
  display: none;
}
@keyframes ai-wait-spin {
  to { transform: rotate(360deg); }
}
/* 動きを減らす設定では、ゆっくり回す。待っていることは時間でも分かる */
@media (prefers-reduced-motion: reduce) {
  .ai-wait-ring { animation-duration: 2.4s; }
}
`;

/* 時間を「経過 12秒」「経過 1分5秒」の形にする */
function elapsedWords(ms) {
  const seconds = Math.floor(ms / 1000);
  return seconds < 60 ? `経過 ${seconds}秒` : `経過 ${Math.floor(seconds / 60)}分${seconds % 60}秒`;
}

export function startWaiting(node, text) {
  if (!document.getElementById("ai-wait-style")) {
    const style = document.createElement("style");
    style.id = "ai-wait-style";
    style.textContent = WAIT_STYLE;
    document.head.append(style);
  }
  node.__waitFinish?.();
  const started = performance.now();
  const ring = el("span", { class: "ai-wait-ring", "aria-hidden": "true" });
  const clock = el("span", { class: "ai-wait-clock", "aria-hidden": "true" });
  node.replaceChildren(ring, el("span", { class: "ai-wait-text", text }), clock);
  node.classList.add("ai-waiting");

  let timer = 0;
  // 中身が書き換えられたら（答えや失敗の文に替わったら）、すぐ枠を外して時計を止める
  const watcher = new MutationObserver(() => {
    if (ring.parentNode !== node) {
      finish();
    }
  });
  function finish() {
    clearInterval(timer);
    watcher.disconnect();
    node.classList.remove("ai-waiting");
    node.__waitFinish = null;
  }
  watcher.observe(node, { childList: true });
  timer = setInterval(() => {
    if (!node.isConnected || node.hidden) {
      finish();
      return;
    }
    clock.textContent = elapsedWords(performance.now() - started);
  }, 1000);
  node.__waitFinish = finish;
}

/* 待つ印の付いた、新しい1行（role="status"）を作る */
export function waitingLine(text, className = "hint") {
  const line = el("p", { class: className, role: "status" });
  startWaiting(line, text);
  return line;
}

/* 下から出る小さな画面。閉じる関数を渡した中身の組み立て役に渡す。 */
export function openSheet(build) {
  const sheet = el("div", { class: "sheet", role: "dialog", "aria-modal": "true" });
  const inner = el("div", { class: "sheet-inner" });
  const onKey = (event) => {
    if (event.key === "Escape") {
      close();
    }
  };
  const close = () => {
    document.removeEventListener("keydown", onKey);
    sheet.remove();
  };
  document.addEventListener("keydown", onKey);
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) {
      close();
    }
  });
  sheet.append(inner);
  document.body.append(sheet);
  build(inner, close);
  return close;
}

/* タブの絵。言葉と一緒に出す。 */
export function tabIcon(name) {
  const line = { fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" };
  const paths = {
    tools: "M4 8h16v11H4zM9 8V5h6v3M4 13h16",
    home: "M3 11 12 3l9 8v10h-6v-6H9v6H3z",
    talk: "M4 5h16v11H9l-5 4z",
    timeline: "M5 4h14v16H5zM8 9h8M8 13h8M8 17h5",
    records: "M6 3h12v18l-6-4-6 4z",
    advocate: "M4 5h16v11H9l-5 4zM8 9h8M8 12h5",
    prorecords: "M6 3h12v18H6zM9 8h6M9 12h6M9 16h4",
  };
  return svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: paths[name], ...line }));
}
