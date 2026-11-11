/*
  役割：ボタンにマウスを置いたとき（キーボードで移ったとき）に、そのボタンの働きを短く伝える吹き出し。
  4つの極：特定の極に属さない土台。
  守る原則：迷わせない。押す前に、押すと何が起きるか（どこに出るか、だれに届くか）が分かるようにする。

  吹き出しの言葉は、ボタンを作るところで el() の tip に書く（data-tip になる。static/ui.js）。
  言葉がボタンのすぐ隣にあるので、ボタンを直すときに吹き出しも一緒に直せる。
  吹き出しは画面に1つだけで、出すたびにボタンの位置と大きさを測る。ボタンの形や置き場所、色を変えても、
  吹き出しはそのまま付いてくる。
  見た目（下の TIP_STYLE）もこのファイルに持つ。別のタブで開くページ（手紙、アセスメントの入力など）は
  style.css を読まないため。色は style.css の色の名前（--shu、--card、--ink）を使い、名前がないページでは
  同じ色をじかに使う。色を変えたときやダークの表示でも一緒に変わる。

  - 指のタップ（スマホ）では出さない。押すのを邪魔しないため。マウスとキーボードだけで出す。
  - 吹き出しは「ポップオーバー」として画面のいちばん上の層に出す。手前に開いた窓（<dialog>）の上にも出る。
    部品の枠（はみ出しを隠す箱、container の箱）に切られない。
  - 吹き出しはマウスを受けない。重なったボタンも、そのまま押せる。
  - 言葉のないボタンにマウスを置くと、開発用の画面（コンソール）に1回だけ知らせる。window.ntTips.missing() で、
    見えているボタンのうち言葉のないものを一覧にできる（画面の確かめに使う）。
*/

// 最初に出すまでの間。マウスが通りすぎただけで出さない
const SHOW_DELAY_MS = 400;
// 吹き出しを閉じてすぐ隣のボタンへ移ったときは、待たずに出す
const WARM_MS = 700;
// ボタンと吹き出しのすき間、画面の端からの余白
const GAP = 10;
const EDGE = 8;
// 吹き出しを出す相手。data-tip のあるものと、ボタンの働きをするもの（言葉がなければ知らせるため）
const TARGET = "[data-tip], button, [role='button'], [role='tab'], [role='option'], summary";
// 置く向きの順。data-tip-side で最初の向きを変えられる（左の縦の列は right）
const SIDES = ["top", "bottom", "right", "left"];

/* 吹き出しの見た目。薄い赤の面に朱の縁で、矢印がボタンの真ん中を指す（目立つように薄い赤に。
   tyumajinさんの依頼、2026-11-07）。面は朱とカードの色を混ぜて作るので、ダークの表示では赤みのある紺になる。
   吹き出しはマウスを受けないので、重なったボタンも押せる。ページの CSS より先に置き、ページの側で上書きできる。 */
const TIP_STYLE = `
.nt-tip {
  --tip-bg: color-mix(in srgb, var(--shu, #c6401f) 18%, var(--card, #ffffff));
  --tip-line: var(--shu, #c6401f);
  position: fixed;
  /* ポップオーバーの決まり（画面の真ん中に置く、枠と余白）を消し、測った位置に置く */
  inset: auto;
  top: 0;
  left: 0;
  margin: 0;
  z-index: 1000;
  display: none;
  box-sizing: border-box;
  width: max-content;
  max-width: min(300px, calc(100vw - 16px));
  padding: 8px 13px 9px;
  overflow: visible;
  border: 1.5px solid var(--tip-line);
  border-radius: 12px;
  background: var(--tip-bg);
  color: var(--ink, #22304a);
  font: 500 15px/1.6 var(--plain, "Hiragino Sans", "Yu Gothic UI", "Meiryo", sans-serif);
  letter-spacing: 0.02em;
  text-align: left;
  /* 日本語を文節で折り返す（対応するブラウザだけ） */
  word-break: auto-phrase;
  overflow-wrap: anywhere;
  box-shadow: var(--shadow-lift, 0 2px 4px rgba(34, 48, 74, 0.08), 0 12px 28px rgba(34, 48, 74, 0.16));
  pointer-events: none;
  opacity: 0;
  translate: 0 3px;
  transition: opacity 140ms ease, translate 140ms ease;
}
.nt-tip.open { display: block; opacity: 1; translate: 0 0; }
/* 開いた瞬間は透明から始める（ふわっと出す） */
@starting-style { .nt-tip.open { opacity: 0; translate: 0 3px; } }
.nt-tip[data-side="bottom"] { translate: 0 0; }
/* 矢印。縁と同じ線の小さな四角を45度回し、吹き出しの辺から半分だけ出す */
.nt-tip::after {
  content: "";
  position: absolute;
  width: 10px;
  height: 10px;
  box-sizing: border-box;
  background: var(--tip-bg);
  border: 1.5px solid var(--tip-line);
  rotate: 45deg;
}
.nt-tip[data-side="top"]::after { left: calc(var(--tip-x, 50%) - 5px); bottom: -6px; border-top-color: transparent; border-left-color: transparent; }
.nt-tip[data-side="bottom"]::after { left: calc(var(--tip-x, 50%) - 5px); top: -6px; border-bottom-color: transparent; border-right-color: transparent; }
.nt-tip[data-side="right"]::after { top: calc(var(--tip-y, 50%) - 5px); left: -6px; border-top-color: transparent; border-right-color: transparent; }
.nt-tip[data-side="left"]::after { top: calc(var(--tip-y, 50%) - 5px); right: -6px; border-bottom-color: transparent; border-left-color: transparent; }
@media (prefers-reduced-motion: reduce) { .nt-tip { transition: none; } }
@media print { .nt-tip { display: none !important; } }
`;

