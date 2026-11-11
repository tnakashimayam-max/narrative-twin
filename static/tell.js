/*
  役割：専門職がツインに伝えたこと（チャットと資料）と、手紙を頼んだ知らせを描く部品。
  4つの極：3. 読み解き、積み上げる（伝えと資料）、4. 10年後に代弁する（手紙）。
  守る原則：1（残すかは人が決める。押すまで記録にならない）、3（今の様子は本人の言葉と分ける）、
            12（必ず「ツイン（AI）」と出す）。

  資料を渡すと、まずツインのレビューを文章で出す。記録に残す候補はその下にたたみ、資料の場面ごとに、どこから読んだかと原文を添えて出す。
  チェックを付けたものだけが、今の様子として記録に入る。
  記録は消せないので、押す前に文を直せるようにする。取り決めは docs/api.md にある。
*/

import { api } from "./api.js";
import { el, startWaiting, twoStepButton } from "./ui.js";

// 項目の種類の短い名前。聞き取りは本人の言葉そのものではないことを、名前で分かるようにする
const KIND_SHORT = {
  観察: "見たこと",
  本人の思いの聞き取り: "本人の思い（聞き取り）",
  家族の思いの聞き取り: "家族の思い（聞き取り）",
};

/* 資料のどこから読んだか（ページと場面） */
function whereOf(item) {
  return [item.page ? `${item.page}ページ` : "", item.section].filter(Boolean).join("・");
}

function decidedLine(item) {
  return el(
    "li",
    { class: `cand cand-done tell-${item.status}` },
    el("span", { class: "tell-kind", text: KIND_SHORT[item.kind] || item.kind }),
    el("span", { class: "cand-text", text: ` ${item.text}` }),
    el("span", { class: "hint", text: item.status === "残した" ? "（記録に残しました）" : "（残しませんでした）" })
  );
}

/* 記録に残す候補の一覧。チェックを付けたものを残し、ほかは残さない。文はその場で直せる。
   1項目ずつ3つのボタンで決める形は、どこを言っているか分からず、判断の数も多すぎた（tyumajinさん、2026-11-06）。
   grouped のときは、資料の場面ごとにまとめる。 */
