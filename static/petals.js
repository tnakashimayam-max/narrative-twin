/*
  役割：専門職の「記録」のいちばん上に出す「ツインの花びら」。真ん中に3Dのツイン（AI）を立たせ、まわりの5つの花びらのボタンで、
        本人にとって何で満たされ、何で損なわれ、今どうかを、ツインの読みとして出す（tyumajinさんの依頼、2026-11-06。デモの見せ場）。
  4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
  守る原則：4（読みは推測として出す。「ツイン（AI）の読み（推測を含みます）」と書き、読み上げない）、
            9（本人の言葉は記録の本文のまま、日付と出どころを添える）、
            12（必ず「ツイン（AI）」と出す）、
            13（花びらの名前と根拠は専門職の画面と、本人のホームの「5つの花びら」だけ。家族には出さない。数字を出さない。
                色がのるのは花びらだけで、選んだことは動き、形、輪郭で示す）。

  読みは GET /api/twin/petals（取り決めは docs/api.md の「花びらの読み」）。AI はここでは呼ばない。
  order は 0〜4 で、姿の花びらの番号と同じ（0 が上で、時計回り）。ボタンは同じ向き（0°、72°、144°、216°、288°）に置き、
  ボタンから姿の花びらへ細い引き出し線を引く。線の先は、姿の揺れと起き上がりに合わせて動く（static/twin_stage.js の onPetals）。
  幅が足りないとき（スマホと狭い窓）は、姿の下にボタンを並べ、引き出し線は引かない（style.css の @container）。
  読みの確からしさ（status）は、言葉と枠の線で分ける。本人が確かめた＝実線、それ以外＝点線。色だけにしない。
  矢印キーで花びらを移り、Esc で選ぶのをやめる。読みがなければ、節ごと出さない。
*/

import { api } from "./api.js";
import { PETAL_SHAPE, el, svg } from "./ui.js";
import { twinStage } from "./twin_stage.js";

// 姿の花びらの色の順（static/twin3d/twin.js の PETAL_COLORS と同じ）。ボタンと読みの見出しの花びらのアイコンにだけ使う
const PETAL_COLORS = ["#3F5F86", "#E67F65", "#C9922C", "#3D7A5B", "#6F5F9A"];
// 花びらのアイコンのグラデーションの id を、アイコンごとに重ならないようにする
let iconSerial = 0;

/* 色を黒か白へ寄せる（amount が負なら黒、正なら白へ）。同じ色の明るさ違いを作るため */
function shade(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const to = amount < 0 ? 0 : 255;
  const k = Math.abs(amount);
  const mix = (v) => Math.round(v + (to - v) * k);
  return `rgb(${mix((n >> 16) & 255)}, ${mix((n >> 8) & 255)}, ${mix(n & 255)})`;
}
// これより狭いと、ボタンを姿の下に並べる（style.css の .petal-orbit-box の @container と同じ値）
const WIDE = 700;
const CONFIRMED = "本人が確かめた";
const CONFIRMED_WORDS = "本人が確かめた読みを含む";
const EMPTY = "空き";
// 何も選んでいないときに並べる、花びらごとの一言の問い（花びらの意味の説明で、ツインの読みではない）
const PETAL_QUESTIONS = {
  くつろぎ: "何があると、心と体がほっとするか",
  結びつき: "だれと、何とつながっていたいか",
  共にあること: "人の輪の中に、どういていたいか",
  たずさわること: "何に手を動かし、役目を感じるか",
  自分らしさ: "何が「自分」を支えているか",
};

/* 花びらのボタンの吹き出し。ボタンには名前しか書いていないので、一言の問いを添える */
function petalTip(petal) {
  const question = PETAL_QUESTIONS[petal.name];
  return question ? `「${question}」についてのツインの読みを出します` : `${petal.name}についてのツインの読みを出します`;
}
const PETAL_CLOSE_TIP = "もう一度押すと、この読みを閉じます";

function monthWords(iso) {
  const [year, month] = String(iso || "").split("-").map(Number);
  return year && month ? `${year}年${month}月までの記録から` : "";
}

function dateWords(iso) {
  const [year, month, day] = String(iso || "").split("-").map(Number);
  return year ? `${year}年${month}月${day}日` : "";
}

