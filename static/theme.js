/*
  役割：明るい表示と暗い表示の切り替え。選んだ方を html の data-theme に置き、style.css が読む。
  4つの極：特定の極に属さない土台。
  守る原則：8（どちらの表示でも「架空データ」の札を消さない）。

  ボタンを押す前は、端末の設定（明るい・暗い）に合わせる。押したらそれをこのブラウザに覚える。
  ページを開いた直後に色が入れ替わって見えないよう、覚えた表示は index.html の頭で先に置く。
  専門職と評価の画面は白を基調のまま変えないので（style.css の data-look="modern"）、そこではボタンを出さない。
*/

import { el, svg } from "./ui.js";

const STORE_KEY = "nt_theme";
const darkDevice = window.matchMedia("(prefers-color-scheme: dark)");
const ICON_MOON = "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z";
const ICON_SUN = "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 1v3M12 20v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M1 12h3M20 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1";

function readChoice() {
  try {
    const value = localStorage.getItem(STORE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function saveChoice(value) {
  try {
    localStorage.setItem(STORE_KEY, value);
  } catch {
    // 覚えられないブラウザでも、開いている間は切り替わる
  }
}

/* 今の表示。選んでいなければ端末の設定 */
export function currentTheme() {
  return document.documentElement.dataset.theme || (darkDevice.matches ? "dark" : "light");
}

/* 表示が変わったことを知らせる。ロゴなど、CSS だけでは変えられない所が受け取る */
function announce() {
  window.dispatchEvent(new CustomEvent("nt-theme", { detail: currentTheme() }));
}

function icon(path) {
  return svg(
    "svg",
    { viewBox: "0 0 24 24", width: "20", height: "20", "aria-hidden": "true", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" },
    svg("path", { d: path })
  );
}

/* 切り替えのボタンを返す。押すたびに明るい・暗いが入れ替わる */
export function startTheme() {
  const saved = readChoice();
  if (saved) {
    document.documentElement.dataset.theme = saved;
  }
  const label = el("span", { class: "theme-switch-text" });
  const button = el("button", { type: "button", class: "theme-switch" });

  function draw() {
    const dark = currentTheme() === "dark";
    // 押したらどうなるかを書く。今の状態を書くと、押すと逆になるのか迷うため
    const words = dark ? "明るくする" : "暗くする";
    label.textContent = words;
    button.replaceChildren(icon(dark ? ICON_SUN : ICON_MOON), label);
    button.setAttribute("aria-label", dark ? "明るい表示にする" : "暗い表示にする");
    // 吹き出し（static/tips.js）。ブラウザの小さな説明（title）と二重に出さない
    button.dataset.tip = dark ? "画面を明るい色の表示に戻します" : "画面を暗い色にして、まぶしさを抑えます";
  }

  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    saveChoice(next);
    draw();
    announce();
  });
  darkDevice.addEventListener("change", () => {
    if (!readChoice()) {
      draw();
      announce();
    }
  });
  draw();
  return button;
}
