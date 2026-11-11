/*
  役割：これまでの記録。本人が、日付ごとに会話、写真、便りを読み返す。
  4つの極：1. 書く・話す。
  守る原則：5（元の記録を消さない）、6、7（記録のある月は点で示すだけで、数は出さない）、
            10（本人だけが開ける。サーバーでも確かめる）。

  PC版は左に日付、右に開いた日の中身を並べ、スマホ版は日付の下に開く。部品は同じで、中身を置く場所だけが違う。
  上の「探す」で、日付を入れて開くか、年と月のホイール（static/month_wheel.js）を回して、その月の一覧を出す。
  月ごとの読み込みは GET /api/archive/months と GET /api/archive?month=（docs/api.md）。
  サーバーがまだ月ごとの読み込みに答えないときは、前と同じく新しい順と「もっと前を見る」で並べる。
*/

import { api } from "./api.js";
import { bubble, personName } from "./bubbles.js";
import { isPc } from "./device.js";
import { monthWheel } from "./month_wheel.js";
import { dateLabel, el, photoImage } from "./ui.js";

const pad = (value) => String(value).padStart(2, "0");
// "2036-05" を「2036年5月」にする
const monthLabel = (key) => `${Number(key.slice(0, 4))}年${Number(key.slice(5, 7))}月`;
const dayLabel = (date) => dateLabel(`${date}T12:00:00`);

/* その月の最後の日（"2036-05" → "2036-05-31"） */
function lastDayOf(key) {
  const [year, month] = key.split("-").map(Number);
  return `${key}-${pad(new Date(year, month, 0).getDate())}`;
}