function monthOf(iso) {
  const [year, month] = String(iso || "").split("-").map(Number);
  return year ? `${year}年${month}月` : "";
}

/* スクロールする枠（専門職の画面では .view）。その枠の見えている範囲を測るため */
function scrollerOf(node) {
  return node.closest(".view") || document.scrollingElement;
}

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/* 花びらのアイコン。その花びらの色で塗り、根元（花の中心側）をやや濃く、先へ向かって明るくする（姿の3Dの花びらに寄せる）。
   輪郭は同じ色の濃いめ、左上にごく薄い光（姿の花びらの PETAL_LIGHT と同じ形）。姿の花びらと同じ向きに回す。
   色がのるのは花びらだけ（原則13）。tyumajinさんの依頼（2026-11-06）で、線だけの白抜きから塗りに変えた */
function petalIcon(order, size = 28) {
  const color = PETAL_COLORS[order];
  const id = `petal-icon-fill-${(iconSerial += 1)}`;
  // 花びらの真ん中を中心にして、姿の花びらと同じ向きへ回す。グラデーションはこの向きの座標（根元 y=0、先 y=-100）で引く
  const transform = `rotate(${order * 72}) translate(0 50)`;
  return svg(
    "svg",
    { class: "petal-icon", viewBox: "-64 -64 128 128", width: String(size), height: String(size), "aria-hidden": "true" },
    svg(
      "defs",
      {},
      svg(
        "linearGradient",
        { id, gradientUnits: "userSpaceOnUse", x1: "0", y1: "0", x2: "0", y2: "-100" },
        svg("stop", { offset: "0", "stop-color": shade(color, -0.22) }),
        svg("stop", { offset: "0.55", "stop-color": color }),
        svg("stop", { offset: "1", "stop-color": shade(color, 0.3) })
      )
    ),
    svg("path", {
      class: "petal-icon-shape",
      d: PETAL_SHAPE,
      transform,
      fill: `url(#${id})`,
      stroke: shade(color, -0.3),
      "stroke-width": "1.6",
      "stroke-linejoin": "round",
      "vector-effect": "non-scaling-stroke",
    }),
    svg("path", { d: "M-20-62C-21-76-13-88-3-90C-12-82-17-72-20-62Z", transform, fill: "#ffffff", opacity: "0.45" })
  );
}

/* 1つの面（何で満たされるか、何で損なわれるか、今どうか）のカード */
function faceCard(face) {
  const empty = face.status === EMPTY || !face.readings.length;
  const confirmed = face.status === CONFIRMED;
  return el(
    "article",
    { class: `petal-face ${empty ? "empty" : confirmed ? "confirmed" : "guess"}` },
    el(
      "header",
      { class: "petal-face-head" },
      el("h4", { text: face.face }),
      // 確からしさは言葉で出す。本人が確かめた面は実線、それ以外は点線の札（数字は出さない）。
      // 本人が確かめた面にも推測の文が混じるので、札は「本人が確かめた読みを含む」にする（UIUX の確かめ、2026-11-06）
      empty ? "" : el("span", { class: `petal-status ${confirmed ? "confirmed" : "guess"}`, text: confirmed ? CONFIRMED_WORDS : face.status })
    ),
    // 「今どうか」が今ではないことがあるので、いちばん新しい根拠の時期を添える
    !empty && face.latest_evidence_date ? el("p", { class: "petal-face-date", text: `いちばん新しい根拠：${monthOf(face.latest_evidence_date)}` }) : "",
    empty
      ? el("p", { class: "petal-face-empty", text: "この面の読みはまだありません。" })
      : el("div", { class: "petal-face-readings" }, ...face.readings.map((text) => el("p", { text }))),
    face.words.length
      ? el(
          "div",
          { class: "petal-words" },
          el("p", { class: "petal-words-label" }, el("span", { class: "source-label self", text: "本人の言葉" })),
          ...face.words.map((word) =>
            el(
              "figure",
              { class: "petal-word" },
              el("blockquote", { class: "petal-word-body hand", text: `「${word.body}」` }),
              el("figcaption", { class: "petal-word-meta", text: `${dateWords(word.effective_date)}・${word.source}` })
            )
          )
        )
      : ""
  );
}

