/*
  役割：サーバーとのやり取りの共通の部品。ログイン中の人の番号と、写真の読み込みを扱う。
  4つの極：特定の極に属さない土台。
  守る原則：10（見える範囲はサーバーが決める。画面では絞り込まない）。

  だれとして使っているかは、ウィンドウごとに sessionStorage で持ち、ヘッダーで送る。
  cookie は同じブラウザのウィンドウで共有されるので、2つのウィンドウで別の人として使えないため。
  写真も同じヘッダーが要るので、<img> の src に道を書かず、fetch して blob で出す。
  取り決めは docs/api.md にある。
*/

const USER_KEY = "nt_user_id";
const photoUrls = new Map();

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function currentUserId() {
  return sessionStorage.getItem(USER_KEY);
}

export function rememberUser(userId) {
  sessionStorage.setItem(USER_KEY, String(userId));
}

export function forgetUser() {
  sessionStorage.removeItem(USER_KEY);
  photoUrls.clear();
}

function identityHeaders() {
  const userId = currentUserId();
  return userId ? { "X-NT-User": userId } : {};
}

async function failure(response) {
  let detail = "うまくいきませんでした。少し時間をおいて、もう一度お試しください。";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") {
      detail = body.detail;
    }
  } catch {
    // 本文が JSON でないときは、決まった文のままにする
  }
  return new ApiError(response.status, detail);
}

/* JSON を送り、JSON を受け取る。通信が切れたときも ApiError にそろえる。 */
export async function api(path, { method = "GET", json, body, headers = {} } = {}) {
  const options = { method, headers: { ...identityHeaders(), ...headers } };
  if (json !== undefined) {
    options.headers["content-type"] = "application/json";
    options.body = JSON.stringify(json);
  } else if (body !== undefined) {
    options.body = body;
  }
  let response;
  try {
    response = await fetch(`/api${path}`, options);
  } catch {
    throw new ApiError(0, "つながりませんでした。少し時間をおいて、もう一度お試しください。");
  }
  if (!response.ok) {
    throw await failure(response);
  }
  return response.status === 204 ? null : response.json();
}

/* 写真を送り、保存された写真の番号を受け取る。 */
export function uploadPhoto(blob, kind) {
  return api(`/photos?kind=${encodeURIComponent(kind)}`, {
    method: "POST",
    body: blob,
    headers: { "content-type": blob.type || "application/octet-stream" },
  });
}

/* 写真を読み、<img> に渡せる URL を返す。見てよくない写真は 404 になるので null を返す。 */
export async function photoUrl(photoId) {
  if (photoUrls.has(photoId)) {
    return photoUrls.get(photoId);
  }
  let response;
  try {
    response = await fetch(`/api/photos/${photoId}`, { headers: identityHeaders() });
  } catch {
    return null;
  }
  if (!response.ok) {
    return null;
  }
  const url = URL.createObjectURL(await response.blob());
  photoUrls.set(photoId, url);
  return url;
}
