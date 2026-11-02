/*
  役割：つながりの地図（エコマップ）。本人を真ん中にして、家族を内側、縁のある人を外側に並べる。
  4つの極：2. 分かち合う。
  守る原則：1（線は本人が選ぶ。AI は提案しない）、10（今は本人だけに見せる）、12（ツインの姿を出す）。

  線の種類は太さと形でも分け、言葉でも添える。色だけで区別しない。
*/

import { api, photoUrl } from "./api.js";
import { avatar, el, openSheet, svg, twinDrawing } from "./ui.js";
import { openPersonForm } from "./people_ui.js";

// 幅360pxの画面で図が約0.86倍になるので、19 の文字が16px以上になる
const SIZE = 380;
const CENTER = SIZE / 2;
const INNER = 98;
const OUTER = 158;
const BADGE = 26;
const FONT = 19;
const INK = "#27477a";
const STRESS = "#9b1c1c";

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
    return { stroke: "#a89a7f", "stroke-width": "1.5", "stroke-dasharray": "2 6", fill: "none" };
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

function badge({ x, y, label, sublabel, photoId, shape = "circle", ring = false, onPress, labelAbove = false }) {
  const group = svg("g", {
    class: "badge",
    role: "button",
    tabindex: "0",
    "aria-label": sublabel ? `${label}（${sublabel}）` : label,
  });
  group.addEventListener("click", onPress);
  group.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onPress();
    }
  });
  if (shape === "square") {
    group.append(svg("rect", { x: x - BADGE, y: y - BADGE, width: BADGE * 2, height: BADGE * 2, rx: 10, fill: "#fdfaf1", stroke: INK, "stroke-width": "2" }));
  } else {
    group.append(svg("circle", { cx: x, cy: y, r: BADGE, fill: "#dfe6f1", stroke: INK, "stroke-width": "2" }));
    if (ring) {
      group.append(svg("circle", { cx: x, cy: y, r: BADGE + 5, fill: "none", stroke: INK, "stroke-width": "2" }));
    }
  }
  const initial = Array.from(label)[0] || "？";
  group.append(svg("text", { x, y: y + 7, "text-anchor": "middle", "font-weight": "700", "font-size": FONT + 2 }, initial));
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
  // 図の端では、名前が切れないように寄せる
  const anchor = x < 80 ? "start" : x > SIZE - 80 ? "end" : "middle";
  const textX = anchor === "start" ? x - BADGE : anchor === "end" ? x + BADGE : x;
  group.append(
    svg("text", { x: textX, y: labelAbove ? y - BADGE - 8 : y + BADGE + 20, "text-anchor": anchor, "font-size": FONT }, shortName(label))
  );
  return group;
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
    el("h3", { text: "線の種類" }),
    group("map-strength", STRENGTHS, strength, (word) => {
      strength = word;
    }),
    el("h3", { text: "支えの向き" }),
    group("map-direction", DIRECTIONS, direction, (word) => {
      direction = word;
    }),
    message,
    el("button", {
      type: "button",
      class: "primary wide",
      text: "この線にする",
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
    el("li", {}, sample(lineAttributes({ strength: "ストレス" }), true), "ぎざぎざの線：ストレスのあるつながり"),
    el("li", {}, sample(lineAttributes(null)), "細い点の線：まだ選んでいません"),
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

  const drawing = svg(
    "svg",
    { viewBox: `0 0 ${SIZE} ${SIZE + 24}`, class: "diagram", role: "group", "aria-label": "本人を真ん中にしたつながりの地図" },
    svg(
      "defs",
      {},
      svg("marker", { id: "map-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto" }, svg("path", { d: "M0 0 L10 5 L0 10 z", fill: INK })),
      svg("marker", { id: "map-arrow-back", viewBox: "0 0 10 10", refX: "1", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto" }, svg("path", { d: "M10 0 L0 5 L10 10 z", fill: INK }))
    ),
    svg("circle", { cx: CENTER, cy: CENTER, r: INNER, fill: "none", stroke: "#d5ccb6", "stroke-dasharray": "3 6" }),
    svg("circle", { cx: CENTER, cy: CENTER, r: OUTER, fill: "none", stroke: "#d5ccb6", "stroke-dasharray": "3 6" })
  );

  const place = (items, radius, start) =>
    items.map((item, index) => {
      const angle = start + (2 * Math.PI * index) / Math.max(items.length, 1);
      return { item, x: CENTER + radius * Math.cos(angle), y: CENTER + radius * Math.sin(angle) };
    });
  // ツインは内側の輪の最初に置く。本人のそばで、人と重ならないようにするため。
  const inner = place([{ twin: true }, ...family], INNER, -Math.PI / 2 + 0.9);
  const twinSpot = inner[0];
  const placed = [...inner.slice(1), ...place(outerItems, OUTER, -Math.PI / 2 + 0.25)];

  // 線を先に描き、バッジで線の端を隠さないようにする
  for (const { item, x, y } of placed) {
    const length = Math.hypot(x - CENTER, y - CENTER);
    const ux = (x - CENTER) / length;
    const uy = (y - CENTER) / length;
    const startGap = BADGE + 7;
    const endGap = BADGE + 3;
    const zigzag = item.link?.strength === "ストレス";
    drawing.append(
      svg("path", {
        d: linePath(CENTER + ux * startGap, CENTER + uy * startGap, x - ux * endGap, y - uy * endGap, zigzag),
        ...lineAttributes(item.link),
        ...arrowEnds(item.link),
      })
    );
  }

  const reopen = () => refresh();
  for (const { item, x, y } of placed) {
    if (item.person) {
      drawing.append(
        badge({
          x,
          y,
          label: item.person.called_name || item.person.name,
          sublabel: item.person.role,
          photoId: item.person.photo_id,
          onPress: () => openPersonSheet(ctx, item, reopen),
        })
      );
    } else {
      drawing.append(
        badge({
          x,
          y,
          label: item.place.name,
          sublabel: item.place.kind || "つながり",
          shape: "square",
          onPress: () => openPlaceSheet(item, reopen),
        })
      );
    }
  }
  drawing.append(
    badge({
      x: CENTER,
      y: CENTER,
      label: data.self.called_name || data.self.name,
      sublabel: "本人",
      photoId: data.self.photo_id,
      ring: true,
      onPress: () => openPersonForm(ctx, data.self, reopen),
    })
  );
  // ツインのバッジは本人のそばに置く。写真には替えられない。
  const twinSize = BADGE * 2 + 8;
  const twinGroup = svg("g", {
    class: "badge",
    role: "button",
    tabindex: "0",
    "aria-label": "ツイン（AI）とのトークを開く",
  });
  const figure = twinDrawing(twinSize);
  figure.setAttribute("x", twinSpot.x - twinSize / 2);
  figure.setAttribute("y", twinSpot.y - twinSize / 2);
  twinGroup.append(
    figure,
    svg("text", { x: twinSpot.x, y: twinSpot.y + BADGE + 20, "text-anchor": "middle", "font-size": FONT }, "ツイン（AI）")
  );
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
    legend(),
    words,
    el(
      "div",
      { class: "row" },
      el("button", { type: "button", text: "つながりを足す", onclick: () => openNewPlaceSheet(reopen) })
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
    const details = [person.role, person.relation, person.residence].filter(Boolean).join("・");
    const message = el("p", { class: "error", role: "alert", hidden: true });
    inner.append(
      el("div", { class: "row" }, avatar(person, "large"), el("div", {}, el("h2", { text: person.name }), el("div", { class: "hint", text: details }))),
      person.memo ? el("p", { text: person.memo }) : null,
      el(
        "div",
        { class: "row" },
        el("button", {
          type: "button",
          class: "primary",
          text: "トークを始める",
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
          text: "直す",
          onclick: () => {
            close();
            openPersonForm(ctx, person, onDone);
          },
        }),
        el("button", { type: "button", class: "quiet", text: "閉じる", onclick: close })
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
    inner.append(
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
        el("button", {
          type: "button",
          class: "quiet",
          text: "このつながりをはずす",
          onclick: async () => {
            try {
              await api(`/map/places/${item.place.id}/remove`, { method: "POST" });
              close();
              onDone();
            } catch (error) {
              message.textContent = error.message;
              message.hidden = false;
            }
          },
        }),
        el("button", { type: "button", class: "quiet", text: "閉じる", onclick: close })
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
      el("button", { type: "submit", class: "primary wide", text: "足す" }),
      el("button", { type: "button", class: "quiet wide", text: "やめる", onclick: close })
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