export function drawTellItems(items, lead = "記録に残す候補です。残すものにチェックを付けてください。", { grouped = false, onDecided } = {}) {
  const frame = el("section", { class: "tell-frame candidates" });
  function render(current) {
    if (!current.length) {
      frame.replaceChildren(el("p", { class: "hint", text: "ツイン（AI）は、記録に残す候補を見つけませんでした。" }));
      return;
    }
    const waiting = current.filter((item) => item.status === "確かめ待ち");
    const decided = current.filter((item) => item.status !== "確かめ待ち");
    const rows = [];
    const count = el("span", { class: "cand-count" });
    const save = el("button", { type: "button", class: "primary" });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    const all = el("button", { type: "button", class: "quiet" });
    // 何も選ばずに決めると、全部が「残さない」に決まり、決め直せない。目立つ朱にせず、2回押す形にする
    let armedNone = false;
    const refresh = () => {
      const chosen = rows.filter((row) => row.check.checked).length;
      armedNone = false;
      count.textContent = `${waiting.length}件のうち ${chosen}件を選んでいます`;
      save.className = chosen ? "primary" : "quiet";
      save.textContent = chosen ? `チェックした${chosen}件を記録に残す（ほかは残さない）` : "どれも残さない";
      save.dataset.tip = chosen ? "チェックした候補を記録に残し、ほかは残さないと決めます" : "どの候補も記録に残さないと決めます。決め直せません";
      all.textContent = chosen === rows.length ? "すべて外す" : "すべて選ぶ";
      all.dataset.tip = chosen === rows.length ? "候補のチェックをすべて外します" : "候補すべてにチェックを付けます";
    };
    all.addEventListener("click", () => {
      const next = !rows.every((row) => row.check.checked);
      rows.forEach((row) => (row.check.checked = next));
      refresh();
    });
    function candidate(item) {
      const check = el("input", { type: "checkbox", "aria-label": `記録に残す：${item.text}` });
      // 記録は消せないので、最初はチェックを外しておく。残すものを専門職が選ぶ
      check.checked = false;
      check.addEventListener("change", refresh);
      const body = el("p", { class: "cand-text", text: item.text });
      const editor = el("textarea", { rows: 2, maxLength: 300, hidden: true, "aria-label": "修正する文" });
      editor.value = item.text;
      const fix = el("button", {
        type: "button",
        class: "link",
        text: "文を修正する",
        tip: "候補の文をその場で修正できます。修正するとチェックが付きます",
        onclick: () => {
          editor.hidden = false;
          body.hidden = true;
          fix.hidden = true;
          check.checked = true;
          refresh();
          editor.focus();
        },
      });
      rows.push({ item, check, editor });
      const where = whereOf(item);
      return el(
        "li",
        { class: "cand" },
        el("label", { class: "cand-check", tip: "チェックを付けた候補だけが、記録に残ります" }, check),
        el(
          "div",
          { class: "cand-body" },
          el(
            "p",
            { class: "cand-head" },
            el("span", { class: "tell-kind", text: KIND_SHORT[item.kind] || item.kind }),
            where ? el("span", { class: "cand-where", text: where }) : "",
            item.about ? el("span", { class: "hint", text: `${item.about}のこと` }) : ""
          ),
          body,
          editor,
          item.quote ? el("p", { class: "cand-quote", text: `原文：${item.quote}` }) : "",
          fix
        )
      );
    }
    const groups = new Map();
    waiting.forEach((item) => {
      const key = grouped ? item.section || "そのほか" : "";
      groups.set(key, [...(groups.get(key) || []), item]);
    });
    save.addEventListener("click", async () => {
      if (!rows.some((row) => row.check.checked) && !armedNone) {
        armedNone = true;
        save.textContent = "本当にどれも残さない（決め直せません）";
        save.dataset.tip = "もう一度押すと決まります。取り消せません";
        return;
      }
      save.disabled = true;
      message.hidden = true;
      const done = [];
      try {
        for (const row of rows) {
          const edited = row.editor.hidden ? null : row.editor.value.trim();
          const json = row.check.checked ? (edited && edited !== row.item.text ? { decision: "残す", text: edited } : { decision: "残す" }) : { decision: "残さない" };
          const { item } = await api(`/advocacy/items/${row.item.id}/decide`, { method: "POST", json });
          done.push(item);
        }
        render([...decided, ...done]);
        // 決めた数を、呼んだ画面（資料の切り替えの数字など）にも伝える
        onDecided?.(done);
      } catch (error) {
        message.textContent = error.message;
        message.hidden = false;
        save.disabled = false;
      }
    });
    const kept = decided.filter((item) => item.status === "残した").length;
    frame.replaceChildren(
      waiting.length ? el("p", { class: "tell-lead", text: lead }) : el("p", { class: "tell-lead", text: `記録に残したのは${kept}件です。` }),
      ...[...groups.entries()].map(([name, list]) => el("div", { class: "cand-group" }, name ? el("h4", { text: name }) : "", el("ul", { class: "cand-list" }, ...list.map(candidate)))),
      waiting.length ? el("div", { class: "cand-actions" }, save, all, count) : "",
      message,
      decided.length ? el("details", { class: "cand-decided" }, el("summary", { text: `決めた項目（${decided.length}件）`, tip: "記録に残した候補と、残さなかった候補を開いて見られます" }), el("ul", { class: "cand-list" }, ...decided.map(decidedLine))) : "",
      waiting.length ? el("p", { class: "hint", text: "残した項目は記録になり、ツイン（AI）の考えの材料になります。記録はふつう消せません。別の人の資料だったときだけ、資料ごと消去できます。" }) : ""
    );
    refresh();
  }
  render(items);
  return frame;
}