/*
  節を作る。返すのは { node, dispose }。読みを読み込むまでは空の節で、読みがなければ節ごと外す。
  本人の名前は /advocacy/overview から読む（読めなくても節は出す）。
  forSelf：本人のホームの「5つの花びら」から開くとき（static/self_petals.js、2026-11-07）。説明を本人に向けた言葉にし、
  専門職の道（/advocacy/overview）は呼ばない。onEmpty：読みがない・読めないときに呼ぶ（本人の画面で知らせを出すため）。
*/
/* 5つの花びらの説明（ツインの姿の下）。花びらは、キットウッドの5つの心理的ニーズ（app/schemas.py の Petal）に対応する */
const ABOUT_PETALS =
  "認知症ケアの理念であるパーソン・センタード・ケアでは、認知症のある人が「自分らしく、人間らしく生きる」ために満たされるべき心理的な欲求を、花びらに例えて「5つの心理的ニーズ」と呼んでいます。これは英国の心理学者トム・キットウッドが提唱したもので、これらが満たされることによって、その人らしさや人間としての尊厳（パーソンフッド）を保ち続けることができます。";

function aboutPetals() {
  return el("aside", { class: "petal-about", "aria-labelledby": "petal-about-title" }, el("h3", { id: "petal-about-title", text: "5つの花びらとは" }), el("p", { text: ABOUT_PETALS }));
}

