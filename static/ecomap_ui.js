/*
  役割：つながりの地図（エコマップ）。本人を真ん中にして、家族を内側、縁のある人を外側に並べる。
  4つの極：2. 分かち合う。
  守る原則：1（線は本人が選ぶ。AI は提案しない）、10（今は本人だけに見せる）、12（ツインの姿を出す）。

  線の種類は太さと形でも分け、言葉でも添える。色だけで区別しない。
*/

import { api, photoUrl } from "./api.js";
import { add, avatar, el, openSheet, profilePhoto, svg, twinDrawing, twoStepButton } from "./ui.js";
import { openPersonForm } from "./people_ui.js";

// 幅360pxの画面で図が約0.82倍になるので、20 の文字が16px以上になる
const SIZE = 400;
const CENTER = SIZE / 2;
const BADGE = 28;
// 地図は幅318pxほどで表示される。続柄が縮んでも16px以上になる大きさにし、名前はそれより大きくする
const FONT = 24;
const ATTR_FONT = 20.5;
const NAME_LINE = FONT + 3;
const ATTR_LINE = ATTR_FONT + 3;
// 丸と線と文字のあいだに、最低これだけ空ける
const ENOUGH_ROOM = 7;
// 暗い表示でも同じ図を使うので、色は style.css の変数で渡す
const INK = "var(--ink)";
const STRESS = "var(--error)";

const STRENGTHS = [
  ["強い", "太い線。よく支え合っている"],
  ["ふつう", "ふつうの線。つながりがある"],
  ["弱い", "点線。ときどきのつながり"],
  ["ストレス", "ぎざぎざの線。気を使う、負担がある"],
];
const DIRECTIONS = [
  ["本人から", "本人から相手へ支えている"],
  ["相手から", "相手から本人へ支えられている"],
  ["お互い", "お互いに支え合っている"],
  ["なし", "支えの向きはない"],
];

function shortName(text) {
  const chars = Array.from(text || "");
  return chars.length > 7 ? `${chars.slice(0, 6).join("")}…` : chars.join("");
}

function linePath(x1, y1, x2, y2, zigzag) {
  if (!zigzag) {
    return `M${x1} ${y1} L${x2} ${y2}`;
  }
  const length = Math.hypot(x2 - x1, y2 - y1);
  const steps = Math.max(4, Math.round(length / 14));
  const ux = (x2 - x1) / length;
  const uy = (y2 - y1) / length;
  const parts = [`M${x1} ${y1}`];
  for (let index = 1; index < steps; index += 1) {
    const side = index % 2 === 0 ? 6 : -6;
    const px = x1 + ((x2 - x1) * index) / steps - uy * side;
    const py = y1 + ((y2 - y1) * index) / steps + ux * side;
    parts.push(`L${px.toFixed(1)} ${py.toFixed(1)}`);
  }
  parts.push(`L${x2} ${y2}`);
  return parts.join(" ");
}

function lineAttributes(link) {
  if (!link) {
    return { stroke: "var(--ink-soft)", "stroke-width": "2", "stroke-dasharray": "1 6", "stroke-linecap": "round", fill: "none" };
  }
  const base = { fill: "none", stroke: link.strength === "ストレス" ? STRESS : INK };
  if (link.strength === "強い") {
    return { ...base, "stroke-width": "5" };
  }
  if (link.strength === "弱い") {
    return { ...base, "stroke-width": "2.5", "stroke-dasharray": "8 7" };
  }
  return { ...base, "stroke-width": "2.5" };
}

function arrowEnds(link) {
  if (!link) {
    return {};
  }
  return {
    "marker-end": link.direction === "本人から" || link.direction === "お互い" ? "url(#map-arrow)" : undefined,
    "marker-start": link.direction === "相手から" || link.direction === "お互い" ? "url(#map-arrow-back)" : undefined,
  };
}

/* 文字の幅の見積もり。全角は文字の大きさ、半角はその6割。 */
function textWidth(text, size) {
  return Array.from(text).reduce((sum, char) => sum + (/[\x20-\x7e]/.test(char) ? size * 0.6 : size), 0);
}