let bubble = null;
let words = null;
let current = null;
let pending = null;
let showTimer = 0;
let hideTimer = 0;
let lastHidden = 0;
let follow = 0;
let restoreDescribedBy = null;
// 押した直後のボタンと、押した位置。マウスがそこを離れるまで吹き出しを出さない。
// 押すと画面が描き直され、同じ位置に新しいボタンができることがあるので（タブなど）、位置でも見る
let quiet = null;
const QUIET_RADIUS = 12;
// 最後に指やマウスで押した時刻。その直後のフォーカスは、キーボードで移ったものとみなさない
let lastPress = 0;
const PRESS_FOCUS_MS = 1000;
// 最後に指で触った時刻。指で押して画面が描き直されると、ブラウザがその場所に「マウスが来た」という
// 見せかけの出来事を出すので、触った直後のマウスの出来事は使わない
let lastTouch = 0;
const TOUCH_MOUSE_MS = 2000;
// マウスのない機器（スマホなど）では、マウスを置いたときの吹き出しは出さない（キーボードは出す）
const NO_HOVER = window.matchMedia?.("(hover: none)");
const warned = new Set();

/* ボタンの吹き出しの言葉。title で書かれていたものは吹き出しへ移す（ブラウザの小さな吹き出しと二重にしない）。
   data-tip="" は「吹き出しを出さない」の印。 */
function tipOf(node) {
  if (node.hasAttribute("data-tip")) {
    return node.getAttribute("data-tip").trim();
  }
  const title = node.getAttribute("title");
  if (title && node.tagName !== "IFRAME") {
    node.setAttribute("data-tip", title);
    node.removeAttribute("title");
    return title.trim();
  }
  return null;
}

function labelOf(node) {
  return (node.getAttribute("aria-label") || node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40);
}

function warnMissing(node) {
  const label = labelOf(node) || node.className || node.tagName;
  if (!warned.has(label)) {
    warned.add(label);
    console.warn("[吹き出し] 言葉のないボタンです。作るところに tip を書いてください：", label, node);
  }
}

function makeBubble() {
  if (!document.getElementById("nt-tip-style")) {
    const sheet = document.createElement("style");
    sheet.id = "nt-tip-style";
    sheet.textContent = TIP_STYLE;
    document.head.prepend(sheet);
  }
  bubble = document.createElement("div");
  bubble.className = "nt-tip";
  bubble.id = "nt-tip";
  bubble.setAttribute("role", "tooltip");
  bubble.lang = "ja";
  words = document.createElement("span");
  words.className = "nt-tip-words";
  bubble.append(words);
  if (typeof bubble.showPopover === "function") {
    bubble.popover = "manual";
  }
  document.body.append(bubble);
}

function isOpen() {
  return bubble.classList.contains("open");
}

/* 吹き出しを開く。開いている窓（<dialog>）の中のボタンなら、窓の中に置く（窓の外は押せない状態になるため）。 */
function open() {
  const host = current.closest("dialog[open]") || document.body;
  if (bubble.parentNode !== host) {
    host.append(bubble);
  }
  if (bubble.popover) {
    // いちばん新しく開いたものが上に来るので、開き直して窓より上にする
    if (bubble.matches(":popover-open")) {
      bubble.hidePopover();
    }
    bubble.showPopover();
  }
  bubble.classList.add("open");
}

