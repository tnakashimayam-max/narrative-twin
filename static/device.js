/*
  役割：PC版とスマホ版の切り替え。今の形を body の data-device に置き、style.css と talk.js が読む。
  4つの極：特定の極に属さない土台。
  守る原則：8（どちらの形でも画面の隅の表示を消さない）。

  アプリは1組で、並べ方だけを変える。機能が片方だけ古くならないようにするため。
  はじめは画面の幅で決める。ボタンを押したらそれを覚え、押す前はウィンドウの幅についていく。
  広い画面でスマホ版を選ぶと幅390pxで中央に置くので、PC 1台で両方を確かめられる（style.css）。
  幅の狭い機器で PC版を選ぶと、viewport を広げて全体を縮めて見せる。スマホの「PC版サイトを見る」と同じ見え方になる。
*/

import { el } from "./ui.js";

const STORE_KEY = "nt_device";
// style.css の @media の境目と同じ
const PC_MIN_WIDTH = 900;
const THREE_PANES_WIDTH = 1100;
const WIDE_VIEWPORT = "width=1280";
const NORMAL_VIEWPORT = "width=device-width, initial-scale=1";

const wideWindow = window.matchMedia(`(min-width: ${PC_MIN_WIDTH}px)`);
let device = "phone";
let chosen = false;
const buttons = new Map();

function readChoice() {
  try {
    const value = localStorage.getItem(STORE_KEY);
    return value === "pc" || value === "phone" ? value : null;
  } catch {
    return null;
  }
}

function saveChoice(value) {
  try {
    localStorage.setItem(STORE_KEY, value);
  } catch {
    // 覚えられなくても、今の切り替えは動く
  }
}

function isSmallMachine() {
  return Math.min(screen.width, screen.height) < PC_MIN_WIDTH;
}

function apply() {
  document.body.dataset.device = device;
  // PC版の最小幅に足りない機器では全体を縮める。縦向きのタブレットで右が切れないようにするため
  document.querySelector('meta[name="viewport"]').content = device === "pc" && isSmallMachine() ? WIDE_VIEWPORT : NORMAL_VIEWPORT;
  for (const [value, button] of buttons) {
    button.setAttribute("aria-pressed", String(value === device));
  }
}

export function isPc() {
  return device === "pc";
}

/* PC版で、右のペインを開いて始める幅があるか。境目は style.css の 1099px と同じ。
   全体を縮める機器は幅1280で描くので、幅があるとする。切り替えた直後は viewport が広がる途中で、幅を読めないことがあるため。 */
export function hasRoomForThreePanes() {
  return isSmallMachine() || window.matchMedia(`(min-width: ${THREE_PANES_WIDTH}px)`).matches;
}

/* 今の形を決めて、切り替えのボタンを返す。onChange は形が変わったときに呼ぶ。 */
export function startDevice(onChange) {
  const saved = readChoice();
  chosen = saved !== null;
  device = saved ?? (wideWindow.matches ? "pc" : "phone");

  function change(next) {
    if (next !== device) {
      device = next;
      apply();
      onChange();
    }
  }

  wideWindow.addEventListener("change", () => {
    if (!chosen) {
      change(wideWindow.matches ? "pc" : "phone");
    }
  });

  const group = el(
    "div",
    { class: "device-switch", role: "group", "aria-label": "画面の形を切り替える" },
    [
      ["pc", "PC", "パソコン向けの、横に広い並べ方で表示します"],
      ["phone", "スマホ", "スマホ向けの、縦に並ぶ表示に切り替えます"],
    ].map(([value, label, tip]) => {
      const button = el("button", {
        type: "button",
        text: label,
        tip,
        onclick: () => {
          chosen = true;
          saveChoice(value);
          change(value);
        },
      });
      buttons.set(value, button);
      return button;
    })
  );
  apply();
  return group;
}
