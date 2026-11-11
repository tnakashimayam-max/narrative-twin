/*
  役割：ツイン（AI）の小さなアイコン（トークの一覧、見出し、吹き出しの横、この部屋のこと、ホームなど）を3Dにする。
  4つの極：1. 書く・話す（話す相手のツインを、生きているように見せる）。
  守る原則：12（姿は花の子のまま。名前の「ツイン（AI）」はアイコンの外の文字で出す）、
            13（色がのるのは花びらだけ。背景の丸と体と顔の色は2Dと同じで変えない）。

  3Dの描画は1つだけ作り、描いた絵を、画面に見えているアイコンの canvas へ写す。
  アイコンごとに3Dを作ると重く、ブラウザが同時に持てる3Dの数の上限も超えるため。
  動きはゆっくりした首振りと、まばたきだけ。動きを減らす設定では止めて描く。
  背景の丸（水色から陽だまりの色）と白い縁は、2Dのアイコン（static/ui.js の twinDrawing）と同じに描く。
  3Dを描けないときは、呼んだ側（static/ui.js）が2Dのアイコンのまま残す。
*/
import * as THREE from "../vendor/three/three.module.min.js";
import { buildTwin } from "./twin.js";

const RENDER_PX = 192; // いちばん大きいアイコン（96px）の2倍
const FRAME_MS = 1000 / 30; // ゆっくりした動きなので、1秒に30回で足りる
const FOV = 24;
// 2Dのアイコンと同じ構図：頭の中心を丸の中心より少し上に置き、花びらが丸の9割ほどに収まる（単位は頭の中心からの SVG の1）
const HALF_HEIGHT = 49;
const LOOK_BELOW_HEAD = 4;
// 背景の丸の色（static/index.html の #twbg と同じ）
const BG_STOPS = [
  [0, "#BDE3F6"],
  [0.62, "#E6F4EC"],
  [1, "#FFEBB8"],
];

let shared = null;
let failed = null;

class IconStage {
  constructor() {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(1);
    this.renderer.setSize(RENDER_PX, RENDER_PX, false);
    this.renderer.setClearColor(0x000000, 0);
    this.canvas = this.renderer.domElement;

    this.scene = new THREE.Scene();
    // 光は大きな舞台（static/twin3d/stage.js）と同じ。影は小さくて見えないので描かない
    this.scene.add(new THREE.HemisphereLight(0xfffdf8, 0xfff2e4, 2.05));
    const key = new THREE.DirectionalLight(0xfff4e0, 1.3);
    key.position.set(-1.6, 2.6, 3);
    this.scene.add(key);
    const back = new THREE.DirectionalLight(0xe6f0ff, 0.55);
    back.position.set(2, 1.2, -3);
    this.scene.add(back);

    this.pivot = new THREE.Group();
    this.scene.add(this.pivot);
    this.twin = buildTwin();
    // 頭の中心を原点にし、SVG の単位のまま置く（大きさはカメラの距離で合わせる）
    this.twin.group.position.copy(this.twin.faceCenter).multiplyScalar(-1);
    this.pivot.add(this.twin.group);

    this.camera = new THREE.PerspectiveCamera(FOV, 1, 1, 2000);
    const distance = HALF_HEIGHT / Math.tan(THREE.MathUtils.degToRad(FOV / 2));
    this.camera.position.set(0, -LOOK_BELOW_HEAD, distance);
    this.camera.lookAt(0, -LOOK_BELOW_HEAD, 0);

    this.entries = new Set();
    this.observer = new IntersectionObserver((records) => {
      for (const record of records) {
        const entry = record.target.__twinIcon;
        if (entry) {
          entry.visible = record.isIntersecting;
          if (entry.visible) {
            this.dirty = true;
            this.start();
          }
        }
      }
    });
    this.reducedQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    this.time = 0;
    this.last = 0;
    this.frame = 0;
    this.dirty = true;
    this.onLost = [];
    this.loop = this.loop.bind(this);
    this.canvas.addEventListener("webglcontextlost", (event) => {
      // 描く力を失ったら、すべてのアイコンを2Dに戻す
      event.preventDefault();
      this.stop();
      for (const entry of this.entries) {
        entry.canvas.remove();
        entry.holder.classList.remove("is-3d");
      }
      this.entries.clear();
      failed = new Error("3Dを描けなくなりました");
      shared = null;
    });
  }

  get reduced() {
    return this.reducedQuery?.matches ?? false;
  }