/* 資料の消去。押す前に消えるものを見せ、2回押して決める。取り込む前の時点に戻す（app/professional_records.py）。 */
export function eraseArea(document, box, onErased) {
  const area = el("div", { class: "erase-area" });
  if (!document.can_erase) {
    area.append(el("p", { class: "hint", text: "取り込んでから時間が経ったので、この資料は消去できません。" }));
    return area;
  }
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const open = el("button", {
    type: "button",
    class: "quiet",
    text: "この資料を消去する（別の人の資料だったとき）",
    tip: "消えるものの一覧を先に出します。この時点ではまだ消えません",
    onclick: async () => {
      try {
        const { preview } = await api(`/advocacy/documents/${document.id}/erase-preview`);
        if (!preview.can_erase) {
          area.replaceChildren(el("p", { class: "hint", text: preview.reason }));
          return;
        }
        const counts = Object.entries(preview.counts)
          .filter(([, count]) => count > 0)
          .map(([name, count]) => `${name}${count}件`)
          .join("、");
        const confirm = twoStepButton("消去する", "本当に消去する（元に戻せません）", async () => {
          try {
            await api(`/advocacy/documents/${document.id}/erase`, { method: "POST" });
            box.replaceChildren(el("p", { class: "hint", text: "資料を消去し、取り込む前の時点に戻しました。" }));
            onErased?.();
          } catch (error) {
            message.textContent = error.message;
            message.hidden = false;
          }
        }, undefined, "資料と残した記録を消し、取り込む前の時点に戻します。取り消せません");
        area.replaceChildren(
          el("p", { class: "notice", text: `消去すると、取り込む前の時点に戻します。消えるもの：${counts}。残したあとにツイン（AI）が作った考えと手紙も消えます。` }),
          preview.returned_letters ? el("p", { class: "notice", text: "返した手紙は、紙で渡っているかもしれません。画面からは消えますが、紙は取り戻せません。" }) : "",
          el("div", { class: "row" }, confirm),
          message
        );
      } catch (error) {
        message.textContent = error.message;
        message.hidden = false;
      }
    },
  });
  area.append(open, message);
  return area;
}

/* ツイン（AI）のレビュー。資料を本人のセルフと照らした考えを、文章で出す。 */
export function drawReview(review) {
  const list = (title, items) => (items.length ? [el("h5", { text: title }), el("ul", {}, ...items)] : []);
  return el(
    "div",
    { class: "review-frame" },
    el("h4", { text: "ツイン（AI）のレビュー" }),
    el("p", { class: "review-overview", text: review.overview }),
    ...list("合っているところ", review.fits.map((text) => el("li", { text }))),
    ...list(
      "気になるところ",
      review.concerns.map((item) => el("li", {}, item.text, item.where ? el("span", { class: "cand-where", text: item.where }) : ""))
    ),
    ...list("ご本人に確かめたいこと", review.asks.map((text) => el("li", { text }))),
    el("p", { class: "hint", text: "ツイン（AI）が、10年の記録とセルフに照らして読んだ考えです。推測を含みます。" })
  );
}

/* ツインに伝える。レビューの読み違いを直したり、書き足したりする。伝えたことは、記録に残す候補になる */
export function tellBack(document) {
  const box = el("textarea", { rows: 3, maxLength: 2000, "aria-label": "ツインに伝えること", placeholder: "例：通所は本人も見学してから決めると話していました" });
  const out = el("div");
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const send = el("button", {
    type: "button",
    class: "primary",
    text: "ツインに伝える",
    tip: "書いたことをツインに伝え、記録に残す候補に分けます",
    onclick: async () => {
      if (!box.value.trim()) {
        message.textContent = "伝えることを書いてください。";
        message.hidden = false;
        box.focus();
        return;
      }
      send.disabled = true;
      message.hidden = true;
      try {
        const text = `（資料「${document.title || "資料"}」のレビューについて）${box.value.trim()}`;
        const { tell } = await api("/advocacy/tell", { method: "POST", json: { text, client_key: crypto.randomUUID() } });
        box.value = "";
        out.replaceChildren(drawTellItems(tell.items, "伝えたことを、記録に残す候補に分けました。残すものにチェックを付けてください。"));
      } catch (error) {
        message.textContent = error.message;
        message.hidden = false;
      } finally {
        send.disabled = false;
      }
    },
  });
  return el(
    "details",
    { class: "tell-back" },
    el("summary", { text: "ツインに伝える（読み違いを修正する・書き足す）", tip: "開くと、レビューの読み違いを修正したり書き足したりできます" }),
    el("p", { class: "hint", text: "レビューに違うところや足りないところがあれば、書いて伝えてください。伝えたことは、記録に残す候補になります。" }),
    box,
    el("div", { class: "row" }, send),
    message,
    out
  );
}

