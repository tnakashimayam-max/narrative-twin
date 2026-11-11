/*
  役割：専門職の書式（アセスメントシート、フェイスシート、ライフサポートプラン）の画面。
        左にこれまでの記録の一覧、右に選んだ記録の中身を出す（資料の画面と同じ形）。
        記録は開いて読み、直し（新しい版になる）、コピーして別の日の記録にし、削除できる。
        新しく入れるときは、JSON か PDF のファイルを読み込める（選ぶか、ドラッグ・アンド・ドロップ）。
  4つの極：4. 10年後に代弁する。
  守る原則：1（決めるのは人。読み込んだ中身は専門職が確かめてから保存する）、3（専門職の記録は本人の言葉と分ける）、
            5（直しても前の版は残す。削除も行は消さず印を足す）。

  欄の名前は「まとまり／欄」の形でサーバーに送る（app/care_forms.py の field_keys と同じ）。
  値が書式に合うかはサーバーが確かめる。ここでは選択肢の中からしか選べない形にする。
  JSON はこの画面の中で読む。PDF はサーバーでツイン（AI）が書式の欄に当てはめて読み、値を返すだけで保存しない。
  保存は2段にする。1回目で中身の数を見せ、2回目で記録に書き込む。
  直すときも記録の日付を変えられる。もとの日付の記録を残したまま別の日にも使うときはコピーを使う
  （tyumajinさん：同じ記録を違う日で使うことがある。修正でも日付を変えたい）。
*/

import { api } from "./api.js";
import { el, startWaiting, twoStepButton } from "./ui.js";

const layout = document.getElementById("cf-layout");
const newButton = document.getElementById("new-record");
const filterBox = document.getElementById("cf-filter");
const recordList = document.getElementById("records");
const emptyNote = document.getElementById("cf-empty");
const status = document.getElementById("status");
const pane = document.getElementById("cf-pane");
const narrow = window.matchMedia("(max-width: 760px)");

let forms = [];
let records = [];
// 一覧の絞り込み。空ならすべて
let filter = "";
let selectedId = null;

function formOf(formId) {
  return forms.find((form) => form.id === formId);
}

function choicesOf(form, field) {
  return field.choice_set ? form.choice_sets[field.choice_set] : field.choices || [];
}