/* 名前と続柄の箱。丸の上か下に2行で置き、図の幅からはみ出さないように寄せる。 */
function labelBox(spot, above = spot.above) {
  // 職種の札は枠の分だけ広い
  const width = Math.max(textWidth(spot.name, FONT), spot.attr ? textWidth(spot.attr, ATTR_FONT) + (spot.job ? 12 : 0) : 0);
  const height = NAME_LINE + (spot.attr ? ATTR_LINE : 0) + (spot.job ? 3 : 0);
  const left = Math.min(Math.max(spot.x - width / 2, 2), SIZE - width - 2);
  const top = above ? spot.y - spot.r - 5 - height : spot.y + spot.r + 5;
  return { left, top, right: left + width, bottom: top + height };
}

function badge({ spot, label, sublabel, photoId, shape = "circle", ring = false, tip, onPress }) {
  const { x, y } = spot;
  const group = svg("g", {
    class: "badge",
    role: "button",
    tabindex: "0",
    "aria-label": sublabel ? `${label}（${sublabel}）` : label,
    tip,
  });
  group.addEventListener("click", onPress);
  group.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onPress();
    }
  });
  if (shape === "square") {
    group.append(svg("rect", { x: x - BADGE, y: y - BADGE, width: BADGE * 2, height: BADGE * 2, rx: 10, fill: "var(--card)", stroke: "var(--sun)", "stroke-width": "2" }));
  } else {
    // 色のついた輪は本人にだけ付ける。丸いアイコンと同じ決まり（style.css の .avatar）
    group.append(svg("circle", { cx: x, cy: y, r: BADGE, fill: "var(--sun-soft)", stroke: ring ? "var(--frame)" : "var(--avatar-ring)", "stroke-width": ring ? "2.5" : "1.5" }));
    if (ring) {
      group.append(svg("circle", { cx: x, cy: y, r: BADGE + 5, fill: "none", stroke: "var(--sun)", "stroke-width": "2" }));
    }
  }
  const initial = Array.from(label)[0] || "？";
  group.append(svg("text", { x, y: y + 8, "text-anchor": "middle", "font-weight": "700", "font-size": FONT }, initial));
  if (photoId) {
    photoUrl(photoId).then((url) => {
      if (url) {
        const clipId = `clip-${photoId}-${Math.round(x)}-${Math.round(y)}`;
        group.append(
          svg("clipPath", { id: clipId }, svg("circle", { cx: x, cy: y, r: BADGE - 1 })),
          svg("image", { href: url, x: x - BADGE, y: y - BADGE, width: BADGE * 2, height: BADGE * 2, "clip-path": `url(#${clipId})`, preserveAspectRatio: "xMidYMid slice" })
        );
      }
    });
  }
  group.append(...labelTexts(spot));
  return group;
}

/* 名前と、その下の小さな続柄。 */
function labelTexts(spot) {
  const box = labelBox(spot);
  const middle = (box.left + box.right) / 2;
  const texts = [svg("text", { class: "map-name", x: middle, y: box.top + FONT - 3, "text-anchor": "middle", "font-size": FONT }, spot.name)];
  if (spot.attr) {
    // 専門職の職種は枠つきの札にする。色だけで分けないため
    if (spot.job) {
      const width = textWidth(spot.attr, ATTR_FONT) + 10;
      texts.push(svg("rect", { class: "map-job", x: middle - width / 2, y: box.top + NAME_LINE - 1, width, height: ATTR_LINE + 2, rx: 4 }));
    }
    texts.push(svg("text", { class: "map-attr", x: middle, y: box.top + NAME_LINE + ATTR_FONT - 3, "text-anchor": "middle", "font-size": ATTR_FONT }, spot.attr));
  }
  return texts;
}

/* 点と箱の距離を返す。 */
function distanceToBox(px, py, box) {
  return Math.hypot(Math.max(box.left - px, 0, px - box.right), Math.max(box.top - py, 0, py - box.bottom));
}

/* 箱どうしの距離を返す。 */
function boxGap(a, b) {
  return Math.hypot(Math.max(a.left - b.right, b.left - a.right, 0), Math.max(a.top - b.bottom, b.top - a.bottom, 0));
}

/* 本人から相手へ引く線の両端。線は丸の手前で止める。 */
function spokeEnds(center, spot) {
  const length = Math.hypot(spot.x - center.x, spot.y - center.y);
  const ux = (spot.x - center.x) / length;
  const uy = (spot.y - center.y) / length;
  return { x1: center.x + ux * (center.r + 2), y1: center.y + uy * (center.r + 2), x2: spot.x - ux * (spot.r + 3), y2: spot.y - uy * (spot.r + 3) };
}

