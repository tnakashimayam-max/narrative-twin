/*
  役割：本人のホームの部品。今日うかがったことの確認、託す人と範囲、記録の整理、進む。
  4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
  守る原則：4（推測は「推測」と表示し、そうです／ちがいます／今は話したくない で答えられる）、
            5（直しても元の記録は消えない）、10（託す人と範囲は本人が決める）、
            13（ツインの色に数字を出さない。花びらの名前を本人の画面に出さない）。

*/

import { api } from "./api.js";
import { el, openSheet, setTwinLevels, startWaiting, twinDrawing } from "./ui.js";

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

// 一度に出す数。並べると宿題の山に見えるので（原則7）、1つずつ出す。答えると次が出て、本人が開けば先も見られる
const REVIEW_SHOWN = 1;

/* ツイン（AI）からの確かめ。onLoaded は読み込めたときに一覧を渡す（トークの右のペインが、中身の変化を見るため）。 */
export function reviewSection(ctx, onChange, { onLoaded } = {}) {
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
      // 3つの答えは色を分けるが、重さは同じにする。「そうです」だけを目立たせて、うなずくように誘わないため（原則6、7）
      el("button", { type: "button", class: "answer-yes", text: "そうです", tip: "合っていると答え、ご本人が確かめた話として残します", onclick: (event) => { event.currentTarget.disabled = true; act(`/statements/${item.id}/confirm`); } }),
      el("button", {
        type: "button",
        class: "answer-no",
        text: "ちがいます",
        tip: "ご自分の言葉で修正する欄を出します。前の記録は消えません",
        onclick: () => {
          const box = el("textarea", { rows: 2, "aria-label": "修正する言葉", placeholder: "ご自分の言葉で書いてください", maxLength: 2000 });
          buttons.hidden = true;
          area.append(
            box,
            el(
              "div",
              { class: "row" },
              el("button", {
                type: "button",
                class: "primary",
                text: "この言葉に修正する",
                tip: "書いた言葉に修正します。前の記録も消えずに残ります",
                onclick: (event) => {
                  if (!box.value.trim()) {
                    message.textContent = "修正する言葉を入れてください。";
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
                tip: "修正するのをやめて、答えのボタンに戻ります",
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
      el("button", { type: "button", class: "quiet answer-rest", text: "今は話したくない", tip: "今は答えずにおき、この話を本人だけに見える形に戻します", onclick: (event) => { event.currentTarget.disabled = true; act(`/statements/${item.id}/rest`); } })
    );
    return el(
      "article",
      { class: `review-item${item.is_guess ? " guess" : ""}` },
      item.is_guess ? el("span", { class: "guess-tag", text: "ツイン（AI）の推測" }) : "",
      el("div", { class: "hint", text: `${dateWords(item.effective_date)}の話から` }),
      // 根拠の言葉がだれのものかを必ず出す（原則3）。本人以外の言葉は、証言の点線の札にする。
      // 話した人の名前がサーバーから届けば添える（origin_speaker。まだないときは「ほかの人の言葉」）
      item.origin_is_self
        ? el("span", { class: "source-tag self", text: "本人の言葉" })
        : item.origin
          ? el("span", { class: "source-tag testimony", text: item.origin_speaker ? `${item.origin_speaker}の言葉` : "ほかの人の言葉" })
          : "",
      item.origin ? el("p", { class: "review-origin hand", text: item.origin }) : "",
      el("p", { class: item.is_guess ? "" : "hand", text: item.is_guess ? `${item.body} 合っていますか？` : item.body }),
      buttons,
      area
    );
  }

  api("/review")
    .then(({ items }) => {
      onLoaded?.(items);
      if (items.length === 0) {
        section.hidden = true;
        return;
      }
      const rest = items.slice(REVIEW_SHOWN);
      const more = el("button", {
        type: "button",
        class: "quiet",
        style: "margin-top:10px",
        text: "ほかの話も見る",
        tip: "ツインからの次の確かめを、この下に出します",
        hidden: rest.length === 0,
        onclick: () => {
          more.before(drawItem(rest.shift()));
          more.hidden = rest.length === 0;
        },
      });
      section.append(
        el("h2", { text: "ツイン（AI）からの確かめ" }),
        el("p", { class: "hint", text: "合っているかを教えてください。答えなくてもかまいません。修正しても、前の記録は消えません。" }),
        ...items.slice(0, REVIEW_SHOWN).map(drawItem),
        more,
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
            tip: "選んだ託す人とテーマで決めます。あとから変えられます",
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
          el("button", { type: "button", class: "quiet", text: "やめる", tip: "何も変えずに、この窓を閉じます", onclick: close })
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
        el("button", { type: "button", class: "wide", text: "託す人と範囲を決める", tip: "もしものときに託す人と、見せるテーマを選ぶ窓を開きます", onclick: () => openForm(view) })
      );
    })
    .catch(() => {
      section.hidden = true;
    });
  return section;
}

/* 記録の整理（固める処理）と、架空用の起動の今日の日付 */
export function demoSection(ctx, onChange) {
  // 新規のデータはデモではないので、整理だけの欄として出す
  const section = el("section", { class: "card" }, el("h2", { text: ctx.modeKind === "fresh" ? "記録の整理" : "デモの操作（架空データ）" }));
  const message = el("p", { role: "status", class: "hint" });
  const organize = el("button", {
    type: "button",
    class: "wide",
    text: "記録を整理する",
    tip: "話を、大切にしていることにまとめ直します。もとの話は消えません",
    onclick: async () => {
      organize.disabled = true;
      startWaiting(message, "整理しています。少しかかります。");
      try {
        const report = await api("/model/consolidate", { method: "POST" });
        message.textContent = report.months.length ? `${report.months.join("、")}の話を整理しました。` : "新しく整理する話はありませんでした。";
        await refreshColor();
      } catch (error) {
        // キーがないときは、待っても直らないのでそう伝える
        message.textContent = ctx.needsKey && error.status === 503 ? "記録を整理するには API キーが要ります。" : error.message;
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
    })
    .catch(() => {
      // 自分用の起動には今日の日付の表示がない
    });
  return section;
}