export function renderRecords(container, ctx) {
  const reader = isPc() ? el("section", { class: "records-reader", "aria-label": "開いた日の記録" }) : null;
  let alive = true;
  let nextBefore = null;
  const list = el("div", { class: "records-list" });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const more = el("button", { type: "button", class: "wide", text: "もっと前を見る", tip: "前の記録を、この一覧の下に足して並べます", hidden: true });
  const empty = el("p", { class: "hint", text: "まだ記録はありません。ツインと話すと、ここに日付ごとに残ります。", hidden: true });

  // 探す：日付で開く欄と、年と月のホイール。月ごとの読み込みができるときだけ出す
  const dateInput = el("input", { type: "date", id: "records-date", name: "date", required: true });
  const dateForm = el(
    "form",
    { class: "find-date" },
    el("label", { class: "find-label", for: "records-date", text: "日付で探す" }),
    el("div", { class: "find-date-controls" }, dateInput, el("button", { type: "submit", text: "開く", tip: "入れた日付の記録を開きます" }))
  );
  const wheelLabel = el("span", { class: "find-label", id: "records-wheel-label", text: "年と月で探す" });
  const finder = el("section", { class: "records-find", "aria-label": "日付と年月から探す", hidden: true }, dateForm, wheelLabel);
  // 探した結果の知らせ（記録のない日や月）と、近い日や月へのボタン
  const statusText = el("p", { class: "records-status-text", role: "status" });
  const statusActions = el("div", { class: "records-status-actions" });
  const status = el("div", { class: "records-status", hidden: true }, statusText, statusActions);

  container.append(
    el(
      "section",
      { class: "panel" },
      el("p", { class: "hint", text: "ツイン（AI）と話したことと便りを、日付ごとに残しています。日付を押すと開きます。" }),
      // 見える範囲の説明は消さない。長いので畳み、開けば全文を読めるようにする
      el(
        "details",
        { class: "fold" },
        el("summary", { text: "だれに見えるか", tip: "開くと、この記録がだれに見えるかの説明が出ます" }),
        el("p", { class: "hint", text: "この一覧を開けるのはご本人だけです。便りは選んだ相手に見えます。託す人と範囲で選んだテーマの話は、託した人と招いた専門職にも見えます。" })
      ),
      finder,
      empty,
      message,
      status,
      list,
      more
    )
  );
  if (reader) {
    container.className = "view records-panes";
    container.append(reader);
  }
  const people = new Map([[ctx.me.id, ctx.me]]);
  let lastYear = "";

  /* PC版で、右に何も開いていないときの案内 */
  function idleReader() {
    reader?.replaceChildren(el("p", { class: "hint reader-empty", text: "左の日付を押すと、ここに開きます。" }));
  }

  /* 日付だけを並べ、押すと開く。長い記録でも目当ての日を探せるようにするため。写真も開いたときに読む。 */
  function drawDay(day) {
    const body = el("div", {});
    // 話題の題は記録にないので、日付と、何があった日かの札だけを出す
    const kinds = [
      day.conversations.length ? "会話" : "",
      day.conversations.some((conversation) => conversation.items.some((item) => item.photo_id)) || day.letters.some((letter) => letter.photo_id) ? "写真" : "",
      day.letters.length ? "便り" : "",
    ].filter(Boolean);
    const section = el(
      "details",
      { class: "day-fold rail-item" },
      el("summary", { tip: reader ? "この日の会話や便りを、右に開きます" : "この日の会話や便りを、この下に開きます" }, el("span", { class: "rail-date", text: dayLabel(day.date) }), el("span", { class: "rail-kinds", text: kinds.join("・") })),
      reader ? null : body
    );
    section.dataset.date = day.date;
    let drawn = false;

    /* PC版では、開いた日の中身を右へ置く。右に出せるのは1日分なので、ほかの日は閉じる。 */
    function showInReader() {
      if (!reader) {
        return;
      }
      if (!section.open) {
        if (body.parentNode === reader) {
          idleReader();
        }
        return;
      }
      for (const other of list.querySelectorAll(".rail-item[open]")) {
        if (other !== section) {
          other.open = false;
        }
      }
      reader.replaceChildren(body);
      reader.scrollTop = 0;
    }

    section.addEventListener("toggle", () => {
      showInReader();
      if (!section.open || drawn) {
        return;
      }
      drawn = true;
      for (const conversation of day.conversations) {
        const box = el("div", {});
        for (const item of conversation.items) {
          box.append(bubble(item, { viewerId: ctx.me.id, people }));
        }
        body.append(box);
      }
      for (const letter of day.letters) {
        body.append(
          el(
            "article",
            { class: "letter-card" },
            letter.photo_id ? photoImage(letter.photo_id, "letter-photo", "便りの写真") : null,
            el("div", { class: "letter-body", text: letter.body }),
            el("p", { class: "hint letter-note", text: `ツイン（AI）の下書きを、${personName(ctx.me)}が確かめて共有した便りです。見せる相手：${letter.share_with}。` })
          )
        );
      }
    });
    return section;
  }

  function showError(error) {
    message.textContent = error.message;
    message.hidden = false;
  }

  /* 探した結果を知らせる。actions は [言葉, 押したときの動き, 吹き出しの言葉] の組 */
  function say(text, actions = []) {
    statusText.textContent = text;
    statusActions.replaceChildren(...actions.map(([label, onclick, tip]) => el("button", { type: "button", class: "quiet", text: label, tip, onclick })));
    status.hidden = !text;
  }

  // ---- 月ごとに並べる（サーバーが月ごとの読み込みに答えるとき） ----

  let monthMode = false;
  let months = []; // 記録のある月（"2036-05"）、新しい順
  let monthSet = new Set();
  let wheel = null;
  let shownTop = ""; // 一覧のいちばん上の月（ホイールで選んだ月）
  let shownBottom = ""; // 一覧のいちばん下の月（「…も見る」はここより前の月を足す）
  let ticket = 0; // 選び直したとき、古い読み込みの答えを捨てるための番号
  const monthCache = new Map();

  function monthDays(key) {
    if (!monthSet.has(key)) {
      return Promise.resolve([]);
    }
    if (!monthCache.has(key)) {
      const loading = api(`/archive?month=${key}`).then((data) => data.days);
      // 失敗した月は、次にもう一度読めるよう覚えない
      loading.catch(() => monthCache.delete(key));
      monthCache.set(key, loading);
    }
    return monthCache.get(key);
  }

  const olderThan = (key) => months.find((month) => month < key) || "";
  const newerThan = (key) => [...months].reverse().find((month) => month > key) || "";

  function drawMonth(key, days) {
    list.append(el("div", { class: "year-plate month-plate", text: monthLabel(key) }));
    for (const day of days) {
      list.append(drawDay(day));
    }
  }

  /* 一覧の下のボタンを、いちばん下の月より前の記録のある月にする */
  function setOlder() {
    const older = olderThan(shownBottom);
    more.hidden = !older;
    more.textContent = older ? `${monthLabel(older)}も見る` : "もっと前を見る";
  }

  /* ホイールと一覧を、その月へ動かす */
  function jumpTo(key, options = {}) {
    wheel?.set(Number(key.slice(0, 4)), Number(key.slice(5, 7)));
    return showMonth(key, options);
  }

  /* その月の日付を並べ直す。openDate があればその日を開き、openNewest ならいちばん新しい日を開く */
  async function showMonth(key, { openDate = "", openNewest = false } = {}) {
    const mine = ++ticket;
    shownTop = key;
    shownBottom = key;
    list.replaceChildren();
    more.hidden = true;
    message.hidden = true;
    idleReader();
    say("");
    list.setAttribute("aria-busy", "true");
    let days;
    try {
      days = await monthDays(key);
    } catch (error) {
      if (alive && mine === ticket) {
        list.removeAttribute("aria-busy");
        showError(error);
      }
      return;
    }
    if (!alive || mine !== ticket) {
      return;
    }
    list.removeAttribute("aria-busy");
    if (!days.length) {
      // 記録のない月は、前後の記録のある月へのボタンを出す
      const older = olderThan(key);
      const newer = newerThan(key);
      say(`${monthLabel(key)}の記録はありません。`, [
        newer ? [`${monthLabel(newer)}を見る`, () => jumpTo(newer, { openNewest: true }), "記録のあるその月へ移り、いちばん新しい日を開きます"] : null,
        older ? [`${monthLabel(older)}を見る`, () => jumpTo(older, { openNewest: true }), "記録のあるその月へ移り、いちばん新しい日を開きます"] : null,
      ].filter(Boolean));
      return;
    }
    drawMonth(key, days);
    setOlder();
    if (openDate) {
      openDay(openDate, days);
    } else if (openNewest) {
      list.querySelector(".rail-item").open = true;
    }
  }

  /* 一覧の下に、前の記録のある月を足す */
  async function appendOlder() {
    const older = olderThan(shownBottom);
    if (!older) {
      return;
    }
    const mine = ticket;
    more.disabled = true;
    try {
      const days = await monthDays(older);
      if (!alive || mine !== ticket) {
        return;
      }
      drawMonth(older, days);
      shownBottom = older;
      setOlder();
    } catch (error) {
      showError(error);
    } finally {
      more.disabled = false;
    }
  }

  /* 並べた中からその日を開く。ない日なら、同じ月のいちばん近い日を示す */
  function openDay(date, days) {
    const section = list.querySelector(`.rail-item[data-date="${date}"]`);
    if (section) {
      say("");
      section.open = true;
      section.scrollIntoView({ block: "center" });
      section.querySelector("summary").focus({ preventScroll: true });
      return;
    }
    const target = Date.parse(date);
    const near = days.map((day) => day.date).sort((a, b) => Math.abs(Date.parse(a) - target) - Math.abs(Date.parse(b) - target))[0];
    say(`${dayLabel(date)}の記録はありません。`, near ? [[`近い日：${dayLabel(near)}を開く`, () => openDay(near, days), "同じ月で、記録のあるいちばん近い日を開きます"]] : []);
  }

  dateForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = dateInput.value;
    if (!monthMode || !value) {
      return;
    }
    const key = value.slice(0, 7);
    const oldest = months.at(-1);
    const newest = months[0];
    if (key < oldest || key > newest) {
      say(`記録は、${monthLabel(oldest)}から${monthLabel(newest)}までです。`);
      return;
    }
    jumpTo(key, { openDate: value });
  });

  more.addEventListener("click", () => (monthMode ? appendOlder() : load()));

  async function start() {
    let data;
    try {
      data = await api("/archive/months");
    } catch (error) {
      if (!alive) {
        return;
      }
      if (error.status === 404 || error.status === 405) {
        // 月ごとの読み込みがまだないサーバーでは、前と同じ並べ方にする
        load();
        return;
      }
      showError(error);
      return;
    }
    if (!alive) {
      return;
    }
    months = (data.months || []).slice().sort().reverse();
    if (!months.length) {
      empty.hidden = false;
      return;
    }
    monthMode = true;
    monthSet = new Set(months);
    const newest = months[0];
    const oldest = months.at(-1);
    // 年は、いちばん古い記録の年から新しい記録の年まで、間の年も入れて並べる
    const years = [];
    for (let year = Number(oldest.slice(0, 4)); year <= Number(newest.slice(0, 4)); year += 1) {
      years.push(year);
    }
    wheel = monthWheel({
      years,
      labelledBy: "records-wheel-label",
      hasRecord: (year, month) => monthSet.has(`${year}-${pad(month)}`),
      onPick: (year, month) => {
        const key = `${year}-${pad(month)}`;
        if (alive && key !== shownTop) {
          showMonth(key);
        }
      },
    });
    finder.append(wheel.node);
    dateInput.min = `${oldest}-01`;
    dateInput.max = lastDayOf(newest);
    finder.hidden = false;
    jumpTo(newest, { openNewest: true });
  }

  // ---- 新しい順に30日ずつ並べる（月ごとの読み込みがないサーバーのとき） ----

  async function load() {
    const query = nextBefore ? `?before=${nextBefore}` : "";
    try {
      const data = await api(`/archive${query}`);
      if (!alive) {
        return;
      }
      const firstLoad = list.children.length === 0;
      for (const day of data.days) {
        const year = day.date.slice(0, 4);
        if (year !== lastYear) {
          lastYear = year;
          list.append(el("div", { class: "year-plate", text: year }));
        }
        list.append(drawDay(day));
      }
      // いちばん新しい日だけ、最初から開いておく
      const newest = list.querySelector(".rail-item");
      if (firstLoad && newest) {
        newest.open = true;
      }
      nextBefore = data.next_before;
      more.hidden = !nextBefore;
      empty.hidden = list.children.length > 0;
    } catch (error) {
      showError(error);
    }
  }

  start();
  return () => {
    alive = false;
    wheel?.dispose();
  };
}