/* 丸、文字、線のあいだの、いちばん狭い所の広さを返す。limit より狭いと分かったら、そこで打ち切る。 */
function narrowest(spots, center, limit) {
  const all = [center, ...spots];
  const boxes = all.map((spot) => labelBox(spot));
  let room = Infinity;
  for (let i = 0; i < all.length; i += 1) {
    for (let j = i + 1; j < all.length; j += 1) {
      room = Math.min(room, Math.hypot(all[i].x - all[j].x, all[i].y - all[j].y) - all[i].r - all[j].r, boxGap(boxes[i], boxes[j]));
    }
    for (let j = 0; j < all.length; j += 1) {
      if (i !== j) {
        room = Math.min(room, distanceToBox(all[j].x, all[j].y, boxes[i]) - all[j].r);
      }
    }
    if (room < limit) {
      return room;
    }
  }
  for (const spot of spots) {
    const line = spokeEnds(center, spot);
    const steps = Math.max(2, Math.ceil(Math.hypot(line.x2 - line.x1, line.y2 - line.y1) / 5));
    for (let step = 0; step <= steps; step += 1) {
      const px = line.x1 + ((line.x2 - line.x1) * step) / steps;
      const py = line.y1 + ((line.y2 - line.y1) * step) / steps;
      all.forEach((other, index) => {
        room = Math.min(room, distanceToBox(px, py, boxes[index]));
        if (other !== spot && other !== center) {
          room = Math.min(room, Math.hypot(px - other.x, py - other.y) - other.r);
        }
      });
    }
    if (room < limit) {
      return room;
    }
  }
  return room;
}

/* 人の置き場所を選ぶ。

   家族を内側、縁のある人を外側に、交互に並べる。本人の名前は丸の下に置くので、真下を空ける。
   中心より上の人は文字を丸の上に置く。線は中心から来るので、丸の下に文字があると必ず線が通るため。
   丸、文字、線が十分に離れる形のうち、いちばん小さく収まるものを選ぶ。図の高さは人数に合わせて伸ばす。
*/
function arrange(innerItems, outerItems, center) {
  const total = innerItems.length + outerItems.length;
  const spacing = (total > 1 ? Math.min(240 / (total - 1), 72) : 0) * (Math.PI / 180);
  let best = null;
  for (let pattern = 0; pattern < Math.max(total, 1); pattern += 1) {
    // 内側と外側を、同じ数ずつ散らして交互にする
    const order = [];
    let innerIndex = 0;
    let outerIndex = 0;
    for (let slot = 0; slot < total; slot += 1) {
      const isInner = innerItems.length > 0 && Math.floor(((slot + 1 + pattern) * innerItems.length) / total) > Math.floor(((slot + pattern) * innerItems.length) / total);
      if ((isInner && innerIndex < innerItems.length) || outerIndex >= outerItems.length) {
        order.push({ spot: innerItems[innerIndex], ring: "inner" });
        innerIndex += 1;
      } else {
        order.push({ spot: outerItems[outerIndex], ring: "outer" });
        outerIndex += 1;
      }
    }
    for (const turn of [0, -8, 8, -16, 16, -24, 24]) {
      for (const innerRadius of [118, 130, 142, 154]) {
        for (const outerRadius of [178, 192, 206, 220]) {
          // 人が増えたら、図を縦に大きく伸ばして場所を作る
          for (const stretch of total > 8 ? [1.5, 1.8, 2.1, 2.4, 2.8, 3.2, 3.6] : [1, 1.12, 1.24, 1.36, 1.5, 1.7]) {
            const spots = order.map(({ spot, ring }, index) => {
              const angle = -Math.PI / 2 + (turn * Math.PI) / 180 + (index - (total - 1) / 2) * spacing;
              const radius = ring === "inner" ? innerRadius : outerRadius;
              const x = Math.min(Math.max(CENTER + radius * Math.cos(angle), spot.r + 2), SIZE - spot.r - 2);
              // 縦に伸ばすのは外側の人だけ。家族が内側にいると見て分かるようにするため
              const y = CENTER + radius * (ring === "inner" ? 1 : stretch) * Math.sin(angle);
              return { ...spot, x, y, above: y < CENTER };
            });
            const room = narrowest(spots, center, best ? Math.min(best.score, ENOUGH_ROOM) : -Infinity);
            const score = Math.min(room, ENOUGH_ROOM);
            const edges = [center, ...spots].flatMap((spot) => {
              const box = labelBox(spot);
              return [spot.y - spot.r, spot.y + spot.r, box.top, box.bottom];
            });
            const top = Math.min(...edges) - 8;
            const height = Math.max(...edges) + 8 - top;
            if (!best || score > best.score || (score === best.score && height < best.height)) {
              best = { spots, score, top, height };
            }
          }
        }
      }
    }
  }
  return best || { spots: [], score: ENOUGH_ROOM, top: 100, height: 200 };
}

