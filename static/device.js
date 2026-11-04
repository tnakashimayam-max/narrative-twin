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
const SMALL_MACHINE = 600;
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

function apply() {
  document.body.dataset.device = device;
  const smallMachine = Math.min(screen.width, screen.height) < SMALL_MACHINE;
  document.querySelector('meta[name="viewport"]').content = device === "pc" && smallMachine ? WIDE_VIEWPORT : NORMAL_VIEWPORT;
  for (const [value, button] of buttons) {
    button.setAttribute("aria-pressed", String(value === device));
  }
}

export function isPc() {
  return device === "pc";
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
      ["pc", "PC"],
      ["phone", "スマホ"],
    ].map(([value, label]) => {
      const button = el("button", {
        type: "button",
        text: label,
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
