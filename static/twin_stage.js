/*
  役割：専門職の画面で、ツイン（AI）を光の台座の上に立たせる。台座の光の輪、床の点、後ろの光は CSS で描き、
        姿は3D（static/twin3d/）で描く。専門職の「記録」のいちばん上（花びらの舞台）と、ケア会議で使う。
  4つの極：4. 10年後に代弁する。
  守る原則：12（舞台の下に「ツイン（AI）」と出す。名前を別に出している会議の画面では省く）、
            13（色がのるのは花びらだけ。気分で変わるのは動きと台座の光だけで、花びらの色は変えない。数字を出さない）。

  3Dのファイルは、舞台を出すときにだけ読み込む。ほかの画面に重いファイルを読ませないため。
  読み込むまでと、3Dを描けないブラウザでは、同じ台座に2Dの花の子を立たせる（static/ui.js の twinDrawing）。
  気分：idle（ふだん）、calm（花びらの舞台のふだん）、thinking（考えている）、speaking（本人の言葉を読み上げている）。

  花びらの舞台（kind "petals"、static/petals.js）では、focusPetal で花びらを選び、petalAnchors と onPetals で
  頭の中心と花びらの位置を知らせる。位置は舞台の姿の枠（figure）の左上からの px。
  2Dの姿のときは、選んだ花びらに藍の輪郭を重ねて示す（色は変えない）。
*/

import { api } from "./api.js";
import { PETAL_SHAPE, currentTwinLevels, el, svg, twinDrawing } from "./ui.js";

const TWIN_NAME = "ツイン（AI）";
// 2Dの姿の中の、頭の中心と花びらの点（viewBox 0 0 100 100。static/ui.js の twinPetal と同じ）
const FLAT_HEAD = { x: 50, y: 46 };
const FLAT_ANCHOR = 31; // 花びらの先は頭の中心から 41。3Dの点（twin3d/stage.js の PETAL_ANCHOR）と同じ所
// 花びらを選んでから、台座の光が話すように動く時間（3Dの姿の話す揺れと同じ）
const SPEAK_MS = 1100;

/* 舞台を作る。kind は "petals"（専門職の「記録」の花びらの舞台）か "meeting"（ケア会議）。
   when を渡すと、その条件の画面の幅のときにだけ3Dを読み込む（隠れている幅では読み込まない）。 */
