/*
  役割：ホームの画面。話に出てきた人の候補、つながりの地図、人の管理、切り替えを出す。
  4つの極：2. 分かち合う。
  守る原則：1（候補を決めるのは本人）、7（催促や数の表示を作らない）、10（地図と家系図は今は本人だけ）。

  切り替えは名前を一度押すだけにする。
  本人のホームは、話すことと確かめのまとまりと、地図や人の管理のまとまりに分ける。PC版は横に並べ、スマホ版は続けて縦に出す（style.css）。
*/

import { api } from "./api.js";
import { renderEcomap } from "./ecomap_ui.js";
import { openGenogram } from "./genogram_ui.js";
import { openOwnForm, openPersonForm, personRow } from "./people_ui.js";
import { demoSection, handoverSection, reviewSection } from "./review.js";
import { toolTitle } from "./tools.js";
import { NEEDS_KEY_WORDS, avatar, dateLabel, el, openSheet, photoImage, profilePhotoButton, svg, twinFigure } from "./ui.js";

// 5つの花びらの色（static/petals.js の PETAL_COLORS と同じ順：くつろぎ、結びつき、共にあること、たずさわること、自分らしさ）
const PETAL_COLORS = ["#3F5F86", "#E67F65", "#C9922C", "#3D7A5B", "#6F5F9A"];

/* 「5つの花びら」のボタンの印。白い丸の中に、5色の花びらと生成りの真ん中を描く（飾りなので読み上げない） */
function petalsMark() {
  return svg(
    "svg",
    { viewBox: "0 0 24 24", width: "34", height: "34" },
    ...PETAL_COLORS.map((color, order) =>
      svg("ellipse", { cx: "12", cy: "6.4", rx: "3.7", ry: "4.9", fill: color, transform: `rotate(${order * 72} 12 12)` })
    ),
    svg("circle", { cx: "12", cy: "12", r: "3.3", fill: "#f6ebd3", stroke: "#ffffff", "stroke-width": "0.8" })
  );
}

const CANDIDATES_SHOWN = 3;
const ROLE_CHOICES = [
  ["家族", "家族"],
  ["縁のある人", "縁のある人"],
  ["専門職", "専門職"],
];

/* 立場の言葉。託された人かどうかが分かるようにする。 */
function otherRoleWords(me) {
  if (me.role === "専門職") {
    return me.profession ? `立場：専門職・${me.profession}` : "立場：専門職・招かれて担当しています";
  }
  return me.can_use_supporter_screen ? `立場：${me.role}・本人から託された人` : `立場：${me.role}`;
}

/* ツールがない職種には、そのことを1行で伝える。押せないボタンは置かない。
   託された専門職には、サーバーのツールがなくても、ツールの画面にケア会議がある（2026-11-06）ので、出さない。 */
function noToolNote(me) {
  const note = el("p", { class: "hint", hidden: true });
  if (me.role === "専門職" && me.profession && !me.can_use_supporter_screen) {
    api("/people/professions")
      .then(({ items }) => {
        if (items.find((item) => item.name === me.profession)?.tools.length === 0) {
          note.textContent = `${me.profession}のツールは、今はありません。`;
          note.hidden = false;
        }
      })
      .catch(() => {
        // 読めなくても、ほかの部分は使える
      });
  }
  return note;
}

function otherGuide(me) {
  if (me.role === "専門職" && !me.can_use_supporter_screen) {
    return "本人から託されるまで、本人の記録は見えません。部屋の話はトークから開けます。";
  }
  if (me.role === "専門職") {
    return "本人の担当として招かれています。本人の記録を、ツイン（AI）に聞いて読めます。ご家族との部屋はトークから開けます。";
  }
  if (me.can_use_supporter_screen) {
    return "便りはタイムラインで、部屋の話はトークで読めます。本人が託した範囲の記録は、ツイン（AI）に聞いて読めます。";
  }
  return "本人がツイン（AI）と話した中から、本人が選んだ便りがタイムラインに届きます。部屋の話はトークで読めます。どちらもタブから開けます。";
}

/* 専門職を招く。招けないときは何も出さない。出すかどうかはサーバーが日付と立場で決める。 */
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
          el("h2", { text: "専門職を招く" }),
          el("p", { class: "hint", text: "招いた人に見えるのは、本人が選んだテーマの話だけです。" }),
          el("button", { type: "button", class: "wide", text: "専門職を招く…", tip: "名前と職種を入れて、担当の専門職を招く窓を開きます", onclick: () => openInviteSheet(onDone) })
        )
      );
    })
    .catch(() => {
      // 読めなくても、ほかの部分は使える
    });
  return section;
}

