/*
  役割：資料の画面。ケアプランやアセスメントの PDF を登録し、資料ごとにレビューとチャットを読む。
  4つの極：3. 読み解き、積み上げる（資料）、4. 10年後に代弁する。
  守る原則：1（記録に残すかは人が決める）、12（必ず「ツイン（AI）」と出す）。

  設計は docs/api.md の「資料の画面（2036年の専門職の道具。11/6 に作り直した）」。
  ツールの「資料を渡してチェックする」から開く（app.js の openTab("documents")）。下のタブは「ツール」を今いる所として示す。
  PC 版は左に「資料を登録する」とこれまでの資料の一覧、右に選んだ資料（static/document_room.js の documentDetail）。
  スマホ版は一覧と資料を切り替える。
  ファイルを選ぶ窓は「資料を登録する」を押したときだけ開く（自動で開くのは余計、と 2026-11-06 ご本人）。
  「今の様子」は出さない。資料ごとに、前に作ったレビュー、元の PDF、チャットの履歴が読める。
*/

import { api } from "./api.js";
import { isPc } from "./device.js";
import { documentDetail } from "./document_room.js";
import { documentButton, uploadDocument } from "./tell.js";
import { dateLabel, el, startWaiting } from "./ui.js";

/* 一覧の並び。新しく登録したものを上にする */
function newestFirst(a, b) {
  return String(b.created_at || "").localeCompare(String(a.created_at || "")) || b.id - a.id;
}

