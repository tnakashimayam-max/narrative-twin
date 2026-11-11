/*
  役割：家系図（ジェノグラム）。登録した親子と夫婦の関係から、親から孫までの図を SVG で描く。
  4つの極：2. 分かち合う。
  守る原則：1（関係を決めるのは本人）、10（今は本人だけに見せる）。

  男性は四角、女性は丸、不明はひし形、亡くなった方は×、同居の人は囲みで示す。
  本人だけは顔を丸く切り抜き、外に藍の線を2本回す。四角で顔を囲まないため、本人は形で性別を示さない。
  図の意味を言葉でも添える。色だけで区別しない。
*/

import { api, photoUrl } from "./api.js";
import { el, openSheet, svg, twoStepButton } from "./ui.js";

const CELL = 112;
const ROW = 138;
const SIZE = 25;
const MARGIN = 40;
const MIN_WIDTH = 328;
// 暗い表示でも同じ図を使うので、色は style.css の変数で渡す
const INK = "var(--ink)";

/* 本人の印。顔か頭文字を丸の中に置き、線の数で本人と分かるようにする（色だけで区別しない）。 */
function selfShape(person, x, y) {
  const ring = { fill: "none", stroke: "var(--frame)", "stroke-width": "2" };
  const initial = svg("text", { x, y: y + 6, "text-anchor": "middle", "font-size": "17", "font-weight": "700" }, Array.from(person.called_name || person.name)[0]);
  const mark = svg(
    "g",
    {},
    svg("circle", { cx: x, cy: y, r: SIZE, fill: "var(--card)", stroke: "var(--frame)", "stroke-width": "2" }),
    initial,
    svg("circle", { cx: x, cy: y, r: SIZE + 5, ...ring })
  );
  if (person.photo_id) {
    photoUrl(person.photo_id).then((url) => {
      if (url) {
        const clipId = `genogram-self-${person.photo_id}`;
        initial.replaceWith(
          svg("clipPath", { id: clipId }, svg("circle", { cx: x, cy: y, r: SIZE - 1 })),
          svg("image", { href: url, x: x - SIZE, y: y - SIZE, width: SIZE * 2, height: SIZE * 2, "clip-path": `url(#${clipId})`, preserveAspectRatio: "xMidYMid slice" })
        );
      }
    });
  }
  return [mark];
}

function shape(person, x, y) {
  if (person.is_self) {
    return selfShape(person, x, y);
  }
  // 形で男性・女性を示す
  const base = { fill: "var(--card)", stroke: "var(--sun)", "stroke-width": "2.5" };
  const outer = (gap = 0) => {
    const half = SIZE + gap;
    if (person.gender === "男性") {
      return svg("rect", { x: x - half, y: y - half, width: half * 2, height: half * 2, ...base, fill: gap ? "none" : base.fill });
    }
    if (person.gender === "女性") {
      return svg("circle", { cx: x, cy: y, r: half, ...base, fill: gap ? "none" : base.fill });
    }
    const reach = half + 5;
    return svg("path", { d: `M${x} ${y - reach} L${x + reach} ${y} L${x} ${y + reach} L${x - reach} ${y} Z`, ...base, fill: gap ? "none" : base.fill });
  };
  const parts = [outer()];
  if (person.is_deceased) {
    const reach = SIZE - 4;
    parts.push(
      svg("path", { d: `M${x - reach} ${y - reach} L${x + reach} ${y + reach} M${x + reach} ${y - reach} L${x - reach} ${y + reach}`, stroke: INK, "stroke-width": "2.5", fill: "none" })
    );
  }
  return parts;
}

