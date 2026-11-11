/*
  役割：専門職のツールの画面。職種ごとに使えるツールをカードで出す。
  4つの極：4. 10年後に代弁する。
  守る原則：1（決めるのは人）、3（ツインの文と記録を分ける）、12（必ず「ツイン（AI）」と出す）。

  使えるツールはサーバーが決める（docs/api.md の tools）。ここは受け取った順に出すだけ。
  ツインの考えは問いに答えて作る。ボタンだけでは問いがないので、問いを入れる所から始める。
*/

import { api } from "./api.js";
import { NEEDS_KEY_WORDS, el, svg, twinFigure } from "./ui.js";

function lineIcon(...parts) {
  return svg(
    "svg",
    { viewBox: "0 0 24 24", width: "32", height: "32", fill: "none", stroke: "currentColor", "stroke-width": "1.5", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
    ...parts
  );
}

const planIcon = () => lineIcon(svg("path", { d: "M8 5H6v16h12V5h-2" }), svg("path", { d: "M9 3h6v4H9z" }), svg("path", { d: "m9 14 2 2 4-4" }));

/* ツールごとの説明とボタン。名前はサーバーの Tool.name を使う。
   画面に出すのは、たずねる、チェック、手紙の3つ（tyumajinさんの決定、2026-11-06）。1枚の紙は手紙に置き換えたので出さない。 */
const letterIcon = () => lineIcon(svg("path", { d: "M3 7l9 6 9-6" }), svg("path", { d: "M3 7h18v12H3z" }));
const TOOL_WORDS = {
  voice: {
    what: "チャットで聞いたり、意見を求めたりできます。ツイン（AI）が、10年の読みから組み上げた本人のセルフをもとに、本人として答えます。「伝える」を押すと、今の状況をツインに伝えられます。",
    button: "ツインにたずねる",
    tip: "問いを入れると、ツイン（AI）が本人として答える画面を開きます",
    icon: () => twinFigure(),
    open: (ctx) => ctx.openTab("advocate", { flow: "voice" }),
    // ケア会議の画面（static/meeting.js）。前は「ツインに聞く」の画面にあったボタンを、ここへ移した（tyumajinさんの依頼、2026-11-06）
    more: [{ text: "ケア会議", tip: "会議の画面を開きます。今の様子とツインを大きく映します", open: (ctx) => ctx.openTab("meeting") }],
    // 準備中のツール（下の plannedTool と plannedList）。名前は tyumajinさんの決定（2026-11-08）。
    // note は、カードの下の「準備中のツール」に出す短い説明
    planned: {
      text: "想い出「回想法」",
      tip: "まだ使えません。押すと、ハッカソンのあとに作る予定のこのツールの説明を、カードの下で示します",
      // つらい出来事はAIから聞かない（原則6）ので、出す話題は好きだったことにする
      note: "訪問のとき、ツイン（AI）が10年分の写真と日記から、本人が好きだった話題を出します。本人が今話したことは、今の様子として残します。",
    },
  },
  plan: {
    // 見出しは「ケア資料をチェックする」。ケアプランだけでなくアセスメントなども渡せるため（tyumajinさんの依頼、2026-11-07）
    title: "ケア資料をチェックする",
    what: "ケアプランやアセスメントの PDF を渡すと、ツイン（AI）が読み、本人のセルフをもとにチェックします。読んだことは、残すと決めたものだけが記録になります。",
    button: "資料を渡してチェックする",
    tip: "資料の画面を開きます。PDF を渡すと、ツインがチェックします",
    icon: planIcon,
    // 資料の画面（static/documents.js）を開く。登録はその画面の「資料を登録する」から。ファイルの窓は自動で開かない
    open: (ctx) => ctx.openTab("documents"),
    // ケアプランの仲間の操作。道具の数は3つのまま、このカードに置く（tyumajinさんの決定、2026-11-06）
    more: [{ text: "アセスメントなどを入力する", tip: "アセスメントやフェイスシートを入れる画面を、新しいタブで開きます", open: () => window.open("care_form.html") }],
    planned: {
      text: "会議の記録をチェックする",
      tip: "まだ使えません。押すと、ハッカソンのあとに作る予定のこのツールの説明を、カードの下で示します",
      note: "サービス担当者会議の記録を渡すと、ツイン（AI）が読み、本人の言葉や希望が抜けていないかをチェックします。",
    },
  },
  // 見出しは「書類を作る」。生活まとめプランを、手紙を頼むの上に置く（tyumajinさんの決定、2026-11-06）
  letter: {
    title: "書類を作る",
    what: "ツイン（AI）が、生活まとめプランや、本人に代わって書く A4 1枚の手紙を作ります。手紙は、ご本人かご家族が読んで修正してから届きます。",
    before: [{ text: "生活まとめプランを作る", tip: "ツインが生活まとめプランの下書きを作る画面を、新しいタブで開きます", open: () => window.open("life_plan.html") }],
    button: "手紙を頼む",
    tip: "本人に代わる手紙をツインに頼む画面を、新しいタブで開きます",
    icon: letterIcon,
    open: () => window.open("letter.html"),
    planned: {
      text: "わたしのことシートを作る",
      tip: "まだ使えません。押すと、ハッカソンのあとに作る予定のこのツールの説明を、カードの下で示します",
      // 手紙と同じく、ツインの下書きは本人か家族が読んでから渡す（原則1）
      note: "新しいヘルパーや施設の職員に渡す A4 1枚の自己紹介（呼ばれたい名前、好きな話題、苦手なこと）を、ツイン（AI）が下書きします。ご本人かご家族が読んでから渡します。",
    },
  },
};

/* 準備中のツール。ハッカソンのあとに増やす予定のツールを、カードごとに1つ見せる（tyumajinさんの依頼、2026-11-08）。
   点線の枠と「準備中」の札で、使えるボタンと見分けられるようにする。
   押しても何も書き込まず、AI も呼ばない。
   押すと、カードの下の「準備中のツール」（plannedList）の、そのツールの行を見える所へ動かして、少しの間だけ色を付ける。
   スマホではカードが縦に並び、説明の行が遠くなるため。 */
const plannedLineId = (toolId) => `tool-planned-${toolId}`;

function plannedTool(toolId, item) {
  return el(
    "button",
    {
      type: "button",
      class: "wide tool-planned",
      "aria-describedby": plannedLineId(toolId),
      tip: item.tip,
      onclick: () => {
        const line = document.getElementById(plannedLineId(toolId));
        if (!line) {
          return;
        }
        const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        line.scrollIntoView({ block: "nearest", behavior: still ? "auto" : "smooth" });
        line.classList.add("is-pointed");
        setTimeout(() => line.classList.remove("is-pointed"), 1600);
      },
    },
    el("span", { class: "tool-planned-name", text: item.text }),
    el("span", { class: "tool-planned-tag", text: "準備中" })
  );
}

/* カードの下の「準備中のツール」。「見えるのは、〜が決めた範囲です。」の下に、同じ文字の形と大きさ（.hint）で、
   準備中のツールの名前と短い説明を1行ずつ出す（tyumajinさんの依頼、2026-11-08）。準備中のツールがない職種には出さない。 */
function plannedList(tools) {
  const items = tools.filter((tool) => TOOL_WORDS[tool.id].planned);
  if (!items.length) {
    return null;
  }
  return el(
    "div",
    { class: "tool-planned-list" },
    el("p", { class: "hint", text: "準備中のツール（ハッカソンのあとに作る予定です。今はまだ使えません）" }),
    el(
      "ul",
      { class: "hint" },
      ...items.map((tool) => {
        const item = TOOL_WORDS[tool.id].planned;
        return el("li", { id: plannedLineId(tool.id), text: `${item.text}：${item.note}` });
      })
    )
  );
}

/* ケア会議のカード。サーバーのツールがない専門職（主治医など）に、ツールの画面の中で会議の入口を出す
   （tyumajinさんの依頼「井口先生へもツールボタンを作って。その中に会議を作ろう」、2026-11-06）。
   「ツインにたずねる」のツールがある専門職（ケアマネジャー）は、そのカードの「ケア会議」から開くので、このカードは出さない。
   色は .tool-card の元の藍のまま。 */
const meetingIcon = () =>
  lineIcon(svg("path", { d: "M3 5h18v11H3z" }), svg("path", { d: "M8 20h8" }), svg("path", { d: "M12 16v4" }), svg("circle", { cx: "9", cy: "10", r: "1.6" }), svg("circle", { cx: "15", cy: "10", r: "1.6" }));
const MEETING_CARD = {
  id: "meeting",
  title: "ケア会議",
  what: "みんなで同じ画面を見ながら、今の様子とツイン（AI）を大きく出し、記録にある本人の言葉を字幕と声で届けます。",
  button: "ケア会議",
  tip: "会議の画面を開きます。今の様子とツインを大きく映します",
  icon: meetingIcon,
  open: (ctx) => ctx.openTab("meeting"),
};

/* ツールのカード。ツールごとに色を決め（style.css の .tool-voice ほか）、見出しの帯と主なボタンをその色にする。
   色は画面の飾りの色（--tone-* と藍）だけを使い、記録やツインの色にはしない（原則13）。 */
function toolCard(tool, ctx, words = TOOL_WORDS[tool.id]) {
  return el(
    "section",
    { class: `tool-card tool-${tool.id}` },
    // 見出しはサーバーの名前を使う。画面で名前を変えたツールだけ、ここの title を使う
    el("div", { class: "tool-head" }, el("span", { class: "tool-icon" }, words.icon()), el("h2", { text: words.title || tool.name })),
    el("p", { class: "tool-what", text: words.what }),
    // before は主なボタンの上、more は下に並べる。主なボタンは色を塗り、ほかは線で囲む
    el(
      "div",
      { class: "tool-actions" },
      // tip は吹き出しの言葉（押すと何が起きるか）。ボタンの言葉と一緒に上の TOOL_WORDS に書く
      ...(words.before || []).map((item) => el("button", { type: "button", class: "wide tool-sub", text: item.text, tip: item.tip, onclick: () => item.open(ctx) })),
      el("button", { type: "button", class: "wide tool-main", text: words.button, tip: words.tip, onclick: () => words.open(ctx) }),
      ...(words.more || []).map((item) => el("button", { type: "button", class: "wide tool-sub", text: item.text, tip: item.tip, onclick: () => item.open(ctx) })),
      // 準備中のツールは、いちばん下に置く
      words.planned ? plannedTool(tool.id, words.planned) : null
    )
  );
}

/* ツールの画面に出す見出し。画面で名前を変えたツールは、その名前（招く窓の一覧もこれにそろえる） */
export function toolTitle(tool) {
  return TOOL_WORDS[tool.id]?.title || tool.name;
}

export function renderTools(container, ctx) {
  const job = ctx.me.profession || "専門職";
  const scope = el("p", { class: "hint" });
  const tools = (ctx.me.tools || []).filter((tool) => TOOL_WORDS[tool.id]);
  const cards = tools.map((tool) => toolCard(tool, ctx));
  // 「ツインにたずねる」がない専門職には、ケア会議を1枚のカードで出す
  if (!tools.some((tool) => tool.id === "voice")) {
    cards.push(toolCard({ id: MEETING_CARD.id, name: MEETING_CARD.title }, ctx, MEETING_CARD));
  }
  ctx.setPlate(`${job}のツール`);
  container.append(
    el(
      "section",
      { class: "panel tools-panel" },
      el("h1", { text: `${job}のツール` }),
      el("p", { class: "hint", text: `${ctx.me.called_name}（専門職・${job}）が使えるツールです。` }),
      ctx.needsKey ? el("p", { class: "notice", text: NEEDS_KEY_WORDS }) : "",
      el("div", { class: "tool-cards" }, ...cards),
      scope,
      plannedList(tools)
    )
  );
  api("/advocacy/overview")
    .then((data) => {
      scope.textContent = data.decided_on ? `見えるのは、${data.self_name}が決めた範囲です。` : `${data.self_name}は、まだ範囲を決めていません。`;
    })
    .catch(() => {
      // 読めなくても、ツールは使える
      scope.remove();
    });
}

/* ツインの考えの進み方。長い答えを読んでいる間も、今どこかが分かるようにする。 */
const FLOW_WORDS = ["問いを入れる", "ツインの考えを読む"];

export function toolSteps() {
  const items = FLOW_WORDS.map((word, index) => el("li", {}, el("span", { class: "tool-step-no", text: String(index + 1) }), word));
  const list = el("ol", { class: "tool-steps", "aria-label": "ツインの考えの進み方" }, ...items);
  list.setStep = (step) => {
    items.forEach((item, index) => {
      if (index === step - 1) {
        item.setAttribute("aria-current", "step");
      } else {
        item.removeAttribute("aria-current");
      }
    });
  };
  list.setStep(1);
  return list;
}

/* 問いの例。押すと入れる欄に入る。送るのは人が押してから。 */
export const ASK_EXAMPLES = ["これからの暮らしを、どう考えていると思いますか", "家で暮らせなくなったときのことを、どう考えていると思いますか", "大切にしてきたことは何だと思いますか"];
