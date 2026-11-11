/*
  役割：別のページ（ケアプラン案、1枚の紙、評価の質問）の上に出す、戻る道。
  4つの極：特定の極に属さない土台。
  守る原則：行き止まりを作らない。どの画面からも、もとの画面か入口へ戻れるようにする。

  ケアプラン案と1枚の紙は新しいタブで開くので、もとの画面はうしろに残っている。
  「もとの画面へ戻る」は、そのタブがあればこのタブを閉じて戻る。書きかけや読んでいた位置を失わないため。
  URL を直接開いたときは閉じられないので、アプリを開く。
  「ログイン画面」は、だれとして使っているかを忘れて入口を開く。もとのタブはそのまま残る。
  ページごとの処理と分けて読み込む。ページの中身が失敗しても、戻る道を残すため。
*/

import * as session from "./api.js";
import { el } from "./ui.js";

function backToApp() {
  if (window.opener && !window.opener.closed) {
    window.close();
    return;
  }
  location.href = "./";
}

/* 左上のロゴを押したとき、アプリのホームへ戻る（tyumajinさんの依頼、2026-11-07）。
   もとの画面（このページを開いたタブ）があれば、そちらをホームにしてこのタブを閉じる。「もとの画面へ戻る」と同じく、
   アプリのタブを2つにしないため。もとの画面がなければ、このタブでアプリのホームを開く（./#home、static/app.js）。 */
function goHome() {
  if (window.opener && !window.opener.closed) {
    try {
      window.opener.postMessage({ type: "nt-home" }, location.origin);
      window.opener.focus();
      window.close();
      return;
    } catch {
      // もとの画面に知らせられないときは、このタブでホームを開く
    }
  }
  location.href = "./#home";
}

/* ロゴの絵そのものを押せるようにする。包む要素を足すと、ページごとの CSS（幅の割合）が変わるため */
function makeLogoGoHome() {
  const logo = document.querySelector(".page-logo > img, header .brand-logo");
  if (!logo) {
    return;
  }
  logo.classList.add("nt-home-logo");
  logo.setAttribute("role", "link");
  logo.setAttribute("tabindex", "0");
  logo.setAttribute("aria-label", "Narrative Twin（ホームへ戻る）");
  logo.dataset.tip = "アプリのホームへ戻ります";
  logo.addEventListener("click", goHome);
  logo.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      goHome();
    }
  });
  const look = document.createElement("style");
  look.textContent = ".nt-home-logo { cursor: pointer; border-radius: 6px; } .nt-home-logo:focus-visible { outline: 3px solid #f0b73a; outline-offset: 3px; }";
  document.head.append(look);
}

function toEntrance() {
  session.forgetUser();
  // 新規のデータを持たない起動では、この関数がない
  session.forgetData?.();
  location.href = "./";
}

makeLogoGoHome();

const host = document.querySelector(".page-nav");

/* 起動の種類（架空データなど）を見出しの帯に出す。アプリの帯と同じく、どのページでも何のデータかを隠さない（原則8）。
   ページの中にもう出ているとき（評価の画面の見出し、1枚の紙の紙の中）は足さない */
async function showMode() {
  if (!host || document.querySelector(".page-mode-tag, header .tag")) {
    return;
  }
  try {
    const mode = await session.api("/mode");
    host.prepend(el("span", { class: "tag page-mode-tag", text: mode.label }));
  } catch {
    // 読めなくても、戻る道は残す
  }
}
showMode();
// 人を選んでいないウィンドウでは、もとの画面と入口が同じになるので、ログイン画面だけを出す
const hasApp = Boolean(window.opener) || Boolean(session.currentUserId());
host?.append(
  hasApp ? el("button", { type: "button", text: "もとの画面へ戻る", tip: "開いていたアプリの画面へ戻ります", onclick: backToApp }) : "",
  el("button", { type: "button", text: "ログイン画面", tip: "使う人を選び直す入口を開きます", onclick: toEntrance })
);