/* 取り込んだ資料。まずツインのレビューを文章で出し、記録に残す候補はその下にたたんでおく。
   名前が本人と合わないときは、レビューと候補を出す前に確かめる。autoReview のときは、レビューをすぐ頼む。
   onChange は、名前を確かめたとき（新しい資料を渡す）と、消去したとき（null を渡す）に呼ぶ。
   showHead が偽なら、題と書いた人などの行を出さない（資料の画面は、上に自分で出すため）。 */
export function drawDocument(document, { onCheck, onChange, selfName = "ご本人", autoReview = false, showHead = true } = {}) {
  const box = el("section", { class: "tell-frame document-frame" });
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const thinking = el("p", { class: "hint", role: "status", hidden: true, text: "ツイン（AI）がケアプランを読んで考えています。1分ほどかかります" });

  function fail(error) {
    message.textContent = error.message;
    message.hidden = false;
  }

  function reviewArea(current) {
    const area = el("div", { class: "review-area" });
    const waitText = el("p", { class: "hint", role: "status", text: "ツイン（AI）が資料を読み、セルフと照らして考えをまとめています。30秒ほどかかります" });
    async function load() {
      startWaiting(waitText, "ツイン（AI）が資料を読み、セルフと照らして考えをまとめています。30秒ほどかかります");
      area.replaceChildren(waitText);
      try {
        const { review } = await api(`/advocacy/documents/${current.id}/review`, { method: "POST" });
        area.replaceChildren(drawReview(review));
      } catch (error) {
        area.replaceChildren(el("p", { class: "error", role: "alert", text: error.message }), el("button", { type: "button", class: "quiet", text: "もう一度レビューを頼む", tip: "ツイン（AI）に、この資料のレビューを書き直してもらいます", onclick: load }));
      }
    }
    if (current.review) {
      area.append(drawReview(current.review));
    } else if (autoReview) {
      load();
    } else {
      area.append(el("button", { type: "button", class: "wide", text: "ツインのレビューを読む", tip: "ツイン（AI）がこの資料を本人の立場で読み、考えを書きます", onclick: load }));
    }
    return area;
  }

  function draw(current) {
    const meta = [current.kind, current.document_date, current.author, current.pages ? `${current.pages}ページ` : ""].filter(Boolean).join("／");
    const chips = [
      !current.family_can_see ? el("span", { class: "doc-chip", text: "専門職だけに見える資料" }) : "",
      current.has_medical ? el("span", { class: "doc-chip", text: "医療の情報あり（診断と薬の指示はしません）" }) : "",
      current.made_by_ai ? el("span", { class: "doc-chip ai", text: "ツイン（AI）が作った資料" }) : "",
    ];
    const parts = showHead ? [el("h3", { text: current.title || "資料" }), el("p", { class: "hint", text: meta }), el("p", { class: "doc-chips" }, ...chips)] : [];
    if (current.status === "名前の確かめ待ち") {
      parts.push(
        el("p", { class: "notice", text: `資料の利用者の名前は「${current.person_name}」です。${selfName}の資料ですか。確かめるまで、レビューと記録の候補は出しません。` }),
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: `${selfName}の資料です`,
            tip: "本人の資料だと確かめ、レビューと記録の候補を出します",
            onclick: async () => {
              try {
                const confirmed = (await api(`/advocacy/documents/${current.id}/person-ok`, { method: "POST" })).document;
                draw(confirmed);
                onChange?.(confirmed);
              } catch (error) {
                fail(error);
              }
            },
          })
        )
      );
    } else {
      parts.push(reviewArea(current), tellBack(current));
      if (current.made_by_ai) {
        // AI の文を、本人の思いや今の様子として記録に戻さない（原則3）
        parts.push(el("p", { class: "notice", text: "この資料はツイン（AI）が作ったものです。ツインの文を記録に戻さないため、記録に残す候補にはしません。" }));
      } else {
        const waiting = current.items.filter((item) => item.status === "確かめ待ち").length;
        parts.push(
          el(
            "details",
            { class: "candidates-fold" },
            el("summary", { text: waiting ? `記録に残す候補（${waiting}件。開いて選んでください）` : `記録に残す候補（${current.items.length}件。決めました）`, tip: "開くと、資料から読み取った記録の候補を見て選べます" }),
            drawTellItems(current.items, "資料から読んだことです。記録に残すものにチェックを付けてください。文はその場で修正できます。", { grouped: true })
          )
        );
      }
      if (current.care_plan && onCheck) {
        const check = el("button", {
          type: "button",
          class: "wide",
          text: "このケアプランをチェックする",
          tip: "ツイン（AI）が本人の立場で、このケアプランに異議を書きます",
          onclick: async () => {
            check.disabled = true;
            startWaiting(thinking, "ツイン（AI）がケアプランを読んで考えています。1分ほどかかります");
            thinking.hidden = false;
            message.hidden = true;
            try {
              const { plan } = await api(`/advocacy/documents/${current.id}/check`, { method: "POST" });
              onCheck(plan);
            } catch (error) {
              check.disabled = false;
              fail(error);
            } finally {
              thinking.hidden = true;
            }
          },
        });
        parts.push(check);
      }
    }
    box.replaceChildren(...parts, thinking, message, eraseArea(current, box, () => onChange?.(null)));
  }
  draw(document);
  return box;
}

