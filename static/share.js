/*
  役割：「この話を共有する」の小さな画面。便りの下書きを本人が直し、相手を選んで共有する。
  4つの極：2. 分かち合う。
  守る原則：1（決めるのは本人）、10（相手は本人が選ぶ）。

  「この内容で共有する」を押すまで、タイムラインには出ない。下書きはサーバーに保存されない。
*/

import { api, photoUrl } from "./api.js";
import { el, openSheet } from "./ui.js";

const SCOPES = [
  ["家族", "家族（家族の人だけ）"],
  ["縁のある人まで", "縁のある人まで（家族と、友人・知人・ご近所の人）"],
];

export function openShareSheet(conversationId, onShared) {
  openSheet(async (inner, close) => {
    const status = el("p", { role: "status", text: "下書きを作っています。少しお待ちください。" });
    const errorLine = el("p", { class: "error", role: "alert", hidden: true });
    inner.append(el("h2", { text: "この話を共有する" }), status, errorLine, el("button", { type: "button", class: "quiet", text: "やめる", onclick: close }));
    let draft;
    try {
      draft = await api(`/conversations/${conversationId}/letter-draft`, { method: "POST" });
    } catch (error) {
      status.remove();
      errorLine.textContent = error.message;
      errorLine.hidden = false;
      return;
    }
    const body = el("textarea", { rows: 5, maxLength: 500, value: draft.body, "aria-label": "便りの本文" });
    let chosenPhoto = draft.photo_ids[0] || null;
    const photoRow = el("div", { class: "choice-row" });
    if (draft.photo_ids.length) {
      const options = [...draft.photo_ids, null];
      for (const photoId of options) {
        const input = el("input", { type: "radio", name: "letter-photo", checked: photoId === chosenPhoto });
        input.addEventListener("change", () => {
          chosenPhoto = photoId;
        });
        const label = el("label", {}, input, photoId ? "この写真を載せる" : "写真を載せない");
        if (photoId) {
          photoUrl(photoId).then((url) => {
            if (url) {
              label.append(el("img", { src: url, alt: "載せる写真", width: 72, height: 72, style: "object-fit:cover;border-radius:8px" }));
            }
          });
        }
        photoRow.append(label);
      }
    }
    let scope = "家族";
    const scopeRow = el(
      "div",
      { class: "stack" },
      SCOPES.map(([value, label], index) => {
        const input = el("input", { type: "radio", name: "letter-scope", checked: index === 0 });
        input.addEventListener("change", () => {
          scope = value;
        });
        return el("label", { class: "check" }, input, label);
      })
    );
    const shareButton = el("button", {
      type: "button",
      class: "primary wide",
      text: "この内容で共有する",
      onclick: async () => {
        errorLine.hidden = true;
        shareButton.disabled = true;
        try {
          await api("/letters", {
            method: "POST",
            json: { conversation_id: conversationId, body: body.value, photo_id: chosenPhoto, share_with: scope },
          });
          close();
          onShared?.();
        } catch (error) {
          errorLine.textContent = error.message;
          errorLine.hidden = false;
          shareButton.disabled = false;
        }
      },
    });
    inner.replaceChildren(
      el("h2", { text: "この話を共有する" }),
      el("p", { class: "hint", text: "ツイン（AI）が作った下書きです。直してから共有できます。押すまで、だれにも見えません。" }),
      el("label", { class: "field" }, "便りの本文", body),
      draft.photo_ids.length ? el("div", { class: "stack" }, el("strong", { text: "写真" }), photoRow) : null,
      el("div", { class: "stack" }, el("strong", { text: "見せる相手" }), scopeRow),
      errorLine,
      el("div", { class: "stack" }, shareButton, el("button", { type: "button", class: "quiet wide", text: "やめる", onclick: close }))
    );
  });
}