function linePicker(target, onDone) {
  const current = target.link || {};
  let strength = current.strength || "ふつう";
  let direction = current.direction || "お互い";
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const group = (name, choices, value, set) =>
    el(
      "div",
      { class: "stack" },
      choices.map(([word, meaning]) => {
        const input = el("input", { type: "radio", name, checked: word === value });
        input.addEventListener("change", () => set(word));
        return el("label", { class: "check" }, input, `${word}：${meaning}`);
      })
    );
  return el(
    "div",
    { class: "stack" },
    target.link ? "" : el("p", { class: "hint", text: "線はまだ選んでいません。選んで「この線にする」を押すと決まります。" }),
    el("h3", { text: "線の種類" }),
    group("map-strength", STRENGTHS, strength, (word) => {
      strength = word;
    }),
    el("h3", { text: "支えの向き" }),
    group("map-direction", DIRECTIONS, direction, (word) => {
      direction = word;
    }),
    message,
    el(
      "div",
      { class: "sheet-actions" },
      el("button", {
        type: "button",
        class: "primary wide",
        text: "この線にする",
        tip: "選んだ線の種類と支えの向きを地図に残します",
        onclick: async () => {
          try {
            await api("/map/links", { method: "PUT", json: { ...target.key, strength, direction } });
            onDone();
          } catch (error) {
            message.textContent = error.message;
            message.hidden = false;
          }
        },
      })
    )
  );
}

function legend() {
  const sample = (attributes, zigzag = false) =>
    svg("svg", { viewBox: "0 0 44 22", "aria-hidden": "true" }, svg("path", { d: linePath(2, 11, 42, 11, zigzag), ...attributes }));
  return el(
    "ul",
    { class: "legend" },
    el("li", {}, sample(lineAttributes({ strength: "強い" })), "太い線：強いつながり"),
    el("li", {}, sample(lineAttributes({ strength: "ふつう" })), "ふつうの線：ふつうのつながり"),
    el("li", {}, sample(lineAttributes({ strength: "弱い" })), "点線：弱いつながり"),
    el("li", {}, sample(lineAttributes({ strength: "ストレス" }), true), "ぎざぎざの線：気を使うつながり"),
    el("li", {}, sample(lineAttributes(null)), "細い点の線：まだ選んでいません"),
    el("li", {}, "枠つきの札の人は、専門職です。"),
    el("li", {}, "矢印は支えの向きです。")
  );
}

