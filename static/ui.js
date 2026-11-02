/*
  役割：画面を組み立てる小さな部品。文字の入った要素、人のアイコン、ツインの姿、日付の表示など。
  4つの極：特定の極に属さない土台。
  守る原則：12（ツインは AI の相棒で、本人にも実在の人にも似せない）、13（色はのせない）。

  文字は textContent で入れる。人の言葉や AI の文を HTML として解釈させないため。
  ツインの姿は丸みのある花の形の鉛筆の下書きで、性別や年齢が分からない形にしている。
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

/* ツインの姿の線。花びらの輪郭と丸い芯だけで、色はのせない。 */
export function twinDrawing(size = 100) {
  const pencil = { fill: "none", stroke: "#6f6a60", "stroke-width": "1.6", "stroke-linecap": "round" };
  const faint = { fill: "none", stroke: "#a39d90", "stroke-width": "1", "stroke-linecap": "round" };
  const petals = [];
  for (let index = 0; index < 5; index += 1) {
    const angle = index * 72;
    petals.push(svg("ellipse", { cx: "50", cy: "23", rx: "15", ry: "19.5", transform: `rotate(${angle} 50 50)`, ...pencil }));
    petals.push(svg("ellipse", { cx: "51", cy: "24", rx: "14", ry: "18.5", transform: `rotate(${angle + 2.5} 50 50)`, ...faint }));
  }
  return svg(
    "svg",
    { viewBox: "0 0 100 100", width: String(size), height: String(size), role: "img", "aria-label": "ツイン（AI）の姿" },
    petals,
    svg("circle", { cx: "50", cy: "50", r: "21", fill: "#fbf7ec", stroke: "#6f6a60", "stroke-width": "1.6" }),
    svg("circle", { cx: "50.6", cy: "50.4", r: "20", ...faint }),
    svg("path", { d: "M41 47 q2 -2.4 4 0", ...pencil }),
    svg("path", { d: "M55 47 q2 -2.4 4 0", ...pencil }),
    svg("path", { d: "M44 56 q6 4.6 12 0", ...pencil })
  );
}

export function twinFigure({ large = false, thinking = false } = {}) {
  const classes = ["twin-figure"];
  if (large) {
    classes.push("large");
  }
  if (thinking) {
    classes.push("thinking");
  }
  return el("span", { class: classes.join(" ") }, twinDrawing());
}

/* 人のアイコン。写真がなければ、名前の頭文字の丸にする。 */
export function avatar(person, size = "") {
  const label = person?.called_name || person?.name || "？";
  const initial = Array.from(label.trim())[0] || "？";
  const node = el("span", { class: `avatar ${size}`.trim(), "aria-hidden": "true" }, initial);
  if (person?.photo_id) {
    photoUrl(person.photo_id).then((url) => {
      if (url) {
        node.replaceChildren(el("img", { src: url, alt: "" }));
      }
    });
  }
  return node;
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
  const close = () => sheet.remove();
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
  };
  return svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: paths[name], ...line }));
}
