/*
  役割：ケア会議の画面。今の様子とツインを大きく出し、本人の言葉を字幕と声で届ける。
  4つの極：4. 10年後に代弁する。
  守る原則：1（決めるのは本人・家族・専門職）、2（今の様子を最初に出す）、3（出どころを分ける）、
            9（本人の言葉は本文のまま）、10（見える範囲はサーバーが絞る）、11（推定は別の枠で読み上げない）、
            12（ツイン（AI）と出す）。

  みんなで同じ画面を見るので、開けるのは専門職だけにする。託された家族が開くと、家族にだけ見える記録が
  専門職の目に入るため。答えは「ツインに聞く」と同じ処理で作る（app/advocacy.py の answer）。
  字幕に出す本文も読み上げる本文も、サーバーが返した記録の本文そのもので、ここで文を作らない。

  届いた言葉はすべて日付のボタンで並べる。1件だけ大きく出すと、それが AI の答えに見え、時期の違う言葉や
  反対の言葉が隠れるため。
*/

import { api } from "./api.js";
import { dateWords, estimateFrame, group } from "./advocate.js";
import { TWIN_NAME } from "./bubbles.js";
import { speak, stopSpeaking } from "./speech.js";
import { el, twinFigure } from "./ui.js";

// 今の様子は新しい順に3件まで出す。いちばん新しい1件はいつも開いておく
const NOW_LINES = 3;
// 届いた言葉のほかにも記録があるかもしれないことを、字幕の下でいつも伝える
const NOT_ALL = "ツイン（AI）が読むのは記録をまとめた文です。ここにない話が、記録のどこかにあることもあります。";
const ASK_THE_PERSON = "この場でご本人に聞いてみてください。";

// 閉じて開き直しても、会議の中で出した字幕に戻れるようにする。もう一度聞くと費用がかかるため
let lastTurn = null;

/* 新しい言葉を先にする。今の本人を最優先するため（原則2）。 */
function byNewest(words) {
  return [...words].sort((a, b) => b.date.localeCompare(a.date));
}

