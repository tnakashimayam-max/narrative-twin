/*
  役割：ケア会議の画面。今の様子とツインを大きく出し、本人の言葉を字幕と声で届ける。
  4つの極：4. 10年後に代弁する。
  守る原則：1（決めるのは本人・家族・専門職）、2（今の様子を最初に出す）、3（出どころを分ける）、
            9（本人の言葉は本文のまま）、10（見える範囲はサーバーが絞る）、11（推定は別の枠で読み上げない）、
            12（ツイン（AI）と出す）。

  みんなで同じ画面を見るので、開けるのは専門職だけにする。託された家族が開くと、家族にだけ見える記録が
  専門職の目に入るため。答えは「ツインに聞く」と同じ処理で作る（app/advocacy.py の answer）。
  入口はツールの画面（2026-11-06 に「ツインに聞く」の画面から移した）。ケアマネジャーは「ツインにたずねる」の枠の「ケア会議」、
  サーバーのツールがない主治医などは、ツールの画面の「ケア会議」のカードから開く（static/tools.js）。
  字幕に出す本文も読み上げる本文も、サーバーが返した記録の本文そのもので、ここで文を作らない。

  届いた言葉はすべて日付のボタンで並べる。1件だけ大きく出すと、それが AI の答えに見え、時期の違う言葉や
  反対の言葉が隠れるため。

  問いには、ツイン（AI）の代弁も出す（tyumajinさんの決定）。代弁は「ツイン（AI）の代弁」の枠に分け、読み上げない。
  読み上げるのは、本人の言葉の字幕だけのまま（原則9、11）。代弁がある問いでは、推定の枠は出さない。AI の枠が2つ並ぶと、
  どちらが答えか分からなくなるため。代弁は「ツインに聞く」と同じ処理で作る（app/advocacy_voice.py）。

  ツイン（AI）は3Dの姿で光の台座に立たせる（static/twin_stage.js）。考えている間は回り、読み上げている間は正面で小さく揺れ、
  答えが届くと台座の輪が広がる。3Dを描けないブラウザでは、同じ台座に2Dの花の子を立たせる。
*/

import { api } from "./api.js";
import { dateWords, estimateFrame, group, sameUtteranceCards } from "./advocate.js";
import { TWIN_NAME } from "./bubbles.js";
import { speak, stopSpeaking } from "./speech.js";
import { el, sendWithKeys, waitingLine } from "./ui.js";
import { drawVoice, offRecordNote } from "./voice.js";
import { twinStage } from "./twin_stage.js";

/* ケア会議で聞きたいことの例。本人に向けて聞く形にする（会議では、記録にある本人の言葉を字幕に出すため） */
const MEETING_EXAMPLES = ["この家で大切にしてきたことは何ですか", "これからの暮らしで、続けたいことは何ですか", "人の手を借りることを、どう思っていますか"];

// 今の様子は新しい順に3件まで出す。いちばん新しい1件はいつも開いておく
const NOW_LINES = 3;
// 届いた言葉のほかにも記録があるかもしれないことを、字幕の下でいつも伝える
const NOT_ALL = "ツイン（AI）が読むのは記録をまとめた文です。ここにない話が、記録のどこかにあることもあります。";
const ASK_THE_PERSON = "この場でご本人に聞いてみてください。";

