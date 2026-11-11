/*
  役割：ツインのアドボカシーを出す仮置きの部品。呼び方（ボタンかチャットか）が決まるまでの最小の形。
  4つの極：4. 10年後に代弁する。
  守る原則：3（ツインの文と記録を分ける）、9（本人の言葉は記録の本文のまま）、12（必ず「ツイン（AI）」と出す）。

  本人の言葉の本文は、サーバーが記録から入れたものだけを出す。ここで文を作らない。
  答えはツインの考えを先に出し、どの読みから考えたかを理由ごとに示す。引いた言葉は、その下に補強として出す（原則16）。
*/

import { api } from "./api.js";
import { group } from "./advocate.js";
import { drawLetterRequested, drawTellItems } from "./tell.js";
import { el, startWaiting, twinFigure } from "./ui.js";

function cardKind(item) {
  if (item.source === "本人の言葉" || item.source === "本人の訂正") {
    return "self";
  }
  return item.source === "AIの推測" ? "guess" : "testimony";
}

// 仕組みの言葉を、読む人の言葉にする
const KIND_WORDS = { 価値: "大切にしていること", 判断の決まり: "決めるときの決まり", 優先順位: "優先すること", 変化: "変わったこと" };

/* 理由のもとにした意思モデルの要素。ツインがどの読みから考えたかを見せる。古い記録にはないので、なければ出さない。 */
function modelLines(elements) {
  return (elements || []).map((item) =>
    el("p", { class: "hint voice-model" }, `${KIND_WORDS[item.kind] || item.kind}：${item.body}（${item.confirmed ? "本人が確かめた記録を含む" : "AI の見立て"}）`)
  );
}

/* ツインが読んだ人物像と気持ち。本人の言葉ではないので、ツインの枠の中に分けて出す。 */
function readingBlock(voice) {
  if (!voice.selfhood && !voice.feeling) {
    return "";
  }
  return el(
    "div",
    { class: "voice-selfhood" },
    voice.selfhood ? el("p", {}, el("strong", { text: "ツインが近づいた人物像：" }), voice.selfhood) : "",
    voice.feeling ? el("p", {}, el("strong", { text: "この問いへの気持ちの読み：" }), voice.feeling) : ""
  );
}

function quoteCards(title, items) {
  const kinds = new Map();
  items.forEach((item) => kinds.set(cardKind(item), [...(kinds.get(cardKind(item)) || []), item]));
  return [...kinds.entries()].map(([kind, list]) => group(title, list, kind));
}

/* 問いに合う本人の言葉が記録になかったときの決まった1文（off_record）。ツインの文と見分けられるよう、藍の細い枠の札に
   ふつうの字で、主張より前に出す（ご本人の決定、2026-11-07）。11/7 より前に作った考えには項目がないので、ないときは出さない */
export function offRecordNote(voice) {
  return voice?.off_record ? el("p", { class: "voice-off-record", text: voice.off_record }) : "";
}

export function drawVoice(voice, { withOffRecord = true } = {}) {
  return el(
    "section",
    { class: "estimate-frame voice-frame" },
    el("h3", { text: "ツイン（AI）の代弁" }),
    el("p", { class: "hint", text: voice.label }),
    withOffRecord ? offRecordNote(voice) : "",
    el("p", { class: "estimate-text", text: voice.claim }),
    readingBlock(voice),
    voice.reading ? el("p", { class: "hint voice-reading", text: voice.reading }) : "",
    // 「決めるのは人」は当たり前なので、どの画面にも出さない（2026-11-06 ご本人の決定）
    ...voice.reasons.flatMap((reason) => [el("h4", { text: reason.text }), ...modelLines(reason.elements), ...quoteCards("補強に引いた言葉", reason.evidence)]),
    ...voice.objections.flatMap((item) => [...quoteCards("反対の言葉", [item.quote]), el("p", {}, el("strong", { text: "ツインの答え：" }), item.answer)]),
    ...(voice.requests.length ? [el("h4", { text: "ツインからのお願い" }), ...voice.requests.map((text) => el("p", { text }))] : []),
    voice.ask_back ? el("p", { class: "notice", text: `ツインから聞きたいこと：${voice.ask_back}` }) : ""
  );
}

function twinBlock(...children) {
  return el("div", { class: "twin-answer" }, el("div", { class: "row" }, twinFigure(), el("span", { class: "bubble-name", text: "ツイン（AI）" })), el("div", { class: "answer-body" }, ...children));
}

function asked(question) {
  return el("div", { class: "bubble-row mine" }, el("div", { class: "bubble-body" }, el("div", { class: "bubble", text: question })));
}