/* 世代ごとに並べる。夫婦は隣にする。 */
function layout(data) {
  const people = data.people.map((person) => ({ ...person, is_self: person.id === data.self_id }));
  const spouses = new Map();
  for (const link of data.links) {
    if (link.kind === "夫婦") {
      spouses.set(link.from_user_id, [...(spouses.get(link.from_user_id) || []), link.to_user_id]);
      spouses.set(link.to_user_id, [...(spouses.get(link.to_user_id) || []), link.from_user_id]);
    }
  }
  const rows = new Map([[-1, []], [0, []], [1, []], [2, []]]);
  const placed = new Set();
  const byId = new Map(people.map((person) => [person.id, person]));
  const ordered = [...people].sort((a, b) => Number(b.is_self) - Number(a.is_self));
  for (const person of ordered) {
    if (placed.has(person.id)) {
      continue;
    }
    const row = rows.get(person.generation);
    row.push(person);
    placed.add(person.id);
    for (const spouseId of spouses.get(person.id) || []) {
      const spouse = byId.get(spouseId);
      if (spouse && !placed.has(spouseId) && spouse.generation === person.generation) {
        row.push(spouse);
        placed.add(spouseId);
      }
    }
  }
  const columns = Math.max(...[...rows.values()].map((row) => row.length), 1);
  // 幅360pxの画面の窓の内側に収める。3人並ぶ世代があるときだけ、図の中で横に動かす
  const width = Math.max(columns * CELL + MARGIN * 2, MIN_WIDTH);
  const positions = new Map();
  // 人がいる世代の分だけ縦に並べる。1人だけのときに図の真ん中へぽつんと出さないため
  const usedRows = [...rows.values()].filter((row) => row.length > 0);
  usedRows.forEach((row, rowIndex) => {
    const start = (width - (row.length - 1) * CELL) / 2;
    row.forEach((person, index) => {
      positions.set(person.id, { x: start + index * CELL, y: MARGIN + 20 + rowIndex * ROW, person });
    });
  });
  return { positions, width, height: MARGIN + 20 + (usedRows.length - 1) * ROW + SIZE + 45, people };
}

function draw(data) {
  const { positions, width, height, people } = layout(data);
  const drawing = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "diagram",
    role: "img",
    "aria-label": "親から孫までの家系図",
    style: `min-width:${Math.min(width, 640)}px;max-width:none`,
  });
  // 同居の人の囲み
  const together = people.filter((person) => person.residence === "同居" || (person.is_self && people.some((p) => p.residence === "同居")));
  if (together.length > 1) {
    const points = together.map((person) => positions.get(person.id));
    const left = Math.min(...points.map((p) => p.x)) - SIZE - 18;
    const right = Math.max(...points.map((p) => p.x)) + SIZE + 18;
    const top = Math.min(...points.map((p) => p.y)) - SIZE - 14;
    const bottom = Math.max(...points.map((p) => p.y)) + SIZE + 34;
    drawing.append(svg("rect", { x: left, y: top, width: right - left, height: bottom - top, rx: 14, fill: "var(--sun-soft)", stroke: "var(--frame)", "stroke-width": "2", "stroke-dasharray": "8 6" }));
  }
  const line = { stroke: INK, "stroke-width": "2", fill: "none" };
  const spousePairs = [];
  for (const link of data.links) {
    if (link.kind === "夫婦") {
      const a = positions.get(link.from_user_id);
      const b = positions.get(link.to_user_id);
      if (a && b) {
        drawing.append(svg("path", { d: `M${a.x} ${a.y} L${b.x} ${b.y}`, ...line }));
        spousePairs.push([link.from_user_id, link.to_user_id]);
      }
    }
  }
  // 親が夫婦なら、2人の間から子へ線を下ろす
  const parentsOf = new Map();
  for (const link of data.links) {
    if (link.kind === "親子") {
      parentsOf.set(link.to_user_id, [...(parentsOf.get(link.to_user_id) || []), link.from_user_id]);
    }
  }
  for (const [childId, parentIds] of parentsOf) {
    const child = positions.get(childId);
    const parents = parentIds.map((id) => positions.get(id)).filter(Boolean);
    if (!child || parents.length === 0) {
      continue;
    }
    const fromX = parents.reduce((sum, parent) => sum + parent.x, 0) / parents.length;
    // 親が1人のときは、名前を線が横切らないよう、名前の下から下ろす
    const fromY = parents[0].y + (parents.length === 1 ? SIZE + 34 : 0);
    const middleY = (fromY + child.y - SIZE) / 2 + 8;
    drawing.append(svg("path", { d: `M${fromX} ${fromY} L${fromX} ${middleY} L${child.x} ${middleY} L${child.x} ${child.y - SIZE - 5}`, ...line }));
  }
  for (const { x, y, person } of positions.values()) {
    drawing.append(
      ...shape(person, x, y),
      // 本人のほかは、頭文字を置く
      person.is_self ? "" : svg("text", { x, y: y + 6, "text-anchor": "middle", "font-size": "17", "font-weight": "700" }, Array.from(person.called_name || person.name)[0]),
      svg("text", { x, y: y + SIZE + 26, "text-anchor": "middle", "font-size": "17" }, Array.from(person.called_name || person.name).slice(0, 7).join(""))
    );
  }
  return drawing;
}