  /* アイコン（.twin-figure の span）に canvas を重ねる */
  attach(holder) {
    if (holder.__twinIcon) {
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.className = "twin-icon-canvas";
    canvas.setAttribute("aria-hidden", "true"); // 読み上げは下の2Dの絵の「ツイン（AI）の姿」で伝える
    const entry = { holder, canvas, visible: false, seen: false, shown: false, size: 0, background: null, created: performance.now() };
    canvas.__twinIcon = entry;
    holder.__twinIcon = entry;
    holder.append(canvas);
    this.entries.add(entry);
    this.observer.observe(canvas);
    this.dirty = true;
    this.start();
  }

  /* immediate が真なら、すぐその色にする。最初に描くときに、作ったときの色（全部こい）から薄れて見えないようにするため（原則13） */
  setLevels(levels, immediate = false) {
    this.twin.setLevels(levels, immediate);
    this.dirty = true;
    this.start();
  }

  start() {
    if (!this.frame) {
      this.frame = requestAnimationFrame(this.loop);
    }
  }

  stop() {
    if (this.frame) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
  }

  /* canvas の大きさを、表示の大きさと画面の細かさに合わせる。背景の丸の色もここで作り直す */
  fit(entry) {
    const css = entry.holder.clientWidth || 44;
    const size = Math.round(css * Math.min(window.devicePixelRatio || 1, 2));
    if (size !== entry.size) {
      entry.size = size;
      entry.canvas.width = size;
      entry.canvas.height = size;
      const ctx = entry.canvas.getContext("2d");
      const gradient = ctx.createLinearGradient(0, 0, 0, size);
      BG_STOPS.forEach(([at, color]) => gradient.addColorStop(at, color));
      entry.background = gradient;
      entry.ring = Math.max(1, (css <= 32 ? 1 : 1.6) * (size / css));
    }
  }

  /* 描いた3Dを、背景の丸と白い縁と一緒に写す */
  blit(entry) {
    this.fit(entry);
    const { size } = entry;
    const ctx = entry.canvas.getContext("2d");
    const r = size / 2;
    ctx.clearRect(0, 0, size, size);
    ctx.save();
    ctx.beginPath();
    ctx.arc(r, r, r - entry.ring / 2, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = entry.background;
    ctx.fillRect(0, 0, size, size);
    ctx.drawImage(this.canvas, 0, 0, size, size);
    ctx.restore();
    ctx.beginPath();
    ctx.arc(r, r, r - entry.ring / 2, 0, Math.PI * 2);
    ctx.lineWidth = entry.ring;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    // 最初の絵を写してから、下の2Dの絵を隠す。写す前に隠すと、一瞬からっぽに見えるため
    if (!entry.shown) {
      entry.shown = true;
      entry.holder.classList.add("is-3d");
    }
  }

  loop(now) {
    this.frame = 0;
    // 画面から外れたアイコンは忘れる。まだ画面に置かれていないアイコン（作ったばかり）は10秒まで待つ。
    // すぐに画面を切り替えると、一度も見ないまま外れることがあるため
    for (const entry of this.entries) {
      if (entry.canvas.isConnected) {
        entry.seen = true;
      } else if (entry.seen || now - entry.created > 10000) {
        this.observer.unobserve(entry.canvas);
        this.entries.delete(entry);
      }
    }
    if (!this.entries.size) {
      return;
    }
    const visible = [...this.entries].filter((entry) => entry.visible);
    const still = this.reduced;
    if (now - this.last >= FRAME_MS || this.dirty) {
      const dt = this.last ? Math.min(0.1, (now - this.last) / 1000) : 0;
      this.last = now;
      if (visible.length && (!still || this.dirty)) {
        this.time += dt;
        if (still) {
          this.pivot.rotation.y = 0;
          this.pivot.position.y = 0;
        } else {
          // ゆっくりした首振りと、わずかな上下
          this.pivot.rotation.y = Math.sin(this.time * 0.5) * 0.32;
          this.pivot.position.y = Math.sin(this.time * 1.2) * 0.6;
        }
        this.twin.tick(this.time, dt, still);
        this.renderer.render(this.scene, this.camera);
        visible.forEach((entry) => this.blit(entry));
        this.dirty = false;
      }
    }
    // 動きを減らす設定では、描き直しが要るときだけ動く。見えるアイコンが増えたときは、見張りが動かし直す
    if (!still || (this.dirty && visible.length)) {
      this.frame = requestAnimationFrame(this.loop);
    }
  }
}

/* アイコンを3Dにする。描けないブラウザでは例外にし、呼んだ側は2Dのまま残す */
export function attachTwinIcon(holder, levels) {
  if (failed) {
    throw failed;
  }
  if (!shared) {
    try {
      shared = new IconStage();
      shared.setLevels(levels, true);
    } catch (error) {
      failed = error;
      throw error;
    }
  }
  shared.attach(holder);
}

export function setTwinIconLevels(levels) {
  shared?.setLevels(levels);
}

/* 確かめる道具から使う。3Dの描画がいくつあるかと、アイコンの数 */
export function twinIconStats() {
  return { stages: shared ? 1 : 0, icons: shared ? shared.entries.size : 0, amounts: shared ? shared.twin.petalAmounts() : [] };
}
