/*
  役割：ツイン（AI）の姿「花の子」を three.js の3Dで組み立てる。専門職の画面の3Dの舞台（static/twin3d/stage.js）で使う。
  もとにしたもの：アプリの static/ui.js の twinDrawing（花びらの形、色、顔の位置）。
  守る原則：12（本人にも実在の人にも似せない）、13（色がのるのは花びらだけ。数字を出さない）。
  座標：単位は SVG の1と同じ。y3 = 55 - y_svg、正面は +z。正面から見た形が 2D と重なるように作る。
  動画用に作った3Dのフィギュア（2026-11-05、花びらの色だけ濃くした案B）と同じ形と色。
  違うのは、花びらごとの濃さを受け取る setLevels を足したこと（原則13。2D と同じく花びらごとに色がのる）と、
  専門職の「記録」の花びらのボタンのための setFocus と petalPoint（選んだ花びらを動きと輪郭だけで示し、色は変えない）。
*/
import * as THREE from "../vendor/three/three.module.min.js";

const HEAD_R = 20.5;
const HEAD_Z = 0.94; // 頭の奥行き（前後を少しだけ薄くする）
const HEAD_Y = 9; // 頭の中心の高さ（y_svg = 46）
const PX = 0.46; // 花びらの横の縮尺（SVG の scale と同じ）
const PY = 0.41; // 花びらの縦の縮尺

/* 色の濃さ。うすい、中くらいはアプリの LEVEL_OPACITY と同じ値。
   こいだけ、tyumajinさんの指定で 1 にする（2026-11-05「花びらの色は濃ゆめにして」）。画面には言葉だけを出す。 */
export const LEVELS = { なし: 0, うすい: 0.26, 中くらい: 0.5, こい: 1 };

/* 舞台の光は頭と体に合わせてあり、花びらでは赤と緑が強く、青が弱く出る。
   色がのる分だけ、赤、緑、青を別々に補正して、元の色に近づける（測って決めた値） */
const PETAL_GAIN = "vec3(0.85, 0.85, 1.12)";

/* 花びらの順の色：藍、朱、山吹、若竹、藤（アプリの pw0〜pw4） */
const PETAL_COLORS = ["#3F5F86", "#E67F65", "#C9922C", "#3D7A5B", "#6F5F9A"];

const LINE_PETAL = "#8E7E6E";
const LINE_HEAD = "#B3A08C";
const LINE_BODY = "#6F9478";

function bez(p0, p1, p2, p3, t) {
  const u = 1 - t;
  return [
    u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
  ];
}

/* 曲線を細かい点にして、長さの割合で同じ間隔に取り直す */
function resample(segments, count) {
  const dense = [];
  for (const seg of segments) {
    for (let k = 0; k < 80; k += 1) dense.push(bez(...seg, k / 80));
  }
  dense.push(segments.at(-1)[3]);
  const lens = [0];
  for (let k = 1; k < dense.length; k += 1) {
    lens.push(lens[k - 1] + Math.hypot(dense[k][0] - dense[k - 1][0], dense[k][1] - dense[k - 1][1]));
  }
  const total = lens.at(-1);
  const out = [];
  let j = 0;
  for (let k = 0; k <= count; k += 1) {
    const target = (total * k) / count;
    while (j < lens.length - 2 && lens[j + 1] < target) j += 1;
    const span = lens[j + 1] - lens[j] || 1;
    const f = (target - lens[j]) / span;
    out.push([dense[j][0] + (dense[j + 1][0] - dense[j][0]) * f, dense[j][1] + (dense[j + 1][1] - dense[j][1]) * f]);
  }
  return out;
}

/* アプリの PETAL_SHAPE を、根元から先へ向かう左右2本の線に分けたもの */
const PETAL_LEFT = [
  [[0, 0], [-13, -16], [-35, -44], [-32, -70]],
  [[-32, -70], [-29, -92], [-9, -102], [3, -100]],
];
const PETAL_RIGHT = [
  [[0, 0], [11, -16], [29, -38], [33, -60]],
  [[33, -60], [37, -80], [25, -97], [3, -100]],
];