export function petalSection({ forSelf = false, onEmpty = null } = {}) {
  let alive = true;
  let stage = null;
  let data = null;
  let selected = null;
  let lastPressed = 0;
  let wide = false;
  let geometry = null; // ボタンの中心と大きさ、姿の枠の位置（orbit の左上から）
  let unlisten = () => {};
  let observer = null;
  const node = el("section", { class: "petal-section", "aria-labelledby": "petal-title", hidden: true });

  const lines = svg("svg", { class: "petal-lines", "aria-hidden": "true" });
  // ボタンを結ぶ、星座のような点線の輪
  const ring = svg("ellipse", { class: "petal-orbit-ring" });
  lines.append(ring);
  const buttonGroup = el("div", { class: "petal-buttons", role: "group", "aria-label": "5つの花びら" });
  const orbit = el("div", { class: "petal-orbit", "data-layout": "stack" });
  // 読みの窓は、読み上げを積まないよう aria-live にしない。代わりに、見えない短い知らせ（status）だけを流す（UIUX の確かめ）
  const reading = el("div", { class: "petal-reading", id: "petal-reading", role: "region", "aria-label": "花びらの読み" });
  const announce = el("p", { class: "visually-hidden", role: "status" });
  const buttons = [];
  const leaders = [];

  /* 何も選んでいないとき：5つの花びらの名前と一言の問いを並べ、そこからも選べる */
  function idle() {
    reading.removeAttribute("aria-labelledby");
    reading.setAttribute("aria-label", "花びらの読み");
    reading.replaceChildren(
      el(
        "div",
        { class: "petal-idle" },
        el("p", { class: "petal-reading-hint", text: "花びらを選ぶと、その花びらについての読みを出します。" }),
        el(
          "ul",
          { class: "petal-index" },
          ...data.petals.map((petal) =>
            el(
              "li",
              {},
              el(
                "button",
                { type: "button", class: "petal-index-item", tip: `${petal.name}についてのツインの読みを、ここに出します`, onclick: () => select(petal.order) },
                el("span", { class: "petal-index-icon" }, petalIcon(petal.order, 34)),
                el("span", { class: "petal-index-text" }, el("strong", { text: petal.name }), el("span", { text: PETAL_QUESTIONS[petal.name] || "" }))
              )
            )
          )
        )
      )
    );
  }

  /* 読みの窓の上の、花びらの切り替えの帯（姿のボタンが画面の外にあるときも、ここで移れる） */
  function switcher(current) {
    const strip = el(
      "nav",
      { class: "petal-switch", "aria-label": "花びらを切り替える" },
      ...data.petals.map((petal) =>
        el(
          "button",
          {
            type: "button",
            class: "petal-switch-item",
            "aria-current": petal.order === current ? "true" : "false",
            tip: petal.order === current ? "今出している花びらの読みです" : `${petal.name}の読みに切り替えます`,
            onclick: () => select(petal.order),
          },
          petalIcon(petal.order, 22),
          el("span", { text: petal.name })
        )
      )
    );
    // 帯が1行に収まらない幅（スマホ）では、選んだ花びらが帯の真ん中に見えるよう横に動かす（ページは動かさない）
    requestAnimationFrame(() => {
      const chip = strip.querySelector('[aria-current="true"]');
      if (chip && strip.scrollWidth > strip.clientWidth) {
        strip.scrollLeft = chip.offsetLeft - (strip.clientWidth - chip.offsetWidth) / 2;
      }
    });
    return strip;
  }

  /* 読みの窓。見出しは花びらの名前、続けて3つの面 */
  function drawReading(petal) {
    reading.removeAttribute("aria-label");
    reading.setAttribute("aria-labelledby", "petal-reading-title");
    reading.replaceChildren(
      el(
        "div",
        { class: "petal-reading-body", "data-order": String(petal.order) },
        switcher(petal.order),
        el(
          "header",
          { class: "petal-reading-head" },
          el("span", { class: "petal-reading-icon" }, petalIcon(petal.order, 48)),
          el(
            "div",
            {},
            el("h3", { id: "petal-reading-title", text: petal.name }),
            el("p", { class: "petal-reading-label" }, el("span", { text: data.label }), el("span", { text: monthWords(data.as_of) })),
            // 線の意味の見本（色だけで分けない）
            el(
              "p",
              { class: "petal-legend" },
              el("span", {}, el("i", { class: "solid", "aria-hidden": "true" }), "実線＝本人が確かめた読みを含む"),
              el("span", {}, el("i", { class: "dashed", "aria-hidden": "true" }), "点線＝ツインの推測")
            )
          )
        ),
        el("div", { class: "petal-faces" }, ...petal.faces.map(faceCard))
      )
    );
  }

  /* 節の中の el が、スクロールする枠の見えている範囲から外れていれば、見える所へ動かす */
  function bringIntoView(target, block) {
    const view = scrollerOf(node).getBoundingClientRect();
    const box = target.getBoundingClientRect();
    const top = Math.max(view.top, 0);
    const bottom = Math.min(view.bottom, window.innerHeight);
    if (box.top < top || box.bottom > bottom) {
      target.scrollIntoView({ block, behavior: reducedMotion() ? "auto" : "smooth" });
    }
  }

  /* 花びらを選ぶ。null で選ぶのをやめる。同じボタンをもう一度押しても、選ぶのをやめる */
  function select(order, { focusButton = false } = {}) {
    selected = order;
    buttons.forEach((button, index) => {
      button.setAttribute("aria-pressed", String(index === order));
      // 選んだボタンは、もう一度押すと閉じることを吹き出しで伝える
      button.dataset.tip = index === order ? PETAL_CLOSE_TIP : petalTip(data.petals[index]);
    });
    leaders.forEach((leader, index) => leader.classList.toggle("on", index === order));
    stage?.focusPetal(order);
    if (order === null) {
      idle();
      announce.textContent = "花びらを選ぶのをやめました。";
      // 読みを下まで読んだあとにやめると、読みが短くなって節もボタンも画面の外に出る。押したボタンが見える所まで戻す
      requestAnimationFrame(() => alive && bringIntoView(buttons[lastPressed] || node, "nearest"));
      return;
    }
    lastPressed = order;
    const petal = data.petals.find((item) => item.order === order) || data.petals[order];
    drawReading(petal);
    announce.textContent = `${petal.name}の読みを出しました。${petal.faces.length}つの面があります。`;
    stage?.pulse({ hop: false });
    if (focusButton) {
      buttons[order].focus();
    }
    // 読みの窓が画面の下に隠れているとき（スマホと、姿の下に読みを出す幅）は、見出しが見える所まで動かす
    const top = reading.getBoundingClientRect().top;
    if (top > window.innerHeight - 120) {
      reading.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
    }
  }

  function petalButton(petal) {
    const button = el(
      "button",
      {
        type: "button",
        class: "petal-button",
        "data-order": String(petal.order),
        "aria-pressed": "false",
        "aria-controls": "petal-reading",
        tip: petalTip(petal),
        onclick: () => select(selected === petal.order ? null : petal.order),
      },
      el("span", { class: "petal-button-icon" }, petalIcon(petal.order, 32)),
      el("span", { class: "petal-button-name", text: petal.name })
    );
    return button;
  }

  /* 矢印キーで花びらを移る（右と下は時計回り、左と上は反時計回り）。Home は上の花びら、End は最後の花びら */
  buttonGroup.addEventListener("keydown", (event) => {
    const at = buttons.indexOf(document.activeElement);
    if (at < 0) {
      return;
    }
    const count = buttons.length;
    const moves = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let next = null;
    if (event.key in moves) {
      next = (at + moves[event.key] + count) % count;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = count - 1;
    }
    if (next !== null) {
      event.preventDefault();
      select(next, { focusButton: true });
    }
  });
  // Esc で選ぶのをやめる。読みの窓の中にいても効き、最後に押したボタンへ戻る
  node.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && selected !== null) {
      event.preventDefault();
      select(null);
      buttons[lastPressed]?.focus();
    }
  });

  /* PC の幅で、ボタンを姿のまわりに花びらと同じ向きで置く。狭い幅では姿の下に並べる */
  function layout() {
    if (!stage) {
      return;
    }
    const box = orbit.getBoundingClientRect();
    wide = box.width >= WIDE;
    orbit.dataset.layout = wide ? "wide" : "stack";
    orbit.classList.add("ready");
    if (!wide) {
      for (const button of buttons) {
        button.style.removeProperty("left");
        button.style.removeProperty("top");
      }
      geometry = null;
      return;
    }
    const anchors = stage.petalAnchors();
    if (!anchors) {
      return;
    }
    const frame = stage.figure.getBoundingClientRect();
    const offset = { x: frame.left - box.left, y: frame.top - box.top };
    const head = { x: offset.x + anchors.rest.head.x, y: offset.y + anchors.rest.head.y };
    // 節の陽だまりの光の中心を、姿の頭に合わせる
    const section = node.getBoundingClientRect();
    node.style.setProperty("--glow-x", `${(frame.left - section.left + anchors.rest.head.x).toFixed(0)}px`);
    node.style.setProperty("--glow-y", `${(frame.top - section.top + anchors.rest.head.y).toFixed(0)}px`);
    // 頭の中心から花びらの点まで（花びらの見かけの大きさ）
    const reach = Math.hypot(anchors.rest.petals[0].x - anchors.rest.head.x, anchors.rest.petals[0].y - anchors.rest.head.y);
    // 横は広く、縦は控えめの楕円に置く。下の2つは体と重ならないよう、横へ離す
    const widest = Math.max(...buttons.map((button) => button.offsetWidth)) / 2;
    const rx = Math.min(box.width / 2 - widest - 6, reach * 1.45 + widest + 70);
    const ry = reach * 1.55 + 52;
    ring.setAttribute("cx", head.x.toFixed(1));
    ring.setAttribute("cy", head.y.toFixed(1));
    ring.setAttribute("rx", rx.toFixed(1));
    ring.setAttribute("ry", ry.toFixed(1));
    const centers = buttons.map((button, index) => {
      const angle = (index * Math.PI * 2) / 5;
      const halfW = button.offsetWidth / 2;
      const halfH = button.offsetHeight / 2;
      let x = head.x + rx * Math.sin(angle);
      const y = head.y - ry * Math.cos(angle);
      if (index === 2 || index === 3) {
        const side = index === 2 ? 1 : -1;
        x = head.x + side * Math.max(Math.abs(x - head.x), reach * 1.2 + halfW + 18);
      }
      x = Math.max(halfW + 4, Math.min(box.width - halfW - 4, x));
      const yy = Math.max(halfH + 4, Math.min(box.height - halfH - 4, y));
      button.style.left = `${x}px`;
      button.style.top = `${yy}px`;
      return { x, y: yy, halfW, halfH };
    });
    geometry = { offset, centers, rest: anchors.rest };
    lines.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    drawLines(anchors.live);
  }

  /* 引き出し線。ボタンの縁から、姿の花びらの点へ */
  function drawLines(live) {
    if (!wide || !geometry || !live) {
      return;
    }
    geometry.centers.forEach((center, index) => {
      const end = { x: geometry.offset.x + live[index].x, y: geometry.offset.y + live[index].y };
      const dx = end.x - center.x;
      const dy = end.y - center.y;
      // ボタンの四角の縁と、線が交わる所から引く
      const t = Math.min(center.halfW / Math.max(Math.abs(dx), 0.001), center.halfH / Math.max(Math.abs(dy), 0.001));
      const start = { x: center.x + dx * Math.min(1, t) * 1.02, y: center.y + dy * Math.min(1, t) * 1.02 };
      const leader = leaders[index];
      leader.firstChild.setAttribute("d", `M${start.x.toFixed(1)} ${start.y.toFixed(1)}L${end.x.toFixed(1)} ${end.y.toFixed(1)}`);
      leader.lastChild.setAttribute("cx", end.x.toFixed(1));
      leader.lastChild.setAttribute("cy", end.y.toFixed(1));
    });
  }

  function build() {
    stage = twinStage({ kind: "petals" });
    data.petals.forEach((petal) => {
      const button = petalButton(petal);
      buttons.push(button);
      buttonGroup.append(button);
      const leader = svg("g", { class: "petal-leader" }, svg("path", { d: "" }), svg("circle", { r: "4.5" }));
      leaders.push(leader);
      lines.append(leader);
    });
    orbit.append(lines, stage.node, buttonGroup);
    node.append(
      el(
        "header",
        { class: "petal-section-head" },
        el("p", { class: "petal-eyebrow", text: "ツイン（AI）の読み" }),
        el("h2", { id: "petal-title", text: forSelf ? "5つの花びら" : "ツインの花びら" }),
        el("p", {
          class: "petal-lead",
          text: forSelf
            ? "あなたにとって、何で心が満たされ、何で損なわれるのか。ツイン（AI）が、これまでの話から読んだことを、5つの花びらで見せます。読みは推測です。違うと思うところは、ツインに話して教えてください。"
            : `${data.selfName ? `${data.selfName}にとって、` : ""}何で心が満たされ、何で損なわれるのか。ツイン（AI）が記録から読んだことを、5つの花びらで見せます。`,
        })
      ),
      // 広い PC では、左に姿とボタン、右に読みの窓（花びらを選んだとき、読みが画面の下に隠れないように）。
      // 姿の下に、5つの花びらが何かの説明を置く（tyumajinさんの依頼、2026-11-07。文はいただいたまま）
      el("div", { class: "petal-row" }, el("div", { class: "petal-orbit-box" }, orbit, aboutPetals()), reading),
      announce
    );
    idle();
    node.hidden = false;
    unlisten = stage.onPetals(() => {
      // 3Dを読み込んだときと大きさが変わったときは置き直し、揺れのたびには線だけ動かす
      const anchors = stage.petalAnchors();
      if (!geometry || anchors?.rest !== geometry.rest) {
        layout();
      } else {
        drawLines(anchors.live);
      }
    });
    observer = new ResizeObserver(() => layout());
    observer.observe(orbit);
    // 2Dの姿のまま（3Dを読み込む前）でも置いておく
    requestAnimationFrame(() => alive && layout());
  }

  Promise.all([api("/twin/petals"), forSelf ? Promise.resolve({}) : api("/advocacy/overview").catch(() => ({}))])
    .then(([petals, overview]) => {
      if (!alive) {
        return;
      }
      if (!petals.petals?.length) {
        node.remove();
        onEmpty?.();
        return;
      }
      data = { ...petals, selfName: overview.self_name || "" };
      build();
    })
    .catch(() => {
      // 託されていない人や、読めないときは、節ごと出さない。ほかの記録は読める
      node.remove();
      if (alive) {
        onEmpty?.();
      }
    });

  return {
    node,
    dispose() {
      alive = false;
      unlisten();
      observer?.disconnect();
      stage?.dispose();
    },
  };
}