function drawObjection(objection) {
  return el(
    "section",
    { class: "estimate-frame voice-frame" },
    el("h3", { text: "ツイン（AI）の異議" }),
    el("p", { class: "hint", text: `${objection.label}　根拠：${objection.verdict}` }),
    el("p", { class: "estimate-text", text: objection.claim }),
    objection.reading ? el("p", { class: "hint voice-reading", text: objection.reading }) : "",
    ...objection.points.flatMap((point) => [el("h4", { text: point.target }), el("p", { text: point.objection }), ...quoteCards("補強に引いた言葉", point.evidence)]),
    ...(objection.requests.length ? [el("h4", { text: "ツインからのお願い" }), ...objection.requests.map((text) => el("p", { text }))] : []),
    objection.ask_back ? el("p", { class: "notice", text: `ツインから聞きたいこと：${objection.ask_back}` }) : ""
  );
}

function drawSecond(second) {
  return el(
    "section",
    { class: "estimate-frame voice-frame" },
    el("h3", { text: "第2版" }),
    el("p", { class: "hint", text: second.label }),
    el("p", { class: "estimate-text", text: second.summary }),
    ...second.items.flatMap((item) => [el("h4", { text: item.aim }), el("p", { text: item.support }), el("p", { class: "hint", text: `なぜ：${item.why}` }), ...quoteCards("補強に引いた言葉", item.evidence)])
  );
}

/* 別のページを新しいタブで開くボタン。tip は吹き出しの言葉 */
function openButton(text, page, tip) {
  return el("button", { type: "button", class: "wide", text, tip, onclick: () => window.open(page) });
}
const PLAN_PAGE_TIP = "ケアプラン案の版を並べて見比べる画面を、新しいタブで開きます";

/* チャットの返事を描く。呼び先はボタンと同じなので、出す形もそろえる。drawTurn は「ツインに聞く」の答えの形。 */
export function drawChatReply(reply, question, drawTurn) {
  if (reply.kind === "問い") {
    return drawTurn(reply.turn);
  }
  if (reply.kind === "問いとツインの考え") {
    return drawTurn({ ...reply.turn, voice: reply.voice });
  }
  const parts = { ツインの考え: () => [drawVoice(reply.voice)] };
  parts["1枚の紙"] = () => [el("p", { class: "estimate-text", text: reply.sheet.claim }), el("p", { class: "hint", text: reply.sheet.label }), openButton("1枚の紙を開く", "sheet.html", "ツインがまとめた紙を、新しいタブで開きます")];
  parts["ケアプラン案"] = () => [
    el("p", { class: "hint", text: `第1版：${reply.plan.first.summary}` }),
    drawObjection(reply.plan.objection),
    el("p", { class: "hint", text: "「第2版を作ってください」と書くと、異議をもとに書き直します。" }),
    openButton("版を並べて見る", "plan.html", PLAN_PAGE_TIP),
  ];
  parts["第2版"] = () => [
    drawSecond(reply.plan.second),
    el("p", { class: "hint", text: "どの版を使うかは、人が選びます。「第2版にします」と書くか、版を並べて見る画面で選べます。" }),
    openButton("版を並べて見る", "plan.html", PLAN_PAGE_TIP),
  ];
  // 伝えたことは項目に分けて返る。残すかは項目ごとに人が決める（app/professional_records.py）
  parts["伝える"] = () => [drawTellItems(reply.tell.items)];
  // 手紙は、本人か託された家族が確かめて返すまで中身を出さない（app/advocacy_letter.py）
  parts["手紙"] = () => [drawLetterRequested(reply.letter)];
  parts["版の選択"] = () => [
    el("p", { class: "estimate-text", role: "status", text: `${reply.plan.choice.chooser}が「${reply.plan.choice.version}」を選びました。` }),
    openButton("版を並べて見る", "plan.html", PLAN_PAGE_TIP),
  ];
  return el("div", { class: "turn" }, asked(question), twinBlock(...parts[reply.kind]()));
}

/* 答えの下に置くボタン。作ってあれば AI を呼ばずに同じ主張が返る。 */
export function voiceArea(turn) {
  const area = el("div", { class: "voice-area" });
  const status = el("p", { class: "hint", role: "status", hidden: true, text: "ツイン（AI）が10年の記録を読んで考えています" });
  const error = el("p", { class: "error", role: "alert", hidden: true });
  const button = el("button", {
    type: "button",
    class: "wide",
    text: "ツインの考えを聞く",
    tip: "ツイン（AI）が記録を読んで考えた意見を、この下に出します",
    onclick: async () => {
      button.disabled = true;
      startWaiting(status, "ツイン（AI）が10年の記録を読んで考えています");
      status.hidden = false;
      error.hidden = true;
      try {
        const { voice } = await api(`/advocacy/turns/${turn.id}/advocate`, { method: "POST", json: {} });
        area.replaceChildren(drawVoice(voice));
        area.dispatchEvent(new CustomEvent("voice-drawn", { bubbles: true }));
      } catch (problem) {
        error.textContent = problem.message;
        error.hidden = false;
        button.disabled = false;
      } finally {
        status.hidden = true;
      }
    },
  });
  area.append(button, status, error);
  return area;
}
