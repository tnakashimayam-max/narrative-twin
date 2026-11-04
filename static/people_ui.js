/*
  役割：人の登録と管理の小さな画面。
  4つの極：2. 分かち合う。
  守る原則：10（託す人と見せる範囲は本人が決める）。

  立場を変えると見える範囲が変わるので、保存の前に「この人に見えるようになるもの」を言葉で出す。
  消さずに「はずす」にする。はずすときは、押し間違えないよう、ボタンを2回押す形にする。
  ブラウザの確認の窓は使わない。
  本人かどうかの確かめはサーバーでも行う。
*/

import { api, uploadPhoto } from "./api.js";
import { shrinkPhoto } from "./photo.js";
import { add, avatar, el, openSheet, twoStepButton } from "./ui.js";

// 人の入力欄の言葉。丸いアイコンと大きく出す写真は別の枠で、別々に入れ替えられる。どちらも AI には送らない。
const ICON_LABEL = "アイコン（丸く表示します。AI には送りません）";
const PROFILE_LABEL = "プロフィール写真（AI には送りません）";

const ROLES = [
  ["家族", "家族"],
  ["縁のある人", "縁のある人（友人・知人・ご近所など）"],
  ["専門職", "専門職"],
];

function options(pairs, current) {
  return pairs.map(([value, label]) => el("option", { value, text: label, selected: value === (current ?? "") }));
}

async function attachPhoto(input, kind = "プロフィール") {
  const file = input.files[0];
  if (!file) {
    return undefined;
  }
  const shrunk = await shrinkPhoto(file);
  const uploaded = await uploadPhoto(shrunk, kind);
  return uploaded.id;
}

/* 選んだ写真を枠ごとに送り、指定する番号を values に足す。同じ画像を選んでも、写真の行は枠ごとに分かれる。 */
async function attachPhotos(values, icon, profile) {
  const iconId = await attachPhoto(icon);
  if (iconId) {
    values.photo_id = iconId;
  }
  const profileId = await attachPhoto(profile);
  if (profileId) {
    values.profile_photo_id = profileId;
  }
}

