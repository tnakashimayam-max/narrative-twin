/*
  役割：画面を組み立てる小さな部品。文字の入った要素、人のアイコン、ツインの姿、日付の表示など。
  4つの極：特定の極に属さない土台。
  守る原則：12（ツインは AI の相棒で、本人にも実在の人にも似せない）、13（色がのるのは花びらだけ。数字を出さない）。

  文字は textContent で入れる。人の言葉や AI の文を HTML として解釈させないため。
  ツインの姿は「花の子」。頭のまわりに5枚の花びらを開いた形で、性別や年齢が分からず、人間そのものに見えない。
  背景、体、顔の色はいつも出る。色がのるのは花びらだけで、本人が確かめた分だけ濃くなる（原則13）。
  色がのる前の花びらは、白い面に細い線で描く。
  ここに写真を入れる道は作らない。
*/

import { photoUrl } from "./api.js";

/* 要素を作る。props の on で始まるものは出来事の受け取り、それ以外は属性にする。 */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) {
      continue;
    }
    if (key.startsWith("on")) {
      node.addEventListener(key.slice(2), value);
    } else if (key === "class") {
      node.className = value;
    } else if (key === "text") {
      node.textContent = value;
    } else if (key in node && key !== "list" && key !== "form") {
      node[key] = value;
    } else {
      node.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of children.flat()) {
    if (child !== undefined && child !== null && child !== false) {
      node.append(child);
    }
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";

/* 親に子を足す。null や false を「null」という文字にしない。 */
export function add(parent, ...children) {
  for (const child of children.flat()) {
    if (child !== undefined && child !== null && child !== false) {
      parent.append(child);
    }
  }
  return parent;
}

/* 取り消せない操作のボタン。押し間違えないよう、2回押す形にする。ブラウザの確認の窓は使わない。 */
export function twoStepButton(label, confirmLabel, action, className = "quiet") {
  let armed = false;
  const button = el("button", {
    type: "button",
    class: className,
    text: label,
    onclick: async () => {
      if (!armed) {
        armed = true;
        button.textContent = confirmLabel;
        return;
      }
      await action();
    },
  });
  return button;
}

/* SVG の要素を作る。 */
export function svg(tag, attributes = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null) {
      continue;
    }
    if (key.startsWith("on")) {
      node.addEventListener(key.slice(2), value);
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of children.flat()) {
    if (child !== undefined && child !== null && child !== false) {
      node.append(child);
    }
  }
  return node;
}

/* ツインの色。花びらの順に、濃さの言葉だけを受け取る。名前も数も出さない（原則13）。
   色は index.html の pw0〜pw4（藍、朱、山吹、若竹、藤）にある。 */
const LEVEL_OPACITY = { なし: 0, うすい: 0.26, 中くらい: 0.5, こい: 0.82 };
let twinLevels = null;

export function setTwinLevels(levels) {
  twinLevels = levels;
}

const PETAL_SHAPE = "M0 0C-13-16-35-44-32-70C-29-92-9-102 3-100C25-97 37-80 33-60C29-38 11-16 0 0Z";
const PETAL_VEIN = "M0-6C-3-32-4-58-1-86M-1-40C-9-52-14-62-16-74M-1-46C7-56 12-66 15-76";
const PETAL_LIGHT = "M-20-62C-21-76-13-88-3-90C-12-82-17-72-20-62Z";
const TWIN_LINE = { stroke: "#6E5E50", "stroke-width": "1", "vector-effect": "non-scaling-stroke" };