/* 頭の後ろに沿わせ、頭の横から外へ開く断面。回転体にするので、花びらの縁も頭に沿う。 */
function makeProfile(tipZ, tipSlope, lift) {
  const R = HEAD_R * 1.015 + 0.5 + lift;
  const hug = 0.9 * R;
  const under = Math.sqrt(R * R - hug * hug);
  const z0 = -HEAD_Z * under;
  const slope = (HEAD_Z * hug) / under;
  const length = 41 - hug;
  return (rho) => {
    if (rho < hug) return -HEAD_Z * Math.sqrt(Math.max(0, R * R - rho * rho));
    const t = Math.min(1.15, (rho - hug) / length);
    const t2 = t * t;
    const t3 = t2 * t;
    return (
      (2 * t3 - 3 * t2 + 1) * z0 +
      (t3 - 2 * t2 + t) * slope * length +
      (-2 * t3 + 3 * t2) * tipZ +
      (t3 - t2) * tipSlope * length
    );
  };
}

/* 花びらの形を、厚みのある閉じた面にする。uv は SVG の座標そのままで、絵がずれない。 */
function petalGeometry({ scaleX = PX, scaleY = PY, tipZ = 2, tipSlope = -0.35, lift = 0, thick = 0.55, pin = 0.11, cup = 1 } = {}) {
  const NS = 56;
  const NW = 20;
  const left = resample(PETAL_LEFT, NS);
  const right = resample(PETAL_RIGHT, NS);
  const profile = makeProfile(tipZ, tipSlope, lift);
  const positions = [];
  const uvs = [];
  const indices = [];

  const place = (sx, sy, h) => {
    const x = sx * scaleX;
    const r = Math.max(0, -sy) * scaleY;
    const rho = Math.hypot(x, r);
    const phi = Math.atan2(x, r);
    // 花びらの片側を少し持ち上げ、隣へ順に重ねる（頭の後ろでだけ強く効かせる）
    const near = Math.max(0, Math.min(1, (24 - rho) / 10));
    const pinwheel = -pin * (x + 8) * near;
    const open = Math.max(0, Math.min(1, (rho - 20) / 10));
    const z = profile(rho) + pinwheel + cup * open * (x * x) * 0.012;
    const dz = (profile(rho + 0.25) - profile(rho - 0.25)) / 0.5;
    const len = Math.hypot(dz, 1);
    const nr = -dz / len;
    const nz = 1 / len;
    return [(rho + h * nr) * Math.sin(phi), (rho + h * nr) * Math.cos(phi), z + h * nz];
  };

  const addVertex = (s, w, side) => {
    const l = left[s];
    const q = right[s];
    const sx = l[0] + (q[0] - l[0]) * w;
    const sy = l[1] + (q[1] - l[1]) * w;
    const width = Math.hypot(q[0] - l[0], q[1] - l[1]);
    const bulge = Math.sqrt(Math.max(0, 1 - (2 * w - 1) ** 2));
    const h = side * thick * bulge * Math.min(1, width / 10);
    positions.push(...place(sx, sy, h));
    uvs.push((sx + 40) / 80, -sy / 105);
  };

  // 表の面
  for (let s = 0; s <= NS; s += 1) {
    for (let w = 0; w <= NW; w += 1) addVertex(s, w / NW, 1);
  }
  const front = (s, w) => s * (NW + 1) + w;
  // 裏の面。縁の点は表と共有して、すき間のない形にする
  const backStart = positions.length / 3;
  for (let s = 0; s <= NS; s += 1) {
    for (let w = 1; w < NW; w += 1) addVertex(s, w / NW, -1);
  }
  const back = (s, w) => {
    if (w === 0 || w === NW) return front(s, w);
    return backStart + s * (NW - 1) + (w - 1);
  };
  for (let s = 0; s < NS; s += 1) {
    for (let w = 0; w < NW; w += 1) {
      indices.push(front(s, w), front(s, w + 1), front(s + 1, w + 1), front(s, w), front(s + 1, w + 1), front(s + 1, w));
      indices.push(back(s, w), back(s + 1, w + 1), back(s, w + 1), back(s, w), back(s + 1, w), back(s + 1, w + 1));
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  faceOutward(geometry);
  return geometry;
}

/* 面の向きを外向きにそろえる。逆だと線の表現と光が崩れるため、体積の符号で確かめる。 */
function faceOutward(geometry) {
  const p = geometry.attributes.position;
  const index = geometry.index;
  let volume = 0;
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  for (let i = 0; i < index.count; i += 3) {
    a.fromBufferAttribute(p, index.getX(i));
    b.fromBufferAttribute(p, index.getX(i + 1));
    c.fromBufferAttribute(p, index.getX(i + 2));
    volume += a.dot(b.cross(c));
  }
  if (volume < 0) {
    const array = index.array;
    for (let i = 0; i < array.length; i += 3) {
      const t = array[i + 1];
      array[i + 1] = array[i + 2];
      array[i + 2] = t;
    }
    geometry.computeVertexNormals();
  }
}

/* 乱数。毎回同じ模様になるように種を固定する */
function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

let grainCanvas = null;
/* 水彩の粒。全部の花びらで同じものを使う */
function grain() {
  if (grainCanvas) return grainCanvas;
  const size = 256;
  grainCanvas = document.createElement("canvas");
  grainCanvas.width = size;
  grainCanvas.height = size;
  const ctx = grainCanvas.getContext("2d");
  const image = ctx.createImageData(size, size);
  const random = seeded(3);
  const coarse = new Float32Array(33 * 33).map(() => random());
  const at = (x, y) => coarse[(y % 33) * 33 + (x % 33)];
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const fx = (x / size) * 32;
      const fy = (y / size) * 32;
      const ix = Math.floor(fx);
      const iy = Math.floor(fy);
      const dx = fx - ix;
      const dy = fy - iy;
      const v =
        at(ix, iy) * (1 - dx) * (1 - dy) + at(ix + 1, iy) * dx * (1 - dy) + at(ix, iy + 1) * (1 - dx) * dy + at(ix + 1, iy + 1) * dx * dy;
      const fine = random();
      const alpha = Math.max(0, (v * 0.6 + fine * 0.4 - 0.32) * 110);
      const k = (y * size + x) * 4;
      image.data[k + 3] = Math.min(34, alpha * 0.5);
    }
  }
  ctx.putImageData(image, 0, 0);
  return grainCanvas;
}

