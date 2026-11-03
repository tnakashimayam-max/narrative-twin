/*
  役割：専門職と託された家族が、ツインと話す画面。今の様子、本人の言葉、証言、AIの推測、推定の枠を分けて出す。
  4つの極：4. 10年後に代弁する。
  守る原則：2（いちばん上に今の様子。今の様子、本人の言葉、AIの推測の順）、3（出どころを色、ラベル、枠の線で分ける）、
            9（本人の言葉は記録の本文のまま。日付と出どころを出す）、11（推定は別の枠で、読み上げない）、
            12（必ず「ツイン（AI）」と出す）。

  読み上げるのは本人の言葉の引用と前置きだけで、ブラウザの読み上げを使う。本人の声に似せない。
  2回押しても増えないよう、送っている間はボタンを止め、同じ印で送る。
*/

import { api } from "./api.js";
import { el, openSheet, twinFigure } from "./ui.js";

const TWIN_NAME = "ツイン（AI）";

function dateWords(iso) {
  const [year, month, day] = iso.split("T")[0].split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

let speakingButton = null;

function stopSpeaking() {
  window.speechSynthesis?.cancel();
  if (speakingButton) {
    speakingButton.textContent = "読み上げる";
    speakingButton = null;
  }
}

/* 押すと読み上げ、もう一度押すと止める。読み上げ中は、何が起きているかをボタンの言葉で見せる。 */
function speak(text, button) {
  if (!window.speechSynthesis) {
    return;
  }
  const wasThis = speakingButton === button;
  stopSpeaking();
  if (wasThis) {
    return;
  }
  const voice = new SpeechSynthesisUtterance(text);
  voice.lang = "ja-JP";
  voice.onend = () => {
    if (speakingButton === button) {
      stopSpeaking();
    }
  };
  speakingButton = button;
  button.textContent = "読み上げ中（押すと止める）";
  window.speechSynthesis.speak(voice);
}

/* 記録を1枚のカードにする。線の形でも出どころが分かるようにする。 */
function sourceCard(item, kind, { canSpeak = false } = {}) {
  const footer = el("div", { class: "source-meta" }, el("span", { class: `source-label ${kind}`, text: item.source }));
  if (item.confirm && kind !== "now") {
    footer.append(el("span", { class: "hint", text: item.confirm }));
  }
  if (canSpeak) {
    footer.append(
      el("button", {
        type: "button",
        class: "link",
        text: "読み上げる",
        onclick: (event) => speak(`${item.lead_in}。${item.body}`, event.currentTarget),
      })
    );
  }
  return el(
    "article",
    { class: `source-card ${kind}` },
    el("p", { class: "lead-in", text: item.lead_in || nowLead(item) }),
    el("p", { class: kind === "self" ? "quote hand" : "quote", text: kind === "self" ? `「${item.body}」` : item.body }),
    footer
  );
}

function nowLead(item) {
  return `${dateWords(item.date)}、${item.speaker_label || item.speaker}の記録です`;
}

/* 答えの中の今の様子。毎回いちばん上に出す（原則2）が、答えを押し下げないよう1件1行にする。 */
function nowLines(items) {
  if (!items?.length) {
    return "";
  }
  return el(
    "section",
    { class: "answer-group now-lines" },
    el("h3", { text: "今の様子（観察）" }),
    ...items.map((item) =>
      el("p", { class: "now-line" }, el("span", { class: "hint", text: `${dateWords(item.date)}、${item.speaker_label || item.speaker}：` }), item.body)
    )
  );
}

function group(title, items, kind, options) {
  if (!items?.length) {
    return "";
  }
  return el("section", { class: "answer-group" }, el("h3", { text: title }), ...items.map((item) => sourceCard(item, kind, options)));
}

function estimateFrame(estimate) {
  if (!estimate) {
    return "";
  }
  return el(
    "section",
    { class: "estimate-frame" },
    el("h3", { text: "AIの推定（本人に確かめていません）" }),
    el("p", { class: "hint", text: "本人の言葉ではありません。決めるのは、ご本人とご家族と専門職です。" }),
    el("p", { class: "estimate-text", text: estimate.text }),
    el("p", { class: "estimate-meter" }, "確からしさ：", el("strong", { class: "tag", text: estimate.confidence })),
    el("h4", { text: "確かめ方" }),
    el("p", { text: estimate.how_to_confirm }),
    el(
      "div",
      { class: "estimate-grounds" },
      el("div", {}, el("h4", { text: "根拠" }), ...estimate.evidence.map((item) => sourceCard(item, cardKind(item)))),
      el(
        "div",
        {},
        el("h4", { text: "反対の根拠" }),
        ...(estimate.counter.length
          ? estimate.counter.map((item) => sourceCard(item, cardKind(item)))
          : [el("p", { text: "反対の根拠は見つかっていません。読んだ記録が少ないだけかもしれません。" })])
      )
    )
  );
}

function cardKind(item) {
  if (item.source === "本人の言葉" || item.source === "本人の訂正") {
    return "self";
  }
  return item.source === "AIの推測" ? "guess" : "testimony";
}

/* 要素の根拠を、言明と発言の原文までたどる。 */
export async function openTrace(elementId) {
  const data = await api(`/advocacy/elements/${elementId}`);
  openSheet((inner, close) => {
    inner.append(
      el("h2", { text: "根拠をたどる" }),
      el("p", { class: "hint", text: "AI の読み取り：" }),
      el("p", { text: data.body }),
      ...data.evidence.map((item) =>
        el(
          "div",
          {},
          sourceCard(item, cardKind(item)),
          ...item.utterances.map((utterance) =>
            el("p", { class: "hint", text: `発言の原文（${dateWords(utterance.spoken_at)}）：${utterance.text}` })
          )
        )
      ),
      el("button", { type: "button", class: "quiet", text: "閉じる", onclick: close })
    );
  });
}

/* 同じ文の要素は1行にする。流し直した要素が同じ文になることがあるため。 */
function foldedElements(elements) {
  const seen = new Set();
  const rows = elements.filter((element) => {
    if (seen.has(element.body)) {
      return false;
    }
    seen.add(element.body);
    return true;
  });
  return el(
    "details",
    { class: "elements-fold" },
    el("summary", { text: "AI がまとめた文と、もとの記録" }),
    el("p", { class: "hint", text: "本人の言葉ではなく、AI が記録をまとめた文です。" }),
    ...rows.map((element) =>
      el(
        "div",
        { class: "person-row" },
        el(
          "div",
          { class: "person-text" },
          el("span", { class: "source-label guess", text: "AI のまとめ" }),
          ` ${element.valid_from ? `${dateWords(element.valid_from)}からの話。` : ""}${element.body}（${element.confirmed ? "本人が確かめた記録から" : "本人がまだ確かめていない記録から"}）`
        ),
        el("button", { type: "button", class: "quiet", text: "根拠をたどる", onclick: () => openTrace(element.element_id) })
      )
    )
  );
}

function drawTurn(turn) {
  const body = el("div", { class: "answer-body" });
  if (turn.notice) {
    body.append(el("p", { class: "notice", text: turn.notice }));
  }
  body.append(nowLines(turn.now));
  if (turn.not_yet) {
    body.append(el("p", { class: "not-yet", text: turn.not_yet }));
  }
  if (turn.changed) {
    body.append(el("p", { class: "notice", text: "時期によって言葉が変わっています。今の思いはご本人に確かめてください。" }));
  }
  body.append(
    group("本人の言葉", turn.self_words, "self", { canSpeak: true }),
    group("家族と縁のある人の証言", turn.testimony, "testimony"),
    group("AIの推測", turn.guesses, "guess")
  );
  if (turn.lead) {
    body.append(el("p", { class: "twin-lead", text: turn.lead }));
  }
  body.append(estimateFrame(turn.estimate));
  body.append(
    el("p", { class: "hint", text: "ツイン（AI）が読むのは記録をまとめた文です。ここにない話が、記録のどこかにあることもあります。「記録」のタブで確かめられます。" })
  );
  if (turn.elements?.length) {
    body.append(foldedElements(turn.elements));
  }
  return el(
    "div",
    { class: "turn" },
    el("div", { class: "bubble-row mine" }, el("div", { class: "bubble-body" }, el("div", { class: "bubble", text: turn.question }))),
    el("div", { class: "twin-answer" }, el("div", { class: "row" }, twinFigure(), el("span", { class: "bubble-name", text: TWIN_NAME })), body)
  );
}

export function renderAdvocate(container, ctx) {
  let alive = true;
  let sending = false;
  const top = el("section", { class: "card now-panel" });
  const log = el("div", { class: "turns" });
  const status = el("p", { class: "error", role: "alert", hidden: true });
  const thinking = el("p", { class: "hint", role: "status", hidden: true, text: "ツイン（AI）が記録を読んでいます" });
  const box = el("textarea", { rows: 2, "aria-label": "ツインに聞くこと", placeholder: "例：これからの暮らしを、どう話していましたか", maxLength: 2100 });
  const send = el("button", { type: "button", class: "primary", text: "聞く" });
  const heading = el("h1", { text: "ツイン（AI）に聞く" });
  container.append(
    el(
      "section",
      { class: "panel advocate-panel" },
      heading,
      el("p", { class: "hint", text: "記録にある言葉を、そのままお届けします。判断するのは、ご本人とご家族と専門職です。" }),
      top,
      log,
      thinking,
      status,
      el("div", { class: "advocate-composer" }, el("div", { class: "composer-row" }, box, send))
    )
  );

  /* 最新の問いが画面の上に来るまで動かす。入れる欄が下に固定なので、答えが長くても問いから読める。 */
  function showLatest() {
    log.lastElementChild?.scrollIntoView({ block: "start" });
  }

  function openObservation(onDone) {
    openSheet((inner, close) => {
      const text = el("textarea", { rows: 3, "aria-label": "今の様子", maxLength: 2000 });
      const message = el("p", { class: "error", role: "alert", hidden: true });
      inner.append(
        el("h2", { text: "今の様子を入れる" }),
        el("p", { class: "hint", text: "ご覧になった様子を書いてください。「今の様子」として、いちばん上に出ます。" }),
        text,
        message,
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "入れる",
            onclick: async () => {
              try {
                await api("/observations", { method: "POST", json: { text: text.value } });
                close();
                onDone();
              } catch (error) {
                message.textContent = error.message;
                message.hidden = false;
              }
            },
          }),
          el("button", { type: "button", class: "quiet", text: "やめる", onclick: close })
        )
      );
    });
  }

  async function drawTop() {
    const data = await api("/advocacy/overview");
    heading.textContent = `${data.self_name}のツイン（AI）に聞く`;
    top.replaceChildren(
      el("h2", { text: "今の様子" }),
      ...(data.now.length
        ? data.now.map((item) => sourceCard(item, "now"))
        : [el("p", { text: "今の様子は、まだ入っていません。" })]),
      el("button", { type: "button", class: "quiet", text: "今の様子を入れる", onclick: () => openObservation(drawTop) }),
      data.decided_on
        ? el("p", { class: "hint scope-note", text: `見えるのは、${dateWords(data.decided_on)}に${data.self_name}が決めた範囲です。` })
        : el("p", { class: "hint scope-note", text: `${data.self_name}は、まだ範囲を決めていません。` })
    );
  }

  async function load() {
    try {
      await drawTop();
      const { items } = await api("/advocacy/turns");
      if (alive) {
        log.replaceChildren(...items.map(drawTurn));
        showLatest();
      }
    } catch (error) {
      status.textContent = error.message;
      status.hidden = false;
    }
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
    thinking.hidden = false;
    status.hidden = true;
    try {
      const { turn } = await api("/advocacy/ask", {
        method: "POST",
        json: { question, client_key: crypto.randomUUID() },
      });
      if (alive) {
        log.append(drawTurn(turn));
        box.value = "";
        showLatest();
      }
    } catch (error) {
      status.textContent = error.message;
      status.hidden = false;
    } finally {
      sending = false;
      send.disabled = false;
      thinking.hidden = true;
    }
  }

  send.addEventListener("click", ask);
  load();
  return () => {
    alive = false;
    stopSpeaking();
  };
}