/* 地図を描く。 */
export async function renderEcomap(section, ctx, refresh) {
  section.replaceChildren(el("h2", { text: "つながりの地図" }));
  let data;
  try {
    data = await api("/map");
  } catch (error) {
    section.append(el("p", { class: "error", text: error.message }));
    return;
  }
  const family = data.people.filter((item) => item.person.role === "家族");
  const others = data.people.filter((item) => item.person.role !== "家族");
  const outerItems = [...others, ...data.places];

  const nameOf = (item) => (item.person ? item.person.called_name || item.person.name : item.place.name);
  // 続柄は登録の言葉をそのまま出す（長女、かかりつけ医など）。なければ立場を出す
  const attrOf = (item) => (item.person ? item.person.profession || item.person.relation || item.person.role : item.place.kind || "つながり");
  const toSpot = (item) => ({ item, r: BADGE, name: shortName(nameOf(item)), attr: attrOf(item), job: item.person?.role === "専門職" });
  const center = { x: CENTER, y: CENTER, r: BADGE + 5, name: shortName(data.self.called_name || data.self.name), attr: "本人", above: false };
  // ツインは内側に置く。本人のそばにいることを示すため
  const twinItem = { twin: true, r: BADGE + 4, name: "ツイン（AI）", attr: "" };
  // 専門職は隣どうしに置き、外側の輪の最後にまとめる
  const outerSpots = outerItems.map(toSpot);
  const layout = arrange([twinItem, ...family.map(toSpot)], [...outerSpots.filter((spot) => !spot.job), ...outerSpots.filter((spot) => spot.job)], center);
  const placed = layout.spots.filter((spot) => !spot.twin);
  const twinSpot = layout.spots.find((spot) => spot.twin);

  const drawing = svg(
    "svg",
    { viewBox: `0 ${layout.top} ${SIZE} ${layout.height}`, class: "diagram", role: "group", "aria-label": "本人を真ん中にしたつながりの地図" },
    svg(
      "defs",
      {},
      // 矢印の大きさは線の太さに合わせず固定する。太い線で矢印だけが大きくなりすぎないようにするため。
      svg("marker", { id: "map-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "13", markerHeight: "13", markerUnits: "userSpaceOnUse", orient: "auto" }, svg("path", { d: "M0 0 L10 5 L0 10 z", fill: INK })),
      svg("marker", { id: "map-arrow-back", viewBox: "0 0 10 10", refX: "1", refY: "5", markerWidth: "13", markerHeight: "13", markerUnits: "userSpaceOnUse", orient: "auto" }, svg("path", { d: "M10 0 L0 5 L10 10 z", fill: INK }))
    )
  );

  // 線を先に描き、バッジで線の端を隠さないようにする
  for (const spot of placed) {
    const line = spokeEnds(center, spot);
    const zigzag = spot.item.link?.strength === "ストレス";
    drawing.append(
      svg("path", {
        class: "map-line",
        d: linePath(line.x1, line.y1, line.x2, line.y2, zigzag),
        ...lineAttributes(spot.item.link),
        ...arrowEnds(spot.item.link),
      })
    );
  }

  // ツインは本人のそばにいることだけを、細い点の線で示す。関係の強さは選ばない
  if (twinSpot) {
    const line = spokeEnds(center, twinSpot);
    drawing.append(svg("path", { class: "map-line", d: linePath(line.x1, line.y1, line.x2, line.y2, false), ...lineAttributes(null) }));
  }

  const reopen = () => refresh();
  for (const spot of placed) {
    const { item } = spot;
    if (item.person) {
      drawing.append(
        badge({
          spot,
          label: nameOf(item),
          sublabel: spot.attr,
          photoId: item.person.photo_id,
          tip: `${nameOf(item)}のプロフィールを見て、トークや線の種類を選べます`,
          onPress: () => openPersonSheet(ctx, item, reopen),
        })
      );
    } else {
      drawing.append(
        badge({
          spot,
          label: item.place.name,
          sublabel: spot.attr,
          shape: "square",
          tip: "このつながりの線を選んだり、はずしたりできます",
          onPress: () => openPlaceSheet(item, reopen),
        })
      );
    }
  }
  drawing.append(
    badge({
      spot: center,
      label: data.self.called_name || data.self.name,
      sublabel: "本人",
      photoId: data.self.photo_id,
      ring: true,
      tip: "自分のプロフィールを修正する窓を開きます",
      onPress: () => openPersonForm(ctx, data.self, reopen),
    })
  );
  // ツインのバッジは本人のそばに置く。写真には替えられない。
  const twinSize = twinItem.r * 2;
  const twinGroup = svg("g", {
    class: "badge",
    role: "button",
    tabindex: "0",
    "aria-label": "ツイン（AI）とのトークを開く",
    tip: "ツイン（AI）と話すトークの画面へ移ります",
  });
  const figure = twinDrawing(twinSize);
  figure.setAttribute("x", twinSpot.x - twinSize / 2);
  figure.setAttribute("y", twinSpot.y - twinSize / 2);
  twinGroup.append(figure, ...labelTexts(twinSpot));
  twinGroup.addEventListener("click", () => openTwinRoom(ctx));
  twinGroup.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openTwinRoom(ctx);
    }
  });
  drawing.append(twinGroup);

  const words = el("ul", { class: "legend" });
  for (const { item } of placed) {
    const label = item.person ? item.person.called_name || item.person.name : item.place.name;
    const link = item.link;
    words.append(el("li", { text: link ? `${label}：${link.strength}・${link.direction}` : `${label}：線はまだ選んでいません` }));
  }

  section.append(
    el("p", { class: "hint", text: "丸を押すと、プロフィールとトークの開始、線の種類を選べます。この地図は本人だけに見えます。" }),
    drawing,
    el("details", { class: "fold" }, el("summary", { text: "線の意味を見る", tip: "線の太さや形、矢印が何を表すかを開いて見ます" }), legend(), words),
    el(
      "div",
      { class: "row" },
      el("button", { type: "button", text: "つながりを足す", tip: "病院や町内会など、人のほかのつながりを地図に足します", onclick: () => openNewPlaceSheet(reopen) })
    )
  );
}