/* 花びら1枚。表示の大きさで描き分ける。小さいときに細かい線を描くと、つぶれて読めないため。 */
function twinPetal(index, opacity, px) {
  const transform = `translate(50 46) rotate(${index * 72}) scale(0.46 0.41)`;
  const parts = [svg("path", { transform, d: PETAL_SHAPE, fill: "url(#pbase)" })];
  if (opacity > 0) {
    parts.push(
      svg(
        "g",
        { filter: px >= 64 ? "url(#wc)" : undefined },
        svg("path", { transform, d: PETAL_SHAPE, fill: `url(#pw${index})`, "fill-opacity": String(opacity) })
      )
    );
  }
  if (px >= 44) {
    parts.push(
      svg("path", {
        transform,
        d: PETAL_VEIN,
        fill: "none",
        ...TWIN_LINE,
        stroke: "#7A6A5A",
        opacity: opacity > 0 ? "0.22" : "0.3",
        "stroke-linecap": "round",
      })
    );
  }
  if (px >= 64) {
    parts.push(svg("path", { transform, d: PETAL_LIGHT, fill: "#fff", opacity: "0.55" }));
  }
  parts.push(
    svg("path", {
      transform,
      d: PETAL_SHAPE,
      fill: "none",
      ...TWIN_LINE,
      "stroke-width": px <= 32 ? "0.7" : "1",
      opacity: opacity >= 0.5 ? "0.5" : "0.8",
      "stroke-linejoin": "round",
    })
  );
  return parts;
}

/* 顔。小さいときは目と口を大きくし、28px以下では口を省く。 */
function twinFace(px) {
  const eyeScale = px <= 28 ? 1.4 : px <= 44 ? 1.18 : 1;
  const radius = 2.8 * eyeScale;
  const eye = (x) => [
    svg("ellipse", { cx: x, cy: 45, rx: radius, ry: radius * 1.14, fill: "#2A2730" }),
    svg("circle", { cx: x + radius * 0.32, cy: 45 - radius * 0.42, r: radius * 0.38, fill: "#fff" }),
  ];
  const cheekScale = px <= 28 ? 1.2 : 1;
  const cheek = (x) =>
    svg("ellipse", { cx: x, cy: 51.2, rx: 3.6 * cheekScale, ry: 2.3 * cheekScale, fill: "#F08A78", opacity: "0.55" });
  const parts = [eye(42.4), eye(57.6), cheek(37.4), cheek(62.6)];
  if (px > 28) {
    const mouthScale = px <= 44 ? 1.15 : 1;
    const half = 2.7 * mouthScale;
    parts.push(
      svg("path", {
        d: `M${50 - half} 51.2Q50 ${51.2 + half * 0.95} ${50 + half} 51.2`,
        fill: "none",
        stroke: "#6B4438",
        "stroke-width": String(1.15 * mouthScale),
        "stroke-linecap": "round",
      })
    );
  }
  return parts;
}

/* ツインの姿。戻り値は <svg> のまま。地図の図の中に入れるので、外を包まない。
   free は背景の丸なしの姿で、入口の主役に使う。 */
export function twinDrawing(size = 100, levels = twinLevels, { free = false } = {}) {
  const figure = [
    svg("path", { d: "M20 102C20 82 33 72 50 72S80 82 80 102Z", fill: "url(#leafG)", ...TWIN_LINE, "stroke-opacity": "0.5" }),
    [0, 1, 2, 3, 4].map((index) => twinPetal(index, LEVEL_OPACITY[levels?.[index]] ?? 0, size)),
    svg("circle", { cx: "50", cy: "46", r: "20.5", fill: "url(#skin)", ...TWIN_LINE, "stroke-opacity": "0.55" }),
    twinFace(size),
  ].flat(Infinity);
  const attributes = { viewBox: "0 0 100 100", width: String(size), height: String(size), role: "img", "aria-label": "ツイン（AI）の姿" };
  if (free) {
    return svg("svg", attributes, figure);
  }
  return svg(
    "svg",
    attributes,
    svg("circle", { cx: "50", cy: "50", r: "50", fill: "url(#twbg)" }),
    svg("g", { "clip-path": "url(#twc)" }, figure),
    svg("circle", { cx: "50", cy: "50", r: "49.4", fill: "none", stroke: "#fff", "stroke-width": size <= 32 ? "1" : "1.6", "vector-effect": "non-scaling-stroke" })
  );
}

