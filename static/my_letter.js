/*
  役割：本人が手紙を書くページの動き。
        「手紙を書く」：期間（今週・ひと月・期間を選ぶ）、送る相手（1人）、あらすじ（なくてもよい）を選んで下書きを頼む。
        下書きは便箋の紙に出し、文面を直す、ツインに校正してもらう、校正の前や前の版に戻す、送る、下書きを捨てる、ができる。
        文面を直している間は、直す欄のボタン（保存する、保存して校正してもらう、キャンセル）だけを出す。
        「これまでの手紙」：下書きと送った手紙の一覧。押すと開く。
        本人のほかの人が開くと、届いた手紙を読むだけの形になる。
        上の「PC／スマホ」で並べ方を切り替える。アプリの画面と同じ切り替え（static/device.js）で、選んだ形を覚える。
  4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
  守る原則：1（送るかは本人が決める。送るボタンは2回押す）、5（版を残し、いつでも戻せる）、7（催促の表示を作らない）、
            12（紙に「ツイン（AI）」と出す。札の文はサーバーが作る）。

  手紙の文はサーバーが返したものだけを出す。ここで文を作らない（app/self_letters.py）。
*/

import { api } from "./api.js";
import { startDevice } from "./device.js";
import { dateWords } from "./letter_paper.js";
import { el, startWaiting, twoStepButton } from "./ui.js";

// PC版とスマホ版の切り替え。並べ方は my-letter.css が body の data-device を見て変える
document.getElementById("ml-device").append(startDevice(() => {}));

const view = document.getElementById("ml-view");
const status = document.getElementById("status");
const switcher = document.getElementById("ml-switch");
const writeTab = document.getElementById("go-write");
const listTab = document.getElementById("go-list");
const lead = document.getElementById("ml-lead");

let me = null;
// いま出している手紙と、その出し方（読む／直す）。挿絵ができたとき、直している最中なら画面を描き直さないため
let current = null;
let currentMode = "read";
// ツインが挿絵を描いている手紙の番号
const drawingArt = new Set();

function say(text, error = false) {
  status.textContent = text;
  status.classList.toggle("ml-error", error);
}