/* 手紙を頼んだ知らせ。確かめを経るまで、中身は出さない。 */
export function drawLetterRequested(letter, selfName = "ご本人") {
  const waiting = letter.status === "確かめ待ち";
  return el(
    "section",
    { class: "tell-frame" },
    el("p", {
      class: "estimate-text",
      text: waiting
        ? `ツイン（AI）が手紙の下書きを書きました。いま${selfName}（またはご家族）が読んで、修正しています。返ってきたら、手紙の画面に届きます。`
        : `手紙は「${letter.status}」です。`,
    }),
    el("button", { type: "button", class: "wide", text: "手紙の画面を開く", tip: "手紙の画面を新しいタブで開きます。返ってきた手紙もそこに届きます", onclick: () => window.open("letter.html") })
  );
}

/* 資料（PDF）を選んで渡すボタン。選んだら onFile にファイルを渡す。言葉と見た目と吹き出しは呼ぶ側で変えられる。 */
export function documentButton(onFile, { label = "資料（PDF）を渡す", className = "quiet", tip = "PDF の資料を選んで、ツイン（AI）に渡します" } = {}) {
  const input = el("input", { type: "file", accept: "application/pdf", hidden: true });
  input.addEventListener("change", () => {
    const [file] = input.files;
    input.value = "";
    if (file) {
      onFile(file);
    }
  });
  const button = el("button", { type: "button", class: className, text: label, tip, onclick: () => input.click() });
  const wrap = el("span", { class: "document-button" }, button, input);
  wrap.button = button;
  return wrap;
}

/* 資料を送る。本文にファイルそのものを送る（写真と同じ形）。 */
export function uploadDocument(file) {
  const params = new URLSearchParams({ client_key: crypto.randomUUID(), file_name: file.name.slice(0, 120) });
  return api(`/advocacy/documents?${params}`, { method: "POST", body: file, headers: { "content-type": "application/pdf" } });
}
