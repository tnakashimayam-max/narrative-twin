/*
  役割：生活まとめプランの画面。下書きを頼み、確かめて直し、印刷か PDF で出す。下書きがなければ白紙の書式を出す。
  4つの極：4. 10年後に代弁する。
  守る原則：1（決めるのは人。専門職が直して保存する）、12（紙の札に必ず「ツイン（AI）」と出す）。

  紙の行の並び、列の名前、幅はサーバーの書式（forms/life_plan.json）から受け取る。ここで文を作らない。
  食事は朝食、昼食、夕食、飲水の4行に分かれるので、場面の欄を縦につなぐ。
*/

import { api } from "./api.js";
import { el, startWaiting, twoStepButton } from "./ui.js";

const sheetBox = document.getElementById("sheet");
const material = document.getElementById("material");
const status = document.getElementById("status");
const makeButton = document.getElementById("make");
const chooseLabel = document.getElementById("choose-label");
const choose = document.getElementById("choose");
const editButton = document.getElementById("edit");
const saveButton = document.getElementById("save");
const cancelButton = document.getElementById("cancel");
const printButton = document.getElementById("print");
const blankButton = document.getElementById("blank");
// 削除のボタンの置き場。「修正」の隣に置き、選んだ下書きが変わるたびに作り直す（2回押す形の状態を戻すため）
const removeSlot = el("span", {});
editButton.after(removeSlot);

const COLUMNS = [
  ["past", "今まで"],
  ["can", "出来ること"],
  ["cannot", "出来ないこと"],
  ["support", "支援して欲しいこと"],
  ["who", "誰が"],
];

let form = null;
let blankLabel = "";
let plans = [];
let current = null;
let editing = false;