/* ボタンのまわりで、吹き出しが画面に収まる向きを選んで置く。矢印はボタンの真ん中を指す。 */
function place() {
  if (!current || !isOpen()) {
    return;
  }
  const target = current.getBoundingClientRect();
  if (!current.isConnected || (target.width === 0 && target.height === 0)) {
    hide();
    return;
  }
  const width = document.documentElement.clientWidth;
  const height = window.innerHeight;
  const box = bubble.getBoundingClientRect();
  const room = {
    top: target.top - GAP - EDGE,
    bottom: height - target.bottom - GAP - EDGE,
    right: width - target.right - GAP - EDGE,
    left: target.left - GAP - EDGE,
  };
  const need = { top: box.height, bottom: box.height, right: box.width, left: box.width };
  const first = current.getAttribute("data-tip-side");
  const order = SIDES.includes(first) ? [first, ...SIDES.filter((side) => side !== first)] : SIDES;
  const side = order.find((name) => room[name] >= need[name]) || (room.top > room.bottom ? "top" : "bottom");

  let left;
  let top;
  if (side === "top" || side === "bottom") {
    left = target.left + target.width / 2 - box.width / 2;
    top = side === "top" ? target.top - GAP - box.height : target.bottom + GAP;
  } else {
    top = target.top + target.height / 2 - box.height / 2;
    left = side === "right" ? target.right + GAP : target.left - GAP - box.width;
  }
  left = Math.min(Math.max(left, EDGE), Math.max(EDGE, width - EDGE - box.width));
  top = Math.min(Math.max(top, EDGE), Math.max(EDGE, height - EDGE - box.height));
  // 矢印の位置。吹き出しの角の丸みにかからないよう、端から少し内側に収める
  const arrowX = Math.min(Math.max(target.left + target.width / 2 - left, 16), box.width - 16);
  const arrowY = Math.min(Math.max(target.top + target.height / 2 - top, 14), box.height - 14);
  bubble.dataset.side = side;
  bubble.style.left = `${Math.round(left)}px`;
  bubble.style.top = `${Math.round(top)}px`;
  bubble.style.setProperty("--tip-x", `${Math.round(arrowX)}px`);
  bubble.style.setProperty("--tip-y", `${Math.round(arrowY)}px`);
}

/* 出ている間は、毎回の描き替えでボタンの位置を測り直す。ボタンが動いたり（花びらのボタンの動きなど）、
   消えたりしても、吹き出しが置いていかれないため。言葉が変わったとき（2回押すボタンなど）も追いかける。 */
function track() {
  if (!current) {
    return;
  }
  const latest = tipOf(current);
  if (!latest) {
    hide();
    return;
  }
  if (latest !== words.textContent) {
    words.textContent = latest;
  }
  place();
  follow = requestAnimationFrame(track);
}

function show(target) {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  pending = null;
  const text = tipOf(target);
  if (text === null) {
    warnMissing(target);
    return;
  }
  if (!text) {
    return;
  }
  if (current && current !== target) {
    unlink();
  }
  current = target;
  words.textContent = text;
  open();
  place();
  // 読み上げソフトに、ボタンの説明として伝える
  restoreDescribedBy = target.getAttribute("aria-describedby");
  const ids = new Set((restoreDescribedBy || "").split(/\s+/).filter(Boolean));
  ids.add(bubble.id);
  target.setAttribute("aria-describedby", [...ids].join(" "));
  cancelAnimationFrame(follow);
  follow = requestAnimationFrame(track);
}

function unlink() {
  if (!current) {
    return;
  }
  if (restoreDescribedBy) {
    current.setAttribute("aria-describedby", restoreDescribedBy);
  } else {
    current.removeAttribute("aria-describedby");
  }
  restoreDescribedBy = null;
}

function hide() {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  pending = null;
  cancelAnimationFrame(follow);
  if (!current) {
    return;
  }
  unlink();
  current = null;
  bubble.classList.remove("open");
  if (bubble.popover && bubble.matches(":popover-open")) {
    bubble.hidePopover();
  }
  lastHidden = Date.now();
}

function schedule(target, delay) {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  pending = target;
  if (delay <= 0) {
    show(target);
    return;
  }
  showTimer = setTimeout(() => show(target), delay);
}

function targetOf(node) {
  return node instanceof Element ? node.closest(TARGET) : null;
}

