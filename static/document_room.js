/*
  役割：資料の画面の右側。選んだ1件の資料の、見出し、レビュー、記録の候補、その資料についてのチャット。元の資料はモーダル画面で開く。
  4つの極：3. 読み解き、積み上げる（資料）、4. 10年後に代弁する（資料についての問いに、本人として答える）。
  守る原則：1（記録に残すかは人が決める）、3（ツインの文と記録を分ける）、12（必ず「ツイン（AI）」と出し、推測を含むと書く）。

  資料の画面（static/documents.js）が、選んだ資料ごとに documentDetail で作る。設計は docs/api.md の「資料の画面」。
  見出し（種類、題、書いた人、資料の日付）の下を「レビュー」「記録の候補」で切り替え、その横か下にチャットを置く。
  横に並べるか下に置くかは、右側の幅で CSS が決める（style.css の .doc-detail）。スマホではチャットも切り替えの1つにする。
  名前の確かめ待ちの資料は、確かめだけを出し、レビュー、記録の候補、チャットは確かめるまで出さない。
  「今の様子」は出さない。
  元の資料は、見出しの「元の資料を開く」で、画面の上に重ねる窓（<dialog> のモーダル）に大きく出す（11/6 ご本人の依頼）。
  レビューやチャットを読みながら、必要なときだけ原本を開くため。名前の確かめ待ちの資料でも開ける（本人の資料か確かめるのに要る）。
  だれとして見ているかをヘッダーで送って読み、Blob の URL で見せる。窓には「閉じる」と「別のタブで開く」。Esc と背景を押しても閉じる。
*/

import { api, currentData, currentUserId } from "./api.js";
import { isPc } from "./device.js";
import { drawReview, drawTellItems, eraseArea, tellBack } from "./tell.js";
import { dateLabel, el, sendWithKeys, startWaiting, waitingLine } from "./ui.js";
import { drawChatReply } from "./voice.js";

/* 切り替えのボタンの吹き出し（押すと下に何が出るか） */
const TAB_TIPS = {
  review: "ツイン（AI）がこの資料を本人の立場で読んだレビューを出します",
  items: "資料から読み取った、記録に残す候補を出します。残すかは人が決めます",
  chat: "この資料について、ツイン（AI）に問いを送れる欄を出します",
};

const CHAT_EXAMPLES = [
  "この資料で、本人の思いと合わないところはどこですか",
  "本人なら、このサービスをどう感じると思いますか",
  "この資料について、本人に確かめたほうがよいことは何ですか",
];

/* 自分が聞いた問いの吹き出し（答えを待つ間に出す） */
function askedBubble(question) {
  return el("div", { class: "bubble-row mine" }, el("div", { class: "bubble-body" }, el("div", { class: "bubble", text: question })));
}

