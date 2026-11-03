/*
  役割：ホームの画面。話に出てきた人の候補、つながりの地図、人の管理、切り替えを出す。
  4つの極：2. 分かち合う。
  守る原則：1（候補を決めるのは本人）、7（催促や数の表示を作らない）、10（地図と家系図は今は本人だけ）。

  切り替えは名前を一度押すだけにする。
*/

import { api } from "./api.js";
import { renderEcomap } from "./ecomap_ui.js";
import { openGenogram } from "./genogram_ui.js";
import { openOwnForm, openPersonForm, personRow } from "./people_ui.js";
import { demoSection, handoverSection, reviewSection } from "./review.js";
import { avatar, el, openSheet, twinFigure } from "./ui.js";

const ROLE_CHOICES = [
  ["家族", "家族"],
  ["縁のある人", "縁のある人"],
  ["専門職", "専門職"],
];

/* 立場の言葉。託された人かどうかが分かるようにする。 */
function otherRoleWords(me) {
  if (me.role === "専門職") {
    return "立場：専門職・招かれて担当しています";
  }
  return me.can_use_supporter_screen ? `立場：${me.role}・本人から託された人` : `立場：${me.role}`;
}

function otherGuide(me) {
  if (me.role === "専門職") {
    return "本人の担当として招かれています。本人の記録を、ツイン（AI）に聞いて読めます。ご家族との部屋はトークから開けます。";
  }
  if (me.can_use_supporter_screen) {
    return "便りはタイムラインで、部屋の話はトークで読めます。本人が託した範囲の記録は、ツイン（AI）に聞いて読めます。";
  }
  return "本人がツイン（AI）と話した中から、本人が選んだ便りがタイムラインに届きます。部屋の話はトークで読めます。下のタブを押してください。";
}

/* ケアマネジャーを招く。招けないときは何も出さない。出すかどうかはサーバーが日付と立場で決める。 */
function inviteSection(ctx, onDone) {
  const section = el("section", { class: "stack" });
  api("/me")
    .then((me) => {
      if (!me.can_invite_professional) {
        return;
      }
      section.append(
        el(
          "div",
          { class: "card" },
          el("h2", { text: "ケアマネジャーを招く" }),
          el("p", { class: "hint", text: "招いた人に見えるのは、本人が選んだテーマの話だけです。" }),
          el("button", { type: "button", class: "wide", text: "ケアマネジャーを招く", onclick: () => openInviteSheet(onDone) })
        )
      );
    })
    .catch(() => {
      // 読めなくても、ほかの部分は使える
    });
  return section;
}