function dateWords(iso) {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

function shortDate(iso) {
  const [, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${month}月${day}日`;
}

/* 「2036年4月2日」のような日付を YYYY-MM-DD にする。読めなければ空 */
function isoFromWords(text) {
  const match = /(\d{4})[年\/-](\d{1,2})[月\/-](\d{1,2})/.exec(text || "");
  return match ? `${match[1]}-${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}` : "";
}

/* 狭い画面では、一覧と記録のどちらかだけを出す */
function setMode(mode) {
  layout.dataset.mode = mode;
  if (narrow.matches) {
    window.scrollTo({ top: 0 });
  }
}

function backButton() {
  return el("button", { type: "button", class: "quiet cf-back", text: "一覧へ戻る", tip: "この記録を閉じて、これまでの記録の一覧へ戻ります", onclick: () => select(null) });
}

/* 書式の欄を、名前から引ける形にする。何件も書くまとまりは、まとまりの名前で引く */
function fieldMap(form) {
  const map = new Map();
  const walk = (items, path) =>
    items.forEach((item) => {
      if (item.type === "group" && item.repeat > 1) {
        map.set(item.name, { ...item, repeatGroup: true });
      } else if (item.type === "group") {
        walk(item.fields, [...path, item.name]);
      } else {
        map.set([...path, item.name].join("／"), item);
      }
    });
  form.sections.forEach((section) => walk(section.fields, []));
  return map;
}

/* ---- 一覧 ---- */

function drawFilter() {
  const choices = [["", "すべて"], ...forms.map((form) => [form.id, form.title])];
  filterBox.replaceChildren(
    ...choices.map(([id, label]) =>
      el("button", {
        type: "button",
        class: "cf-filter-chip",
        "aria-pressed": String(filter === id),
        text: label,
        tip: id ? `${label}の記録だけを一覧に出します` : "どの書式の記録も、一覧にすべて出します",
        onclick: () => {
          filter = id;
          drawFilter();
          drawList();
        },
      })
    )
  );
}

function entry(item) {
  const chips = [
    item.version > 1 ? el("span", { class: "cf-chip", text: `第${item.version}版（修正した版）` }) : "",
    item.copied_from_date ? el("span", { class: "cf-chip copy", text: `${shortDate(item.copied_from_date)}の記録のコピー` }) : "",
  ];
  return el(
    "li",
    {},
    el(
      "button",
      { type: "button", class: "cf-entry", "data-id": String(item.id), "aria-current": String(item.id === selectedId), tip: "この記録の中身を開いて読みます", onclick: () => select(item.id) },
      el("span", { class: "cf-entry-top" }, el("span", { class: "cf-entry-kind", text: item.title })),
      el("span", { class: "cf-entry-title", text: `${dateWords(item.record_date)}の記録` }),
      el("span", { class: "cf-entry-sub", text: `入力した人：${item.author || "専門職"}` }),
      el("span", { class: "cf-chips" }, ...chips)
    )
  );
}

function drawList() {
  const shown = records.filter((item) => !filter || item.form_id === filter);
  recordList.replaceChildren(...shown.map(entry));
  emptyNote.hidden = shown.length > 0;
  emptyNote.textContent = records.length ? "この書式の記録はまだありません。" : "まだ入れた記録はありません。「新しく入れる」から入れてください。";
}

async function loadList() {
  ({ items: records } = await api("/advocacy/care-forms/records"));
  drawList();
}

function markSelected() {
  recordList.querySelectorAll(".cf-entry").forEach((button) => button.setAttribute("aria-current", String(Number(button.dataset.id) === selectedId)));
}

function placeholder() {
  return el(
    "div",
    { class: "cf-empty-pane" },
    el("p", { text: "左の一覧から記録を選ぶと、ここに中身が出ます。修正、コピーして別の日に使う、削除も、ここからできます。" }),
    el("p", { class: "hint", text: "新しい記録は、左上の「新しく入れる」から入れてください。JSON や PDF のファイルからも読み込めます。" })
  );
}

/* 記録を選ぶ。右に中身を出し、一覧の印を動かす。id がなければ案内を出す */
async function select(id) {
  selectedId = id;
  markSelected();
  if (!id) {
    pane.replaceChildren(placeholder());
    setMode("list");
    return;
  }
  try {
    const { record } = await api(`/advocacy/care-forms/records/${id}`);
    pane.replaceChildren(recordView(record));
    setMode("detail");
  } catch (error) {
    status.textContent = error.status === 404 ? "この記録は削除されたか、見つかりません。" : error.message;
    await loadList();
    pane.replaceChildren(placeholder());
    setMode("list");
  }
}

/* ---- 記録の中身（読むだけ） ---- */

function valueText(value) {
  return Array.isArray(value) ? value.join("、") : String(value);
}

function repeatTable(group, rows) {
  const names = group.fields.map((field) => field.name);
  return el(
    "div",
    { class: "cf-value-row" },
    el("dt", { text: group.name }),
    el(
      "dd",
      {},
      el(
        "table",
        { class: "repeat-table cf-repeat" },
        el("tr", {}, ...names.map((name) => el("th", { text: name }))),
        ...rows.map((row) => el("tr", {}, ...names.map((name) => el("td", { text: row[name] || "" }))))
      )
    )
  );
}

/* 入っている欄だけを、まとまりごとに並べる。空の欄は出さない */
function filledRows(items, path, values, details) {
  return items.flatMap((item) => {
    if (item.type === "group" && item.repeat > 1) {
      const rows = (values[item.name] || []).filter((row) => row && Object.keys(row).length);
      return rows.length ? [repeatTable(item, rows)] : [];
    }
    if (item.type === "group") {
      const inner = filledRows(item.fields, [...path, item.name], values, details);
      return inner.length ? [el("div", { class: "cf-group-name", text: item.name }), ...inner] : [];
    }
    const key = [...path, item.name].join("／");
    const value = values[key];
    const detail = details[key];
    const empty = value === undefined || value === "" || (Array.isArray(value) && !value.length);
    if (empty && !detail) {
      return [];
    }
    return [
      el(
        "div",
        { class: "cf-value-row" },
        el("dt", { text: item.name }),
        el("dd", {}, empty ? "" : valueText(value), detail ? el("div", { class: "cf-detail-text", text: `詳細：${detail}` }) : "")
      ),
    ];
  });
}

function historyList(record) {
  return el(
    "details",
    { class: "cf-history" },
    el("summary", { text: `前の版（${record.earlier.length}）`, tip: "修正する前の版の一覧を開きます" }),
    el(
      "ul",
      {},
      ...record.earlier.map((item) =>
        el(
          "li",
          {},
          el("button", {
            type: "button",
            class: "quiet",
            text: `第${item.version}版　${dateWords(item.record_date)}の記録　${item.author || "専門職"}が入力${item.created_at ? `　保存：${dateWords(item.created_at)}` : ""}`,
            tip: "この版の中身を開いて読みます",
            onclick: () => select(item.id),
          })
        )
      )
    )
  );
}

function recordView(record) {
  const form = formOf(record.form_id);
  const latest = record.latest_id === record.id;
  const values = record.values || {};
  const details = record.details || {};
  const sections = form.sections
    .map((section) => {
      const rows = filledRows(section.fields, [], values, details);
      return rows.length ? el("section", { class: "cf-section" }, el("h3", { text: section.title }), el("dl", { class: "cf-values" }, ...rows)) : "";
    })
    .filter(Boolean);
  const actions = latest
    ? [
        el("button", { type: "button", class: "primary", text: "修正する", tip: "この記録を修正する欄を開きます（前の版も残ります）", onclick: () => openEditor("edit", record) }),
        el("button", { type: "button", text: "コピーして別の日に使う", tip: "この記録の中身を写して、別の日の記録を作る欄を開きます", onclick: () => openEditor("copy", record) }),
        twoStepButton("削除", "本当に削除する", () => removeRecord(record), "quiet cf-delete", "この記録を一覧から消します（取り消せません）"),
      ]
    : [el("button", { type: "button", class: "primary", text: "新しい版を開く", tip: "この記録を修正した、いちばん新しい版を開きます", onclick: () => select(record.latest_id) })];
  return el(
    "div",
    { class: "cf-detail" },
    el(
      "div",
      { class: "cf-detail-head" },
      backButton(),
      el(
        "div",
        { class: "cf-detail-title" },
        el("span", { class: "cf-entry-kind", text: record.title }),
        el("h2", { text: `${dateWords(record.record_date)}の${record.title}` }),
        el(
          "p",
          { class: "cf-detail-meta" },
          el("span", { text: `記録の日付：${dateWords(record.record_date)}` }),
          el("span", { text: `入力した人：${record.author || "専門職"}` }),
          el("span", { text: `第${record.version}版` }),
          record.created_at ? el("span", { text: `保存：${dateWords(record.created_at)}` }) : ""
        ),
        el(
          "div",
          { class: "cf-chips" },
          el("span", { class: "cf-chip private", text: "ご本人とご家族には見せない記録" }),
          record.copied_from_date ? el("span", { class: "cf-chip copy", text: `${shortDate(record.copied_from_date)}の記録のコピー` }) : "",
          latest ? "" : el("span", { class: "cf-chip old", text: "前の版です（読むだけ）" })
        )
      ),
      el("div", { class: "cf-actions" }, ...actions)
    ),
    el(
      "div",
      { class: "cf-detail-body" },
      latest
        ? el("p", { class: "hint", text: `入っている欄は ${Object.keys(values).length} か所です。空の欄は出していません。この記録が、資料のレビュー、ケアプランのチェック、生活まとめプランの材料になります。` })
        : el("p", { class: "read-note", text: "これは前の版です。資料のレビュー、ケアプランのチェック、生活まとめプランには、新しい版を使います。" }),
      ...(sections.length ? sections : [el("p", { class: "hint", text: "入っている欄はありません。" })]),
      record.earlier?.length ? historyList(record) : ""
    )
  );
}

async function removeRecord(record) {
  try {
    await api(`/advocacy/care-forms/records/${record.id}`, { method: "DELETE" });
  } catch (error) {
    status.textContent = error.message;
    return;
  }
  await loadList();
  await select(null);
  status.textContent = `${dateWords(record.record_date)}の${record.title}を削除しました。資料のレビュー、ケアプランのチェック、生活まとめプランの材料からも外れます。`;
}

/* ---- 入力（新しく入れる、修正、コピー） ---- */

/* 1つの欄の入力。選ぶ欄は丸いボタン、文は入力欄にする */
function control(form, field, key, values, details) {
  const value = values[key];
  let input;
  if (field.type === "select" || field.type === "multi_select") {
    const multi = field.type === "multi_select";
    const chosen = new Set(multi ? value || [] : value ? [value] : []);
    input = el(
      "div",
      { class: "chips", role: multi ? "group" : "radiogroup", "aria-label": key },
      ...choicesOf(form, field).map((choice) => {
        const box = el("input", { type: multi ? "checkbox" : "radio", name: key, value: choice });
        box.checked = chosen.has(choice);
        box.dataset.key = key;
        box.dataset.kind = field.type;
        // 1つ選ぶ欄は、もう一度押すと選んでいない状態に戻せるようにする
        if (!multi) {
          box.addEventListener("click", () => {
            if (box.dataset.was === "1") {
              box.checked = false;
              box.dataset.was = "";
            } else {
              box.closest(".chips").querySelectorAll("input").forEach((other) => (other.dataset.was = ""));
              box.dataset.was = "1";
            }
          });
          box.dataset.was = box.checked ? "1" : "";
        }
        return el("label", {}, box, choice);
      })
    );
  } else {
    input = field.type === "long_text" ? el("textarea", { "aria-label": key, maxLength: 2000 }) : el("input", { type: "text", "aria-label": key, maxLength: 2000 });
    input.value = value || "";
    input.dataset.key = key;
    input.dataset.kind = "text";
  }
  const parts = [input];
  if (field.detail) {
    const detail = el("textarea", { "aria-label": `${key}の詳細`, placeholder: "詳細", maxLength: 2000 });
    detail.value = details[key] || "";
    detail.dataset.detail = key;
    parts.push(el("div", { class: "detail-box" }, detail));
  }
  return el("div", { class: "field-row" }, el("div", { class: "field-name", text: field.name }), el("div", {}, ...parts));
}

/* 同じまとまりを何件も書く欄は、小さな表にする */
function repeatControl(group, values) {
  const names = group.fields.map((field) => field.name);
  const rows = values[group.name] || [];
  const body = Array.from({ length: group.repeat }, (_, index) =>
    el(
      "tr",
      {},
      ...names.map((name) => {
        const input = el("input", { type: "text", "aria-label": `${group.name}${index + 1}の${name}`, maxLength: 2000 });
        input.value = rows[index]?.[name] || "";
        input.dataset.group = group.name;
        input.dataset.index = String(index);
        input.dataset.name = name;
        return el("td", {}, input);
      })
    )
  );
  return el(
    "div",
    { class: "field-row" },
    el("div", { class: "field-name", text: group.name }),
    el("table", { class: "repeat-table" }, el("tr", {}, ...names.map((name) => el("th", { text: name }))), ...body)
  );
}

function fieldsOf(form, items, path, values, details) {
  return items.flatMap((item) => {
    if (item.type === "group" && item.repeat > 1) {
      return [repeatControl(item, values)];
    }
    if (item.type === "group") {
      return [el("div", { class: "group-name", text: item.name }), ...fieldsOf(form, item.fields, [...path, item.name], values, details)];
    }
    return [control(form, item, [...path, item.name].join("／"), values, details)];
  });
}

function collect(formBox) {
  const values = {};
  const details = {};
  formBox.querySelectorAll("[data-kind]").forEach((input) => {
    const key = input.dataset.key;
    if (input.dataset.kind === "multi_select") {
      values[key] = values[key] || [];
      if (input.checked) {
        values[key].push(input.value);
      }
    } else if (input.dataset.kind === "select") {
      if (input.checked) {
        values[key] = input.value;
      }
    } else if (input.value.trim()) {
      values[key] = input.value.trim();
    }
  });
  formBox.querySelectorAll("[data-detail]").forEach((box) => {
    if (box.value.trim()) {
      details[box.dataset.detail] = box.value.trim();
    }
  });
  formBox.querySelectorAll("[data-group]").forEach((input) => {
    const rows = (values[input.dataset.group] = values[input.dataset.group] || []);
    const index = Number(input.dataset.index);
    rows[index] = rows[index] || {};
    if (input.value.trim()) {
      rows[index][input.dataset.name] = input.value.trim();
    }
  });
  Object.keys(values).forEach((key) => {
    if (Array.isArray(values[key]) && values[key].length && typeof values[key][0] !== "string") {
      values[key] = values[key].filter((row) => row && Object.keys(row).length);
    }
    if (Array.isArray(values[key]) && !values[key].length) {
      delete values[key];
    }
  });
  return { values, details };
}

// saveTip は保存のボタンの吹き出し。1回目は中身の確かめを出し、2回目で書き込む
const MODE_WORDS = {
  new: { chip: "新しく入れる", save: "保存する", saveTip: "書き込む前に、入れた中身の確かめを出します" },
  edit: { chip: "修正", save: "修正して新しい版で保存", saveTip: "中身を確かめてから、新しい版で残します（前の版も残ります）" },
  copy: { chip: "コピー", save: "コピーを保存する", saveTip: "中身を確かめてから、別の日の記録として残します" },
};
const CONFIRM_SAVE_TIP = "本人と家族には見せない記録として書き込みます";

/* 入力欄を右に出す。mode は new（新しく入れる）、edit（直す）、copy（コピーして別の日に使う）。
   record は直すかコピーするもとの記録。prefill はファイルから読んだ中身（新しく入れるときだけ） */
function openEditor(mode, record = null, { formId = null, prefill = null, note = null } = {}) {
  const form = formOf(record ? record.form_id : formId || filter || forms[0].id);
  const words = MODE_WORDS[mode];
  const source = prefill || record || {};
  const values = source.values || {};
  const details = source.details || {};
  let confirming = false;
  status.textContent = "";

  // 記録の日付。直すときは今の日付を入れておき、変えることもできる（tyumajinさんの決定）。コピーは空から入れる。
  // 新しく入れるときも、ファイルに日付がなければ空にする。パソコンの今日と、記録の中の日付（架空の2036年など）がずれることがあるため
  const dateInput = el("input", { type: "date", required: true, "aria-label": "記録の日付", id: "record-date" });
  dateInput.value = mode === "edit" ? record.record_date : mode === "new" ? source.record_date || "" : "";
  const dateHint = {
    edit: "日付を変えると、修正した版の日付が変わります。もとの日付の記録も残したいときは「コピーして別の日に使う」を使ってください。",
    copy: "コピーした記録を使う日を入れてください。",
  }[mode];
  const dateCell = el("div", {}, dateInput, dateHint ? el("p", { class: "hint", text: dateHint }) : "");

  const save = el("button", { type: "submit", class: "primary", text: words.save, tip: words.saveTip });
  const cancel = el("button", { type: "button", class: "quiet", text: "やめる", tip: "保存せずに入力をやめ、もとの画面へ戻ります", onclick: () => select(record ? record.id : null) });
  const confirmBox = el("div", { class: "confirm-box", hidden: true });
  const formBox = el(
    "form",
    { class: "care-form", novalidate: true },
    el("p", { class: "hint", text: form.use }),
    el("div", { class: "field-row" }, el("div", { class: "field-name", text: "記録の日付" }), dateCell),
    ...form.sections.map((section) => el("fieldset", {}, el("legend", { text: section.title }), ...fieldsOf(form, section.fields, [], values, details))),
    confirmBox,
    el("div", { class: "form-actions" }, save, cancel, el("span", { class: "hint", text: "ご本人とご家族には見せない記録です。" }))
  );

  // 確かめのあとで欄を直したら、もう一度確かめてもらう
  formBox.addEventListener("input", () => {
    if (confirming) {
      confirming = false;
      confirmBox.hidden = true;
      save.textContent = words.save;
      save.dataset.tip = words.saveTip;
    }
  });

  formBox.addEventListener("submit", async (event) => {
    event.preventDefault();
    const date = dateInput.value;
    if (!date) {
      status.textContent = "記録の日付を入れてください。";
      dateInput.focus();
      return;
    }
    const collected = collect(formBox);
    if (!confirming) {
      confirming = true;
      confirmBox.replaceChildren(
        el("strong", {
          text: `${dateWords(date)}の${form.title}として記録に書き込みますか。入っている欄は ${Object.keys(collected.values).length} か所、詳細は ${Object.keys(collected.details).length} か所です。`,
        }),
        el("p", { text: "ご本人とご家族には見せない記録です。違うところがあれば、欄を修正してから、もう一度押してください。" })
      );
      confirmBox.hidden = false;
      save.textContent = "この内容で記録に書き込む";
      save.dataset.tip = CONFIRM_SAVE_TIP;
      confirmBox.scrollIntoView({ block: "center" });
      status.textContent = "中身を確かめて、もう一度押すと記録に書き込みます。";
      return;
    }
    save.disabled = true;
    try {
      const { record: saved } = await api("/advocacy/care-forms/records", {
        method: "POST",
        json: {
          form_id: form.id,
          record_date: date,
          ...collected,
          replaces_id: mode === "edit" ? record.id : null,
          copied_from: mode === "copy" ? record.id : null,
          client_key: crypto.randomUUID(),
        },
      });
      await loadList();
      await select(saved.id);
      status.textContent = `記録に書き込みました。${mode === "edit" ? "前の版も残っています。" : ""}資料のレビュー、ケアプランのチェック、生活まとめプランの材料になります。`;
    } catch (error) {
      status.textContent = error.message;
      confirming = false;
      save.textContent = words.save;
      save.dataset.tip = words.saveTip;
    } finally {
      save.disabled = false;
    }
  });

  const heading =
    mode === "edit"
      ? `${dateWords(record.record_date)}の${form.title}を修正する`
      : mode === "copy"
        ? `${dateWords(record.record_date)}の${form.title}をコピーして、別の日に使う`
        : prefill
          ? `${form.title}（ファイルから読み込んだ中身。まだ保存していません）`
          : `${form.title}を新しく入れる`;
  const lead =
    mode === "edit"
      ? el("p", { class: "read-note", text: `保存すると第${record.version + 1}版になり、前の版も残ります。` })
      : mode === "copy"
        ? el("p", {
            class: "read-note",
            text: "中身はもとの記録と同じです。日付を入れ、違うところを修正してから保存してください。書式の中の日付の欄（調査実施日、作成日など）は、もとのままです。もとの記録はそのまま残ります。",
          })
        : "";

  pane.replaceChildren(
    el(
      "div",
      { class: "cf-detail" },
      el(
        "div",
        { class: "cf-detail-head" },
        backButton(),
        el("div", { class: "cf-detail-title" }, el("span", { class: "cf-entry-kind", text: words.chip }), el("h2", { text: heading }))
      ),
      el("div", { class: "cf-detail-body" }, mode === "new" ? newTop(form) : "", note ? el("div", { class: "read-note" }, ...note) : "", lead, formBox)
    )
  );
  setMode("detail");
}

/* 新しく入れるときの、書式を選ぶ所とファイルを読み込む所 */
function newTop(form) {
  const choice = el("select", { id: "form-choice" }, ...forms.map((item) => el("option", { value: item.id, text: item.title })));
  choice.value = form.id;
  choice.addEventListener("change", () => openEditor("new", null, { formId: choice.value }));
  const fileInput = el("input", { type: "file", accept: ".json,application/json,.pdf,application/pdf", hidden: true });
  const zone = el(
    "div",
    { class: "drop-zone", tabindex: "0", "aria-describedby": "drop-help" },
    el("p", { class: "drop-title", text: "ファイルから読み込む" }),
    el("p", {
      class: "hint",
      id: "drop-help",
      text: "JSON か PDF を、ここにドラッグ・アンド・ドロップするか、ファイルを選んでください。読み込んだ中身は下の欄に入ります。確かめてから保存するまで、記録にはなりません。",
    }),
    el("p", {
      class: "hint",
      text: "紙をスキャンしただけの PDF（文字の層がないもの）も、ツイン（AI）がページの画像から読みます。30ページまで、10MB までです。手書きやかすれた所は読み違えることがあるので、欄を確かめてください。",
    }),
    el("label", { class: "file-button", tip: "JSON か PDF のファイルを選んで、下の欄に読み込みます" }, "ファイルを選ぶ", fileInput)
  );
  fileInput.addEventListener("change", async () => {
    await readFile(fileInput.files[0], choice.value, zone);
    fileInput.value = "";
  });
  ["dragenter", "dragover"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.remove("over");
    })
  );
  zone.addEventListener("drop", (event) => readFile(event.dataTransfer.files[0], choice.value, zone));
  return el("div", { class: "cf-new-top" }, el("label", { class: "cf-form-choice" }, "書式", choice), zone);
}

/* ---- ファイルから読み込む ---- */

/* JSON を書式の欄に当てはめる。書式にない欄と、選択肢にない値は入れず、名前を返す */
function fitJson(form, spec) {
  const map = fieldMap(form);
  const values = {};
  const details = {};
  const skipped = [];
  Object.entries(spec.values || {}).forEach(([key, value]) => {
    const field = map.get(key);
    if (!field) {
      skipped.push(key);
    } else if (field.repeatGroup) {
      if (Array.isArray(value)) {
        values[key] = value.slice(0, field.repeat).filter((row) => row && typeof row === "object");
      }
    } else if (field.type === "select") {
      choicesOf(form, field).includes(value) ? (values[key] = value) : value && skipped.push(key);
    } else if (field.type === "multi_select") {
      values[key] = (Array.isArray(value) ? value : []).filter((choice) => choicesOf(form, field).includes(choice));
    } else if (typeof value === "string") {
      values[key] = value;
    }
  });
  Object.entries(spec.details || {}).forEach(([key, text]) => {
    map.get(key)?.detail && typeof text === "string" ? (details[key] = text) : skipped.push(`${key}の詳細`);
  });
  const recordDate = spec.record_date || isoFromWords(values["調査実施日"] || values["作成日"] || "");
  return { values, details, record_date: recordDate, skipped };
}

function noteLines(source, skipped, warning = "") {
  return [
    el("strong", { text: `${source}から読み込みました。中身を確かめて修正してから、保存してください。まだ記録にはなっていません。` }),
    warning ? el("p", { text: warning }) : "",
    skipped.length ? el("p", { text: `書式に合わず、入れなかった欄：${skipped.join("、")}` }) : "",
  ];
}

async function readJson(file, formId) {
  let spec;
  try {
    spec = JSON.parse(await file.text());
  } catch {
    status.textContent = "JSON として読めませんでした。ファイルを確かめてください。";
    return;
  }
  const named = spec.form || spec.form_id;
  const chosen = named && formOf(named) ? named : formId;
  const read = fitJson(formOf(chosen), spec.values ? spec : { values: spec });
  openEditor("new", null, { formId: chosen, prefill: read, note: noteLines(`JSON（${file.name}）`, read.skipped) });
  status.textContent = "JSON を読み込みました。確かめてから保存してください。";
}

async function readPdf(file, formId, zone) {
  startWaiting(status, `ツイン（AI）が PDF を読んでいます。1分ほどかかります（${file.name}）。`);
  zone.setAttribute("aria-busy", "true");
  try {
    const { read } = await api(`/advocacy/care-forms/read?form_id=${encodeURIComponent(formId)}&file_name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      body: file,
      headers: { "content-type": "application/pdf" },
    });
    const warning = read.name_matches ? "" : `資料の利用者の名前（${read.person_name}）が、ご本人と合いません。別の方の資料ではないか確かめてください。`;
    openEditor("new", null, { formId, prefill: read, note: noteLines(`PDF（${file.name}）`, read.skipped, warning) });
    status.textContent = "PDF を読み込みました。確かめてから保存してください。";
  } catch (error) {
    status.textContent = error.message;
  } finally {
    zone.removeAttribute("aria-busy");
  }
}

