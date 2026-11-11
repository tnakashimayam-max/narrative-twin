/*
  役割：専門職の画面に出すツイン（AI）の3Dの舞台。カメラ、光、接地の影、ドラッグでの回転、気分ごとの動きを受け持つ。
  4つの極：4. 10年後に代弁する（代弁する相手としてのツインを、画面の中に立たせる）。
  守る原則：12（姿は花の子のまま。「ツイン（AI）」の名前は舞台の下の文字で出す。static/twin_stage.js）、
            13（色がのるのは花びらだけ。気分で変えるのは動きだけで、色は変えない）。

  舞台は1つだけ作り、画面が変わるたびに同じ canvas を新しい場所へ移す。ブラウザが同時に持てる3Dの数には上限があるため。
  canvas が画面から外れたら描くのをやめる。タブが裏に回ったときは、ブラウザが描く回数を止める。
  動きを減らす設定のときは、揺れも回転もしない。ドラッグと矢印キーで回すことだけできる。
  専門職の「記録」の花びらの舞台では、focusPetal で花びらを選び、onRest と onLive で花びらの位置を知らせる（ボタンと引き出し線のため）。
  光と影の値は、動画用に作った3Dのフィギュアの舞台と同じ（陽だまりの光。黒い影にしない）。
*/
import * as THREE from "../vendor/three/three.module.min.js";
import { buildTwin } from "./twin.js";

const FOV = 24;
const DRAG_SPEED = 0.0105; // 1px あたりの回転（ラジアン）
const PITCH_LIMIT = 0.35; // 上下に倒せる角度。足が床から離れて見えないように小さくする
const BACK_AFTER = 2.4; // 手を離してから、いつもの動きに戻るまでの秒
// 映す範囲（舞台の単位）。足の下に台座の輪が入るよう、下を広めに取る
const FRAME_TOP = 0.98;
const FRAME_BOTTOM = -1.16;
const FRAME_HALF_WIDTH = 0.84;

/* 気分ごとの動き。sway は左右の首振り、turn は回り続ける速さ、bob は上下の揺れ。
   calm は専門職の「記録」の花びらの舞台のふだん（ボタンからの線が大きく振れないよう、首振りを小さくする）。
   focus は花びらを選んだあと（正面を向いて止まり、ゆっくり上下するだけ） */
const MOODS = {
  idle: { sway: 0.42, swaySpeed: 0.42, turn: 0, bob: 0.018, bobSpeed: 1.1 },
  calm: { sway: 0.2, swaySpeed: 0.36, turn: 0, bob: 0.016, bobSpeed: 1.1 },
  thinking: { sway: 0, swaySpeed: 0, turn: 1.15, bob: 0.03, bobSpeed: 2.3 },
  speaking: { sway: 0.1, swaySpeed: 0.9, turn: 0, bob: 0.016, bobSpeed: 5.6 },
  focus: { sway: 0, swaySpeed: 0, turn: 0, bob: 0.012, bobSpeed: 1.2 },
};
// 花びらを選んでから、話すように揺れる秒
const SPEAK_ON_FOCUS = 1.1;
// ボタンからの引き出し線の先にする、花びらの上の点（頭の中心からの距離。twin.js の単位）
const PETAL_ANCHOR = 31;

let shared = null;
let failed = null;

function contactShadowTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(128, 128, 0, 128, 128, 128);
  g.addColorStop(0, "rgba(96,78,48,0.36)");
  g.addColorStop(0.55, "rgba(96,78,48,0.16)");
  g.addColorStop(1, "rgba(96,78,48,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 256);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/* 角度の差を -π〜π にする。回ったあと、近い向きから正面へ戻すため */
function angleGap(from, to) {
  const gap = to - from;
  return Math.atan2(Math.sin(gap), Math.cos(gap));
}

class TwinStage {
  constructor() {
    // 3Dを描けないブラウザでは、ここで例外になる。呼んだ側が2Dの姿に切り替える
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.setClearColor(0x000000, 0);
    this.canvas = this.renderer.domElement;
    this.canvas.className = "twin-stage-canvas";

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 0.05, 40);

    // 陽だまりの光。黒い影にしない（遺影に見せない）
    this.scene.add(new THREE.HemisphereLight(0xfffdf8, 0xfff2e4, 2.05));
    const key = new THREE.DirectionalLight(0xfff4e0, 1.3);
    key.position.set(-1.6, 2.6, 3);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    Object.assign(key.shadow.camera, { left: -1.3, right: 1.3, top: 1.3, bottom: -1.3, near: 0.5, far: 8 });
    key.shadow.bias = -0.0012;
    key.shadow.normalBias = 0.014;
    key.shadow.radius = 5;
    key.shadow.intensity = 0.5;
    this.scene.add(key);
    const back = new THREE.DirectionalLight(0xe6f0ff, 0.55);
    back.position.set(2, 1.2, -3);
    this.scene.add(back);

    // 回す軸。この下に姿を入れる
    this.pivot = new THREE.Group();
    this.pivot.rotation.order = "XYZ"; // 先に左右に回し、そのあと手前に倒す
    this.scene.add(this.pivot);

    this.twin = buildTwin();
    const scale = 1 / this.twin.radius;
    this.twin.group.scale.setScalar(scale);
    this.twin.group.position.copy(this.twin.center).multiplyScalar(-scale);
    this.pivot.add(this.twin.group);
    this.groundY = (this.twin.groundY - this.twin.center.y) * scale;

    // 接地の影。浮き沈みしても床に残す
    this.shadow = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: contactShadowTexture(), transparent: true, depthWrite: false, depthTest: false })
    );
    this.shadow.renderOrder = -1;
    const width = this.twin.radius * 1.5 * scale;
    this.shadow.scale.set(width, Math.min(width * 0.17, 0.3), 1);
    this.shadow.position.set(0, this.groundY, -0.2);
    this.scene.add(this.shadow);

    this.yaw = 0;
    this.pitch = 0;
    this.spin = 0; // 指を離したあとのすべり
    this.dragging = false;
    this.touchedAt = -Infinity;
    this.mood = "idle";
    this.focus = null; // 選んだ花びら（0〜4）
    this.speakUntil = -Infinity;
    this.hopAt = -Infinity;
    this.time = 0;
    this.point = new THREE.Vector3();
    this.clock = new THREE.Clock();
    this.reducedQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    this.container = null;
    this.handlers = {};
    this.running = false;
    this.loop = this.loop.bind(this);
    this.observer = new ResizeObserver(() => this.resize());
    this.bindDrag();
    this.canvas.addEventListener("webglcontextlost", (event) => {
      // 描く力を失ったら、2Dの姿に戻す。直ったときに戻さないのは、画面が行き来して見えないようにするため
      event.preventDefault();
      this.stop();
      failed = new Error("3Dを描けなくなりました");
      shared = null;
      this.handlers.onLost?.();
    });
  }

  get reduced() {
    return this.reducedQuery?.matches ?? false;
  }

  /* 舞台を container へ移す。levels は花びらごとの濃さの言葉。onFeet は足の位置（上からの割合）を受け取る */
  attach(container, levels, handlers = {}) {
    this.container = container;
    this.handlers = handlers;
    this.twin.setLevels(levels, true);
    container.replaceChildren(this.canvas);
    this.observer.disconnect();
    this.observer.observe(container);
    this.yaw = 0;
    this.pitch = 0;
    this.spin = 0;
    this.mood = "idle";
    // 前の画面で選んだ花びらを持ち越さない。選ぶ画面は、移したあとに focusPetal で選び直す
    this.focus = null;
    this.twin.setFocus(null);
    this.resize();
    this.start();
  }

  /* container から外す。別の画面がもう使っているときは何もしない */
  detach(container) {
    if (this.container !== container) {
      return;
    }
    this.stop();
    this.observer.disconnect();
    this.canvas.remove();
    this.container = null;
    this.handlers = {};
  }

  setLevels(levels) {
    this.twin.setLevels(levels);
  }

  setMood(mood) {
    this.mood = MOODS[mood] ? mood : "idle";
  }

  /* 答えが届いたときの小さな跳ね */
  hop() {
    this.hopAt = this.time;
  }

  /* 花びらを選ぶ（0〜4、上から時計回り）。選んだ花びらは前へ起き上がり、姿は正面を向いて1秒ほど話すように揺れる。
     null でいつもの動きに戻す。色と濃さは変えない（原則13）。speak を偽にすると、話す揺れを省く（開き直したとき） */
  focusPetal(index, { speak = true } = {}) {
    this.focus = Number.isInteger(index) ? index : null;
    this.twin.setFocus(this.focus);
    if (this.focus !== null) {
      this.speakUntil = speak ? this.time + SPEAK_ON_FOCUS : -Infinity;
      this.touchedAt = -Infinity; // 回したあとでも、すぐ正面へ戻る
      this.spin = 0;
    }
  }

  /* 止まっている姿（回さず、揺らさず）での、頭の中心と花びらの点の、canvas の上の位置（px）。ボタンを置く場所に使う */
  restAnchors(width, height) {
    const scale = 1 / this.twin.radius;
    const head = this.twin.faceCenter.clone().sub(this.twin.center).multiplyScalar(scale);
    const toScreen = (v) => {
      const p = v.clone().project(this.camera);
      return { x: ((p.x + 1) / 2) * width, y: ((1 - p.y) / 2) * height };
    };
    const petals = [0, 1, 2, 3, 4].map((index) => {
      const angle = (index * Math.PI * 2) / 5;
      return toScreen(head.clone().add(new THREE.Vector3(Math.sin(angle), Math.cos(angle), 0).multiplyScalar(PETAL_ANCHOR * scale)));
    });
    return { head: toScreen(head), petals };
  }

  /* いま描いた姿での、花びらの点の canvas の上の位置（px）。引き出し線の先を、揺れと起き上がりに合わせるため */
  liveAnchors(width, height) {
    return [0, 1, 2, 3, 4].map((index) => {
      const p = this.twin.petalPoint(index, this.point, PETAL_ANCHOR).project(this.camera);
      return { x: ((p.x + 1) / 2) * width, y: ((1 - p.y) / 2) * height };
    });
  }

  start() {
    if (!this.running) {
      this.running = true;
      this.clock.getDelta();
      this.renderer.setAnimationLoop(this.loop);
    }
  }

  stop() {
    this.running = false;
    this.renderer.setAnimationLoop(null);
  }

  resize() {
    if (!this.container) {
      return;
    }
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (!width || !height) {
      return;
    }
    this.renderer.setSize(width, height, false);
    this.fitCamera(width / height);
    this.render();
  }

  /* 姿の全身と足の下の台座が入る距離に置く。足の位置を呼んだ側へ知らせ、CSS の台座の輪をそこに合わせる */
  fitCamera(aspect) {
    const tan = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
    const middle = (FRAME_TOP + FRAME_BOTTOM) / 2;
    const half = (FRAME_TOP - FRAME_BOTTOM) / 2;
    // 花びらは前に出ているので、そのぶん少し離す
    const distance = Math.max(half / tan, FRAME_HALF_WIDTH / (tan * aspect)) + 0.25;
    this.camera.aspect = aspect;
    this.camera.position.set(0, middle, distance);
    this.camera.lookAt(0, middle, 0);
    this.camera.updateProjectionMatrix();
    const feet = new THREE.Vector3(0, this.groundY, 0).project(this.camera);
    this.handlers.onFeet?.(((1 - feet.y) / 2) * 100);
    if (this.handlers.onRest && this.container) {
      this.handlers.onRest(this.restAnchors(this.container.clientWidth, this.container.clientHeight));
    }
  }

  bindDrag() {
    const canvas = this.canvas;
    canvas.style.touchAction = "pan-y"; // 縦のスクロールはそのまま。横に動かすと回る
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "ツイン（AI）の3Dの姿。ドラッグか矢印キーで回せます");
    let active = null;
    let lastX = 0;
    let lastY = 0;
    let lastTime = 0;
    canvas.addEventListener("pointerdown", (event) => {
      if (active !== null) {
        return; // 2本目の指は使わない
      }
      active = event.pointerId;
      canvas.setPointerCapture(active);
      lastX = event.clientX;
      lastY = event.clientY;
      lastTime = performance.now();
      this.spin = 0;
      this.dragging = true;
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", (event) => {
      if (event.pointerId !== active) {
        return;
      }
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      this.yaw += dx * DRAG_SPEED;
      this.pitch = THREE.MathUtils.clamp(this.pitch + dy * DRAG_SPEED, -PITCH_LIMIT, PITCH_LIMIT);
      const now = performance.now();
      const dt = Math.max(0.008, (now - lastTime) / 1000);
      lastTime = now;
      this.spin = (dx * DRAG_SPEED) / dt;
      this.touchedAt = this.time;
    });
    const release = (event) => {
      if (event.pointerId !== active) {
        return;
      }
      active = null;
      this.dragging = false;
      this.touchedAt = this.time;
      canvas.classList.remove("dragging");
      // 手を離す前に止まっていたら、すべらせない
      if (this.reduced || performance.now() - lastTime > 90) {
        this.spin = 0;
      }
    };
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", release);
    canvas.addEventListener("keydown", (event) => {
      const step = THREE.MathUtils.degToRad(15);
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        this.yaw += event.key === "ArrowLeft" ? -step : step;
        this.touchedAt = this.time;
      } else if (event.key === "Home") {
        event.preventDefault();
        this.touchedAt = -Infinity;
      }
    });
  }

  update(dt) {
    // 花びらを選んでいる間は、はじめの1秒ほど話すように揺れ、そのあと正面で止まる
    const moodName = this.focus === null ? this.mood : this.time < this.speakUntil ? "speaking" : "focus";
    const mood = MOODS[moodName];
    const still = this.reduced;
    if (!this.dragging) {
      if (this.spin) {
        this.yaw += this.spin * dt;
        this.spin *= Math.exp(-dt * 4);
        if (Math.abs(this.spin) < 0.01) {
          this.spin = 0;
        }
      } else if (this.time - this.touchedAt > BACK_AFTER) {
        // 触っていないときは、気分の動きに戻る。動きを減らす設定では正面で止まる
        const ease = Math.min(1, dt * 2.2);
        if (!still && mood.turn) {
          this.yaw += mood.turn * dt;
        } else {
          const target = still ? 0 : Math.sin(this.time * mood.swaySpeed) * mood.sway;
          this.yaw += angleGap(this.yaw, target) * ease;
        }
        this.pitch += (0 - this.pitch) * ease;
      }
    }
    // 回り続けても数が大きくなりすぎないようにする
    this.yaw = Math.atan2(Math.sin(this.yaw), Math.cos(this.yaw));
    this.pivot.rotation.set(this.pitch, this.yaw, 0);

    let lift = 0;
    if (!still) {
      lift = Math.sin(this.time * mood.bobSpeed) * mood.bob;
      const since = this.time - this.hopAt;
      if (since >= 0 && since < 0.5) {
        lift += Math.sin((since / 0.5) * Math.PI) * 0.07;
      }
    }
    this.pivot.position.y = lift;
    // 浮くと影が少し薄くなる
    this.shadow.material.opacity = 1 - Math.max(0, lift) * 6;
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }

  loop() {
    // 画面から外れたら止める。片付けを呼び忘れた画面があっても、描き続けないため
    if (!this.container || !this.canvas.isConnected) {
      this.stop();
      return;
    }
    const dt = Math.min(0.05, this.clock.getDelta());
    this.time += dt;
    // 隠れている間（幅が0）は描かない
    if (!this.container.clientWidth) {
      return;
    }
    this.update(dt);
    this.twin.tick(this.time, dt, this.reduced);
    this.render();
    // 描いたあとの位置を知らせる（花びらの舞台だけが受け取る）
    if (this.handlers.onLive) {
      this.handlers.onLive(this.liveAnchors(this.container.clientWidth, this.container.clientHeight));
    }
  }
}

/* 舞台を1つだけ作って返す。描けないブラウザでは、2回目からはすぐ例外にする */
export function sharedTwinStage() {
  if (failed) {
    throw failed;
  }
  if (!shared) {
    try {
      shared = new TwinStage();
    } catch (error) {
      failed = error;
      throw error;
    }
  }
  return shared;
}
