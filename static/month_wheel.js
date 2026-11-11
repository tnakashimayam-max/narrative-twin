/*
  役割：年と月を回して選ぶホイール。記録（static/records.js）で、見たい月の一覧を出すのに使う。
  4つの極：1. 書く・話す（長い記録の中から、目当ての月をすぐ開けるようにする）。
  守る原則：6、7（記録のある月は小さな点で示すだけで、数は出さない）。

  マウスのホイールは、1目盛りで1つずつ動かす。指やつまみで動かしたときは、止まった所の真ん中を選ぶ。
  キーボードでは、上下の矢印、Page Up と Page Down、Home と End で動かす。
  選んだ値は、回し終わってから少し待って知らせる。回している途中の月を、いちいち読み込まないため。
*/

import { el } from "./ui.js";

const PICK_WAIT_MS = 350; // 回し終わってから、選んだ月を知らせるまで
const SETTLE_MS = 120; // 指で動かしたあと、止まったとみなすまで
const WHEEL_STEP = 50; // タッチパッドの細かい動きを、これだけためてから1つ動かす
let serial = 0;

function reduced() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/* 縦に回す1本の列。values の中から1つを選ぶ */
function wheelColumn({ label, values, format, onChange }) {
  const id = `wheel-${++serial}`;
  const list = el("div", { class: "wheel-list", role: "listbox", tabindex: "0", "aria-label": label });
  const options = values.map((value, index) =>
    el("div", {
      class: "wheel-option",
      role: "option",
      id: `${id}-${index}`,
      "aria-selected": "false",
      text: format(value),
      // ボタンではないが押せる行なので、吹き出しを付ける（static/tips.js）
      tip: `${format(value)}を選んで、その記録を下の一覧に出します`,
      onclick: () => select(index, { scroll: true }),
    })
  );
  list.append(...options);
  let current = -1;
  let wheelSum = 0;
  let settle = 0;

  // 1行の高さは CSS（--wheel-row）で決める。ここでは測って使う
  const row = () => options[0]?.offsetHeight || 44;

  function select(index, { scroll = false, jump = false, silent = false } = {}) {
    const next = Math.max(0, Math.min(values.length - 1, index));
    if (jump) {
      list.scrollTop = next * row();
    } else if (scroll) {
      list.scrollTo({ top: next * row(), behavior: reduced() ? "auto" : "smooth" });
    }
    if (next === current) {
      return;
    }
    options[current]?.setAttribute("aria-selected", "false");
    current = next;
    options[next].setAttribute("aria-selected", "true");
    list.setAttribute("aria-activedescendant", options[next].id);
    if (!silent) {
      onChange(values[next]);
    }
  }

  list.addEventListener(
    "wheel",
    (event) => {
      // 一覧のほうが動かないよう、ホイールはこの列で受け止める
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 400 : 1);
      if (Math.abs(delta) >= WHEEL_STEP) {
        // マウスの1目盛り。大きさによらず1つだけ動かす
        wheelSum = 0;
        select(current + Math.sign(delta), { scroll: true });
        return;
      }
      wheelSum += delta;
      if (Math.abs(wheelSum) >= WHEEL_STEP) {
        select(current + Math.sign(wheelSum), { scroll: true });
        wheelSum = 0;
      }
    },
    { passive: false }
  );

  // 指やつまみで動かしたときは、止まった所の真ん中の値を選ぶ。止める位置は CSS の scroll-snap が合わせる
  list.addEventListener("scroll", () => {
    clearTimeout(settle);
    settle = setTimeout(() => select(Math.round(list.scrollTop / row())), SETTLE_MS);
  });

  list.addEventListener("keydown", (event) => {
    const moves = { ArrowUp: -1, ArrowDown: 1, PageUp: -3, PageDown: 3 };
    let next;
    if (event.key in moves) {
      next = current + moves[event.key];
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = values.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    select(next, { scroll: true });
  });

  const node = el("div", { class: "wheel" }, el("div", { class: "wheel-window" }, list));
  return { node, options, select, index: () => current };
}

/*
  年と月のホイールを作る。
  years は古い順の年の数。hasRecord(年, 月) が真の月と年に点を付ける。
  onPick(年, 月) は、回し終わって止まったときに呼ぶ。set(年, 月) は知らせずに合わせる。
*/
export function monthWheel({ years, hasRecord, onPick, labelledBy }) {
  let year = years.at(-1);
  let month = 1;
  let timer = 0;

  function picked() {
    clearTimeout(timer);
    timer = setTimeout(() => onPick(year, month), PICK_WAIT_MS);
  }

  const yearColumn = wheelColumn({
    label: "年",
    values: years,
    format: (value) => `${value}年`,
    onChange: (value) => {
      year = value;
      markMonths();
      picked();
    },
  });
  const monthColumn = wheelColumn({
    label: "月",
    values: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    format: (value) => `${value}月`,
    onChange: (value) => {
      month = value;
      picked();
    },
  });

  /* 記録のある年と月に点を付ける。読み上げでは「記録あり」と伝える */
  function mark(option, on, text) {
    option.classList.toggle("has-record", on);
    option.setAttribute("aria-label", on ? `${text}、記録あり` : text);
  }

  function markMonths() {
    monthColumn.options.forEach((option, index) => mark(option, hasRecord(year, index + 1), `${index + 1}月`));
  }

  yearColumn.options.forEach((option, index) => {
    const value = years[index];
    const any = Array.from({ length: 12 }, (_, at) => hasRecord(value, at + 1)).some(Boolean);
    mark(option, any, `${value}年`);
  });

  const node = el("div", { class: "month-wheel", role: "group", "aria-labelledby": labelledBy }, yearColumn.node, monthColumn.node);
  return {
    node,
    /* 画面に出してから呼ぶ。隠れている間は位置を測れないため */
    set(nextYear, nextMonth) {
      clearTimeout(timer);
      year = nextYear;
      month = nextMonth;
      yearColumn.select(years.indexOf(nextYear), { jump: true, silent: true });
      monthColumn.select(nextMonth - 1, { jump: true, silent: true });
      markMonths();
    },
    dispose() {
      clearTimeout(timer);
    },
  };
}