async function readFile(file, formId, zone) {
  if (!file) {
    return;
  }
  const name = file.name.toLowerCase();
  if (name.endsWith(".json") || file.type === "application/json") {
    await readJson(file, formId);
  } else if (name.endsWith(".pdf") || file.type === "application/pdf") {
    await readPdf(file, formId, zone);
  } else {
    status.textContent = "JSON か PDF のファイルを選んでください。";
  }
}

// 画面のほかの所に落としても、ブラウザがファイルを開いて画面が消えないようにする
["dragover", "drop"].forEach((type) => window.addEventListener(type, (event) => event.preventDefault()));

newButton.addEventListener("click", () => {
  selectedId = null;
  markSelected();
  openEditor("new");
});

async function load() {
  ({ items: forms } = await api("/advocacy/care-forms/forms"));
  drawFilter();
  await loadList();
  // 広い画面は、いちばん新しい記録を右に開く。狭い画面は一覧から始める
  if (records.length && !narrow.matches) {
    await select(records[0].id);
  } else {
    pane.replaceChildren(placeholder());
    setMode("list");
  }
}

load().catch((error) => {
  status.textContent = error.status === 404 ? "この画面は、ケアプランのツールを使える専門職だけが開けます。" : error.message;
  newButton.disabled = true;
});