function shortDate(iso) {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

export function renderMeeting(container, ctx) {
  let alive = true;
  let sending = false;
  let writing = false;
  let selfName = "ご本人";
  const wide = window.matchMedia("(min-width: 900px)").matches;
  const nowArea = el("section", { class: "meeting-now" });
  const asked = el("p", { class: "meeting-question", hidden: true });
  const chips = el("div", { class: "meeting-chips", role: "group", "aria-label": "届いた言葉の日付", hidden: true });
  const caption = el("div", { class: "meeting-caption", "aria-live": "polite" });
  const others = el("div", { class: "meeting-others" });
  const status = el("p", { class: "error", role: "alert", hidden: true });
  const olderArea = el("div", {});
  const box = el("textarea", { id: "meeting-ask", rows: wide ? 2 : 1, "aria-label": "ツインに聞くこと", placeholder: "例：この家で大切にしてきたことは何ですか", maxLength: 2100 });
  const send = el("button", { type: "button", class: "primary", text: "聞く" });
  const figure = twinFigure(wide ? { large: true } : {});
  const reading = el("span", { class: "meeting-reading", role: "status", hidden: true, text: "読み上げています" });
  const twinRow = el("div", { class: "meeting-twin" }, figure, el("strong", { text: TWIN_NAME }), reading);

  container.append(
    el(
      "section",
      { class: "panel meeting-panel" },
      nowArea,
      el(
        "div",
        { class: "meeting-stage" },
        twinRow,
        el("div", { class: "meeting-main" }, asked, chips, caption, el("p", { class: "hint", text: NOT_ALL }), others)
      ),
      el("p", { class: "meeting-rule", text: "決めるのは、ご本人とご家族と専門職です。" }),
      olderArea,
      el(
        "div",
        { class: "advocate-composer" },
        status,
        el("label", { class: "meeting-ask-label", for: "meeting-ask", text: "ツインに聞くこと" }),
        el("div", { class: "composer-row" }, box, send)
      ),
      el("button", { type: "button", class: "quiet meeting-close", text: "会議の画面を閉じる", onclick: () => ctx.openTab("advocate") })
    )
  );

  function openObservation() {
    writing = true;
    const text = el("textarea", { rows: 2, "aria-label": "今の様子", maxLength: 2000, placeholder: "例：言葉を聞いて、うなずかれました" });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    const close = async (note) => {
      writing = false;
      await drawNow(note);
    };
    nowArea.replaceChildren(
      el("h2", { text: `${selfName}の今の様子を入れる` }),
      el(
        "div",
        { class: "stack" },
        el("label", { class: "field" }, "ご覧になった様子を書いてください", text),
        message,
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "入れる",
            onclick: async () => {
              if (!text.value.trim()) {
                message.textContent = "今の様子を入れてください。";
                message.hidden = false;
                text.focus();
                return;
              }
              try {
                await api("/observations", { method: "POST", json: { text: text.value, at_meeting: true } });
                await close("入れました。");
              } catch (error) {
                message.textContent = error.message;
                message.hidden = false;
              }
            },
          }),
          el("button", { type: "button", class: "quiet", text: "やめる", onclick: () => close() })
        )
      )
    );
    text.focus();
  }

  /* 今の様子をいちばん上に大きく出す（原則2）。 */
  async function drawNow(note) {
    const data = await api("/advocacy/overview");
    if (!alive) {
      return;
    }
    selfName = data.self_name;
    ctx.setPlate("MEETING", `${data.self_name}のケア会議`);
    if (writing) {
      // 書きかけの様子を、問いのあとの読み直しで消さない
      return;
    }
    const line = (item, className) =>
      el("p", { class: className }, el("span", { class: "hint", text: `${dateWords(item.date)}、${item.speaker_label || item.speaker}の記録` }), item.body);
    const [latest, ...older] = data.now.slice(0, NOW_LINES);
    nowArea.replaceChildren(
      el(
        "div",
        { class: "meeting-now-head" },
        el("h2", { text: `${data.self_name}の今の様子` }),
        el("button", { type: "button", class: "quiet", text: "様子を入れる", onclick: openObservation })
      ),
      note ? el("p", { class: "hint", role: "status", text: note }) : "",
      latest ? line(latest, "meeting-now-latest") : el("p", { text: "今の様子は、まだ入っていません。" })
    );
    // ほかの様子は開いたときだけ出す。家族の様子など、本人の前で大きく出さなくてよい記録もあるため
    olderArea.replaceChildren(
      older.length ? el("details", { class: "fold" }, el("summary", { text: "ほかの様子も見る" }), ...older.map((item) => line(item, "meeting-now-line"))) : ""
    );
  }

  /* 本人の言葉を字幕に出す。読み上げるのは前置きと本文だけ（原則9、11）。 */
  function showWord(words, index) {
    stopSpeaking();
    [...chips.children].forEach((chip, number) => chip.setAttribute("aria-pressed", String(number === index)));
    const item = words[index];
    caption.replaceChildren(
      el("p", { class: "meeting-lead-in", text: item.lead_in }),
      el("p", { class: "meeting-quote hand", text: `「${item.body}」` }),
      el(
        "div",
        { class: "source-meta" },
        el("span", { class: "source-label self", text: item.source }),
        item.confirm ? el("span", { class: "hint", text: item.confirm }) : ""
      ),
      el("button", {
        type: "button",
        class: "primary meeting-speak",
        text: "読み上げる",
        onclick: (event) =>
          speak(`${item.lead_in}。${item.body}`, event.currentTarget, {
            busyLabel: "止める",
            onChange: (speaking) => {
              reading.hidden = !speaking;
              figure.classList.toggle("thinking", speaking);
            },
          }),
      })
    );
  }

  function drawTurn(turn) {
    stopSpeaking();
    clearChanged();
    lastTurn = turn;
    asked.textContent = `問い：${turn.question}`;
    asked.hidden = false;
    const words = byNewest(turn.self_words || []);
    chips.replaceChildren(
      ...words.map((item, index) => el("button", { type: "button", text: shortDate(item.date), onclick: () => showWord(words, index) }))
    );
    chips.hidden = words.length < 2;
    others.replaceChildren();
    if (words.length) {
      showWord(words, 0);
    } else {
      caption.replaceChildren(
        el("p", { class: "not-yet", text: turn.not_yet || "まだ聞けていません。" }),
        el("p", { class: "meeting-next", text: ASK_THE_PERSON })
      );
    }
    // 言葉が時期で変わるときは、字幕より先に読めるようにする
    if (turn.changed) {
      chips.before(el("p", { class: "notice meeting-changed", text: "時期によって言葉が変わっています。今の思いはご本人に確かめてください。" }));
    }
    if (turn.notice) {
      others.append(el("p", { class: "notice", text: turn.notice }));
    }
    // 証言と推測は、本人の言葉と混ざらないように開いたときだけ出す（原則3）
    const hasTestimony = Boolean(turn.testimony?.length);
    const hasGuesses = Boolean(turn.guesses?.length);
    if (hasTestimony || hasGuesses) {
      const title = hasTestimony && hasGuesses ? "証言と AI の推測" : hasTestimony ? "証言" : "AI の推測";
      others.append(
        el(
          "details",
          { class: "fold" },
          el("summary", { text: title }),
          group("家族と縁のある人の証言", turn.testimony, "testimony"),
          group("AIの推測", turn.guesses, "guess")
        )
      );
    }
    // 推定は読み上げない。別の枠のまま出す（原則11）
    if (turn.estimate) {
      others.append(estimateFrame(turn.estimate));
    }
  }

  function clearChanged() {
    container.querySelector(".meeting-changed")?.remove();
  }

  function drawEmpty() {
    asked.hidden = true;
    chips.hidden = true;
    caption.replaceChildren(el("p", { class: "meeting-waiting", text: "聞きたいことを下に入れると、記録にある本人の言葉をそのまま字幕でお届けします。" }));
  }

  async function ask() {
    const question = box.value.trim();
    if (sending) {
      return;
    }
    if (!question) {
      status.textContent = "聞きたいことを入れてください。";
      status.hidden = false;
      return;
    }
    sending = true;
    send.disabled = true;
    status.hidden = true;
    figure.classList.add("thinking");
    stopSpeaking();
    // 前の答えを新しい問いの答えと間違えないよう、待つ間は前の字幕を残さない
    const before = lastTurn;
    clearChanged();
    asked.textContent = `問い：${question}`;
    asked.hidden = false;
    chips.hidden = true;
    others.replaceChildren();
    caption.replaceChildren(el("p", { class: "meeting-waiting", role: "status", text: "ツイン（AI）が記録を読んでいます" }));
    try {
      const { turn } = await api("/advocacy/ask", { method: "POST", json: { question, client_key: crypto.randomUUID() } });
      if (alive) {
        drawTurn(turn);
        box.value = "";
        await drawNow();
        // 幅の狭い画面では、ツインと届いた言葉が見える所まで動かす。今の様子は上に固定なので、その高さの分だけ下に止める
        if (!wide) {
          twinRow.style.scrollMarginTop = `${nowArea.offsetHeight + 6}px`;
          twinRow.scrollIntoView({ block: "start" });
        }
      }
    } catch (error) {
      status.textContent = error.message;
      status.hidden = false;
      if (before) {
        drawTurn(before);
      } else {
        drawEmpty();
      }
    } finally {
      sending = false;
      send.disabled = false;
      figure.classList.remove("thinking");
    }
  }

  send.addEventListener("click", ask);
  if (lastTurn) {
    drawTurn(lastTurn);
  } else {
    drawEmpty();
  }
  drawNow().catch((error) => {
    status.textContent = error.message;
    status.hidden = false;
  });
  return () => {
    alive = false;
    stopSpeaking();
  };
}
