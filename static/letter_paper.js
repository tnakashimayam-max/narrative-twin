/*
  役割：手紙の紙（A4 1枚）を組み立てる部品。専門職の画面と、確かめる画面で同じ形にする。
  4つの極：4. 10年後に代弁する。
  守る原則：9（引いた本人の言葉は記録の本文のまま）、12（紙の札に必ず「ツイン（AI）」と出す）。

  本文、引いた言葉、札はサーバーが作ったものだけを出す。ここで文を作らない（札の文は app/advocacy_letter.py）。
*/

import { el } from "./ui.js";

export function dateWords(iso) {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

function art(illustration) {
  if (!illustration) {
    return el("div", { class: "paper-art empty", "aria-hidden": "true" });
  }
  return el("figure", { class: "paper-art" }, el("img", { src: illustration.src, alt: illustration.title }));
}

/* A4 1枚に収まるまで、本文の字と挿絵を少しずつ小さくする。印刷の前に呼ぶ。
   短い行の多い手紙は、そのままだと紙の下が切れるため。字は 9pt より小さくしない。 */
export function fitToA4(paper) {
  const a4 = (297 * 96) / 25.4;
  let size = 12.5;
  let line = 2.05;
  let artHeight = 82;
  const apply = () => {
    paper.style.setProperty("--body-size", `${size}pt`);
    paper.style.setProperty("--body-line", String(line));
    paper.style.setProperty("--art-height", `${artHeight}mm`);
  };
  apply();
  paper.style.minHeight = "0";
  while (paper.scrollHeight > a4 && size > 9) {
    size -= 0.5;
    line = Math.max(1.5, line - 0.06);
    artHeight = Math.max(35, artHeight - 5);
    apply();
  }
  // それでもはみ出すときは、紙の余白を詰める
  paper.style.padding = paper.scrollHeight > a4 ? "11mm 15mm 9mm" : "";
  paper.style.minHeight = "";
}

/* 紙を描く。editable のときは、本文とお願いを直せる欄にする（確かめる画面）。 */
export function drawPaper(letter, { editable = false } = {}) {
  const body = editable
    ? el("textarea", { class: "paper-body", maxLength: 1200, "aria-label": "手紙の本文" })
    : el("p", { class: "paper-body", text: letter.body });
  if (editable) {
    body.value = letter.body;
    // ご本人が中で送らずに全部読めるよう、本文の行数に合わせて欄を伸ばす
    body.rows = Math.max(12, letter.body.split("\n").length + 3);
  }
  const requests = editable
    ? [0, 1, 2].map((index) => {
        const input = el("input", { class: "request", maxLength: 120, "aria-label": `お願い${index + 1}` });
        input.value = letter.requests[index] || "";
        return input;
      })
    : null;
  const requestPart = editable
    ? el("section", { class: "paper-requests" }, el("h2", { text: "お願い" }), ...requests)
    : letter.requests.length
      ? el("section", { class: "paper-requests" }, el("h2", { text: "お願い" }), el("ul", {}, ...letter.requests.map((item) => el("li", { text: item }))))
      : "";
  const quote = letter.quote
    ? el(
        "p",
        { class: "paper-quote" },
        el("span", { class: "lead", text: `${dateWords(letter.quote.date)}に話したこと（記録のまま）` }),
        el("span", { class: "words", text: `「${letter.quote.body}」` })
      )
    : "";
  const label = el("p", { class: "label", text: letter.label });
  const paper = el(
    "article",
    { class: "paper" },
    el("header", { class: "paper-head" }, el("h2", { class: "paper-to", text: `${letter.recipient}へ` }), el("span", { class: "paper-date", text: dateWords(letter.written_on) })),
    art(letter.illustration),
    body,
    requestPart,
    quote,
    el("footer", { class: "paper-foot" }, label)
  );
  paper.readEdits = () => ({
    body: body.value.trim(),
    requests: requests ? requests.map((input) => input.value.trim()).filter(Boolean) : letter.requests,
  });
  paper.setLabel = (text) => {
    label.textContent = text;
  };
  return paper;
}