/* 花びらの絵。SVG と同じ座標で描く。color があれば水彩の色をのせる。 */
function petalCanvas(color, seed) {
  const W = 512;
  const H = 672;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  const toSvg = () => ctx.setTransform(W / 80, 0, 0, H / 105, W / 2, H);
  toSvg();
  const base = ctx.createLinearGradient(0, -100, 0, 0);
  base.addColorStop(0, "#FFFFFF");
  base.addColorStop(1, "#F1E8DA");
  ctx.fillStyle = base;
  ctx.fillRect(-40, -105, 80, 105);

  const shape = new Path2D("M0 0C-13-16-35-44-32-70C-29-92-9-102 3-100C25-97 37-80 33-60C29-38 11-16 0 0Z");
  if (color) {
    const layer = document.createElement("canvas");
    layer.width = W;
    layer.height = H;
    const l = layer.getContext("2d");
    l.setTransform(W / 80, 0, 0, H / 105, W / 2, H);
    const wash = l.createLinearGradient(0, -100, 0, 0);
    // 下の紙の色が透けると薄く見えるので、根元までほぼ不透明にする
    wash.addColorStop(0, hexA(color, 1));
    wash.addColorStop(1, hexA(color, 0.94));
    l.fillStyle = wash;
    l.fillRect(-40, -105, 80, 105);
    // 水彩のにじみ
    const random = seeded(seed);
    l.globalCompositeOperation = "destination-out";
    for (let k = 0; k < 9; k += 1) {
      const x = -28 + random() * 56;
      const y = -92 + random() * 80;
      const r = 8 + random() * 16;
      const blot = l.createRadialGradient(x, y, 0, x, y, r);
      blot.addColorStop(0, `rgba(0,0,0,${0.04 + random() * 0.07})`);
      blot.addColorStop(1, "rgba(0,0,0,0)");
      l.fillStyle = blot;
      l.fillRect(x - r, y - r, r * 2, r * 2);
    }
    // 粒で色を少し抜く
    l.setTransform(1, 0, 0, 1, 0, 0);
    l.drawImage(grain(), 0, 0, W, H);
    l.globalCompositeOperation = "source-over";
    l.setTransform(W / 80, 0, 0, H / 105, W / 2, H);
    // 縁に色がたまる水彩の表情
    l.save();
    l.clip(shape);
    for (let k = 0; k < 6; k += 1) {
      l.strokeStyle = hexA(color, 0.07);
      l.lineWidth = 12 - k * 2;
      l.stroke(shape);
    }
    l.restore();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(layer, 0, 0);
    toSvg();
  }
  // 葉脈（アプリの PETAL_VEIN）
  ctx.lineCap = "round";
  ctx.strokeStyle = color ? "rgba(122,106,90,0.3)" : "rgba(122,106,90,0.38)";
  ctx.lineWidth = 0.9;
  ctx.stroke(new Path2D("M0-6C-3-32-4-58-1-86M-1-40C-9-52-14-62-16-74M-1-46C7-56 12-66 15-76"));
  // 光（アプリの PETAL_LIGHT）
  ctx.fillStyle = "rgba(255,255,255,0.32)";
  ctx.fill(new Path2D("M-20-62C-21-76-13-88-3-90C-12-82-17-72-20-62Z"));
  return canvas;
}