function onPointerDown(event) {
  hide();
  lastPress = Date.now();
  if (event.pointerType !== "mouse") {
    lastTouch = lastPress;
  }
  quiet = { target: targetOf(event.target), x: event.clientX, y: event.clientY };
}

/* 押した直後のボタン（か、描き直されて同じ位置にできたボタン）の上なら、まだ出さない */
function stillQuiet(target, event) {
  if (!quiet) {
    return false;
  }
  const near = Math.hypot(event.clientX - quiet.x, event.clientY - quiet.y) < QUIET_RADIUS;
  if (target === quiet.target || (near && !quiet.target?.isConnected)) {
    return true;
  }
  quiet = null;
  return false;
}

function onPointerOver(event) {
  if (event.pointerType !== "mouse" || NO_HOVER?.matches || Date.now() - lastTouch < TOUCH_MOUSE_MS) {
    return;
  }
  const target = targetOf(event.target);
  if (!target || stillQuiet(target, event)) {
    return;
  }
  if (target === current) {
    clearTimeout(hideTimer);
    return;
  }
  if (target === pending) {
    return;
  }
  const warm = current || Date.now() - lastHidden < WARM_MS;
  schedule(target, warm ? 0 : SHOW_DELAY_MS);
}

function onPointerOut(event) {
  if (event.pointerType !== "mouse") {
    return;
  }
  const leaving = targetOf(event.target);
  const next = event.relatedTarget;
  // ボタンの中の文字やアイコンの間を動いただけなら、閉じない
  if (leaving && next instanceof Node && leaving.contains(next)) {
    return;
  }
  // 押したボタン（か、描き直されたボタン）から離れたら、また出せるようにする
  if (quiet && leaving && (leaving === quiet.target || !quiet.target?.isConnected)) {
    quiet = null;
  }
  if (leaving && leaving === pending) {
    clearTimeout(showTimer);
    pending = null;
  }
  if (leaving && leaving === current) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 80);
  }
}

function onFocusIn(event) {
  const target = targetOf(event.target);
  if (!target || target !== event.target) {
    return;
  }
  // キーボードで移ったときだけ出す。マウスや指で押したあとのフォーカスでは出さない
  // （スマホのタップでも :focus-visible になることがあるので、押した時刻でも見る）
  if (Date.now() - lastPress < PRESS_FOCUS_MS) {
    return;
  }
  let byKeyboard = false;
  try {
    byKeyboard = target.matches(":focus-visible");
  } catch {
    byKeyboard = false;
  }
  if (byKeyboard) {
    schedule(target, 120);
  }
}

function onFocusOut(event) {
  const target = targetOf(event.target);
  if (target && (target === current || target === pending)) {
    hide();
  }
}

function onKeyDown(event) {
  // Esc で閉じる（窓を閉じるなど、ほかの Esc の働きは止めない）。押すキーでも閉じる
  if (current && (event.key === "Escape" || event.key === "Enter" || event.key === " ")) {
    hide();
  }
}

/* 見えているボタンのうち、吹き出しの言葉がないもの。画面の確かめ（自動の点検）から呼ぶ。 */
function missing(root = document) {
  return [...root.querySelectorAll("button, [role='button'], [role='tab'], [role='option'], summary")]
    .filter((node) => node.getClientRects().length > 0 && !node.closest("[hidden], [inert]"))
    .filter((node) => !(node.getAttribute("data-tip") || node.getAttribute("title") || "").trim() && node.getAttribute("data-tip") !== "")
    .map((node) => labelOf(node) || node.className || node.tagName);
}

/* 吹き出しを使えるようにする。どのページでも1回だけ動く（2回呼んでも同じ）。 */
export function installTips() {
  if (bubble || !document.body) {
    return;
  }
  makeBubble();
  document.addEventListener("pointerover", onPointerOver, true);
  document.addEventListener("pointerout", onPointerOut, true);
  document.addEventListener("focusin", onFocusIn, true);
  document.addEventListener("focusout", onFocusOut, true);
  document.addEventListener("pointerdown", onPointerDown, true);
  document.addEventListener("touchstart", () => (lastPress = lastTouch = Date.now()), { capture: true, passive: true });
  document.addEventListener("keydown", onKeyDown, true);
  // 画面のどこかが動いたら閉じる（.view の中の動きも拾うため、取り込みの段階で受け取る）
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
  window.addEventListener("blur", hide);
  window.ntTips = { missing, hide };
}