function legend() {
  const icon = (nodes) => svg("svg", { viewBox: "0 0 44 22", "aria-hidden": "true" }, nodes);
  const line = { fill: "none", stroke: INK, "stroke-width": "1.5" };
  return el(
    "ul",
    { class: "legend" },
    el("li", {}, icon(svg("rect", { x: 14, y: 3, width: 16, height: 16, ...line })), "四角：男性"),
    el("li", {}, icon(svg("circle", { cx: 22, cy: 11, r: 8, ...line })), "丸：女性"),
    el("li", {}, icon(svg("path", { d: "M22 1 L32 11 L22 21 L12 11 Z", ...line })), "ひし形：性別が分からない、答えない"),
    el("li", {}, icon([svg("circle", { cx: 22, cy: 11, r: 6, ...line }), svg("circle", { cx: 22, cy: 11, r: 10, ...line })]), "二重の丸の線と顔：ご本人（形で性別を示しません）"),
    el("li", {}, icon(svg("path", { d: "M14 3 L30 19 M30 3 L14 19", ...line })), "×：亡くなった方"),
    el("li", {}, icon(svg("rect", { x: 4, y: 3, width: 36, height: 16, rx: 4, ...line, "stroke-dasharray": "4 3" })), "点線の囲み：同居している人"),
    el("li", {}, "横の線は夫婦、下へ伸びる線は親子です。")
  );
}

export async function openGenogram(ctx) {
  openSheet(async (inner, close) => {
    async function redraw() {
      inner.replaceChildren(el("h2", { text: "家系図" }), el("p", { role: "status", text: "読み込んでいます。" }));
      let data;
      let everyone;
      try {
        data = await api("/genogram");
        everyone = (await api("/people")).items;
      } catch (error) {
        inner.replaceChildren(el("p", { class: "error", text: error.message }), el("button", { type: "button", text: "閉じる", tip: "家系図の窓を閉じます", onclick: close }));
        return;
      }
      const names = new Map(everyone.map((person) => [person.id, person.called_name || person.name]));
      const message = el("p", { class: "error", role: "alert", hidden: true });
      const kind = el("select", {}, el("option", { value: "親子", text: "親子" }), el("option", { value: "夫婦", text: "夫婦" }));
      const personOptions = () => everyone.map((person) => el("option", { value: String(person.id), text: person.called_name || person.name }));
      const first = el("select", {}, personOptions());
      const second = el("select", {}, personOptions());
      const firstLabel = el("span", { text: "親" });
      const secondLabel = el("span", { text: "子" });
      kind.addEventListener("change", () => {
        firstLabel.textContent = kind.value === "親子" ? "親" : "1人目";
        secondLabel.textContent = kind.value === "親子" ? "子" : "2人目";
      });
      const form = el(
        "form",
        { class: "stack card" },
        el("h3", { text: "関係を足す" }),
        el("label", { class: "field" }, "関係", kind),
        el("label", { class: "field" }, firstLabel, first),
        el("label", { class: "field" }, secondLabel, second),
        message,
        el("button", { type: "submit", class: "primary wide", text: "この関係を足す", tip: "選んだ二人の親子か夫婦の関係を、家系図に描き足します" })
      );
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        message.hidden = true;
        try {
          await api("/family-links", {
            method: "POST",
            json: { kind: kind.value, from_user_id: Number(first.value), to_user_id: Number(second.value) },
          });
          redraw();
        } catch (error) {
          message.textContent = error.message;
          message.hidden = false;
        }
      });
      const words = el("ul", { class: "legend" });
      for (const link of data.all_links) {
        const a = names.get(link.from_user_id) || "はずした人";
        const b = names.get(link.to_user_id) || "はずした人";
        words.append(
          el(
            "li",
            {},
            el("span", { style: "flex:1", text: link.kind === "親子" ? `${a}は${b}の親です` : `${a}と${b}は夫婦です` }),
            twoStepButton(
              "消す",
              "もう一度押すと消します",
              async () => {
                await api(`/family-links/${link.id}`, { method: "DELETE" });
                redraw();
              },
              "quiet",
              "この関係を家系図から消します"
            )
          )
        );
      }
      inner.replaceChildren(
        el("div", { class: "row spread" }, el("h2", { text: "家系図" }), el("button", { type: "button", class: "quiet", text: "閉じる", tip: "家系図の窓を閉じます", onclick: close })),
        el("p", { class: "hint", text: "親子と夫婦の関係から、親から孫までを描きます。この図は本人だけに見えます。" }),
        data.all_links.length ? "" : el("p", { class: "hint", text: "まだ関係がありません。下の「関係を足す」から足せます。" }),
        el("div", { style: "overflow-x:auto" }, draw(data)),
        legend(),
        data.all_links.length ? words : "",
        form
      );
    }
    redraw();
  });
}
