// 役割：評価の質問と正解を入力し、一覧で確認する。
// 4つの極：特定の極に属さない土台（評価の道具）。
// 守る原則：8（架空の人物についての質問だけを扱う）。
//
// サーバーとのやり取りの取り決めは docs/api.md にある。
"use strict";

const form = document.getElementById("case-form");
const idInput = document.getElementById("case-id");
const questionInput = document.getElementById("case-question");
const answerInput = document.getElementById("case-answer");
const typeInput = document.getElementById("case-type");
const statusLine = document.getElementById("case-status");
const list = document.getElementById("case-list");

function fillForm(item) {
  idInput.value = item.id;
  questionInput.value = item.question;
  answerInput.value = item.correct_answer;
  typeInput.value = item.question_type;
  statusLine.textContent = "この質問を修正しています。";
  questionInput.focus();
}

function renderCases(items) {
  list.replaceChildren(
    ...items.map((item) => {
      const row = document.createElement("li");
      const question = document.createElement("p");
      question.textContent = `【${item.question_type}】${item.question}`;
      const answer = document.createElement("p");
      answer.textContent = item.correct_answer ? `正解：${item.correct_answer}` : "正解：まだありません";
      const edit = document.createElement("button");
      edit.type = "button";
      edit.textContent = "この質問を修正する";
      // 吹き出しの言葉（static/tips.js が出す）
      edit.dataset.tip = "この質問と正解を上の欄に入れて、修正できるようにします";
      edit.addEventListener("click", () => fillForm(item));
      row.append(question, answer, edit);
      return row;
    }),
  );
}

async function refresh() {
  const response = await fetch("/api/eval/cases");
  renderCases((await response.json()).items);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = JSON.stringify({
    question: questionInput.value,
    correct_answer: answerInput.value,
    question_type: typeInput.value,
  });
  const editing = idInput.value !== "";
  const response = await fetch(editing ? `/api/eval/cases/${idInput.value}` : "/api/eval/cases", {
    method: editing ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!response.ok) {
    statusLine.textContent = "保存できませんでした。質問を確かめてください。";
    return;
  }
  form.reset();
  idInput.value = "";
  statusLine.textContent = "保存しました。";
  await refresh();
});

refresh().catch(() => {
  statusLine.textContent = "読み込めませんでした。再読み込みしてください。";
});