/*
  1件の資料の右側を作る。
  onBack（スマホで一覧へ戻る）、onChanged（名前を確かめた・消去した・レビューができたなど、一覧を読み直すとき）を受け取る。
  返すのは { node, dispose }。
*/
export function documentDetail(documentId, ctx, { onBack, onChanged } = {}) {
  let alive = true;
  let current = null;
  let pdfUrl = "";
  let pdfLoading = false;
  let sending = false;
  const phone = !isPc();
  const node = el("section", { class: "doc-detail", "aria-labelledby": `doc-title-${documentId}` }, el("p", { class: "hint doc-detail-loading", text: "資料を読み込んでいます。" }));

  // ---- 切り替え（元の資料は切り替えに入れず、モーダル画面で開く） ----
  const tabNames = phone
    ? [
        ["review", "レビュー"],
        ["items", "記録の候補"],
        ["chat", "チャット"],
      ]
    : [
        ["review", "レビュー"],
        ["items", "記録の候補"],
      ];
  const tabButtons = {};
  const panes = {};
  let show = "review";

  function setShow(key) {
    show = key;
    for (const [name, button] of Object.entries(tabButtons)) {
      button.setAttribute("aria-selected", String(name === key));
      button.tabIndex = name === key ? 0 : -1;
    }
    for (const [name, pane] of Object.entries(panes)) {
      // PC ではチャットはいつも見える（切り替えの外）
      pane.hidden = name === "chat" && !phone ? false : name !== key;
    }
  }

  // ---- 元の資料（モーダル画面） ----
  const pdfBox = el("div", { class: "doc-pdf-box" }, el("p", { class: "hint", text: "元の資料を読み込んでいます。" }));
  const openPdf = el("button", { type: "button", class: "quiet", text: "別のタブで開く", tip: "元の資料を、ブラウザの新しいタブで大きく開きます", disabled: true, onclick: () => pdfUrl && window.open(pdfUrl, "_blank") });
  const pdfTitle = el("h2", { class: "doc-pdf-title", id: `doc-pdf-title-${documentId}`, text: "元の資料" });
  const closePdf = el("button", { type: "button", class: "primary", text: "閉じる", tip: "元の資料の窓を閉じて、レビューの画面へ戻ります", onclick: () => pdfDialog.close() });
  const pdfDialog = el(
    "dialog",
    { class: "doc-pdf-dialog", "aria-labelledby": pdfTitle.id },
    el("div", { class: "doc-pdf-head" }, pdfTitle, el("div", { class: "doc-pdf-actions" }, openPdf, closePdf)),
    phone ? el("p", { class: "hint doc-pdf-hint", text: "資料が出ないときは「別のタブで開く」を押してください。" }) : "",
    pdfBox
  );
  // 見出しのボタン。見出しを描き直しても同じボタンを使い、閉じたらここへフォーカスを戻す
  const pdfButton = el("button", { type: "button", class: "quiet doc-pdf-open", "aria-haspopup": "dialog", text: "元の資料を開く", tip: "受け取った資料の原本を、この画面の上の窓で開きます", onclick: openPdfDialog });
  let pressedOutside = false;
  let closing = false;

  /* 押した所が窓の外（背景）か。窓の中の余白やボタンは外にしない */
  function outside(event) {
    if (event.target !== pdfDialog) {
      return false;
    }
    const box = pdfDialog.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
  }
  // 背景を押したら閉じる。窓の中で押して背景で離した（文字を選んだ）ときは閉じない
  pdfDialog.addEventListener("pointerdown", (event) => {
    pressedOutside = outside(event);
  });
  pdfDialog.addEventListener("click", (event) => {
    if (pressedOutside && outside(event)) {
      pdfDialog.close();
    }
    pressedOutside = false;
  });
  // Esc でも閉じる（<dialog> の決まりで cancel のあと close が来る）。閉じたら見出しのボタンに戻る
  pdfDialog.addEventListener("close", () => {
    if (!closing && pdfButton.isConnected) {
      pdfButton.focus();
    }
  });

  function openPdfDialog() {
    if (pdfDialog.open) {
      return;
    }
    pdfTitle.textContent = `元の資料：${current?.title || "資料"}`;
    pdfDialog.showModal();
    closePdf.focus();
    loadPdf();
  }

  async function loadPdf() {
    if (pdfUrl || pdfLoading) {
      return;
    }
    pdfLoading = true;
    const headers = {};
    if (currentUserId()) {
      headers["X-NT-User"] = currentUserId();
    }
    if (currentData()) {
      headers["X-NT-Data"] = currentData();
    }
    try {
      const response = await fetch(`/api/advocacy/documents/${documentId}/file`, { headers });
      if (!response.ok) {
        throw new Error("元の資料を読めませんでした。");
      }
      const blob = await response.blob();
      if (!alive) {
        return;
      }
      pdfUrl = URL.createObjectURL(blob);
      openPdf.disabled = false;
      pdfBox.replaceChildren(el("iframe", { class: "doc-pdf-frame", src: pdfUrl, title: `元の資料：${current?.title || "資料"}` }));
    } catch (error) {
      if (alive) {
        pdfBox.replaceChildren(el("p", { class: "error", role: "alert", text: error.message || "元の資料を読めませんでした。" }));
      }
    } finally {
      pdfLoading = false;
    }
  }

  // ---- レビュー ----
  const reviewBox = el("div", { class: "doc-review-box" });

  async function makeReview() {
    const waiting = el("p", { class: "hint", role: "status" });
    startWaiting(waiting, "ツイン（AI）が資料を読み、セルフと照らしてレビューを書いています。30秒ほどかかります");
    reviewBox.replaceChildren(waiting);
    try {
      const { review } = await api(`/advocacy/documents/${documentId}/review`, { method: "POST" });
      if (!alive) {
        return;
      }
      current = { ...current, review };
      drawReviewPane();
      onChanged?.();
    } catch (error) {
      if (alive) {
        reviewBox.replaceChildren(el("p", { class: "error", role: "alert", text: error.message }), el("button", { type: "button", class: "quiet", text: "もう一度レビューを頼む", tip: "ツイン（AI）に、この資料のレビューを書き直してもらいます", onclick: makeReview }));
      }
    }
  }

  function drawReviewPane() {
    if (current.review) {
      reviewBox.replaceChildren(drawReview(current.review), tellBack(current));
    } else {
      makeReview();
    }
  }

  // ---- 記録の候補 ----
  const itemsBox = el("div", { class: "doc-items-box" });
  const checkWaiting = el("p", { class: "hint", role: "status", hidden: true });
  const itemsError = el("p", { class: "error", role: "alert", hidden: true });

  function drawItemsPane() {
    const parts = [];
    if (current.made_by_ai) {
      // AI の文を、本人の思いや今の様子として記録に戻さない（原則3）
      parts.push(el("p", { class: "notice", text: "この資料はツイン（AI）が作ったものです。ツインの文を記録に戻さないため、記録に残す候補にはしません。" }));
    } else {
      parts.push(
        drawTellItems(current.items, "資料から読んだことです。記録に残すものにチェックを付けてください。文はその場で修正できます。", {
          grouped: true,
          // 決めたら、切り替えの「記録の候補」の数字を描き直し、一覧も読み直す（決めたあとも数字が残っていた）
          onDecided: (done) => {
            current.items = current.items.map((item) => done.find((made) => made.id === item.id) || item);
            const waiting = current.items.filter((item) => item.status === "確かめ待ち").length;
            const badge = tabButtons.items?.querySelector(".doc-tab-badge");
            if (badge && waiting) {
              badge.textContent = String(waiting);
              badge.setAttribute("aria-label", `決めていない候補 ${waiting}件`);
            } else {
              badge?.remove();
            }
            onChanged?.();
          },
        })
      );
    }
    if (current.care_plan) {
      const check = el("button", {
        type: "button",
        class: "wide doc-check-button",
        text: "このケアプランをチェックする",
        tip: "ツイン（AI）が本人の立場で異議を書き、チャットに出します",
        onclick: async () => {
          check.disabled = true;
          itemsError.hidden = true;
          startWaiting(checkWaiting, "ツイン（AI）がケアプランを読んで考えています。1分ほどかかります");
          checkWaiting.hidden = false;
          try {
            const { plan } = await api(`/advocacy/documents/${documentId}/check`, { method: "POST" });
            if (alive) {
              showCheck(plan);
            }
          } catch (error) {
            check.disabled = false;
            itemsError.textContent = error.message;
            itemsError.hidden = false;
          } finally {
            checkWaiting.hidden = true;
          }
        },
      });
      parts.push(el("h4", { class: "doc-items-sub", text: "ケアプランのチェック" }), el("p", { class: "hint", text: "ツイン（AI）が、このケアプランを第1版として受け取り、本人の立場から異議を書きます。答えはチャットに出ます。" }), check, checkWaiting, itemsError);
    }
    parts.push(eraseArea(current, itemsBox, () => {
      onErased();
    }));
    itemsBox.replaceChildren(...parts);
  }

  // ---- チャット ----
  const chatLog = el("div", { class: "doc-chat-log" });
  const chatError = el("p", { class: "error", role: "alert", hidden: true });
  const box = el("textarea", { rows: 2, maxLength: 1000, "aria-label": "この資料についてツインに聞くこと", placeholder: "例：この資料で、本人の思いと合わないところはどこですか" });
  const send = el("button", { type: "button", class: "primary", text: "聞く", tip: "書いた問いをツイン（AI）に送り、本人としての考えを聞きます" });
  const examples = el(
    "div",
    { class: "ask-examples doc-chat-examples" },
    ...CHAT_EXAMPLES.map((words) =>
      el("button", {
        type: "button",
        class: "quiet",
        text: words,
        tip: "この問いを入れる欄に写します。送るのは「聞く」を押してからです",
        onclick: () => {
          // 押すと入れる欄に入る。送るのは人が「聞く」を押してから
          box.value = words;
          box.focus();
        },
      })
    )
  );

  function drawTurn(turn) {
    return drawChatReply({ kind: "ツインの考え", voice: turn.voice }, turn.question);
  }

  /* 問いの例は、まだやり取りがないときだけ出す。やり取りが増えたら、答えを読む場所を広く取るため */
  function refreshExamples() {
    examples.hidden = chatLog.querySelector(".turn") !== null;
  }

  async function loadChat() {
    try {
      const { items } = await api(`/advocacy/documents/${documentId}/chat`);
      if (alive) {
        chatLog.replaceChildren(...items.map(drawTurn));
        refreshExamples();
        chatLog.scrollTop = chatLog.scrollHeight;
      }
    } catch {
      // 前のやり取りが読めなくても、聞くことはできる
    }
  }

  async function ask() {
    const question = box.value.trim();
    if (sending) {
      return;
    }
    if (!question) {
      chatError.textContent = "聞きたいことを入れてください。";
      chatError.hidden = false;
      return;
    }
    sending = true;
    send.disabled = true;
    chatError.hidden = true;
    // 答えを待つ間も、何を聞いたかを見せる。失敗したら消して、入れる欄の言葉はそのまま残す
    const pending = el("div", { class: "turn" }, askedBubble(question), waitingLine("ツイン（AI）が資料とレビューを読んで考えています。30秒ほどかかります"));
    chatLog.append(pending);
    refreshExamples();
    pending.scrollIntoView({ block: "nearest" });
    try {
      const { turn } = await api(`/advocacy/documents/${documentId}/chat`, { method: "POST", json: { question, client_key: crypto.randomUUID() } });
      if (!alive) {
        return;
      }
      const drawn = drawTurn(turn);
      pending.replaceWith(drawn);
      box.value = "";
      drawn.scrollIntoView({ block: "start" });
      onChanged?.();
    } catch (error) {
      pending.remove();
      refreshExamples();
      chatError.textContent = error.message;
      chatError.hidden = false;
    } finally {
      sending = false;
      send.disabled = false;
    }
  }
  send.addEventListener("click", ask);
  sendWithKeys(box, send);

  /* ケアプランのチェックの答えは、チャットの流れに足す */
  function showCheck(plan) {
    const drawn = drawChatReply({ kind: "ケアプラン案", plan }, "このケアプランをチェックしてください");
    chatLog.append(drawn);
    refreshExamples();
    if (phone) {
      setShow("chat");
    }
    drawn.scrollIntoView({ block: "start" });
  }

  function onErased() {
    node.replaceChildren(el("p", { class: "hint doc-detail-loading", text: "この資料を消去し、取り込む前の時点に戻しました。" }));
    onChanged?.({ erased: documentId });
  }

  // ---- 見出し ----
  function head(doc) {
    const meta = [doc.author ? `書いた人：${doc.author}` : "", doc.document_date ? `資料の日付：${/^\d{4}-\d{2}-\d{2}$/.test(doc.document_date) ? doc.document_date.replace(/^(\d{4})-0?(\d+)-0?(\d+)$/, "$1年$2月$3日") : doc.document_date}` : "", doc.pages ? `${doc.pages}ページ` : "", doc.created_at ? `${dateLabel(doc.created_at)}に登録` : ""].filter(Boolean);
    return el(
      "header",
      { class: "doc-detail-head" },
      phone && onBack ? el("button", { type: "button", class: "quiet doc-back", text: "← 資料の一覧へ", tip: "この資料を閉じて、資料の一覧へ戻ります", onclick: onBack }) : "",
      el(
        "div",
        { class: "doc-detail-title" },
        doc.kind ? el("span", { class: "doc-entry-kind", text: doc.kind }) : "",
        el("h2", { id: `doc-title-${documentId}`, text: doc.title || "資料" }),
        el("p", { class: "doc-detail-meta" }, ...meta.map((words) => el("span", { text: words }))),
        el(
          "p",
          { class: "doc-chips" },
          !doc.family_can_see ? el("span", { class: "doc-chip", text: "専門職だけに見える資料" }) : "",
          doc.has_medical ? el("span", { class: "doc-chip", text: "医療の情報あり（診断と薬の指示はしません）" }) : "",
          doc.made_by_ai ? el("span", { class: "doc-chip ai", text: "ツイン（AI）が作った資料" }) : ""
        )
      ),
      pdfButton,
      pdfDialog
    );
  }

  /* 名前の確かめ待ち：確かめだけを出す */
  function drawNameCheck(doc) {
    const message = el("p", { class: "error", role: "alert", hidden: true });
    node.replaceChildren(
      head(doc),
      el(
        "div",
        { class: "doc-name-check" },
        el("p", { class: "notice", text: `資料の利用者の名前は「${doc.person_name}」です。ご本人の資料ですか。確かめるまで、レビュー、記録の候補、チャットは出しません。` }),
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "ご本人の資料です",
            tip: "本人の資料だと確かめ、レビューと記録の候補とチャットを出します",
            onclick: async () => {
              try {
                const { document } = await api(`/advocacy/documents/${documentId}/person-ok`, { method: "POST" });
                if (alive) {
                  current = document;
                  drawAll();
                  onChanged?.();
                }
              } catch (error) {
                message.textContent = error.message;
                message.hidden = false;
              }
            },
          })
        ),
        message,
        // 別の人の資料だったときは、ここで消去できる
        eraseArea(doc, node, () => onErased())
      )
    );
  }

  /* 確かめた資料：切り替えとチャット */
  function drawAll() {
    if (current.status === "名前の確かめ待ち") {
      drawNameCheck(current);
      return;
    }
    const tabs = el("div", { class: "doc-tabs", role: "tablist", "aria-label": "資料の中身の切り替え" });
    for (const [key, label] of tabNames) {
      const waitingItems = key === "items" && !current.made_by_ai ? current.items.filter((item) => item.status === "確かめ待ち").length : 0;
      tabButtons[key] = el(
        "button",
        { type: "button", role: "tab", id: `doc-tab-${documentId}-${key}`, "aria-controls": `doc-pane-${documentId}-${key}`, tip: TAB_TIPS[key], onclick: () => setShow(key) },
        label,
        waitingItems ? el("span", { class: "doc-tab-badge", "aria-label": `決めていない候補 ${waitingItems}件`, text: String(waitingItems) }) : ""
      );
      tabs.append(tabButtons[key]);
    }
    // 切り替えのボタンは、左右の矢印でも移れるようにする
    tabs.addEventListener("keydown", (event) => {
      const keys = tabNames.map(([key]) => key);
      const at = keys.indexOf(show);
      const next = event.key === "ArrowRight" ? at + 1 : event.key === "ArrowLeft" ? at - 1 : null;
      if (next === null) {
        return;
      }
      event.preventDefault();
      const key = keys[(next + keys.length) % keys.length];
      setShow(key);
      tabButtons[key].focus();
    });
    const pane = (key, label, ...children) => {
      panes[key] = el("div", { class: `doc-pane doc-pane-${key}`, id: `doc-pane-${documentId}-${key}`, role: key === "chat" && !phone ? "region" : "tabpanel", "aria-label": label }, ...children);
      if (key !== "chat" || phone) {
        panes[key].setAttribute("aria-labelledby", `doc-tab-${documentId}-${key}`);
      }
      return panes[key];
    };
    const content = el(
      "div",
      { class: "doc-content" },
      tabs,
      pane("review", "ツイン（AI）のレビュー", reviewBox),
      pane("items", "記録の候補", itemsBox),
      phone ? pane("chat", "この資料についてのチャット", chatParts()) : ""
    );
    node.replaceChildren(head(current), el("div", { class: "doc-detail-body" }, content, phone ? "" : pane("chat", "この資料についてのチャット", chatParts())));
    drawReviewPane();
    drawItemsPane();
    setShow(show);
    loadChat();
  }

  function chatParts() {
    return el(
      "div",
      { class: "doc-chat" },
      el("h3", { text: "この資料についてツインに聞く" }),
      el("p", { class: "hint", text: "ツイン（AI）が、この資料とレビューをもとに、本人として考えを答えます。推測を含みます。今の思いは、ご本人に確かめてください。" }),
      chatLog,
      examples,
      chatError,
      el("div", { class: "composer-row doc-chat-composer" }, box, send)
    );
  }

  async function load() {
    try {
      const { document } = await api(`/advocacy/documents/${documentId}`);
      if (!alive) {
        return;
      }
      current = document;
      drawAll();
    } catch (error) {
      if (alive) {
        node.replaceChildren(phone && onBack ? el("button", { type: "button", class: "quiet doc-back", text: "← 資料の一覧へ", tip: "この資料を閉じて、資料の一覧へ戻ります", onclick: onBack }) : "", el("p", { class: "error", role: "alert", text: error.message }));
      }
    }
  }
  load();

  return {
    node,
    dispose() {
      alive = false;
      // 窓を開いたまま画面を離れたときは閉じてから、前の資料の Blob の URL を解放する
      if (pdfDialog.open) {
        closing = true;
        pdfDialog.close();
      }
      if (pdfUrl) {
        URL.revokeObjectURL(pdfUrl);
      }
    },
  };
}
