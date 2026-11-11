/*
  役割：専門職と託された家族が、ツインと話す画面。ツインの考えのツールを使える人にはツインの考えで答える（原則16）。
  使えない人には、本人の言葉、証言、AIの推測、推定の枠を分けて出す。
  4つの極：4. 10年後に代弁する。
  守る原則：2（今の様子は画面の上の枠に出す。答えの中には重ねて出さない）、3（出どころを色、ラベル、枠の線で分ける）、
            9（本人の言葉は記録の本文のまま。日付と出どころを出す）、11（推定は別の枠で、読み上げない）、
            12（必ず「ツイン（AI）」と出す）。

  専門職の PC の広い画面では、右の余白にツイン（AI）の3Dの姿を光の台座に立たせる（static/twin_stage.js）。
  考えている間は回り、答えが届くと小さく跳ねる。読む順（今の様子 → 本人の言葉 → AI）には入れず、横に置く（原則2）。

  読み上げるのは本人の言葉の引用と前置きだけで、ブラウザの読み上げを使う。本人の声に似せない。
  2回押しても増えないよう、送っている間はボタンを止め、同じ印で送る。
*/

import { api } from "./api.js";
import { speak, stopSpeaking } from "./speech.js";
import { NEEDS_KEY_WORDS, el, openSheet, sendWithKeys, startWaiting, twinFigure } from "./ui.js";
import { ASK_EXAMPLES, toolSteps } from "./tools.js";
import { drawTellItems } from "./tell.js";
import { drawChatReply, drawVoice, voiceArea } from "./voice.js";

const TWIN_NAME = "ツイン（AI）";
// 右の3Dの窓は、専門職の「記録」のいちばん上（花びらの舞台、static/petals.js）へ移した（2026-11-06）

/* 職種のツールを使えるかを返す。決めるのはサーバーで、ここは出すボタンを選ぶだけ。 */
function hasTool(person, id) {
  return Boolean(person?.tools?.some((tool) => tool.id === id));
}

/* 託された人（家族と専門職）には、ツインに聞くと代弁で答える。ツールの有無で分けない（サーバーの app/advocacy_chat.py と同じ）。 */
function answersWithVoice(person) {
  return hasTool(person, "voice") || Boolean(person?.can_use_supporter_screen);
}

