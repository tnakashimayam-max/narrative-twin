/*
  役割：専門職の手紙の画面。手紙を頼み、頼んだ手紙の状態を見て、返ってきた手紙を読んで印刷する。
  4つの極：4. 10年後に代弁する。
  守る原則：1（決めるのは人）、3（ツインの文と記録を分ける）、12（紙に必ず「ツイン（AI）」と出す）。

  もとにしたピースと読んだ記録は、画面にだけ出し、紙には載せない。
  頼んだ手紙は「削除」で消せる。取り消せないので2回押す形にする。消しても、手紙で直した言葉は記録に残る（app/made_documents.py）。
*/

import { api } from "./api.js";
import { el, startWaiting, twoStepButton } from "./ui.js";
import { dateWords, drawPaper, fitToA4 } from "./letter_paper.js";

const list = document.getElementById("list");
const paperBox = document.getElementById("paper");
const material = document.getElementById("material");
const status = document.getElementById("status");
const make = document.getElementById("make");
const print = document.getElementById("print");
const purpose = document.getElementById("purpose");

const STATUS_WORDS = {
  確かめ待ち: "ご本人かご家族が確かめています",
  返した: "届きました",
  出さない: "ご本人かご家族が、出さないと決めました",
};

function show(letter) {
  paperBox.replaceChildren(drawPaper(letter.paper));
  print.hidden = false;
  material.replaceChildren(
    el("h2", { text: "ツイン（AI）がもとにしたピース（紙には載りません）" }),
    letter.pieces?.length
      ? el("ul", {}, ...letter.pieces.map((piece) => el("li", { text: `${piece.kind}：${piece.body}` })))
      : el("p", { class: "hint", text: "ありません。" }),
    letter.reading ? el("p", { class: "hint", text: letter.reading }) : "",
    letter.paper.edited ? el("p", { class: "hint", text: "ご本人かご家族が、ツインの下書きを修正しています。" }) : ""
  );
}

async function load(selectId) {
  const { items } = await api("/advocacy/letters");
  list.replaceChildren(
    ...items.map((letter) =>
      el(
        "li",
        {},
        el("span", { text: `${dateWords(letter.created_at)}　${letter.purpose}` }),
        el("span", { class: letter.status === "返した" ? "chip returned" : "chip", text: STATUS_WORDS[letter.status] || letter.status }),
        letter.paper ? el("button", { type: "button", text: "読む", tip: "この手紙の紙と、もとにした材料を下に出します", onclick: () => show(letter) }) : "",
        twoStepButton("削除", "本当に削除する", async () => {
          try {
            await api(`/advocacy/made/letter/${letter.id}`, { method: "DELETE" });
          } catch (error) {
            status.textContent = error.message;
            return;
          }
          // 開いていた紙が消した手紙なら、紙と材料も閉じる
          paperBox.replaceChildren();
          material.replaceChildren();
          print.hidden = true;
          status.textContent = "手紙を削除しました。";
          await load();
        }, "quiet", "この手紙を一覧から消します（取り消せません）")
      )
    )
  );
  const chosen = items.find((letter) => letter.id === selectId && letter.paper) || items.find((letter) => letter.paper);
  if (chosen) {
    show(chosen);
  }
  if (!items.length) {
    status.textContent = "まだ手紙を頼んでいません。";
  }
}

make.addEventListener("click", async () => {
  make.disabled = true;
  startWaiting(status, "ツイン（AI）が手紙を書いています。1分ほどかかります。");
  try {
    const { letter } = await api("/advocacy/letters", { method: "POST", json: { purpose: purpose.value, client_key: crypto.randomUUID() } });
    status.textContent = "下書きを書きました。ご本人かご家族が読んで修正してから、ここに届きます。";
    await load(letter.id);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    make.disabled = false;
  }
});

// 印刷の前に、A4 1枚に収まるよう字と挿絵を合わせる（ブラウザの印刷メニューから印刷したときも）
function fit() {
  const paper = paperBox.querySelector(".paper");
  if (paper) {
    fitToA4(paper);
  }
}
window.addEventListener("beforeprint", fit);
print.addEventListener("click", () => {
  fit();
  window.print();
});

load().catch((error) => {
  status.textContent = error.message;
});