async function openTwinRoom(ctx) {
  const { items } = await api("/rooms");
  ctx.openTab("talk", { roomId: items[0].id });
}

function openPersonSheet(ctx, item, onDone) {
  openSheet((inner, close) => {
    const person = item.person;
    const details = [person.role, person.profession || person.relation, person.residence].filter(Boolean).join("・");
    const message = el("p", { class: "error", role: "alert", hidden: true });
    add(
      inner,
      profilePhoto(person),
      el("div", { class: "row" }, avatar(person, "large"), el("div", {}, el("h2", { text: person.called_name || person.name }), el("div", { class: "hint", text: details }))),
      person.memo ? el("p", { text: person.memo }) : null,
      el(
        "div",
        { class: "row" },
        el("button", {
          type: "button",
          class: "primary",
          text: "トークを始める",
          tip: "この人と二人で話す部屋を開きます",
          onclick: async () => {
            try {
              const room = await api("/rooms", { method: "POST", json: { kind: "1対1", member_ids: [person.id] } });
              close();
              ctx.openTab("talk", { roomId: room.id });
            } catch (error) {
              message.textContent = error.message;
              message.hidden = false;
            }
          },
        }),
        el("button", {
          type: "button",
          // 直すボタンの言葉は「修正」にそろえる（2026-11-06 ご本人の決定）
          text: "修正",
          tip: "この人の名前や立場、写真を修正する窓を開きます",
          onclick: () => {
            close();
            openPersonForm(ctx, person, onDone);
          },
        }),
        el("button", { type: "button", class: "quiet", text: "閉じる", tip: "この窓を閉じて、つながりの地図へ戻ります", onclick: close })
      ),
      message,
      linePicker(
        { key: { user_id: person.id }, link: item.link },
        () => {
          close();
          onDone();
        }
      )
    );
  });
}

function openPlaceSheet(item, onDone) {
  openSheet((inner, close) => {
    const message = el("p", { class: "error", role: "alert", hidden: true });
    add(
      inner,
      el("h2", { text: item.place.name }),
      item.place.kind ? el("p", { class: "hint", text: item.place.kind }) : null,
      linePicker(
        { key: { place_id: item.place.id }, link: item.link },
        () => {
          close();
          onDone();
        }
      ),
      message,
      el(
        "div",
        { class: "row" },
        twoStepButton(
          "このつながりをはずす",
          "もう一度押すと、はずします",
          async () => {
            try {
              await api(`/map/places/${item.place.id}/remove`, { method: "POST" });
              close();
              onDone();
            } catch (error) {
              message.textContent = error.message;
              message.hidden = false;
            }
          },
          "quiet",
          "このつながりを地図からはずします"
        ),
        el("button", { type: "button", class: "quiet", text: "閉じる", tip: "この窓を閉じて、つながりの地図へ戻ります", onclick: close })
      )
    );
  });
}

function openNewPlaceSheet(onDone) {
  openSheet((inner, close) => {
    const name = el("input", { type: "text", maxLength: 40, required: true, placeholder: "例：ひまわりデイサービス" });
    const kind = el("input", { type: "text", maxLength: 40, placeholder: "例：病院、デイサービス、町内会" });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    const form = el(
      "form",
      { class: "stack" },
      el("h2", { text: "つながりを足す" }),
      el("p", { class: "hint", text: "病院、デイサービス、町内会など、人のほかのつながりです。ログインはできません。" }),
      el("label", { class: "field" }, "名前", name),
      el("label", { class: "field" }, "種類", kind),
      message,
      el("button", { type: "submit", class: "primary wide", text: "足す", tip: "書いた名前と種類で、つながりを地図に足します" }),
      el("button", { type: "button", class: "quiet wide", text: "やめる", tip: "何も足さずに、この窓を閉じます", onclick: close })
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await api("/map/places", { method: "POST", json: { name: name.value, kind: kind.value || null } });
        close();
        onDone();
      } catch (error) {
        message.textContent = error.message;
        message.hidden = false;
      }
    });
    inner.append(form);
  });
}