/* options.documentId があれば、その資料を開いた形で始める（登録のあとや、PC版とスマホ版の切り替えのあと） */
export function renderDocuments(container, ctx, options = {}) {
  let alive = true;
  let sending = false;
  let selected = options.documentId || null;
  let detail = null;
  const phone = !isPc();

  const list = el("ul", { class: "doc-list", "aria-label": "これまでの資料" });
  const empty = el("p", { class: "hint", hidden: true, text: "まだ登録した資料はありません。「資料を登録する」から PDF を選んでください。" });
  const listError = el("p", { class: "error", role: "alert", hidden: true });
  const waiting = el("p", { class: "hint", role: "status", hidden: true });
  const uploadError = el("p", { class: "error", role: "alert", hidden: true });
  const register = documentButton(upload, { label: "資料を登録する", className: "primary doc-register-button", tip: "PDF を選ぶと、ツイン（AI）が読み取り、本人の立場でレビューします" });
  const main = el("main", { class: "docs-main", "aria-label": "選んだ資料" });

  const side = el(
    "aside",
    { class: "docs-side", "aria-label": "資料の登録と一覧" },
    el(
      "div",
      { class: "doc-register" },
      el("h1", { text: "資料" }),
      el("p", { text: "ケアプランやアセスメントの PDF を登録すると、ツイン（AI）が読み取り、本人のセルフをもとにレビューします。資料ごとに、レビューとチャットが残ります。" }),
      register,
      waiting,
      uploadError
    ),
    el("h2", { class: "doc-list-title", text: "これまでの資料" }),
    empty,
    listError,
    list
  );
  const layout = el("div", { class: "docs-layout" }, side, main);
  container.className = "view docs-view";
  container.append(layout);

  /* スマホでは、一覧と資料のどちらかを出す */
  function setMode(mode) {
    layout.dataset.mode = mode;
  }

  function placeholder() {
    return el(
      "div",
      { class: "docs-empty" },
      el("p", { text: "左の一覧から資料を選ぶと、ここにレビュー、記録の候補、その資料についてのチャットが出ます。元の資料（PDF）も、ここから大きく開けます。" }),
      el("p", { class: "hint", text: "新しい資料は、左上の「資料を登録する」から PDF を選んでください。" })
    );
  }

  /* 資料を選ぶ。右に開き、一覧の印を動かす */
  function select(id) {
    detail?.dispose();
    detail = null;
    selected = id;
    options.documentId = id; // PC版とスマホ版が変わって開き直したときも、同じ資料を開くため（app.js）
    for (const button of list.querySelectorAll(".doc-entry")) {
      button.setAttribute("aria-current", String(Number(button.dataset.id) === id));
    }
    if (!id) {
      main.replaceChildren(placeholder());
      setMode("list");
      return;
    }
    detail = documentDetail(id, ctx, {
      onBack: () => select(null),
      onChanged: (change) => {
        if (change?.erased) {
          selected = null;
          options.documentId = null;
        }
        loadList();
      },
    });
    main.replaceChildren(detail.node);
    setMode("detail");
    if (phone) {
      container.scrollTop = 0;
    }
  }

  /* 1件の資料の行。登録した日、種類、題、書いた人、レビューの有無、話した数 */
  function row(doc) {
    const chips = [
      doc.status === "名前の確かめ待ち" ? el("span", { class: "doc-chip wait", text: "名前の確かめ待ち" }) : "",
      doc.review ? el("span", { class: "doc-chip done", text: "レビューあり" }) : el("span", { class: "doc-chip", text: "レビューはまだ" }),
      doc.chat_count ? el("span", { class: "doc-chip talk", text: `チャット ${doc.chat_count}件` }) : "",
      doc.made_by_ai ? el("span", { class: "doc-chip ai", text: "ツイン（AI）が作った資料" }) : "",
    ];
    return el(
      "li",
      {},
      el(
        "button",
        { type: "button", class: "doc-entry", "data-id": String(doc.id), "aria-current": String(doc.id === selected), tip: "この資料のレビュー、記録の候補、チャットを開きます", onclick: () => select(doc.id) },
        el(
          "span",
          { class: "doc-entry-top" },
          el("span", { class: "doc-entry-date", text: doc.created_at ? `${dateLabel(doc.created_at)}に登録` : "" }),
          doc.kind ? el("span", { class: "doc-entry-kind", text: doc.kind }) : ""
        ),
        el("span", { class: "doc-entry-title", text: doc.title || "資料" }),
        doc.author ? el("span", { class: "doc-entry-sub", text: `書いた人：${doc.author}` }) : "",
        el("span", { class: "doc-chips" }, ...chips)
      )
    );
  }

  async function loadList() {
    try {
      const { items } = await api("/advocacy/documents");
      if (!alive) {
        return;
      }
      const sorted = [...items].sort(newestFirst);
      list.replaceChildren(...sorted.map(row));
      empty.hidden = sorted.length > 0;
      listError.hidden = true;
      return sorted;
    } catch (error) {
      if (alive) {
        listError.textContent = error.message;
        listError.hidden = false;
      }
      return [];
    }
  }

  /* 登録する。読み取りに1分ほどかかるので、待つ印を出す。読み取れたら、その資料を右に開く */
  async function upload(file) {
    if (sending) {
      return;
    }
    sending = true;
    register.button.disabled = true;
    uploadError.hidden = true;
    startWaiting(waiting, `ツイン（AI）が資料を読み取っています。1分ほどかかります（${file.name}）`);
    waiting.hidden = false;
    let received = null;
    try {
      ({ document: received } = await uploadDocument(file));
    } catch (error) {
      if (alive) {
        uploadError.textContent = error.message;
        uploadError.hidden = false;
      }
    } finally {
      sending = false;
      register.button.disabled = false;
      waiting.hidden = true;
    }
    if (received && alive) {
      await loadList();
      select(received.id);
    }
  }

  async function start() {
    const items = await loadList();
    if (!alive) {
      return;
    }
    // 開く資料が決まっていなければ、PC は一覧のいちばん新しい資料を右に開く。スマホは一覧から始める
    if (!selected && !phone && items.length) {
      selected = items[0].id;
    }
    select(selected && items.some((doc) => doc.id === selected) ? selected : null);
  }

  main.replaceChildren(placeholder());
  setMode(selected ? "detail" : "list");
  start();
  return () => {
    alive = false;
    detail?.dispose();
  };
}