function openInviteSheet(onDone) {
  return openSheet((inner, close) => {
    const name = el("input", { type: "text", maxLength: 40, autocomplete: "off" });
    const relation = el("input", { type: "text", maxLength: 40, value: "ケアマネジャー", autocomplete: "off" });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    inner.append(
      el("h2", { text: "ケアマネジャーを招く" }),
      el("label", { class: "field" }, "お名前（例：田中さん）", name),
      el("label", { class: "field" }, "続柄や役割", relation),
      message,
      el(
        "div",
        { class: "row" },
        el("button", {
          type: "button",
          class: "primary",
          text: "招く",
          onclick: async () => {
            try {
              await api("/people/professional", {
                method: "POST",
                json: { name: name.value, relation: relation.value || null },
              });
              close();
              onDone();
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

function candidateCard(candidate, onDone) {
  // 続柄だけで呼ばれた人（父、孫など）には「さん」を付けない
  const honored = candidate.name === candidate.relation || candidate.name.endsWith("さん") ? candidate.name : `${candidate.name}さん`;
  const shown = candidate.relation && candidate.relation !== candidate.name ? `${honored}（${candidate.relation}）` : honored;
  const message = el("p", { class: "error", role: "alert", hidden: true });
  const role = el(
    "select",
    { "aria-label": "登録するときの立場" },
    ROLE_CHOICES.map(([value, text]) => el("option", { value, text, selected: value === (["親", "子", "配偶者", "きょうだい"].includes(candidate.relative_kind) ? "家族" : "縁のある人") }))
  );
  const decide = (decision) => async () => {
    try {
      await api(`/people/candidates/${candidate.id}/decide`, { method: "POST", json: { decision, role: decision === "登録する" ? role.value : null } });
      onDone();
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  };
  return el(
    "div",
    { class: "card" },
    el("p", {}, `話に出てきた人：${shown}。登録しますか？`),
    el("p", { class: "candidate-evidence", text: candidate.evidence }),
    el("label", { class: "field" }, "登録するときの立場", role),
    message,
    el(
      "div",
      { class: "row" },
      el("button", { type: "button", class: "primary", text: "登録する", onclick: decide("登録する") }),
      el("button", { type: "button", text: "ちがう", onclick: decide("ちがう") }),
      el("button", { type: "button", class: "quiet", text: "今はいい", onclick: decide("今はいい") })
    )
  );
}

export function renderHome(container, ctx) {
  let alive = true;
  const panel = el("section", { class: "panel stack" });
  container.append(panel);

  const switcher = el("section", { class: "card" }, el("h2", { text: "ほかの人に切り替える" }));
  const switchList = el("div", { class: "switch-list" });
  switcher.append(switchList);

  async function loadSwitcher() {
    try {
      const { items } = await api("/users");
      if (!alive) {
        return;
      }
      switchList.replaceChildren(
        ...items
          .filter((user) => user.id !== ctx.me.id)
          .map((user) => el("button", { type: "button", class: "quiet", text: user.name, onclick: () => ctx.switchUser(user.id) }))
      );
      if (switchList.children.length === 0) {
        switcher.hidden = true;
      }
    } catch {
      switcher.hidden = true;
    }
  }

  async function drawSelfHome() {
    panel.replaceChildren();
    panel.append(
      el(
        "div",
        { class: "row" },
        avatar(ctx.me, "large"),
        el("h1", { text: `${ctx.me.called_name}の画面` })
      ),
      el(
        "button",
        {
          type: "button",
          class: "user-button lead",
          onclick: async () => {
            const { items } = await api("/rooms");
            ctx.openTab("talk", { roomId: items[0].id });
          },
        },
        twinFigure(),
        "ツイン（AI）と話す"
      )
    );
    panel.append(reviewSection(ctx, drawSelfHome));
    const candidates = el("section", { class: "stack" });
    panel.append(candidates);
    try {
      const { items } = await api("/people/candidates");
      if (alive && items.length) {
        candidates.append(...items.map((item) => candidateCard(item, drawSelfHome)));
      }
    } catch {
      // 候補が読めなくても、ほかの部分は使える
    }
    const resting = el("section", { class: "stack" });
    panel.append(resting);
    try {
      const { items } = await api("/resting-themes");
      if (alive && items.length) {
        resting.append(
          el(
            "div",
            { class: "card" },
            el("h2", { text: "お休みにしている話題" }),
            el("p", { class: "hint", text: "ツイン（AI）はこの話題を聞きません。" }),
            ...items.map((theme) =>
              el(
                "div",
                { class: "person-row" },
                el("div", { class: "person-text", text: theme }),
                el("button", {
                  type: "button",
                  class: "quiet",
                  text: "お休みをやめる",
                  onclick: async () => {
                    await api("/resting-themes/resume", { method: "POST", json: { theme } });
                    drawSelfHome();
                  },
                })
              )
            )
          )
        );
      }
    } catch {
      // 読めなくても、ほかの部分は使える
    }
    panel.append(handoverSection(ctx, drawSelfHome));
    const mapSection = el("section", { class: "card map-card" });
    panel.append(mapSection);
    renderEcomap(mapSection, ctx, drawSelfHome);
    panel.append(
      el("button", { type: "button", class: "wide", text: "家系図を見る", onclick: () => openGenogram(ctx) })
    );
    const peopleSection = el("section", { class: "card" }, el("h2", { text: "家族と知人" }));
    panel.append(peopleSection);
    try {
      const { items } = await api("/people");
      peopleSection.append(...items.map((person) => personRow(person, ctx, drawSelfHome)));
    } catch (error) {
      peopleSection.append(el("p", { class: "error", text: error.message }));
    }
    peopleSection.append(
      el("button", {
        type: "button",
        class: "wide",
        style: "margin-top:10px",
        text: "人を登録する",
        onclick: () => openPersonForm(ctx, null, drawSelfHome),
      })
    );
    panel.append(demoSection(ctx, drawSelfHome));
    panel.append(inviteSection(ctx, drawSelfHome));
    panel.append(switcher);
    loadSwitcher();
  }

  function drawOtherHome() {
    panel.replaceChildren(
      el("div", { class: "row" }, avatar(ctx.me, "large"), el("h1", { text: `${ctx.me.called_name}の画面` })),
      el("p", { class: "hint", text: otherRoleWords(ctx.me) }),
      el("p", { text: otherGuide(ctx.me) }),
      ctx.me.can_use_supporter_screen
        ? el("button", { type: "button", class: "primary wide", text: "ツイン（AI）に聞く", onclick: () => ctx.openTab("advocate") })
        : "",
      el("button", {
        type: "button",
        class: "wide",
        text: "自分の名前や呼び方を直す",
        onclick: () =>
          openOwnForm(ctx, async () => {
            ctx.me = await ctx.refreshMe();
            drawOtherHome();
          }),
      }),
      inviteSection(ctx, drawOtherHome),
      switcher
    );
    loadSwitcher();
  }

  if (ctx.me.role === "本人") {
    drawSelfHome();
  } else {
    drawOtherHome();
  }
  return () => {
    alive = false;
  };
}