function hexA(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

function canvasTexture(canvas) {
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

/* 2Dの細い線を、3Dでは面を裏返した一回り大きな形で出す */
function outlineMaterial(color, width) {
  const material = new THREE.MeshBasicMaterial({ color, side: THREE.BackSide });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader.replace(
      "#include <begin_vertex>",
      `#include <begin_vertex>\n transformed += normal * ${width.toFixed(3)};`
    );
  };
  return material;
}

/* 花びらの輪郭。選んだ花びらを、色を変えずに輪郭の太さと線の色だけで示すため、太さを後から変えられるようにする（原則13） */
const PETAL_LINE_WIDTH = 0.26;
const PETAL_LINE_FOCUS = 0.85;
const PETAL_LINE_FOCUS_COLOR = "#27507F"; // 台座の輪と同じ藍。花びらの面の色ではなく、線の色だけ
function petalOutlineMaterial(widthUniform) {
  const material = new THREE.MeshBasicMaterial({ color: LINE_PETAL, side: THREE.BackSide });
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uWidth = widthUniform;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform float uWidth;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\n transformed += normal * uWidth;");
  };
  // ほかの輪郭と同じ作りの影を使い回されないよう、別の名前を付ける
  material.customProgramCacheKey = () => "nt-petal-outline";
  return material;
}

function withOutline(mesh, color, width = 0.26) {
  const line = new THREE.Mesh(mesh.geometry, outlineMaterial(color, width));
  mesh.add(line);
  return mesh;
}

/* 頭の楕円の表面の z */
function headZ(x, y) {
  const d = HEAD_R * HEAD_R - x * x - y * y;
  return HEAD_Z * Math.sqrt(Math.max(0, d));
}

function headNormal(x, y) {
  const z = headZ(x, y);
  return new THREE.Vector3(x, y, z / (HEAD_Z * HEAD_Z)).normalize();
}

/* 頭に貼りつく小さな面（ほほ） */
function headPatch(cx, cy, rx, ry, texture) {
  const geometry = new THREE.PlaneGeometry(rx * 2, ry * 2, 14, 14);
  const p = geometry.attributes.position;
  for (let i = 0; i < p.count; i += 1) {
    const x = cx + p.getX(i);
    const y = cy + p.getY(i);
    p.setXYZ(i, x, y, headZ(x, y) + 0.05);
  }
  geometry.computeVertexNormals();
  const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false });
  return new THREE.Mesh(geometry, material);
}

function cheekTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, "rgba(240,138,120,0.62)");
  g.addColorStop(0.6, "rgba(240,138,120,0.42)");
  g.addColorStop(1, "rgba(240,138,120,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  return canvasTexture(canvas);
}

