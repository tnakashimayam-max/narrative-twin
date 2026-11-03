/*
  役割：本人のホームの部品。今日うかがったことの確認、託す人と範囲、記録の整理、10年後へ進む。
  4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
  守る原則：4（推測は「推測」と表示し、そうです／ちがいます／今は話したくない で答えられる）、
            5（直しても元の記録は消えない）、10（託す人と範囲は本人が決める）、
            13（ツインの色に数字を出さない。花びらの名前を本人の画面に出さない）。

  「10年後へ進む」では、1年ずつ色がのる様子を見せる。回数は出さない。
*/

import { api } from "./api.js";
import { el, openSheet, setTwinLevels, twinDrawing } from "./ui.js";

const YEAR_STEP_MS = 650;
const POLL_MS = 1500;

function dateWords(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

async function refreshColor() {
  try {
    setTwinLevels((await api("/twin/appearance")).levels);
  } catch {
    // 読めなくても、ほかの部分は使える
  }
}

/* 今日うかがったこと */
export function reviewSection(ctx, onChange) {
  const section = el("section", { class: "card review-list" });
  const message = el("p", { class: "error", role: "alert", hidden: true });

  async function act(path, json) {
    message.hidden = true;
    try {
      await api(path, { method: "POST", json });
      await refreshColor();
      onChange();
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  }

  function drawItem(item) {
    const area = el("div", {});
    const buttons = el(
      "div",
      { class: "row" },
      el("button", { type: "button", text: "そうです", onclick: (event) => { event.currentTarget.disabled = true; act(`/statements/${item.id}/confirm`); } }),
      el("button", {
        type: "button",
        text: "ちがいます",
        onclick: () => {
          const box = el("textarea", { rows: 2, "aria-label": "直す言葉", placeholder: "ご自分の言葉で書いてください", maxLength: 2000 });
          buttons.hidden = true;
          area.append(
            box,
            el(
              "div",
              { class: "row" },
              el("button", {
                type: "button",
                class: "primary",
                text: "この言葉に直す",
                onclick: (event) => {
                  if (!box.value.trim()) {
                    message.textContent = "直す言葉を入れてください。";
                    message.hidden = false;
                    return;
                  }
                  event.currentTarget.disabled = true;
                  act(`/statements/${item.id}/correct`, { text: box.value });
                },
              }),
              el("button", {
                type: "button",
                class: "quiet",
                text: "やめる",
                onclick: () => {
                  area.replaceChildren();
                  buttons.hidden = false;
                },
              })
            )
          );
          box.focus();
        },
      }),
      el("button", { type: "button", class: "quiet", text: "今は話したくない", onclick: (event) => { event.currentTarget.disabled = true; act(`/statements/${item.id}/rest`); } })
    );
    return el(
      "article",
      { class: `review-item${item.is_guess ? " guess" : ""}` },
      el(
        "div",
        { class: "hint" },
        item.is_guess ? el("span", { class: "guess-tag", text: "推測" }) : "",
        ` ${dateWords(item.effective_date)}の話から`
      ),
      el("p", { class: item.is_guess ? "" : "hand", text: item.body }),
      buttons,
      area
    );
  }

  api("/review")
    .then(({ items }) => {
      if (items.length === 0) {
        section.hidden = true;
        return;
      }
      section.append(
        el("h2", { text: "今日うかがったこと" }),
        el("p", { class: "hint", text: "合っているかを教えてください。答えなくてもかまいません。直しても、前の記録は消えません。" }),
        ...items.map(drawItem),
        message
      );
    })
    .catch(() => {
      section.hidden = true;
    });
  return section;
}

/* 託す人と範囲 */
export function handoverSection(ctx, onChange) {
  const section = el("section", { class: "card" }, el("h2", { text: "託す人と範囲" }));

  function openForm(view) {
    openSheet((inner, close) => {
      const family = view.family.map((person) => ({
        person,
        input: el("input", { type: "checkbox", checked: view.delegate_ids.includes(person.id) }),
      }));
      const themes = view.all_themes.map((theme) => ({
        theme,
        input: el("input", { type: "checkbox", checked: view.themes.includes(theme) }),
      }));
      const message = el("p", { class: "error", role: "alert", hidden: true });
      inner.append(
        el("h2", { text: "託す人と範囲を決める" }),
        el("p", { class: "hint", text: "決めるのはご本人です。あとから変えられます。" }),
        el("h3", { text: "託す人" }),
        ...family.map(({ person, input }) => el("label", { class: "check" }, input, person.name)),
        el("h3", { text: "もしものときに見せるテーマ" }),
        el("p", { class: "hint", text: "選んだテーマの話は、まだ確かめていない話も含めて、託す人と、招かれた専門職に見えます。「今は話したくない」と答えた話は見えません。" }),
        ...themes.map(({ theme, input }) => el("label", { class: "check" }, input, theme)),
        message,
        el(
          "div",
          { class: "row" },
          el("button", {
            type: "button",
            class: "primary",
            text: "この内容で決める",
            onclick: async () => {
              try {
                await api("/handover", {
                  method: "PUT",
                  json: {
                    delegate_ids: family.filter(({ input }) => input.checked).map(({ person }) => person.id),
                    themes: themes.filter(({ input }) => input.checked).map(({ theme }) => theme),
                  },
                });
                close();
                onChange();
              } catch (error) {
                message.textContent = error.message;
                message.hidden = false;
              }
            },
          }),
          el("button", { type: "button", class: "quiet", text: "やめる", onclick: close })
        )
      );
    });
  }

  api("/handover")
    .then((view) => {
      const names = view.family.filter((person) => view.delegate_ids.includes(person.id)).map((person) => person.name);
      section.append(
        el("p", { text: `託す人：${names.join("、") || "まだ決めていません"}` }),
        el("p", { text: `見せるテーマ：${view.themes.join("、") || "まだ決めていません"}` }),
        view.decided_on ? el("p", { class: "hint", text: `${dateWords(view.decided_on)}に決めました。` }) : "",
        el("button", { type: "button", class: "wide", text: "託す人と範囲を決める", onclick: () => openForm(view) })
      );
    })
    .catch(() => {
      section.hidden = true;
    });
  return section;
}

/* 1年ずつ色をのせる。回数は出さない（原則13）。 */
function showGrowth(onDone) {
  openSheet(async (inner, close) => {
    const figure = el("span", { class: "twin-figure growth" }, twinDrawing(200, null));
    const year = el("p", { class: "growth-year", role: "status", text: "読み込んでいます" });
    const done = el("button", {
      type: "button",
      class: "primary wide",
      text: "2036年へ",
      hidden: true,
      onclick: () => {
        close();
        onDone();
      },
    });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    inner.append(el("h2", { text: "10年後へ進みます" }), figure, year, message, done);
    try {
      let state = await api("/dev/advance", { method: "POST" });
      while (state.working) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        state = await api("/dev/story");
      }
      const { items } = await api("/twin/growth");
      for (const item of items) {
        figure.replaceChildren(twinDrawing(200, item.levels));
        year.textContent = `${item.year}年`;
        await new Promise((resolve) => setTimeout(resolve, YEAR_STEP_MS));
      }
      year.textContent = "10年分の会話を積み上げました。";
      await refreshColor();
      done.hidden = false;
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
      inner.append(el("button", { type: "button", class: "quiet", text: "閉じる", onclick: close }));
    }
  });
}

/* 記録の整理（固める処理）と、架空用の起動の「10年後へ進む」 */
export function demoSection(ctx, onChange) {
  const section = el("section", { class: "card" }, el("h2", { text: "記録を整理する" }));
  const message = el("p", { role: "status", class: "hint" });
  const organize = el("button", {
    type: "button",
    class: "wide",
    text: "記録を整理する",
    onclick: async () => {
      organize.disabled = true;
      message.textContent = "整理しています。少しかかります。";
      try {
        const report = await api("/model/consolidate", { method: "POST" });
        message.textContent = report.months.length ? `${report.months.join("、")}の話を整理しました。` : "新しく整理する話はありませんでした。";
        await refreshColor();
      } catch (error) {
        message.textContent = error.message;
      }
      organize.disabled = false;
    },
  });
  section.append(
    el("p", { class: "hint", text: "話を、大切にしていることや決まりごとにまとめ直します。もとの話は消えません。" }),
    organize,
    message
  );
  api("/dev/story")
    .then((story) => {
      section.prepend(el("p", { class: "hint", text: `今日：${dateWords(story.today)}` }));
      if (!story.future_loaded && story.ready) {
        section.append(
          el("button", {
            type: "button",
            class: "primary wide",
            text: "10年後へ進む",
            style: "margin-top:10px",
            onclick: () =>
              showGrowth(async () => {
                ctx.me = await ctx.refreshMe();
                onChange();
              }),
          })
        );
      }
    })
    .catch(() => {
      // 自分用の起動には「10年後へ進む」がない
    });
  return section;
}