function shortDate(iso) {
  const [, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${month}月${day}日`;
}

function setTab(name) {
  writeTab.setAttribute("aria-pressed", String(name === "write"));
  listTab.setAttribute("aria-pressed", String(name === "list"));
}

// 敬称が付いていればそのまま（健太くん、井ノ口先生に「さん」を重ねない）
function honor(name) {
  return /(さん|くん|ちゃん|先生|様)$/.test(name) ? name : `${name}さん`;
}

function toName(recipient) {
  return recipient ? honor(recipient.name) : "相手";
}

/* ---- 書く ---- */

async function drawWrite() {
  setTab("write");
  say("");
  current = null;
  let people = [];
  try {
    ({ items: people } = await api("/self-letters/recipients"));
  } catch (error) {
    say(error.message, true);
    return;
  }
  const periods = [
    ["今週", "今週（7日）"],
    ["ひと月", "この1か月"],
    ["期間", "期間を選ぶ"],
  ];
  const start = el("input", { type: "date", "aria-label": "期間の始まり" });
  const end = el("input", { type: "date", "aria-label": "期間の終わり" });
  const range = el("div", { class: "ml-range", hidden: true }, start, el("span", { text: "から" }), end, el("span", { text: "まで" }));
  const periodChips = el(
    "div",
    { class: "ml-chips", role: "radiogroup", "aria-label": "いつの話を書きますか" },
    ...periods.map(([value, label]) => {
      const input = el("input", { type: "radio", name: "period", value });
      // 最初は「この1か月」を選んでおく。1週間だと話が少なく、手紙が薄くなりやすいため（ご本人の決定、2026-11-07）
      input.checked = value === "ひと月";
      input.addEventListener("change", () => (range.hidden = value !== "期間"));
      return el("label", {}, input, label);
    })
  );
  const peopleBox = el(
    "div",
    { class: "ml-people", role: "radiogroup", "aria-label": "だれに送りますか" },
    ...people.map((person) => {
      const input = el("input", { type: "radio", name: "recipient", value: String(person.id) });
      return el(
        "label",
        { class: "ml-person" },
        input,
        el("strong", { text: person.name }),
        el("span", { text: person.is_deceased ? `${person.relation}（亡くなった方）` : person.relation })
      );
    })
  );
  const outline = el("textarea", {
    class: "ml-outline",
    maxLength: 600,
    "aria-label": "あらすじ",
    placeholder: "例：紫陽花が咲いたことと、健太の将棋のこと。夏に来るのを楽しみにしていると書きたい。",
  });
  const make = el("button", { type: "submit", class: "primary ml-make", text: "下書きを作る", tip: "選んだ期間の話をもとに、ツイン（AI）が手紙の下書きを書きます" });
  const form = el(
    "form",
    { class: "ml-card", novalidate: true },
    el("fieldset", { class: "ml-step" }, el("legend", {}, el("span", { class: "ml-step-no", text: "1" }), "いつの話を書きますか"), periodChips, range),
    el(
      "fieldset",
      { class: "ml-step" },
      el("legend", {}, el("span", { class: "ml-step-no", text: "2" }), "だれに送りますか"),
      people.length ? peopleBox : el("p", { class: "ml-note", text: "送る相手がまだいません。" })
    ),
    el(
      "div",
      { class: "ml-step" },
      el("label", { class: "ml-step-title", for: "ml-outline" }, el("span", { class: "ml-step-no", text: "3" }), "あらすじ（書かなくてもかまいません）"),
      outline,
      el("p", { class: "ml-note", text: "書いたことを骨組みにして、ツイン（AI）が手紙にします。" })
    ),
    make,
    el("p", { class: "ml-note", text: "ツイン（AI）が、この期間に話したことを読んで、あなたの言葉づかいで下書きを書きます。送る前に、読んで修正できます。" })
  );
  outline.id = "ml-outline";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const period = form.querySelector('input[name="period"]:checked').value;
    const chosen = form.querySelector('input[name="recipient"]:checked');
    if (!chosen) {
      say("だれに送るかを選んでください。", true);
      return;
    }
    if (period === "期間" && (!start.value || !end.value)) {
      say("期間の始まりと終わりを入れてください。", true);
      return;
    }
    make.disabled = true;
    status.classList.remove("ml-error");
    startWaiting(status, "ツイン（AI）が、この期間に話したことを読んで、手紙を書いています。1分ほどかかります。");
    try {
      const { letter } = await api("/self-letters", {
        method: "POST",
        json: {
          period,
          start: period === "期間" ? start.value : null,
          end: period === "期間" ? end.value : null,
          recipient_id: Number(chosen.value),
          outline: outline.value.trim(),
          client_key: crypto.randomUUID(),
        },
      });
      say("下書きができました。読んで、修正したいところがあれば「文面を修正する」を押してください。");
      drawLetter(letter);
      // 下書きができたら、この手紙に合う挿絵をツインが描く（2026-11-07 の依頼）
      drawArt(letter);
    } catch (error) {
      say(error.message, true);
    } finally {
      make.disabled = false;
    }
  });
  view.replaceChildren(form);
}

/* ---- 紙 ---- */

function paper(letter) {
  const content = letter.content;
  const paragraphs = content.body.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
  return el(
    "article",
    { class: "ml-paper", "aria-label": `${toName(letter.recipient)}への手紙` },
    el("p", { class: "ml-date", text: dateWords(letter.sent_at || letter.created_at) }),
    letter.illustration
      ? el(
          "figure",
          { class: drawingArt.has(letter.id) ? "ml-art drawing" : "ml-art" },
          el("img", { src: letter.illustration.src, alt: letter.illustration.title }),
          drawingArt.has(letter.id) ? el("figcaption", { class: "ml-art-wait", text: "ツイン（AI）が、この手紙に合う挿絵を描いています…" }) : ""
        )
      : "",
    el(
      "div",
      { class: "ml-lines" },
      el("p", { class: "ml-to", text: content.salutation }),
      el("div", { class: "ml-body" }, ...paragraphs.map((text) => el("p", { text }))),
      content.closing ? el("p", { class: "ml-closing", text: content.closing }) : "",
      content.signature ? el("p", { class: "ml-sign", text: content.signature }) : ""
    ),
    el("p", { class: "ml-foot", text: letter.label })
  );
}

function meta(letter) {
  return el(
    "div",
    { class: "ml-meta" },
    el("span", { class: letter.status === "送った" ? "ml-chip sent" : "ml-chip", text: letter.status === "送った" ? "送りました" : "下書き" }),
    el("span", { text: letter.versions ? `${toName(letter.recipient)}へ` : `${honor(letter.author)}から` }),
    el("span", { text: `${shortDate(letter.period_start)}〜${shortDate(letter.period_end)}の話から` })
  );
}

async function act(path, json) {
  try {
    const { letter } = await api(path, { method: "POST", ...(json ? { json } : {}) });
    drawLetter(letter);
    return letter;
  } catch (error) {
    say(error.message, true);
    return null;
  }
}

/* 校正を頼む。parts（直す欄の文）を渡すと、サーバーが先に「直し」の版として残してから校正する。
   直す欄に足した文を、校正で失わないため（2026-11-07 の直し）。 */
async function proofread(letter, parts = null) {
  status.classList.remove("ml-error");
  startWaiting(status, "ツイン（AI）が、言葉を整えています。文は消さずに、整えるだけです。");
  const proofed = await act(`/self-letters/${letter.id}/proofread`, parts);
  if (proofed) {
    if (proofed.proofread_dropped) {
      say("校正で文が大きく変わったので、その校正は使いませんでした。文面はそのままです。", true);
    } else if (proofed.proofread_changed) {
      say("校正しました。修正したところを確かめてください。気に入らなければ「校正の前に戻す」で戻せます。");
    } else {
      say(parts ? "修正を保存しました。校正で修正するところは見つかりませんでした。" : "修正するところは見つかりませんでした。このままで大丈夫です。");
    }
  }
  return proofed;
}

/* ツインに、今の文面に合う挿絵を描いてもらう。描いている間も、読んだり直したりできる。
   できたときに同じ手紙を読む形で出していれば、紙を描き直す。直している最中なら、直す欄を消さずに知らせだけ出す。 */
async function drawArt(letter) {
  if (drawingArt.has(letter.id)) {
    return;
  }
  drawingArt.add(letter.id);
  const showing = () => current?.id === letter.id && currentMode === "read";
  if (showing()) {
    drawLetter(current);
  }
  try {
    const { letter: drawn } = await api(`/self-letters/${letter.id}/art`, { method: "POST" });
    drawingArt.delete(letter.id);
    if (showing()) {
      drawLetter(drawn);
      say(`挿絵を描きました（${drawn.illustration.title}）。気に入らなければ「挿絵を描き直す」を押してください。`);
    } else if (current?.id === letter.id) {
      current = { ...current, illustration: drawn.illustration };
      say("挿絵を描きました。修正を保存すると、紙に出ます。");
    }
  } catch (error) {
    drawingArt.delete(letter.id);
    if (showing()) {
      drawLetter(current);
    }
    say(error.message, true);
  }
}

/* 文面を直す欄。開いている間は、紙と上のボタン（校正、送る）を隠し、この欄のボタンだけにする。
   保存しないまま校正や送るを押して、直した文が消えることがないようにするため。 */
function editForm(letter) {
  const content = letter.content;
  const field = (label, value, max, multi = false) => {
    const input = multi ? el("textarea", { maxLength: max }) : el("input", { type: "text", maxLength: max });
    input.value = value || "";
    return [input, el("label", {}, label, input)];
  };
  const [salutation, salutationLabel] = field("宛名", content.salutation, 40);
  const [body, bodyLabel] = field("本文（段落の間は1行あけてください）", content.body, 1600, true);
  const [closing, closingLabel] = field("結び", content.closing, 200);
  const [signature, signatureLabel] = field("署名", content.signature, 40);
  const typed = () => ({ salutation: salutation.value, body: body.value, closing: closing.value, signature: signature.value });
  const changed = () => Object.entries(typed()).some(([key, value]) => value.trim() !== (content[key] || "").trim());

  const save = el("button", { type: "submit", class: "primary", text: "修正を保存する", tip: "修正した文面を新しい版で残します（前の版にも戻れます）" });
  const saveAndProof = el("button", {
    type: "button",
    text: "保存して、ツインに校正してもらう",
    tip: "いま書いた文を先に残してから、ツイン（AI）が誤字や読みにくい所を整えます",
  });
  // キャンセル：直していなければすぐ戻る。直していたら、もう一度押して確かめてから戻る
  let armed = false;
  const cancel = el("button", {
    type: "button",
    class: "quiet",
    text: "キャンセル",
    tip: "修正したところを残さずに、もとの文面に戻ります",
    onclick: () => {
      if (changed() && !armed) {
        armed = true;
        cancel.textContent = "修正した文を捨てて戻る";
        say("修正した文はまだ保存していません。もう一度押すと、修正する前の文面に戻ります。", true);
        return;
      }
      say("修正するのをやめました。文面は前のままです。");
      drawLetter(letter);
    },
  });
  const buttons = [save, saveAndProof, cancel];
  const form = el(
    "form",
    { class: "ml-edit", novalidate: true, "aria-label": "文面を修正する" },
    el("h2", { class: "ml-edit-title", text: "文面を修正する" }),
    salutationLabel,
    bodyLabel,
    closingLabel,
    signatureLabel,
    el("div", { class: "ml-actions" }, ...buttons),
    el("p", { class: "ml-note", text: "「保存して、ツインに校正してもらう」は、いま書いた文を先に残してから、誤字や読みにくい所と、残っている方言を整えます。文は消しません。" })
  );
  const filled = () => {
    if (!salutation.value.trim() || !body.value.trim()) {
      say("宛名と本文は空にできません。", true);
      return false;
    }
    return true;
  };
  const busy = (value) => buttons.forEach((button) => (button.disabled = value));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!filled()) {
      return;
    }
    busy(true);
    const saved = await act(`/self-letters/${letter.id}/edit`, typed());
    if (saved) {
      say(saved.version > letter.version ? `修正を保存しました（第${saved.version}版）。前の版にも戻せます。` : "文面は変わっていません。");
    } else {
      busy(false);
    }
  });
  saveAndProof.addEventListener("click", async () => {
    if (!filled()) {
      return;
    }
    busy(true);
    const proofed = await proofread(letter, typed());
    if (!proofed) {
      busy(false);
    }
  });
  return form;
}

// 版の種類の画面での呼び名。サーバーの値（下書き／直し／校正／戻す）はデータベースの決まりなので変えず、
// 画面に出すときだけ「直し」を「修正」と呼ぶ（tyumajinさんの決定、2026-11-08）
const KIND_WORDS = { 直し: "修正" };

function versionList(letter) {
  const items = [...letter.versions].reverse();
  return el(
    "details",
    { class: "ml-versions" },
    el("summary", { text: `これまでの版（${letter.versions.length}）`, tip: "開くと、これまでの版が並び、好きな版に戻せます" }),
    el(
      "ul",
      {},
      ...items.map((item) =>
        el(
          "li",
          {},
          el("span", { text: `第${item.version}版　${KIND_WORDS[item.kind] ?? item.kind}　${dateWords(item.created_at)}` }),
          item.version === letter.version
            ? el("span", { class: "ml-chip", text: "いまの文面" })
            : el("button", {
                type: "button",
                class: "quiet",
                text: "この版に戻す",
                tip: "この版の文面を、いまの文面にします。ほかの版も残ります",
                onclick: async () => {
                  const back = await act(`/self-letters/${letter.id}/revert`, { to_version: item.version });
                  if (back) {
                    say(`第${item.version}版の文面に戻しました。戻す前の版にも、またいつでも戻せます。`);
                  }
                },
              })
        )
      )
    )
  );
}

function drawLetter(letter, mode = "read") {
  const mine = me && letter.versions;
  // 挿絵を描いている間に直しを保存しても、描き終えた絵を残す
  if (current?.id === letter.id && current.illustration?.drawn && !letter.illustration?.drawn) {
    letter = { ...letter, illustration: current.illustration };
  }
  current = letter;
  currentMode = mine && mode === "edit" && letter.status !== "送った" ? "edit" : "read";
  if (currentMode === "edit") {
    // 直している間は、直す欄だけを出す
    view.replaceChildren(meta(letter), editForm(letter));
    view.querySelector("textarea")?.focus();
    return;
  }
  const parts = [meta(letter)];
  if (letter.status === "送った") {
    parts.push(el("p", { class: "ml-sent-note", text: mine ? `${dateWords(letter.sent_at)}に送りました。送った手紙は修正できません。` : `${honor(letter.author)}からの手紙です。` }));
  }
  if (mine && letter.status !== "送った") {
    // 校正のあとは、何を直したかと「校正の前に戻す」を出す
    if (letter.kind === "校正") {
      parts.push(
        el(
          "section",
          { class: "ml-proof" },
          el("h3", { text: "ツイン（AI）が校正で修正したところ" }),
          letter.notes.length ? el("ul", {}, ...letter.notes.map((note) => el("li", { text: note }))) : el("p", { text: "言葉を整えました。" }),
          el("button", {
            type: "button",
            text: "校正の前に戻す",
            tip: "ツイン（AI）が校正する前の文面に戻します",
            onclick: async () => {
              const back = await act(`/self-letters/${letter.id}/revert`, { to_version: letter.version - 1 });
              if (back) {
                say("校正の前の文面に戻しました。校正したほうにも、「これまでの版」から戻れます。");
              }
            },
          })
        )
      );
    }
    const proofButton = el("button", {
      type: "button",
      text: "ツインに校正してもらう",
      tip: "ツイン（AI）が言葉を整えます。修正したところは下に出ます",
      onclick: async () => {
        proofButton.disabled = true;
        await proofread(letter);
        proofButton.disabled = false;
      },
    });
    const sendButton = twoStepButton(
      "送る",
      `本当に送る（送ったら修正できません）`,
      async () => {
        const sent = await act(`/self-letters/${letter.id}/send`);
        if (sent) {
          say(`${toName(sent.recipient)}へ送りました。「これまでの手紙」から、いつでも見返せます。`);
        }
      },
      "ml-send",
      "選んだ相手に手紙を届けます。送ったあとは修正できません"
    );
    parts.push(
      el(
        "div",
        { class: "ml-actions" },
        el("button", {
          type: "button",
          class: "primary",
          text: "文面を修正する",
          tip: "宛名や本文、結びを、自分の言葉で修正する欄を開きます",
          onclick: () => {
            say("修正したら「修正を保存する」を押してください。やめるときは「キャンセル」です。");
            drawLetter(letter, "edit");
          },
        }),
        proofButton,
        el("button", {
          type: "button",
          text: drawingArt.has(letter.id) ? "挿絵を描いています…" : letter.illustration?.drawn ? "挿絵を描き直す" : "この手紙の挿絵を描く",
          disabled: drawingArt.has(letter.id),
          tip: "ツイン（AI）が、いまの文面に合う挿絵を水彩の味わいで描きます",
          onclick: () => drawArt(letter),
        }),
        sendButton
      )
    );
  }
  parts.push(paper(letter));
  if (mine && letter.versions.length > 1 && letter.status !== "送った") {
    parts.push(versionList(letter));
  }
  if (mine && letter.status !== "送った") {
    // いらない下書きを捨てる。送った手紙は捨てられない
    parts.push(
      el(
        "div",
        { class: "ml-discard" },
        twoStepButton(
          "この下書きを捨てる",
          "本当に捨てる（一覧から消えます）",
          async () => {
            try {
              await api(`/self-letters/${letter.id}`, { method: "DELETE" });
              await drawList();
              say("下書きを捨てました。");
            } catch (error) {
              say(error.message, true);
            }
          },
          "quiet",
          "まだ送っていないこの下書きを、一覧から消します。2回押すと消えます"
        )
      )
    );
  }
  view.replaceChildren(...parts);
}

/* ---- これまでの手紙 ---- */

function entry(item, open) {
  const sent = item.status === "送った";
  return el(
    "li",
    {},
    el(
      "button",
      { type: "button", class: sent ? "ml-entry sent" : "ml-entry", tip: "この手紙を開いて読みます", onclick: () => open(item.id) },
      el("span", { class: "ml-entry-to", text: me?.role === "本人" ? `${toName(item.recipient)}へ` : `${honor(item.author)}から` }),
      el("span", { class: sent ? "ml-chip sent" : "ml-chip", text: sent ? "送りました" : "下書き" }),
      el("span", { class: "ml-entry-sub", text: `${dateWords(item.sent_at || item.created_at)}　${shortDate(item.period_start)}〜${shortDate(item.period_end)}の話から` }),
      item.first_line ? el("span", { class: "ml-entry-first", text: item.first_line }) : ""
    )
  );
}

async function openLetter(id) {
  try {
    const { letter } = await api(`/self-letters/${id}`);
    say("");
    drawLetter(letter);
  } catch (error) {
    say(error.status === 404 ? "この手紙は見つかりません。" : error.message, true);
  }
}

async function drawList() {
  setTab("list");
  say("");
  // 手紙を閉じた。描いている途中の挿絵ができても、この画面は描き直さない
  current = null;
  try {
    const { items } = await api(me?.role === "本人" ? "/self-letters" : "/self-letters/inbox");
    view.replaceChildren(
      items.length
        ? el("ul", { class: "ml-list" }, ...items.map((item) => entry(item, openLetter)))
        : el("p", { class: "ml-empty", text: me?.role === "本人" ? "まだ手紙はありません。「手紙を書く」から書いてみてください。" : "届いた手紙はまだありません。" })
    );
  } catch (error) {
    say(error.message, true);
  }
}

writeTab.addEventListener("click", drawWrite);
listTab.addEventListener("click", drawList);

async function start() {
  me = await api("/me");
  const id = Number(new URLSearchParams(location.search).get("id"));
  if (me.role !== "本人") {
    // 送られた相手は、届いた手紙を読むだけ
    lead.textContent = "届いた手紙です。";
    if (id) {
      await openLetter(id);
    } else {
      await drawList();
    }
    return;
  }
  switcher.hidden = false;
  if (id) {
    setTab("list");
    await openLetter(id);
  } else {
    await drawWrite();
  }
}

start().catch((error) => say(error.message, true));