export function dateWords(iso) {
  const [year, month, day] = iso.split("T")[0].split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

/* 同じ発言から分かれた文を1枚にまとめる。組はサーバーが決める。本文は変えず、文ごとに段落にする（原則9）。 */
export function sameUtteranceCards(items) {
  const cards = new Map();
  for (const item of items || []) {
    const key = item.group ?? `言明${item.statement_id}`;
    const card = cards.get(key);
    if (card) {
      card.parts.push(item);
      card.top = card.top || item.top;
    } else {
      cards.set(key, { ...item, parts: [item] });
    }
  }
  return [...cards.values()];
}

/* 記録を1枚のカードにする。線の形でも出どころが分かるようにする。 */
function sourceCard(item, kind, { canSpeak = false } = {}) {
  const parts = item.parts || [item];
  const footer = el("div", { class: "source-meta" }, el("span", { class: `source-label ${kind}`, text: item.source }));
  if (item.confirm && kind !== "now") {
    footer.append(el("span", { class: "hint", text: item.confirm }));
  }
  if (canSpeak) {
    footer.append(
      el("button", {
        type: "button",
        class: "link",
        text: "読み上げる",
        tip: "この本人の言葉を声で読み上げます。もう一度押すと止まります",
        onclick: (event) => speak(`${item.lead_in}。${parts.map((part) => part.body).join(" ")}`, event.currentTarget),
      })
    );
  }
  return el(
    "article",
    { class: `source-card ${kind}` },
    el("p", { class: "lead-in", text: item.lead_in || nowLead(item) }),
    ...parts.map((part) => el("p", { class: kind === "self" ? "quote hand" : "quote", text: kind === "self" ? `「${part.body}」` : part.body })),
    footer
  );
}

function nowLead(item) {
  return `${dateWords(item.date)}、${item.speaker_label || item.speaker}の記録です`;
}

/* 本人の言葉。問いに近い言葉を先に見せ、残りは開いたときに出す。 */
function selfWords(items) {
  if (!items?.length) {
    return "";
  }
  const cards = sameUtteranceCards(items);
  const first = cards.filter((item) => item.top);
  const rest = cards.filter((item) => !item.top);
  const shown = first.length ? first : cards;
  const others = first.length ? rest : [];
  const section = el(
    "section",
    { class: "answer-group" },
    el("h3", { text: "本人の言葉" }),
    ...shown.map((item) => sourceCard(item, "self", { canSpeak: true }))
  );
  if (others.length) {
    const more = el("button", {
      type: "button",
      class: "quiet",
      text: "ほかの言葉も見る",
      tip: "残りの本人の言葉を、ここに並べます",
      onclick: () => more.replaceWith(...others.map((item) => sourceCard(item, "self", { canSpeak: true }))),
    });
    section.append(more);
  }
  return section;
}

export function group(title, items, kind, options) {
  if (!items?.length) {
    return "";
  }
  return el("section", { class: "answer-group" }, el("h3", { text: title }), ...sameUtteranceCards(items).map((item) => sourceCard(item, kind, options)));
}

export function estimateFrame(estimate) {
  if (!estimate) {
    return "";
  }
  // 反対の根拠が少ないときは1列にする。左右に並べると片方が空くため
  const twoColumns = estimate.counter.length >= 2;
  return el(
    "section",
    { class: "estimate-frame" },
    el("h3", { text: "AIの推定（本人に確かめていません）" }),
    el("p", { class: "hint", text: "本人の言葉ではありません。" }),
    el("p", { class: "estimate-text", text: estimate.text }),
    el("p", { class: "estimate-meter" }, "確からしさ：", el("strong", { class: "tag", text: estimate.confidence })),
    el("h4", { text: "確かめ方" }),
    el("p", { text: estimate.how_to_confirm }),
    el(
      "div",
      { class: twoColumns ? "estimate-grounds two" : "estimate-grounds" },
      el("div", {}, el("h4", { text: "根拠" }), ...estimate.evidence.map((item) => sourceCard(item, cardKind(item)))),
      el(
        "div",
        {},
        el("h4", { text: "反対の根拠" }),
        ...(estimate.counter.length
          ? estimate.counter.map((item) => sourceCard(item, cardKind(item)))
          : [el("p", { text: "反対の根拠は見つかっていません。読んだ記録が少ないだけかもしれません。" })])
      )
    )
  );
}

function cardKind(item) {
  if (item.source === "本人の言葉" || item.source === "本人の訂正") {
    return "self";
  }
  return item.source === "AIの推測" ? "guess" : "testimony";
}

/* 要素の根拠を、言明と発言の原文までたどる。 */
const TRACE_FIRST = 5;

export async function openTrace(elementId) {
  const data = await api(`/advocacy/elements/${elementId}`);
  const card = (item) =>
    el(
      "div",
      {},
      sourceCard(item, cardKind(item)),
      ...item.utterances
        // 本文と同じ文の原文は、二重に出さない
        .filter((utterance) => utterance.text !== item.body)
        .map((utterance) => el("p", { class: "hint", text: `発言の原文（${dateWords(utterance.spoken_at)}）：${utterance.text}` }))
    );
  openSheet((inner, close) => {
    const first = data.evidence.slice(0, TRACE_FIRST);
    const rest = data.evidence.slice(TRACE_FIRST);
    const more = el("button", {
      type: "button",
      class: "wide",
      text: "もっと見る",
      tip: "残りの根拠を、ここに並べます",
      hidden: rest.length === 0,
      onclick: () => more.replaceWith(...rest.map(card)),
    });
    inner.append(
      el("div", { class: "sheet-top" }, el("h2", { text: "根拠をたどる" }), el("button", { type: "button", class: "quiet", text: "閉じる", tip: "根拠の窓を閉じて、もとの画面に戻ります", onclick: close })),
      el("p", { class: "hint", text: "AI がまとめた文：" }),
      el("p", { text: data.body }),
      // 見える範囲に根拠がないときは、そのことを1行で伝える（空の窓だと壊れて見えた。件数は出さない）
      data.evidence.length ? "" : el("p", { class: "notice", text: "あなたに見える範囲の記録には、この文の根拠が見つかりません。" }),
      ...first.map(card),
      more
    );
  });
}

/* 同じ文の要素は1行にする。流し直した要素が同じ文になることがあるため。 */
function foldedElements(elements) {
  const seen = new Set();
  const rows = elements.filter((element) => {
    if (seen.has(element.body)) {
      return false;
    }
    seen.add(element.body);
    return true;
  });
  return el(
    "details",
    { class: "elements-fold" },
    el("summary", { text: "AI がまとめた文と、もとの記録", tip: "開くと、ツインが読んだまとめの文が並びます。根拠もたどれます" }),
    el("p", { class: "hint", text: "本人の言葉ではなく、AI が記録をまとめた文です。" }),
    ...rows.map((element) =>
      el(
        "div",
        { class: "person-row" },
        el(
          "div",
          { class: "person-text" },
          el("span", { class: "source-label guess", text: "AI のまとめ" }),
          ` ${element.valid_from ? `${element.valid_from.slice(0, 4)}年ごろからの話。` : ""}${element.body}（${element.confirmed ? "本人が確かめた記録を含む" : "本人がまだ確かめていない記録から"}）`
        ),
        el("button", { type: "button", class: "quiet", text: "根拠をたどる", tip: "このまとめの文のもとになった記録と、発言の原文を開きます", onclick: () => openTrace(element.element_id) })
      )
    )
  );
}

function drawTurn(turn, me) {
  const body = el("div", { class: "answer-body" });
  if (turn.notice) {
    body.append(el("p", { class: "notice", text: turn.notice }));
  }
  // 今の様子は画面の上の枠にあるので、答えの頭には出さない（2026-11-06 ご本人の決定）
  if (turn.lead) {
    body.append(el("p", { class: "twin-lead" }, el("span", { class: "hint", text: "ツイン（AI）の案内：" }), turn.lead));
  }
  if (turn.not_yet) {
    body.append(el("p", { class: "not-yet", text: turn.not_yet }));
  }
  if (turn.changed) {
    body.append(el("p", { class: "notice", text: "時期によって言葉が変わっています。今の思いはご本人に確かめてください。" }));
  }
  body.append(
    selfWords(turn.self_words),
    group("家族と縁のある人の証言", turn.testimony, "testimony"),
    group("AIの推測", turn.guesses, "guess")
  );
  body.append(estimateFrame(turn.estimate));
  const usesVoice = answersWithVoice(me);
  if (usesVoice && turn.voice) {
    body.append(drawVoice(turn.voice));
  } else if (usesVoice && turn.can_voice !== false) {
    // 前に作れなかった答えには、もう一度作るボタンを置く
    body.append(voiceArea(turn));
  }
  body.append(
    el("p", {
      class: "hint turn-note",
      text: usesVoice
        ? "ツイン（AI）の考えには推測が入ります。今の思いは、ご本人に確かめてください。"
        : me?.role === "専門職"
          ? "ツイン（AI）が読むのは記録をまとめた文です。ここにない話が、記録のどこかにあることもあります。「記録」のタブで探せます。"
          : "ツイン（AI）が読むのは記録をまとめた文です。ここにない話が、記録のどこかにあることもあります。ご本人やケアマネジャーに確かめてください。",
    })
  );
  if (turn.elements?.length) {
    body.append(foldedElements(turn.elements));
  }
  return el(
    "div",
    { class: "turn" },
    el("div", { class: "bubble-row mine" }, el("div", { class: "bubble-body" }, el("div", { class: "bubble", text: turn.question }))),
    el("div", { class: "twin-answer" }, el("div", { class: "row" }, twinFigure(), el("span", { class: "bubble-name", text: TWIN_NAME })), body)
  );
}

export function renderAdvocate(container, ctx, options = {}) {
  let alive = true;
  // ツールの「ツインの考え」から来たときは、問い、答え、考えの順に進むことを見せる
  const steps = options.flow === "voice" ? toolSteps() : null;
  let sending = false;
  const top = el("section", { class: "card now-panel" });
  const log = el("div", { class: "turns" });
  const status = el("p", { class: "error", role: "alert", hidden: true });
  const usesVoice = answersWithVoice(ctx.me);
  const thinkingWords = usesVoice ? "ツイン（AI）が10年の読みをもとに考えています。30秒ほどかかります" : "ツイン（AI）が記録を読んでいます";
  const thinking = el("p", { class: "hint", role: "status", hidden: true, text: thinkingWords });
  const box = el("textarea", { rows: 2, "aria-label": "ツインに聞くこと", placeholder: usesVoice ? "例：これからの暮らしを、どう考えていると思いますか" : "例：これからの暮らしを、どう話していましたか", maxLength: 2100 });
  // 送るボタンの吹き出しは、聞くと伝えるで変える。Alt+Enter の案内（ui.js の sendWithKeys が足す）は残す
  const askTip = usesVoice ? "ツイン（AI）に問いを送り、本人として考えた答えを下に出します" : "ツイン（AI）に問いを送り、記録にある本人の言葉を下に出します";
  const tellTip = "書いた様子をツインに伝えます。残す項目は、あとで選べます";
  const send = el("button", { type: "button", class: "primary", text: "聞く", tip: askTip });
  const setSendTip = (words) => {
    send.dataset.tip = words + (send.dataset.tip.match(/（Alt\+Enter.*）$/)?.[0] || "");
  };
  const toggleTip = (on) => (on ? "押すと、ツインに聞く形に戻ります" : "押すと、聞く代わりに今の様子をツインに伝える形に変わります");
  // 伝える形の例文。専門職は「伺ったら」、家族は「行ったら」（家族が「伺う」とは言わないため）
  const tellExample = ctx.me.role === "専門職" ? "例：今日伺ったら、炊飯器の最後のボタンを押せていませんでした" : "例：今日行ったら、炊飯器の最後のボタンを押せていませんでした";
  // 伝える：今の状況をツインに伝える。分けた項目を、残すかどうか人が決める（app/professional_records.py）
  let telling = false;
  const tellToggle = el("button", { type: "button", class: "quiet tell-toggle", "aria-pressed": "false", text: "伝える", tip: toggleTip(false) });
  tellToggle.addEventListener("click", () => {
    telling = !telling;
    tellToggle.setAttribute("aria-pressed", String(telling));
    tellToggle.dataset.tip = toggleTip(telling);
    // 押したあとは、送るボタンが「伝える」になるので、切り替えは「聞く形に戻す」にする（同じ言葉が2つ並んでいた）
    tellToggle.textContent = telling ? "聞く形に戻す" : "伝える";
    send.textContent = telling ? "伝える" : "聞く";
    // 「1 問いを入れる 2 ツインの考えを読む」と聞き方の案内は、聞く形のときだけ出す
    for (const part of [steps, guide, examples]) {
      if (part) {
        part.hidden = telling;
      }
    }
    setSendTip(telling ? tellTip : askTip);
    box.placeholder = telling ? tellExample : usesVoice ? "例：これからの暮らしを、どう考えていると思いますか" : "例：これからの暮らしを、どう話していましたか";
    box.setAttribute("aria-label", telling ? "ツインに伝えること" : "ツインに聞くこと");
  });
  // 資料（PDF）は「資料」の画面（static/documents.js）で登録し、資料ごとにレビューとチャットを見る。
  // ここには入口のボタンを1つだけ置く（入口を2つにしないため。2026-11-06 ご本人の依頼）
  const docButton = hasTool(ctx.me, "plan") ? el("button", { type: "button", class: "quiet", text: "資料の画面を開く", tip: "資料の画面に移ります。PDF を渡して、チェックと質問ができます", onclick: () => ctx.openTab("documents") }) : "";
  const guide = steps ? el("p", { class: "ask-guide", text: "聞きたいことを入れてください。ツイン（AI）が、10年の読みをもとに自分の考えを答えます。" }) : "";
  const examples = steps
    ? el(
        "div",
        { class: "ask-examples" },
        ...ASK_EXAMPLES.map((words) =>
          el("button", {
            type: "button",
            class: "quiet",
            text: words,
            tip: "この問いを、聞く欄に入れます。送るのは「聞く」を押したときです",
            onclick: () => {
              box.value = words;
              box.focus();
            },
          })
        )
      )
    : "";
  const panel = el(
    "section",
    { class: steps ? "panel advocate-panel advocate-flow" : "panel advocate-panel" },
    steps || "",
    guide,
    examples,
    // 「決めるのは人」は当たり前なので、この画面には書かない（2026-11-06 ご本人の決定）
    el("p", { class: "hint", text: usesVoice ? "ツイン（AI）が、10年の読みをもとに自分の考えをお伝えします。本人の言葉ではありません。" : "ツイン（AI）が、記録にある言葉をそのままお届けします。" }),
    top,
    log,
    el(
      "div",
      { class: "advocate-composer" },
      ctx.needsKey ? el("p", { class: "notice", text: NEEDS_KEY_WORDS }) : "",
      thinking,
      status,
      el("div", { class: "composer-row" }, box, send),
      el("div", { class: "composer-tools" }, tellToggle, docButton)
    )
  );
  container.append(panel);

  /* 最新の問いが画面の上に来るまで動かす。入れる欄が下に固定なので、答えが長くても問いから読める。 */
  function showLatest() {
    log.lastElementChild?.scrollIntoView({ block: "start" });
  }

  function openObservation(onDone) {
    openSheet((inner, close) => {
      const text = el("textarea", { rows: 3, "aria-label": "今の様子", maxLength: 2000 });
      const message = el("p", { class: "error", role: "alert", hidden: true });
      inner.append(
        el("h2", { text: "今の様子を入れる" }),
        el("p", { class: "hint", text: "ご覧になった様子を書いてください。「今の様子」として、いちばん上に出ます。" }),
        text,
        message,
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "入れる",
            tip: "書いた様子を「今の様子」として、いちばん上に出します",
            onclick: async () => {
              if (!text.value.trim()) {
                message.textContent = "今の様子を入れてください。";
                message.hidden = false;
                return;
              }
              try {
                await api("/observations", { method: "POST", json: { text: text.value } });
                close();
                onDone();
              } catch (error) {
                message.textContent = error.message;
                message.hidden = false;
              }
            },
          }),
          el("button", { type: "button", class: "quiet", text: "やめる", tip: "書いた様子を入れずに、窓を閉じます", onclick: close })
        )
      );
    });
  }

  /* 伝えたことのうち、まだ残すか決めていない項目。開き直しても確かめられるようにする。
     資料の記録の候補は、資料の画面の資料ごとの部屋で決める */
  const waitingBox = el("div", { class: "tell-waiting" });
  async function drawWaiting() {
    const waiting = [];
    const { items: tells } = await api("/advocacy/tells");
    tells.forEach((tell) => waiting.push(...tell.items.filter((item) => item.status === "確かめ待ち")));
    waitingBox.replaceChildren(...(waiting.length ? [el("h3", { text: "残すか決めていない伝え" }), drawTellItems(waiting, "前に伝えたことです。残しますか。")] : []));
  }

  async function drawTop() {
    const data = await api("/advocacy/overview");
    // 読み込む間にほかの画面へ移っていたら、見出しを書き換えない
    if (!alive) {
      return;
    }
    drawWaiting().catch(() => waitingBox.replaceChildren());
    ctx.setPlate(`${data.self_name}のツインに聞く`);
    top.replaceChildren(
      el("h2", { text: "今の様子" }),
      ...(data.now.length
        ? data.now.map((item) => sourceCard(item, "now"))
        : [el("p", { text: "今の様子は、まだ入っていません。" })]),
      el("button", { type: "button", class: "quiet", text: "今の様子を入れる", tip: "ご覧になった本人の様子を書く窓を開きます", onclick: () => openObservation(drawTop) }),
      waitingBox,
      // ケア会議の入口は、ツールの画面に移した（2026-11-06。主治医などにもツールのタブを出し、その中にケア会議を置く）
      data.decided_on
        ? el("p", { class: "hint scope-note", text: `見えるのは、${dateWords(data.decided_on)}に${data.self_name}が決めた範囲です。` })
        : el("p", { class: "hint scope-note", text: `${data.self_name}は、まだ範囲を決めていません。` })
    );
  }

  /* ツールから来たときの進み方。問いを入れるまでは、前のやり取りを出さない。 */
  function goStep(step) {
    if (!steps) {
      return;
    }
    steps.setStep(step);
    log.hidden = step === 1;
    guide.hidden = step !== 1;
    examples.hidden = step !== 1;
    if (step === 1) {
      box.focus();
    }
  }
  panel.addEventListener("voice-drawn", () => {
    goStep(2);
    panel.querySelector(".voice-frame")?.scrollIntoView({ block: "start" });
  });

  async function load() {
    try {
      await drawTop();
      const { items } = await api("/advocacy/turns");
      if (alive) {
        log.replaceChildren(...items.map((turn) => drawTurn(turn, ctx.me)));
        if (steps) {
          goStep(1);
        } else {
          showLatest();
        }
      }
    } catch (error) {
      status.textContent = error.message;
      status.hidden = false;
    }
  }

  async function ask() {
    const question = box.value.trim();
    if (sending) {
      return;
    }
    if (!question) {
      status.textContent = "聞きたいことを入れてください。";
      status.hidden = false;
      return;
    }
    sending = true;
    send.disabled = true;
    startWaiting(thinking, thinkingWords);
    thinking.hidden = false;
    status.hidden = true;
    // 答えを待つ間も、何を聞いたかを見せる。失敗したら消して、聞く欄の言葉はそのまま残す
    const waiting = el("div", { class: "turn" }, el("div", { class: "bubble-row mine" }, el("div", { class: "bubble-body" }, el("div", { class: "bubble", text: question }))));
    log.append(waiting);
    goStep(2);
    showLatest();
    try {
      // チャットの言葉で処理を呼び分ける。当てはまらなければ、ふだんの問いになる（app/advocacy_chat.py）
      // 「伝える」を押しているときは、伝える処理へ送る
      const reply = telling
        ? { kind: "伝える", tell: (await api("/advocacy/tell", { method: "POST", json: { text: question, client_key: crypto.randomUUID() } })).tell }
        : await api("/advocacy/chat", {
            method: "POST",
            json: { question, client_key: crypto.randomUUID() },
          });
      if (alive) {
        waiting.replaceWith(drawChatReply(reply, question, (turn) => drawTurn(turn, ctx.me)));
        box.value = "";
        showLatest();
      }
    } catch (error) {
      waiting.remove();
      goStep(1);
      status.textContent = error.message;
      status.hidden = false;
    } finally {
      sending = false;
      send.disabled = false;
      thinking.hidden = true;
    }
  }

  send.addEventListener("click", ask);
  // Enter だけなら改行。Alt+Enter か Ctrl+Enter で「聞く」を押したのと同じにする
  sendWithKeys(box, send);
  load();
  return () => {
    alive = false;
    stopSpeaking();
  };
}
