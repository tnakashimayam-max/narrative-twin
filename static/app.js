// 役割：名前を選ぶとその人の空の画面に切り替える。
// 4つの極：特定の極に属さない土台。
// 守る原則：10（だれが何を見られるかは画面で決めず、サーバーの応答に従う）。
//
// サーバーとのやり取りの取り決めは docs/api.md にある。
"use strict";

// 役割ごとの見た目。本人と家族と縁のある人は手紙・アルバム風、専門職は白を基調にする。
const LOOK_BY_ROLE = {
  本人: "letter",
  家族: "letter",
  縁のある人: "letter",
  専門職: "modern",
};

const loginView = document.getElementById("login-view");
const homeView = document.getElementById("home-view");
const userList = document.getElementById("user-list");
const loginError = document.getElementById("login-error");
const homeTitle = document.getElementById("home-title");
const homeRole = document.getElementById("home-role");
const photoSection = document.getElementById("photo-section");
const photoInput = document.getElementById("photo-input");
const photoStatus = document.getElementById("photo-status");
const photoQuestion = document.getElementById("photo-question");

// 写真の問いは、本人の画面にだけ出す。問いには必ず「ツイン（AI）」と付ける。
async function askAboutPhoto(file) {
  photoQuestion.hidden = true;
  photoStatus.textContent = "写真を見ています。少しお待ちください。";
  let message = "つながりませんでした。もう一度お試しください。";
  try {
    const response = await fetch("/api/photo-question", {
      method: "POST",
      headers: { "Content-Type": file.type },
      body: file,
    });
    const body = await response.json();
    if (response.ok) {
      photoStatus.textContent = "";
      photoQuestion.textContent = `ツイン（AI）：${body.question}`;
      photoQuestion.hidden = false;
      return;
    }
    message = body.detail;
  } catch {
    // つながらないときは、上のメッセージのまま出す
  }
  photoStatus.textContent = message;
}

photoInput.addEventListener("change", () => {
  if (photoInput.files.length > 0) {
    const file = photoInput.files[0];
    // 値を消しておくと、失敗のあとに同じ写真を選び直せる
    photoInput.value = "";
    askAboutPhoto(file);
  }
});

function showError(message) {
  loginError.textContent = message;
  loginError.hidden = false;
}

function showHome(user) {
  document.body.dataset.look = LOOK_BY_ROLE[user.role];
  homeTitle.textContent = `${user.name}の画面`;
  homeRole.textContent = `立場：${user.role}`;
  photoSection.hidden = user.role !== "本人";
  photoQuestion.hidden = true;
  photoStatus.textContent = "";
  photoInput.value = "";
  loginView.hidden = true;
  homeView.hidden = false;
  // 画面が切り替わったことが、読み上げの機能を使う人にも分かるようにする
  homeTitle.focus();
}

function showLogin() {
  document.body.dataset.look = "letter";
  homeView.hidden = true;
  loginView.hidden = false;
}

async function chooseUser(userId) {
  loginError.hidden = true;
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!response.ok) {
    showError("選べませんでした。もう一度お試しください。");
    return;
  }
  showHome(await response.json());
}

function renderUsers(users) {
  userList.replaceChildren(
    ...users.map((user) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${user.name}（${user.role}）`;
      button.addEventListener("click", () => {
        chooseUser(user.id).catch(() => showError("つながりませんでした。もう一度お試しください。"));
      });
      return button;
    }),
  );
}

async function start() {
  const usersResponse = await fetch("/api/users");
  if (!usersResponse.ok) {
    showError("名前の一覧を読み込めませんでした。再読み込みしてください。");
    return;
  }
  renderUsers((await usersResponse.json()).items);

  // すでに選んだ人がいれば、その人の画面から始める
  const meResponse = await fetch("/api/me");
  if (meResponse.ok) {
    showHome(await meResponse.json());
  } else {
    showLogin();
  }
}

document.getElementById("logout-button").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  showLogin();
});

fetch("/api/mode")
  .then((response) => response.json())
  .then((mode) => {
    document.getElementById("mode-label").textContent = mode.label;
  })
  .catch(() => {});

start().catch(() => showError("つながりませんでした。再読み込みしてください。"));