/* 下から上へ、色を移す。頂点の色を使う */
function verticalColors(geometry, top, bottom, yTop, yBottom) {
  const p = geometry.attributes.position;
  const colors = new Float32Array(p.count * 3);
  const a = new THREE.Color(top);
  const b = new THREE.Color(bottom);
  const c = new THREE.Color();
  for (let i = 0; i < p.count; i += 1) {
    const t = Math.max(0, Math.min(1, (yTop - p.getY(i)) / (yTop - yBottom)));
    c.copy(a).lerp(b, t);
    colors.set([c.r, c.g, c.b], i * 3);
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
}

/* 体：2D の緑の丸屋根（M20 102C20 82 33 72 50 72）を回転体にし、裾を5つに波打たせる */
function bodyGeometry() {
  const ring = [];
  for (let k = 0; k <= 24; k += 1) {
    const q = bez([50, 72], [33, 72], [20, 82], [20, 102], k / 24);
    const y = 55 - q[1];
    const low = Math.max(0, Math.min(1, (-17 - y) / 30));
    ring.push([(50 - q[0]) * (0.96 - 0.15 * low), y]); // 2D は下が切れているので、裾を少し細める
  }
  // 裾は円弧で丸く閉じる
  const hemR = ring.at(-1)[0];
  const hemY = ring.at(-1)[1];
  for (let k = 1; k <= 14; k += 1) {
    const a = (k / 14) * (Math.PI / 2);
    ring.push([hemR - 4.5 + 4.5 * Math.cos(a), hemY - 4.5 * Math.sin(a)]);
  }
  for (let k = 1; k <= 6; k += 1) ring.push([(hemR - 4.5) * (1 - k / 6) + 0.01, hemY - 4.5]);
  const NA = 120;
  const NP = ring.length;
  const positions = [];
  for (let j = 0; j < NP; j += 1) {
    const [r, y] = ring[j];
    const low = Math.max(0, Math.min(1, (-24 - y) / 24));
    for (let a = 0; a < NA; a += 1) {
      const angle = (a / NA) * Math.PI * 2;
      const wave = 1 + 0.03 * low * Math.cos(angle * 5);
      positions.push(r * wave * Math.sin(angle), y, r * wave * Math.cos(angle) * 0.8 - 1);
    }
  }
  const indices = [];
  for (let j = 0; j < NP - 1; j += 1) {
    for (let a = 0; a < NA; a += 1) {
      const a2 = (a + 1) % NA;
      indices.push(j * NA + a, (j + 1) * NA + a, (j + 1) * NA + a2, j * NA + a, (j + 1) * NA + a2, j * NA + a2);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  faceOutward(geometry);
  verticalColors(geometry, "#A8DBAC", "#62AD82", -17, -50);
  return geometry;
}

/* 組み立て。戻り値は Group と、動きと色の操作。 */
export function buildTwin() {
  const root = new THREE.Group();
  root.name = "ツイン（AI）";
  const soft = (options) => new THREE.MeshStandardMaterial({ roughness: 0.82, metalness: 0, ...options });

  // 体
  const body = withOutline(new THREE.Mesh(bodyGeometry(), soft({ vertexColors: true })), LINE_BODY);
  root.add(body);

  // 手（葉の形）。2D にはないが、後ろや横から見たときに体が筒に見えないようにする
  const armGeometry = new THREE.SphereGeometry(1, 32, 24);
  armGeometry.scale(4.3, 8.5, 3.1);
  verticalColors(armGeometry, "#9AD2A4", "#6DB58B", 8, -8);
  const arms = [-1, 1].map((side) => {
    const arm = withOutline(new THREE.Mesh(armGeometry, soft({ vertexColors: true })), LINE_BODY, 0.22);
    arm.position.set(side * 25.5, -30.5, 0.5);
    arm.rotation.z = side * 0.55;
    root.add(arm);
    return arm;
  });

  // 足
  const footGeometry = new THREE.SphereGeometry(1, 32, 20);
  footGeometry.scale(7, 2.9, 9);
  verticalColors(footGeometry, "#6DB58B", "#4F9A70", 3, -3);
  for (const side of [-1, 1]) {
    const foot = withOutline(new THREE.Mesh(footGeometry, soft({ vertexColors: true })), LINE_BODY, 0.22);
    foot.position.set(side * 10.5, -52.2, 7.5);
    root.add(foot);
  }

  // 頭より後ろのものをまとめる。頭の中心を軸にする
  const headGroup = new THREE.Group();
  headGroup.position.set(0, HEAD_Y, 0);
  root.add(headGroup);

  // 花びら
  const petals = [];
  const amounts = [];
  const petalLines = []; // 花びらごとの輪郭（太さと線の色）
  const lowerGeometry = petalGeometry({ tipZ: 8.5, tipSlope: -0.1 }); // 下の2枚は肩にかかるので前へ
  const upperGeometry = petalGeometry({ tipZ: 2, tipSlope: -0.35 });
  for (let index = 0; index < 5; index += 1) {
    const amount = { value: LEVELS.こい };
    amounts.push(amount);
    const baseTexture = canvasTexture(petalCanvas(null, 0));
    const colorTexture = canvasTexture(petalCanvas(PETAL_COLORS[index], 11 + index * 7));
    const material = soft({ map: baseTexture, roughness: 0.9 });
    // 白い花びらと色のついた花びらを、濃さの値で混ぜる
    material.onBeforeCompile = (shader) => {
      shader.uniforms.uColorMap = { value: colorTexture };
      shader.uniforms.uAmount = amount;
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", "#include <common>\nuniform sampler2D uColorMap;\nuniform float uAmount;")
        .replace(
          "#include <map_fragment>",
          `#include <map_fragment>
           // 見た目の色の空間で混ぜる。光の計算用の空間のまま混ぜると、白に寄って灰色に見える
           vec3 paperColor = pow(diffuseColor.rgb, vec3(1.0 / 2.2));
           vec3 dyeColor = pow(texture2D(uColorMap, vMapUv).rgb, vec3(1.0 / 2.2));
           diffuseColor.rgb = pow(mix(paperColor, dyeColor, uAmount), vec3(2.2));
           diffuseColor.rgb *= mix(vec3(1.0), ${PETAL_GAIN}, uAmount);`
        );
    };
    const geometry = index === 2 || index === 3 ? lowerGeometry : upperGeometry;
    const mesh = new THREE.Mesh(geometry, material);
    const lineWidth = { value: PETAL_LINE_WIDTH };
    const lineMaterial = petalOutlineMaterial(lineWidth);
    mesh.add(new THREE.Mesh(geometry, lineMaterial));
    petalLines.push({ width: lineWidth, material: lineMaterial });
    const pivot = new THREE.Group();
    pivot.rotation.order = "ZXY";
    pivot.rotation.z = -(index * Math.PI * 2) / 5; // 時計回り（2D の rotate と同じ向き）
    pivot.add(mesh);
    headGroup.add(pivot);
    petals.push(pivot);
  }

  // 後ろ姿：花びらの集まる所に、がくと小さなへた
  const sepalGeometry = petalGeometry({ scaleX: 0.15, scaleY: 0.14, lift: 2.2, thick: 0.4, pin: 0, cup: 0 });
  const sepalMaterial = soft({ color: "#7CC08E" });
  for (let index = 0; index < 5; index += 1) {
    const sepal = withOutline(new THREE.Mesh(sepalGeometry, sepalMaterial), LINE_BODY, 0.2);
    const pivot = new THREE.Group();
    pivot.rotation.z = -((index + 0.5) * Math.PI * 2) / 5;
    pivot.add(sepal);
    headGroup.add(pivot);
  }
  const stem = new THREE.TubeGeometry(
    new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0, -HEAD_R * HEAD_Z - 1.6),
      new THREE.Vector3(0.2, 0.6, -HEAD_R * HEAD_Z - 4.2),
      new THREE.Vector3(1.4, 1.9, -HEAD_R * HEAD_Z - 6),
    ]),
    16,
    1.15,
    14,
    false
  );
  headGroup.add(withOutline(new THREE.Mesh(stem, sepalMaterial), LINE_BODY, 0.18));
  const stemCap = new THREE.Mesh(new THREE.SphereGeometry(1.15, 16, 12), sepalMaterial);
  stemCap.position.set(1.4, 1.9, -HEAD_R * HEAD_Z - 6);
  headGroup.add(stemCap);

  // 頭。頂点の色で 2D の skin のぼかしをつける
  const headGeometry = new THREE.SphereGeometry(HEAD_R, 96, 64);
  headGeometry.scale(1, 1, HEAD_Z);
  {
    const p = headGeometry.attributes.position;
    const colors = new Float32Array(p.count * 3);
    const c0 = new THREE.Color("#FFFCF3");
    const c1 = new THREE.Color("#FCF1DE");
    const c2 = new THREE.Color("#F0DBBC");
    const c = new THREE.Color();
    const light = new THREE.Vector3(-3.3, 6.5, 14);
    const v = new THREE.Vector3();
    for (let i = 0; i < p.count; i += 1) {
      v.fromBufferAttribute(p, i);
      const t = Math.min(1, v.distanceTo(light) / 32);
      if (t < 0.62) c.copy(c0).lerp(c1, t / 0.62);
      else c.copy(c1).lerp(c2, (t - 0.62) / 0.38);
      colors.set([c.r, c.g, c.b], i * 3);
    }
    headGeometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  }
  const head = withOutline(new THREE.Mesh(headGeometry, soft({ vertexColors: true, roughness: 0.7 })), LINE_HEAD);
  headGroup.add(head);

  // ほほ。2D は y_svg=51.2、x=±12.6
  const cheeks = cheekTexture();
  for (const side of [-1, 1]) headGroup.add(headPatch(side * 12.6, -5.2, 4.6, 3.2, cheeks));

  // 目。2D は y_svg=45（頭の中心より 1 上）、x=±7.6
  const eyeMaterial = new THREE.MeshStandardMaterial({ color: "#2A2730", roughness: 0.22, metalness: 0 });
  const shineMaterial = new THREE.MeshBasicMaterial({ color: "#ffffff" });
  const eyes = [-1, 1].map((side) => {
    const x = side * 7.6;
    const y = 1;
    const eye = new THREE.Group();
    const normal = headNormal(x, y);
    eye.position.set(x, y, headZ(x, y)).addScaledVector(normal, -0.15);
    eye.lookAt(new THREE.Vector3().copy(eye.position).add(normal));
    const ball = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24), eyeMaterial);
    ball.scale.set(2.8, 3.2, 1.0);
    eye.add(ball);
    const shine = new THREE.Mesh(new THREE.SphereGeometry(1.06, 16, 12), shineMaterial);
    shine.scale.z = 0.35;
    shine.position.set(0.9, 1.18, 0.82);
    eye.add(shine);
    headGroup.add(eye);
    return eye;
  });

  // 口。2D の M47.3 51.2 Q50 53.76 52.7 51.2
  {
    const pts = [];
    for (let k = 0; k <= 16; k += 1) {
      const t = k / 16;
      const x = (1 - t) * (1 - t) * -2.7 + 2 * (1 - t) * t * 0 + t * t * 2.7;
      const y = (1 - t) * (1 - t) * -5.2 + 2 * (1 - t) * t * -7.76 + t * t * -5.2;
      pts.push(new THREE.Vector3(x, y, headZ(x, y) + 0.05));
    }
    const curve = new THREE.CatmullRomCurve3(pts);
    const mouthMaterial = soft({ color: "#6B4438", roughness: 0.6 });
    headGroup.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 32, 0.58, 10, false), mouthMaterial));
    for (const end of [pts[0], pts.at(-1)]) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.58, 12, 10), mouthMaterial);
      dot.position.copy(end);
      headGroup.add(dot);
    }
  }

  root.traverse((node) => {
    if (node.isMesh && node.material.side !== THREE.BackSide && !node.material.transparent) {
      node.castShadow = true;
      node.receiveShadow = true;
    }
  });

  /* 濃さを言葉で受け取る。targets は全部の花びらの目標 */
  const targets = amounts.map((a) => a.value);
  let blinkAt = 2.4;
  // 選んだ花びら（専門職の「記録」の花びらのボタン）。raise は花びらごとに、1＝前へ起き上がる、-1＝少し引く、0＝ふだん
  let focusIndex = null;
  const raise = [0, 0, 0, 0, 0];
  const lineBase = new THREE.Color(LINE_PETAL);
  const lineFocus = new THREE.Color(PETAL_LINE_FOCUS_COLOR);
  const anchorPoint = new THREE.Vector3();
  const twin = {
    group: root,
    center: new THREE.Vector3(0, -1.5, 0),
    radius: 58,
    spanY: 111, // 撮影のときに大きく写すための高さと幅（SVG の単位）
    spanX: 88,
    groundY: -55.5,
    faceCenter: new THREE.Vector3(0, HEAD_Y, 0),
    faceRadius: 44,
    setLevel(word, immediate = false) {
      const value = LEVELS[word] ?? 0;
      targets.fill(value);
      if (immediate) amounts.forEach((a) => { a.value = value; });
    },
    /* 花びらごとの濃さを、言葉の並びで受け取る（サーバーの /twin/appearance と同じ順）。分からない花びらは「なし」 */
    setLevels(words, immediate = false) {
      targets.forEach((_, i) => {
        targets[i] = LEVELS[words?.[i]] ?? 0;
        if (immediate) amounts[i].value = targets[i];
      });
    },
    /* 今の花びらごとの濃さの値。画面には出さず、確かめる道具から使う */
    petalAmounts() {
      return amounts.map((a) => a.value);
    },
    /* 花びらを選ぶ（0〜4、上から時計回り）。null でふだんに戻す。色と濃さは変えない（原則13） */
    setFocus(index) {
      focusIndex = Number.isInteger(index) && index >= 0 && index < 5 ? index : null;
    },
    /* 花びらの上の1点（根元から先へ、頭の中心から radius のところ）の、いまの舞台の中の位置。
       ボタンからの引き出し線の先を、揺れや起き上がりに合わせて動かすため。matrixWorld は描いたあとの値を使う */
    petalPoint(index, target, radius = 31) {
      anchorPoint.set(0, radius, 2);
      return target.copy(anchorPoint).applyMatrix4(petals[index].matrixWorld);
    },
    /* 動き。still が真なら、まばたきと揺れをしない（動きを減らす設定のとき） */
    tick(time, dt, still) {
      amounts.forEach((a, i) => {
        a.value += (targets[i] - a.value) * Math.min(1, dt * 5);
      });
      // 選んだ花びらの輪郭は、動きを減らす設定でも太くする（動かさずに輪郭だけで示す）
      const ease = still ? 1 : Math.min(1, dt * 5);
      petals.forEach((p, i) => {
        const goal = focusIndex === null ? 0 : i === focusIndex ? 1 : -1;
        raise[i] += (goal - raise[i]) * ease;
        const on = Math.max(0, raise[i]);
        petalLines[i].width.value = PETAL_LINE_WIDTH + (PETAL_LINE_FOCUS - PETAL_LINE_WIDTH) * on;
        petalLines[i].material.color.copy(lineBase).lerp(lineFocus, on);
      });
      if (still) {
        petals.forEach((p) => {
          p.rotation.x = 0;
          p.scale.setScalar(1);
        });
        return;
      }
      petals.forEach((p, i) => {
        const up = Math.max(0, raise[i]);
        const back = Math.max(0, -raise[i]);
        // 選んだ花びらは前へ起き上がって大きくなり、息をするようにゆっくりふくらむ。ほかの4枚は少し引く
        const breathe = up * Math.sin(time * 1.7) * 0.022;
        p.rotation.x = Math.sin(time * 0.9 + i * 1.3) * 0.018 * (1 - up) + up * 0.36 - back * 0.1;
        p.scale.setScalar(1 + up * 0.1 + breathe - back * 0.045);
      });
      headGroup.rotation.z = Math.sin(time * 0.6) * 0.012;
      arms[0].rotation.z = -0.55 + Math.sin(time * 1.1) * 0.03;
      arms[1].rotation.z = 0.55 - Math.sin(time * 1.1 + 0.6) * 0.03;
      const since = time - blinkAt;
      let open = 1;
      if (since > 0 && since < 0.18) open = Math.abs(since - 0.09) / 0.09;
      if (since >= 0.18) blinkAt = time + 2.5 + ((time * 37) % 1) * 3.5;
      eyes.forEach((e) => { e.scale.y = 0.12 + 0.88 * open; });
    },
  };
  return twin;
}
