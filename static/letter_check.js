/*
  役割：ご本人か、託されたご家族が、手紙の下書きを確かめる画面。直して、返すか、出さないかを決める。
  4つの極：4. 10年後に代弁する。
  守る原則：1（決めるのはご本人とご家族）、9（引いた言葉は記録のまま。ここでは直せない）、12（札に必ず「ツイン（AI）」と出す）。

  直したところは、返したときに、直した人の言葉として記録に入る（app/advocacy_letter.py）。
  「出さない」は2回押して決める。取り消せないため。
*/

import { api } from "./api.js";
import { el, twoStepButton } from "./ui.js";
import { drawPaper } from "./letter_paper.js";

const paperBox = document.getElementById("paper");
const buttons = document.getElementById("buttons");
const status = document.getElementById("status");
const lead = document.getElementById("lead");

function fail(error) {
  // 確かめられない人（専門職など）が開いたときは、だれのための画面かを伝える
  status.textContent = error.status === 404 ? "この画面は、ご本人か託されたご家族が、手紙の下書きを確かめる画面です。" : error.message;
}

function finished(letter) {
  paperBox.replaceChildren(drawPaper(letter));
  buttons.replaceChildren();
  status.textContent = letter.status === "返した" ? `${letter.recipient}に返しました。` : "この手紙は出しません。";
}

function draw(letter) {
  if (letter.status !== "確かめ待ち") {
    finished(letter);
    return;
  }
  lead.textContent = `${letter.recipient}への手紙の下書きです。ツイン（AI）が、${letter.self_name}に代わって書きました。違うところは、そのまま修正してください。下の「前に話したこと」は記録のままなので、修正できません。`;
  const paper = drawPaper(letter, { editable: true });
  paperBox.replaceChildren(paper);

  async function save() {
    const { letter: next } = await api(`/letters/${letter.id}/edit`, { method: "POST", json: paper.readEdits() });
    return next;
  }

  const keep = el("button", {
    type: "button",
    text: "修正したところを残す",
    tip: "修正したところを保存します（まだ相手には返しません）",
    onclick: async () => {
      try {
        const next = await save();
        paper.setLabel(next.label);
        status.textContent = "修正したところを残しました。まだ返していません。";
      } catch (error) {
        fail(error);
      }
    },
  });
  const send = el("button", {
    type: "button",
    class: "primary",
    text: `${letter.recipient}に返す`,
    tip: "修正したところも含めて、この手紙を相手に届けます",
    onclick: async () => {
      send.disabled = true;
      try {
        await save();
        const { letter: done } = await api(`/letters/${letter.id}/return`, { method: "POST" });
        finished(done);
      } catch (error) {
        send.disabled = false;
        fail(error);
      }
    },
  });
  const decline = twoStepButton("出さない", "本当に出さない（取り消せません）", async () => {
    try {
      const { letter: done } = await api(`/letters/${letter.id}/decline`, { method: "POST" });
      finished(done);
    } catch (error) {
      fail(error);
    }
  }, "quiet", "この手紙を相手に届けないと決めます（取り消せません）");
  buttons.replaceChildren(keep, send, decline);
}

async function load() {
  const id = new URLSearchParams(location.search).get("id");
  let letterId = id ? Number(id) : null;
  if (!letterId) {
    const { items } = await api("/letters/pending");
    if (!items.length) {
      status.textContent = "確かめ待ちの手紙はありません。";
      return;
    }
    letterId = items[0].id;
  }
  const { letter } = await api(`/letters/${letterId}/check`);
  draw(letter);
}

load().catch(fail);