/* 本人が人を登録する、直す。person を省くと新しい人。 */
export function openPersonForm(ctx, person, onDone) {
  openSheet((inner, close) => {
    const isNew = !person;
    const isSelfTarget = person?.role === "本人";
    const name = el("input", { type: "text", maxLength: 40, required: true, value: person?.name ?? "" });
    const called = el("input", { type: "text", maxLength: 40, value: person?.called_name ?? "" });
    const role = el("select", {}, options(ROLES, person?.role ?? "家族"));
    const relation = el("input", { type: "text", maxLength: 40, value: person?.relation ?? "", placeholder: "例：妻、長女、ご近所の友人" });
    const residence = el("select", {}, options([["", "選ばない"], ["同居", "同居"], ["近く", "近く"], ["遠く", "遠く"]], person?.residence));
    const year = el("input", { type: "number", min: 1880, max: 2100, value: person?.birth_year ?? "" });
    const gender = el("select", {}, options([["", "選ばない"], ["男性", "男性"], ["女性", "女性"], ["不明", "答えない"]], person?.gender));
    const deceased = el("input", { type: "checkbox", checked: Boolean(person?.is_deceased) });
    const memo = el("textarea", { rows: 2, maxLength: 2000, value: person?.memo ?? "" });
    const icon = el("input", { type: "file", accept: "image/*" });
    const profile = el("input", { type: "file", accept: "image/*" });
    const preview = el("div", { class: "notice", hidden: true });
    const message = el("p", { class: "error", role: "alert", hidden: true });

    role.addEventListener("change", async () => {
      preview.hidden = true;
      if (isNew || role.value === person.role) {
        return;
      }
      try {
        const data = await api(`/people/${person.id}/role-preview?role=${encodeURIComponent(role.value)}`);
        preview.replaceChildren(
          el("strong", { text: "立場を変えると、次のように変わります" }),
          ...data.sees.map((text) => el("div", { text: `この人に見えるようになるもの：${text}` })),
          ...data.stops.map((text) => el("div", { text: `この人に見えなくなるもの：${text}` })),
          ...data.same.map((text) => el("div", { text }))
        );
        preview.hidden = false;
      } catch (error) {
        message.textContent = error.message;
        message.hidden = false;
      }
    });

    const form = el("form", { class: "stack" });
    add(
      form,
      el("h2", { text: isNew ? "人を登録する" : `${person.name}を直す` }),
      el("label", { class: "field" }, "お名前", name),
      el("label", { class: "field" }, "呼ばれ方（ツインや家族がこう呼びます。例：和子さん）", called),
      isSelfTarget ? null : el("label", { class: "field" }, "立場", role),
      preview,
      isSelfTarget ? null : el("label", { class: "field" }, "続柄", relation),
      isSelfTarget ? null : el("label", { class: "field" }, "住まい", residence),
      el("label", { class: "field" }, "生まれた年（なくてもかまいません）", year),
      el("label", { class: "field" }, "性別（なくてもかまいません）", gender),
      isSelfTarget ? null : el("label", { class: "check" }, deceased, "亡くなった方"),
      isSelfTarget ? null : el("label", { class: "field" }, "メモ", memo),
      el("label", { class: "field" }, ICON_LABEL, icon),
      el("label", { class: "field" }, PROFILE_LABEL, profile),
      message,
      el("button", { type: "submit", class: "primary wide", text: "保存する" }),
      el("button", { type: "button", class: "quiet wide", text: "やめる", onclick: close })
    );

    if (!isNew && !isSelfTarget) {
      form.append(
        twoStepButton(
          "この人をはずす",
          "もう一度押すと、はずします（過去の発言は残ります）",
          async () => {
            try {
              await api(`/people/${person.id}/remove`, { method: "POST" });
              close();
              onDone?.();
            } catch (error) {
              message.textContent = error.message;
              message.hidden = false;
            }
          },
          "quiet wide"
        )
      );
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.hidden = true;
      const values = {
        name: name.value,
        called_name: called.value || null,
        birth_year: year.value ? Number(year.value) : null,
        gender: gender.value || null,
      };
      if (!isSelfTarget) {
        Object.assign(values, {
          role: role.value,
          relation: relation.value || null,
          residence: residence.value || null,
          is_deceased: deceased.checked,
          memo: memo.value || null,
        });
      }
      try {
        await attachPhotos(values, icon, profile);
        if (isNew) {
          await api("/people", { method: "POST", json: values });
        } else {
          await api(`/people/${person.id}`, { method: "PUT", json: values });
        }
        close();
        onDone?.();
      } catch (error) {
        message.textContent = error.message;
        message.hidden = false;
      }
    });
    inner.append(form);
  });
}

/* 本人以外が自分の名前、呼ばれ方、アイコン、プロフィール写真を直す。 */
export function openOwnForm(ctx, onDone) {
  openSheet((inner, close) => {
    const name = el("input", { type: "text", maxLength: 40, required: true, value: ctx.me.name });
    const called = el("input", { type: "text", maxLength: 40, value: ctx.me.called_name ?? "" });
    const icon = el("input", { type: "file", accept: "image/*" });
    const profile = el("input", { type: "file", accept: "image/*" });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    const form = el(
      "form",
      { class: "stack" },
      el("h2", { text: "自分のことを直す" }),
      el("label", { class: "field" }, "お名前", name),
      el("label", { class: "field" }, "呼ばれ方", called),
      el("label", { class: "field" }, ICON_LABEL, icon),
      el("label", { class: "field" }, PROFILE_LABEL, profile),
      message,
      el("button", { type: "submit", class: "primary wide", text: "保存する" }),
      el("button", { type: "button", class: "quiet wide", text: "やめる", onclick: close })
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.hidden = true;
      try {
        const values = { name: name.value, called_name: called.value || null };
        await attachPhotos(values, icon, profile);
        await api(`/people/${ctx.me.id}`, { method: "PUT", json: values });
        close();
        onDone?.();
      } catch (error) {
        message.textContent = error.message;
        message.hidden = false;
      }
    });
    inner.append(form);
  });
}

/* 人の一覧の1行。 */
export function personRow(person, ctx, onDone) {
  const detail = [person.role, person.relation, person.residence, person.is_deceased ? "亡くなった方" : null].filter(Boolean).join("・");
  return el(
    "div",
    { class: "person-row" },
    avatar(person),
    el("div", { class: "person-text" }, el("strong", { text: person.called_name || person.name }), el("div", { class: "hint", text: detail })),
    el("button", { type: "button", class: "quiet", text: "直す", onclick: () => openPersonForm(ctx, person, onDone) })
  );
}