function openInviteSheet(onDone) {
  return openSheet(async (inner, close) => {
    const name = el("input", { type: "text", maxLength: 40, autocomplete: "off" });
    const job = el("select", { "aria-label": "職種" });
    const tools = el("dl", { class: "job-table" });
    const message = el("p", { class: "error", role: "alert", hidden: true });
    inner.append(
      el("h2", { text: "専門職を招く" }),
      el("label", { class: "field" }, "お名前（例：田中さん）", name),
      el("label", { class: "field" }, "職種", job),
      tools,
      el("p", { class: "hint", text: "招いた人に見えるのは、本人が選んだテーマの話だけです。" }),
      message,
      el(
        "div",
        { class: "row" },
        el("button", {
          type: "button",
          class: "primary",
          text: "招く",
          tip: "この人を専門職として登録し、本人が選んだ話だけを見せます",
          onclick: async () => {
            if (!name.value.trim()) {
              message.textContent = "お名前を入れてください。";
              message.hidden = false;
              name.focus();
              return;
            }
            try {
              await api("/people/professional", {
                method: "POST",
                json: { name: name.value, relation: job.value || null },
              });
              close();
              onDone();
            } catch (error) {
              message.textContent = error.message;
              message.hidden = false;
            }
          },
        }),
        el("button", { type: "button", class: "quiet", text: "やめる", tip: "招かずに、この窓を閉じます", onclick: close })
      )
    );
    try {
      const { items } = await api("/people/professions");
      job.append(...items.map((item) => el("option", { value: item.name, text: item.name })));
      // 画面のツールの画面と同じ並びにする。1枚の紙は画面に出さない（static/tools.js）。
      // ケア会議は、本人から託されればどの職種もツールの画面から開ける（2026-11-06）
      // 使えるツールが同じ職種は1行にまとめる。職種を7つに増やしても、同じ行が続いて窓が長くならないように（2026-11-08）
      const groups = new Map();
      for (const item of items) {
        const usable = `${[...item.tools.filter((tool) => tool.id !== "sheet").map(toolTitle), "ケア会議"].join("、")}（本人から託されたとき）`;
        groups.set(usable, [...(groups.get(usable) || []), item.name]);
      }
      tools.append(...[...groups].flatMap(([usable, names]) => [el("dt", { text: `${names.join("、")}が使えるツール` }), el("dd", { text: usable })]));
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
    }
  });
}

/* 名前の行。同じ顔を2つ並べないよう、プロフィール写真がある人は写真だけを出す。 */
function homeHead(me) {
  return el("div", { class: "row home-head" }, profilePhotoButton(me) || avatar(me, "large"), el("h1", { text: `${me.called_name}の画面` }));
}

// 「さん」を付けるのは、名前で呼ばれた人だけにする。続柄や役目で呼ばれた人、呼び方がすでに付いている人には付けない。
// 語尾だけで見ると「和子」「和夫」「直人」のような名前まで外れるので、1字の続柄は語全体か「〇〇の」の形でだけ見る
const KIN_ALONE = /^(夫|妻|兄|姉|弟|妹|父|母|子|娘|孫|甥|姪)$/;
const KIN_OF_SOMEONE = /の(夫|妻|兄|姉|弟|妹|父|母|子|娘|孫|甥|姪)$/;
const CALLED_BY_ROLE = /(さん|くん|君|ちゃん|様|先生|兄貴|姉貴|親父|おふくろ|お袋|父親|母親|息子|子供|子ども|先輩|後輩|上司|部下|同僚|友人|住職|ヘルパー|店長|社長|会長|店員|職員|世話役|の人|医|師|屋|内科|医院|病院|クリニック)$/;

function calledByName({ name, relation }) {
  if (relation && name.includes(relation)) {
    return false;
  }
  return !KIN_ALONE.test(name) && !KIN_OF_SOMEONE.test(name) && !CALLED_BY_ROLE.test(name);
}

function candidateCard(candidate, onDone) {
  const honored = calledByName(candidate) ? `${candidate.name}さん` : candidate.name;
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
    { class: "card candidate-card" },
    el("p", {}, `話に出てきた人：${shown}。登録しますか？`),
    el("p", { class: "candidate-evidence", text: candidate.evidence }),
    el("label", { class: "field" }, "登録するときの立場", role),
    message,
    el(
      "div",
      { class: "row" },
      el("button", { type: "button", class: "primary", text: "登録する", tip: "選んだ立場で、この人をつながりのある人に入れます", onclick: decide("登録する") }),
      el("button", { type: "button", text: "ちがう", tip: "話に出た人とちがうので、登録せずに一覧から外します", onclick: decide("ちがう") }),
      el("button", { type: "button", class: "quiet", text: "今はいい", tip: "今は登録せずに、この候補を一覧から外します", onclick: decide("今はいい") })
    )
  );
}