export function twinFigure({ large = false, small = false, thinking = false } = {}) {
  const classes = ["twin-figure"];
  if (large) {
    classes.push("large");
  }
  if (small) {
    classes.push("small");
  }
  if (thinking) {
    classes.push("thinking");
  }
  // 表示の大きさを渡す。描き分けは表示の大きさで決まるため
  return el("span", { class: classes.join(" ") }, twinDrawing(large ? 96 : small ? 36 : 44));
}

/* 人のアイコン。写真がなければ、名前の頭文字の丸にする。 */
export function avatar(person, size = "") {
  const label = person?.called_name || person?.name || "？";
  const initial = Array.from(label.trim())[0] || "？";
  const classes = ["avatar", size, person?.role === "本人" ? "me" : ""].filter(Boolean).join(" ");
  const node = el("span", { class: classes, "aria-hidden": "true" }, initial);
  if (person?.photo_id) {
    photoUrl(person.photo_id).then((url) => {
      if (url) {
        node.replaceChildren(el("img", { src: url, alt: "" }));
      }
    });
  }
  return node;
}

/* 大きく出すプロフィール写真。丸いアイコンとは別の枠で、写真がない人には何も出さない。
   顔を黒い面や濃い四角い枠で囲むと遺影に見えるので、暗い表示でも明るい面に角丸で置く（style.css の .profile-photo）。 */
export function profilePhoto(person) {
  if (!person?.profile_photo_id) {
    return null;
  }
  const frame = el("figure", { class: "profile-photo", hidden: true });
  photoUrl(person.profile_photo_id).then((url) => {
    if (url) {
      frame.append(el("img", { src: url, alt: `${person.called_name || person.name}のプロフィール写真` }));
      frame.hidden = false;
    }
  });
  return frame;
}

/* 写真の <img> を作る。見てよい写真だけが読める。 */
export function photoImage(photoId, className = "bubble-photo", alt = "送られた写真") {
  const image = el("img", { class: className, alt });
  photoUrl(photoId).then((url) => {
    if (url) {
      image.src = url;
    }
  });
  return image;
}

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

export function dateLabel(iso) {
  const day = new Date(iso);
  return `${day.getFullYear()}年${day.getMonth() + 1}月${day.getDate()}日（${WEEKDAYS[day.getDay()]}）`;
}

export function dateKey(iso) {
  const day = new Date(iso);
  return `${day.getFullYear()}-${day.getMonth() + 1}-${day.getDate()}`;
}

export function timeLabel(iso) {
  const day = new Date(iso);
  return `${day.getHours()}:${String(day.getMinutes()).padStart(2, "0")}`;
}

/* 下から出る小さな画面。閉じる関数を渡した中身の組み立て役に渡す。 */
export function openSheet(build) {
  const sheet = el("div", { class: "sheet", role: "dialog", "aria-modal": "true" });
  const inner = el("div", { class: "sheet-inner" });
  const onKey = (event) => {
    if (event.key === "Escape") {
      close();
    }
  };
  const close = () => {
    document.removeEventListener("keydown", onKey);
    sheet.remove();
  };
  document.addEventListener("keydown", onKey);
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) {
      close();
    }
  });
  sheet.append(inner);
  document.body.append(sheet);
  build(inner, close);
  return close;
}

/* タブの絵。言葉と一緒に出す。 */
export function tabIcon(name) {
  const line = { fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" };
  const paths = {
    home: "M3 11 12 3l9 8v10h-6v-6H9v6H3z",
    talk: "M4 5h16v11H9l-5 4z",
    timeline: "M5 4h14v16H5zM8 9h8M8 13h8M8 17h5",
    records: "M6 3h12v18l-6-4-6 4z",
    advocate: "M4 5h16v11H9l-5 4zM8 9h8M8 12h5",
    prorecords: "M6 3h12v18H6zM9 8h6M9 12h6M9 16h4",
  };
  return svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: paths[name], ...line }));
}