// 閉じて開き直しても、会議の中で出した字幕に戻れるようにする。もう一度聞くと費用がかかるため
let lastTurn = null;
let lastVoice = null;

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
  const voiceArea = el("div", { class: "meeting-voice", hidden: true });
  const others = el("div", { class: "meeting-others" });
  const status = el("p", { class: "error", role: "alert", hidden: true });
  const olderArea = el("div", {});
  const box = el("textarea", { id: "meeting-ask", rows: wide ? 2 : 1, "aria-label": "ツインに聞くこと", placeholder: "例：この家で大切にしてきたことは何ですか", maxLength: 2100 });
  const send = el("button", { type: "button", class: "primary", text: "聞く", tip: "本人の言葉を字幕に、ツイン（AI）の代弁を別の枠に出します" });
  // 聞きたいことの例の札（ご本人の決定、2026-11-07 の見直しの案B）。初めての人が文字を打たずに会議を動かせるようにする。
  // 押すと聞く欄に入るだけで、送るのは「聞く」を押したとき（ツインに聞くの例の札と同じ）
  const examples = el(
    "div",
    { class: "ask-examples meeting-examples" },
    ...MEETING_EXAMPLES.map((words) =>
      el("button", {
        type: "button",
        class: "quiet",
        text: words,
        tip: "この問いを、聞く欄に入れます。送るのは「聞く」を押したときです",
        onclick: () => {
          box.value = words;
          box.focus();
        },
      })
    )
  );
  // 名前はすぐ下に大きく出すので、舞台の名前は省く（原則12）
  const twinView = twinStage({ kind: "meeting", name: false });
  const reading = el("span", { class: "meeting-reading", role: "status", hidden: true, text: "読み上げています" });
  const twinRow = el("div", { class: "meeting-twin" }, twinView.node, el("strong", { text: TWIN_NAME }), reading);

  container.append(
    el(
      "section",
      { class: "panel meeting-panel" },
      // 閉じるボタンは画面の上に置く（下に置くと PC の 1280×800 で画面の外に切れていた）。閉じたら入口のツールへ戻る
      el(
        "div",
        { class: "meeting-top" },
        el("button", { type: "button", class: "quiet meeting-close", text: "会議の画面を閉じる", tip: "会議の画面を閉じて、ツールの画面に戻ります", onclick: () => ctx.openTab("tools") })
      ),
      nowArea,
      el(
        "div",
        { class: "meeting-stage" },
        twinRow,
        el("div", { class: "meeting-main" }, asked, voiceArea, chips, caption, el("p", { class: "hint", text: NOT_ALL }), others)
      ),
      olderArea,
      el(
        "div",
        { class: "advocate-composer" },
        status,
        el("label", { class: "meeting-ask-label", for: "meeting-ask", text: "ツインに聞くこと" }),
        examples,
        el("div", { class: "composer-row" }, box, send)
      )
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
            tip: "書いた様子を、今の様子のいちばん上に出します",
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
          el("button", { type: "button", class: "quiet", text: "やめる", tip: "書いた様子を入れずに、もとの表示に戻ります", onclick: () => close() })
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
    ctx.setPlate(`${data.self_name}のケア会議`);
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
        el("button", { type: "button", class: "quiet", text: "様子を入れる", tip: "会議の場で見た本人の様子を書く欄を開きます", onclick: openObservation })
      ),
      note ? el("p", { class: "hint", role: "status", text: note }) : "",
      latest ? line(latest, "meeting-now-latest") : el("p", { text: "今の様子は、まだ入っていません。" })
    );
    // ほかの様子は開いたときだけ出す。家族の様子など、本人の前で大きく出さなくてよい記録もあるため
    olderArea.replaceChildren(
      older.length ? el("details", { class: "fold" }, el("summary", { text: "ほかの様子も見る", tip: "開くと、少し前の今の様子も見られます" }), ...older.map((item) => line(item, "meeting-now-line"))) : ""
    );
  }

  /* 本人の言葉を字幕に出す。読み上げるのは前置きと本文だけ（原則9、11）。 */
  function showWord(words, index) {
    stopSpeaking();
    [...chips.children].forEach((chip, number) => chip.setAttribute("aria-pressed", String(number === index)));
    const item = words[index];
    caption.replaceChildren(
      el("p", { class: "meeting-lead-in", text: item.lead_in }),
      ...item.parts.map((part) => el("p", { class: "meeting-quote hand", text: `「${part.body}」` })),
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
        tip: "字幕の本人の言葉を声で読み上げます。もう一度押すと止まります",
        onclick: (event) =>
          speak(`${item.lead_in}。${item.parts.map((part) => part.body).join(" ")}`, event.currentTarget, {
            busyLabel: "止める",
            onChange: (speaking) => {
              reading.hidden = !speaking;
              twinView.setMood(speaking ? "speaking" : "idle");
            },
          }),
      })
    );
  }

  function drawTurn(turn, voice = null) {
    stopSpeaking();
    clearChanged();
    lastTurn = turn;
    asked.textContent = `問い：${turn.question}`;
    asked.hidden = false;
    const words = byNewest(sameUtteranceCards(turn.self_words));
    chips.replaceChildren(
      ...words.map((item, index) => el("button", { type: "button", text: shortDate(item.date), tip: `${shortDate(item.date)}の本人の言葉を、字幕に出します`, onclick: () => showWord(words, index) }))
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
          el("summary", { text: title, tip: `開くと、${title}が見られます。本人の言葉とは分けています` }),
          group("家族と縁のある人の証言", turn.testimony, "testimony"),
          group("AIの推測", turn.guesses, "guess")
        )
      );
    }
    // 推定は読み上げない。別の枠のまま出す（原則11）。代弁がある問いでは出さない
    if (turn.estimate && !(voice && voice.turn_id === turn.id)) {
      others.append(estimateFrame(turn.estimate));
    }
    drawMeetingVoice(voice && voice.turn_id === turn.id ? voice : null);
  }

  /* ツイン（AI）の代弁を、本人の言葉の字幕とは別の枠に出す。読み上げない。理由ともとにした言葉は開いたときだけ出す。 */
  function drawMeetingVoice(voice) {
    lastVoice = voice;
    voiceArea.hidden = !voice;
    if (!voice) {
      voiceArea.replaceChildren();
      return;
    }
    voiceArea.replaceChildren(
      el(
        "section",
        { class: "meeting-voice-frame" },
        el("h3", { text: "ツイン（AI）の代弁" }),
        el("p", { class: "hint", text: voice.label }),
        offRecordNote(voice),
        el("p", { class: "meeting-voice-claim", text: voice.claim }),
        voice.reading ? el("p", { class: "hint", text: voice.reading }) : "",
        // 記録にないことの札は上に出したので、たたんだ中には重ねて出さない
        el("details", { class: "fold" }, el("summary", { text: "理由と、もとにした言葉", tip: "開くと、ツインがそう考えた理由と、もとにした記録が見られます" }), drawVoice(voice, { withOffRecord: false }))
      )
    );
  }

  function drawVoiceWaiting() {
    voiceArea.hidden = false;
    voiceArea.replaceChildren(waitingLine("ツイン（AI）が10年の読みをもとに考えています。30秒ほどかかります", "meeting-waiting"));
  }

  function clearChanged() {
    container.querySelector(".meeting-changed")?.remove();
  }

  function drawEmpty() {
    asked.hidden = true;
    chips.hidden = true;
    caption.replaceChildren(el("p", { class: "meeting-waiting", text: "聞きたいことを下に入れると、記録にある本人の言葉をそのまま字幕でお届けします。ツイン（AI）の代弁は、別の枠に出します。" }));
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
    stopSpeaking();
    twinView.setMood("thinking");
    // 前の答えを新しい問いの答えと間違えないよう、待つ間は前の字幕を残さない
    const before = lastTurn;
    const beforeVoice = lastVoice;
    clearChanged();
    asked.textContent = `問い：${question}`;
    asked.hidden = false;
    chips.hidden = true;
    others.replaceChildren();
    caption.replaceChildren(waitingLine("ツイン（AI）が記録を読んでいます", "meeting-waiting"));
    try {
      const { turn } = await api("/advocacy/ask", { method: "POST", json: { question, client_key: crypto.randomUUID() } });
      if (alive) {
        drawTurn(turn);
        twinView.pulse();
        box.value = "";
        await drawNow();
        // 健康の問いには、サーバーが代弁を作らず voice を空で返す（ツインに聞くと同じ）
        if (!turn.not_yet) {
          drawVoiceWaiting();
          try {
            const { voice } = await api(`/advocacy/turns/${turn.id}/advocate`, { method: "POST", json: {} });
            if (alive && lastTurn?.id === turn.id) {
              drawTurn(turn, voice);
              twinView.pulse();
            }
          } catch (error) {
            drawMeetingVoice(null);
            status.textContent = error.message;
            status.hidden = false;
          }
        }
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
        drawTurn(before, beforeVoice);
      } else {
        drawEmpty();
      }
    } finally {
      sending = false;
      send.disabled = false;
      twinView.setMood("idle");
    }
  }

  send.addEventListener("click", ask);
  // Enter だけなら改行。Alt+Enter か Ctrl+Enter で「聞く」を押したのと同じにする
  sendWithKeys(box, send);
  if (lastTurn) {
    drawTurn(lastTurn, lastVoice);
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
    twinView.dispose();
  };
}