/* 候補は記録が増えるほど積み上がり、10年分では38枚でホームが12,274px まで伸びて、下の話題や人の管理に届かなくなる。
   最初の3枚だけを出し、残りは本人が開く形にする。催促にしないよう、残りの数は出さない（原則7）。 */
function candidateList(items, onDone) {
  const cards = items.map((item) => candidateCard(item, onDone));
  const rest = cards.slice(CANDIDATES_SHOWN);
  return [
    ...cards.slice(0, CANDIDATES_SHOWN),
    ...(rest.length ? [el("details", { class: "fold" }, el("summary", { text: "話に出てきた人をもっと見る", tip: "残りの候補の人を開いて見られます" }), el("div", { class: "stack" }, ...rest))] : []),
  ];
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
          // 名前だけだと見分けにくいので、小さな丸い顔を付ける（2026-11-07 の見直しの案C）
          .map((user) => el("button", { type: "button", class: "quiet switch-person", tip: `${user.name}の画面に切り替えます`, onclick: () => ctx.switchUser(user.id) }, avatar(user, "small"), user.name))
      );
      if (switchList.children.length === 0) {
        switcher.hidden = true;
      }
    } catch {
      switcher.hidden = true;
    }
  }

  /* 本人が自分のアイコンや写真を入れ替えたあとに、新しい番号で描き直す。 */
  async function redrawSelfHome() {
    ctx.me = await ctx.refreshMe();
    await drawSelfHome();
  }

  async function drawSelfHome() {
    const mainColumn = el("div", { class: "home-main stack" });
    const sideColumn = el("div", { class: "home-side stack" });
    panel.classList.add("home-wide");
    panel.replaceChildren(el("div", { class: "home-columns stack" }, mainColumn, sideColumn));
    mainColumn.append(
      homeHead(ctx.me),
      el(
        "button",
        {
          type: "button",
          // ホームでいちばん使う入口。塗りの大きなボタンにして、下の確かめ（白いカード）と見分けられるようにする
          class: "user-button lead talk-cta",
          tip: "ツインとの話の部屋を開いて、話し始められます",
          onclick: async () => {
            const { items } = await api("/rooms");
            ctx.openTab("talk", { roomId: items[0].id });
          },
        },
        twinFigure(),
        "ツイン（AI）と話す"
      ),
      // 手紙を書く。ツインと話したことをもとに、本人が送る手紙の下書きを作る（static/my_letter.html、tyumajinさんの依頼）
      el(
        "button",
        {
          type: "button",
          // 2番目の入口。朱（郵便ポストの色）で塗り、藍の「話す」と並べて目立たせる（tyumajinさんの依頼、2026-11-07）
          class: "user-button lead letter-cta",
          tip: "ツインと話したことから、送る手紙の下書きを作る画面を開きます",
          onclick: () => window.open("my_letter.html"),
        },
        el(
          "span",
          { class: "letter-cta-mark", "aria-hidden": "true" },
          svg(
            "svg",
            { viewBox: "0 0 24 24", width: "30", height: "30", fill: "none", stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round" },
            svg("path", { d: "M3 7h18v12H3z" }),
            svg("path", { d: "M3 7l9 6 9-6" })
          )
        ),
        "手紙を書く"
      ),
      // 5つの花びら。ツイン（AI）が読んだ、何で心が満たされ何で損なわれるかを、本人も見られる（static/self_petals.js、
      // tyumajinさんの依頼、2026-11-07）。色は、ツインの体の若草から取った深い緑。藍の「話す」と朱の「手紙」と並べて見分けられる
      el(
        "button",
        {
          type: "button",
          class: "user-button lead petals-cta",
          tip: "ツインが話から読んだ、あなたの5つの花びらを見る画面を開きます",
          onclick: () => ctx.openTab("petals"),
        },
        el("span", { class: "petals-cta-mark", "aria-hidden": "true" }, petalsMark()),
        "5つの花びら"
      ),
      ctx.needsKey ? el("p", { class: "notice", text: NEEDS_KEY_WORDS }) : ""
    );
    mainColumn.append(reviewSection(ctx, drawSelfHome));
    const candidates = el("section", { class: "stack" });
    mainColumn.append(candidates);
    try {
      const { items } = await api("/people/candidates");
      if (alive && items.length) {
        candidates.append(...candidateList(items, drawSelfHome));
      }
    } catch {
      // 候補が読めなくても、ほかの部分は使える
    }
    const resting = el("section", { class: "stack" });
    mainColumn.append(resting);
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
                  tip: "ツインがこの話題を、また聞くようになります",
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
    mainColumn.append(handoverSection(ctx, drawSelfHome));
    const mapSection = el("section", { class: "card map-card" });
    sideColumn.append(mapSection);
    renderEcomap(mapSection, ctx, redrawSelfHome);
    sideColumn.append(
      el("button", { type: "button", class: "wide", text: "家系図を見る", tip: "登録した家族を、家系図の形で開いて見られます", onclick: () => openGenogram(ctx) })
    );
    const peopleSection = el("section", { class: "card people-card" }, el("h2", { text: "つながりのある人" }));
    sideColumn.append(peopleSection);
    try {
      const { items } = await api("/people");
      // 本人と家族、縁のある人、専門職に分ける
      const groups = [
        ["本人と家族", ["本人", "家族"]],
        ["縁のある人", ["縁のある人"]],
        ["専門職", ["専門職"]],
      ];
      for (const [title, roles] of groups) {
        const people = items.filter((person) => roles.includes(person.role));
        if (people.length) {
          peopleSection.append(el("h3", { class: "people-group", text: title }), ...people.map((person) => personRow(person, ctx, redrawSelfHome)));
        }
      }
    } catch (error) {
      peopleSection.append(el("p", { class: "error", text: error.message }));
    }
    peopleSection.append(
      el("button", {
        type: "button",
        class: "wide",
        style: "margin-top:10px",
        text: "人を登録する",
        tip: "名前や立場を入れて、つながりのある人を足します",
        onclick: () => openPersonForm(ctx, null, drawSelfHome),
      })
    );
    sideColumn.append(demoSection(ctx, drawSelfHome));
    sideColumn.append(inviteSection(ctx, drawSelfHome));
    sideColumn.append(switcher);
    loadSwitcher();
  }

  /* 本人以外のホームのいちばん上の「誠一さんの今」（ご本人の決定、2026-11-07 の見直しの案C）。本人の丸い写真と、
     いちばん新しい便り（タイムラインと同じ、本人が選んで共有したもの）。見える便りがなければ出さない */
  function selfNowCard() {
    const card = el("section", { class: "card self-now", hidden: true });
    api("/timeline")
      .then(({ items, people, self_id: selfId }) => {
        const self = people.find((person) => person.id === selfId);
        const latest = items[0];
        if (!self || !latest || !alive) {
          return;
        }
        const name = (self.called_name || self.name).replace(/(さん|くん|ちゃん|先生|様)$/, "");
        card.replaceChildren(
          el("h2", { text: `${name}さんの今` }),
          el(
            "div",
            { class: "self-now-row" },
            avatar(self, "large"),
            el(
              "div",
              { class: "self-now-text" },
              el("p", { class: "self-now-date", text: `${dateLabel(latest.shared_at)}の便り` }),
              el("p", { class: "self-now-body hand", text: latest.body.length > 90 ? `${latest.body.slice(0, 90)}…` : latest.body })
            ),
            latest.photo_id ? photoImage(latest.photo_id, "self-now-photo", "便りの写真") : null
          ),
          el("button", { type: "button", class: "wide", text: "タイムラインで読む", tip: "便りとコメントを、タイムラインで読みます", onclick: () => ctx.openTab("timeline") })
        );
        card.hidden = false;
      })
      .catch(() => {
        // 読めなくても、ほかの部分は使える
      });
    return card;
  }

  function drawOtherHome() {
    panel.replaceChildren(
      homeHead(ctx.me),
      selfNowCard(),
      el("p", { class: "hint", text: otherRoleWords(ctx.me) }),
      el("p", { text: otherGuide(ctx.me) }),
      noToolNote(ctx.me),
      ctx.me.can_use_supporter_screen
        ? el(
            "button",
            {
              type: "button",
              // 本人のホームの「ツイン（AI）と話す」と同じ、花の子つきの藍の大きな入口にする（2026-11-07 の見直し）
              class: "user-button lead talk-cta",
              tip: "ツインが本人の立場で答える画面を開きます",
              onclick: () => ctx.openTab("advocate"),
            },
            twinFigure(),
            "ツイン（AI）に聞く"
          )
        : "",
      el("button", {
        type: "button",
        class: "wide",
        text: "自分の名前や呼び方を修正",
        tip: "自分の名前や、画面での呼ばれ方を修正する窓を開きます",
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