function dateWords(iso) {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

/* 白紙の行。紙の書式の並びのまま、列はすべて空にする */
function blankRows() {
  return form.rows.flatMap((row) =>
    row.parts?.length ? row.parts.map((part) => ({ area: row.name, part })) : [{ area: row.name, part: null }]
  );
}

function linesOf(area) {
  return form.rows.find((row) => row.name === area)?.lines || [area];
}

function cell(row, key, rowIndex) {
  const text = row[key] || "";
  const td = el("td", { class: `${key}${text ? "" : " empty"}` });
  if (editing) {
    const box = el("textarea", { "aria-label": `${row.part || row.area}の${COLUMNS.find(([name]) => name === key)[1]}`, maxLength: 300 });
    box.value = text;
    box.dataset.row = String(rowIndex);
    box.dataset.key = key;
    td.append(box);
  } else {
    td.textContent = text;
  }
  return td;
}

function drawTable(rows) {
  const widths = form.columns.map((column) => column.width);
  // 場面の幅を、場面の名前と食事の分けの2つに割る
  const colgroup = el(
    "colgroup",
    {},
    el("col", { style: `width:${(widths[0] * 0.62).toFixed(1)}%` }),
    el("col", { style: `width:${(widths[0] * 0.38).toFixed(1)}%` }),
    ...widths.slice(1).map((width) => el("col", { style: `width:${width}%` }))
  );
  const head = el(
    "thead",
    {},
    el("tr", {}, el("th", { colspan: "2", text: "場面" }), ...COLUMNS.map(([key, name]) => el("th", { class: key, text: name })))
  );
  const body = el("tbody");
  rows.forEach((row, index) => {
    const tr = el("tr");
    if (row.part) {
      const first = index === 0 || rows[index - 1].area !== row.area;
      if (first) {
        const span = rows.filter((other) => other.area === row.area).length;
        tr.append(el("th", { class: "area", rowspan: String(span), scope: "rowgroup" }, ...linesOf(row.area).map((line) => el("span", { text: line }))));
      }
      tr.append(el("th", { class: "part", scope: "row", text: row.part }));
    } else {
      tr.append(el("th", { class: "area", colspan: "2", scope: "row" }, ...linesOf(row.area).map((line) => el("span", { text: line }))));
    }
    COLUMNS.forEach(([key]) => tr.append(cell(row, key, index)));
    body.append(tr);
  });
  return el("table", { class: "plan-table" }, colgroup, head, body);
}

function drawSheet() {
  const rows = current ? current.rows : blankRows();
  const meta = el(
    "dl",
    { class: "sheet-meta" },
    el("dt", { text: "ご利用者名" }),
    el("dd", {}, current ? current.person_name || current.self_name.replace(/さん$/, "") : "", el("span", { class: "honor", text: "様" })),
    el("dt", { text: "作成日" }),
    el("dd", { text: current ? dateWords(current.written_on) : "　　　　年　　月　　日" }),
    el("dt", { text: "作成者" }),
    // 作った時点の作成者はツイン（AI）。専門職が直したら、直した人を添える（帳票なので「さん」を付けない）
    el("dd", { text: current ? `${current.author}${current.editors?.length ? `（修正：${current.editors.join("、")}）` : ""}` : "" })
  );
  const sheet = el(
    "article",
    { class: "sheet", "aria-label": "生活まとめプラン" },
    el(
      "header",
      { class: "sheet-head" },
      el("div", {}, el("h2", { class: "sheet-title", text: form.title }), el("p", { class: "sheet-sub", text: "暮らしの場面ごとの、今までと今、そして支えてほしいこと" })),
      meta
    ),
    drawTable(rows),
    el(
      "footer",
      { class: "sheet-foot" },
      el("p", { class: "label", text: current ? current.label : blankLabel }),
      el("p", { class: "brand", text: "Narrative Twin" })
    )
  );
  sheetBox.replaceChildren(sheet);
}

function drawMaterial() {
  if (!current) {
    material.replaceChildren();
    return;
  }
  material.replaceChildren(
    el("h2", { text: "ツイン（AI）がもとにしたピース（紙には載りません）" }),
    current.pieces?.length ? el("ul", {}, ...current.pieces.map((piece) => el("li", { text: `${piece.kind}：${piece.body}` }))) : el("p", { class: "hint", text: "ありません。" }),
    current.reading ? el("p", { class: "hint", text: current.reading }) : ""
  );
}

function drawButtons() {
  chooseLabel.hidden = !plans.length;
  editButton.hidden = !current || editing;
  saveButton.hidden = !editing;
  cancelButton.hidden = !editing;
  printButton.disabled = editing;
  blankButton.hidden = editing;
  // 作った下書きは「削除」で消せる。取り消せないので2回押す形にする（app/made_documents.py）
  removeSlot.replaceChildren(
    current && !editing
      ? twoStepButton("削除", "本当に削除する", async () => {
          try {
            await api(`/advocacy/made/life_plan/${current.id}`, { method: "DELETE" });
          } catch (error) {
            status.textContent = error.message;
            return;
          }
          await load();
          status.textContent = "下書きを削除しました。";
        }, "quiet", "いま出ている下書きを消します（取り消せません）")
      : ""
  );
}

function draw() {
  drawSheet();
  drawMaterial();
  drawButtons();
}

function fillChoices() {
  choose.replaceChildren(
    ...plans.map((plan) => el("option", { value: String(plan.id), text: `${dateWords(plan.created_at)}${plan.edited ? "（修正した版）" : ""}` }))
  );
  if (current) {
    choose.value = String(current.id);
  }
}

async function load(selectId = null) {
  const blank = await api("/life-plan-form");
  form = blank.form;
  blankLabel = blank.label;
  try {
    ({ items: plans } = await api("/advocacy/life-plans"));
    makeButton.hidden = false;
  } catch {
    // ケアプランのツールを持たない人には、白紙の書式だけを出す
    plans = [];
  }
  current = plans.find((plan) => plan.id === selectId) || plans[0] || null;
  fillChoices();
  draw();
  if (!current && !makeButton.hidden) {
    status.textContent = "まだ下書きがありません。白紙の書式を出しています。";
  }
}

makeButton.addEventListener("click", async () => {
  makeButton.disabled = true;
  startWaiting(status, "ツイン（AI）が下書きしています。1分ほどかかります。");
  try {
    const { plan } = await api("/advocacy/life-plans", { method: "POST", json: { client_key: crypto.randomUUID() } });
    await load(plan.id);
    status.textContent = "下書きができました。確かめて、違うところがあれば「修正」を押してください。";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    makeButton.disabled = false;
  }
});

choose.addEventListener("change", () => {
  current = plans.find((plan) => String(plan.id) === choose.value) || null;
  editing = false;
  draw();
});

editButton.addEventListener("click", () => {
  editing = true;
  draw();
  status.textContent = "表の中を修正して、「修正したところを保存」を押してください。前の版は残ります。";
});

cancelButton.addEventListener("click", () => {
  editing = false;
  draw();
  status.textContent = "";
});

saveButton.addEventListener("click", async () => {
  const rows = current.rows.map((row) => ({ area: row.area, part: row.part, past: row.past, can: row.can, cannot: row.cannot, support: row.support, who: row.who }));
  sheetBox.querySelectorAll("textarea").forEach((box) => {
    rows[Number(box.dataset.row)][box.dataset.key] = box.value.trim();
  });
  saveButton.disabled = true;
  try {
    const { plan } = await api(`/advocacy/life-plans/${current.id}/edit`, { method: "POST", json: { rows } });
    editing = false;
    await load(plan.id);
    status.textContent = "修正したところを保存しました。";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    saveButton.disabled = false;
  }
});

printButton.addEventListener("click", () => window.print());

blankButton.addEventListener("click", () => {
  current = null;
  editing = false;
  draw();
  status.textContent = "白紙の書式です。このまま印刷できます。作った下書きは「作った日」から選べます。";
});

load().catch((error) => {
  status.textContent = error.message;
});