export function twinStage({ kind = "petals", name = true, when = null } = {}) {
  const figure = el("div", { class: "twin-stage-figure" });
  const burst = el("i", { class: "twin-stage-burst" });
  const node = el(
    "div",
    { class: `twin-stage twin-stage-${kind}`, "data-mood": "idle" },
    el(
      "div",
      { class: "twin-stage-scene" },
      el("div", { class: "twin-stage-light", "aria-hidden": "true" }),
      el("div", { class: "twin-stage-grid", "aria-hidden": "true" }),
      el("div", { class: "twin-stage-beam", "aria-hidden": "true" }),
      el("div", { class: "twin-stage-rings", "aria-hidden": "true" }, el("i"), el("i"), el("i"), burst),
      // 台座からのぼる光の粒。考えている間は速くなる
      el("div", { class: "twin-stage-motes", "aria-hidden": "true" }, ...Array.from({ length: 7 }, () => el("i"))),
      figure,
      // 考えている間だけ、足元から頭へ上がる光の輪
      el("div", { class: "twin-stage-scan", "aria-hidden": "true" })
    ),
    name ? el("p", { class: "twin-stage-name", text: TWIN_NAME }) : ""
  );
  let stage = null;
  let gone = false;
  let loading = false;
  let mood = kind === "petals" ? "calm" : "idle";
  let focus = null;
  let speakTimer = 0;
  let flatFocus = null; // 2Dの姿で、選んだ花びらに重ねる輪郭
  let rest = null; // 3Dの止まった姿での位置
  let live = null; // 3Dのいま描いた姿での花びらの位置
  const listeners = new Set();
  node.dataset.mood = mood;

  function flatTwin() {
    const drawing = twinDrawing(160, currentTwinLevels(), { free: true });
    if (kind === "petals") {
      flatFocus = svg("path", {
        class: "twin-flat-focus",
        d: PETAL_SHAPE,
        fill: "none",
        stroke: "#27507F",
        "stroke-width": "3",
        "stroke-linejoin": "round",
        "vector-effect": "non-scaling-stroke",
        visibility: "hidden",
      });
      // 花びらの上、頭の下に重ねる。頭の後ろに回る所は、姿と同じく頭に隠れる
      drawing.insertBefore(flatFocus, drawing.querySelector('[data-part="head"]'));
      showFlatFocus();
    }
    return drawing;
  }
  figure.append(flatTwin());

  function showFlatFocus() {
    if (!flatFocus) {
      return;
    }
    if (focus === null) {
      flatFocus.setAttribute("visibility", "hidden");
      return;
    }
    flatFocus.setAttribute("transform", `translate(50 46) rotate(${focus * 72}) scale(0.46 0.41)`);
    flatFocus.setAttribute("visibility", "visible");
  }

  function notify() {
    listeners.forEach((listener) => listener());
  }

  /* 2Dの姿に戻す。3Dを描けないときと、描く力を失ったとき */
  function fallBack() {
    stage = null;
    rest = null;
    live = null;
    node.classList.remove("is-3d");
    node.style.removeProperty("--feet");
    figure.replaceChildren(flatTwin());
    notify();
  }

  async function load() {
    if (loading || gone) {
      return;
    }
    loading = true;
    try {
      // 今の色を読み直す。ログインしたあとに本人が確かめて、色がのっていることがあるため
      const levels = await api("/twin/appearance")
        .then((data) => data.levels)
        .catch(() => currentTwinLevels());
      const { sharedTwinStage } = await import("./twin3d/stage.js");
      if (gone) {
        return;
      }
      stage = sharedTwinStage();
      const handlers = {
        onFeet: (percent) => node.style.setProperty("--feet", `${percent.toFixed(1)}%`),
        onLost: fallBack,
      };
      if (kind === "petals") {
        handlers.onRest = (anchors) => {
          rest = anchors;
          notify();
        };
        // 毎回描くたびに届く。0.5px より動いたときだけ知らせる
        handlers.onLive = (points) => {
          const moved = !live || points.some((point, index) => Math.abs(point.x - live[index].x) + Math.abs(point.y - live[index].y) > 0.5);
          if (moved) {
            live = points;
            notify();
          }
        };
      }
      flatFocus = null;
      stage.attach(figure, levels, handlers);
      stage.setMood(mood);
      // 3Dを読み込む前に選んだ花びらも、移したあとに選び直す
      if (focus !== null) {
        stage.focusPetal(focus, { speak: false });
      }
      node.classList.add("is-3d");
    } catch {
      fallBack();
    }
  }

  /* 2Dの姿の、頭の中心と花びらの点（figure の左上からの px） */
  function flatAnchors() {
    const drawing = figure.querySelector("svg");
    if (!drawing) {
      return null;
    }
    const box = drawing.getBoundingClientRect();
    const frame = figure.getBoundingClientRect();
    if (!box.width) {
      return null;
    }
    const at = (x, y) => ({ x: box.left - frame.left + (x / 100) * box.width, y: box.top - frame.top + (y / 100) * box.height });
    const petals = [0, 1, 2, 3, 4].map((index) => {
      const angle = (index * Math.PI * 2) / 5;
      return at(FLAT_HEAD.x + Math.sin(angle) * FLAT_ANCHOR, FLAT_HEAD.y - Math.cos(angle) * FLAT_ANCHOR);
    });
    return { head: at(FLAT_HEAD.x, FLAT_HEAD.y), petals };
  }

  let query = null;
  const onWidth = () => {
    if (query.matches) {
      load();
    }
  };
  if (when) {
    query = window.matchMedia(when);
    query.addEventListener("change", onWidth);
    onWidth();
  } else {
    load();
  }

  return {
    node,
    figure,
    setMood(next) {
      mood = next;
      node.dataset.mood = next;
      stage?.setMood(next);
    },
    /* 答えが届いたときに、台座の輪を1回広げる。hop が真なら姿も小さく跳ねる */
    pulse({ hop = true } = {}) {
      burst.classList.remove("go");
      void burst.offsetWidth; // 続けて届いたときも、もう一度動かす
      burst.classList.add("go");
      if (hop) {
        stage?.hop();
      }
    },
    /* 花びらを選ぶ（0〜4、上から時計回り）。null で選ぶのをやめる。台座の光も1秒ほど話すように動かす */
    focusPetal(index) {
      focus = Number.isInteger(index) ? index : null;
      stage?.focusPetal(focus);
      showFlatFocus();
      clearTimeout(speakTimer);
      if (focus === null) {
        node.dataset.mood = mood;
        return;
      }
      node.dataset.mood = "speaking";
      speakTimer = setTimeout(() => {
        node.dataset.mood = mood;
      }, SPEAK_MS);
    },
    /* 頭の中心と花びらの点。rest はボタンを置く場所（止まった姿）、live は引き出し線の先（いまの姿）。
       3Dを読み込む前と2Dの姿では、どちらも2Dの姿から測る */
    petalAnchors() {
      if (stage && rest) {
        return { rest, live: live || rest.petals };
      }
      const flat = flatAnchors();
      return flat ? { rest: flat, live: flat.petals } : null;
    },
    /* 位置が変わったら呼ぶ。戻り値は取り消し */
    onPetals(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    dispose() {
      gone = true;
      clearTimeout(speakTimer);
      listeners.clear();
      query?.removeEventListener("change", onWidth);
      stage?.detach(figure);
    },
  };
}
