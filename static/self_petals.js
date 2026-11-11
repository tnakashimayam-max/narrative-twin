/*
  役割：本人のホームの「5つの花びら」から開く画面。専門職の「記録」の上と同じ「ツインの花びら」（static/petals.js）を、
        本人に向けた言葉で出す（tyumajinさんの依頼、2026-11-07）。上に「ホームへ戻る」を置く。
  4つの極：3. 読み解き、積み上げる、4. 10年後に代弁する。
  守る原則：4（読みは推測として出す）、9（本人の言葉は記録の本文のまま）、12（必ず「ツイン（AI）」と出す）、
            13（数字を出さない。家族には出さない。読みはサーバーが本人と専門職にだけ返す：app/petals.py）。

  下のタブは「ホーム」のまま（static/app.js の TAB_PARENT）。読みがまだないときは、そう知らせてホームへの道を残す。
*/

import { petalSection } from "./petals.js";
import { el } from "./ui.js";

export function renderSelfPetals(container, ctx) {
  const panel = el("section", { class: "panel self-petals-panel stack" });
  const back = el("button", {
    type: "button",
    class: "quiet self-petals-back",
    text: "‹ ホームへ戻る",
    tip: "ホームの画面に戻ります",
    onclick: () => ctx.openTab("home"),
  });
  const empty = el("p", {
    class: "notice self-petals-empty",
    hidden: true,
    text: "まだ花びらの読みがありません。ツイン（AI）と話していくと、ここに読みが出てきます。",
  });
  const petals = petalSection({
    forSelf: true,
    onEmpty: () => {
      empty.hidden = false;
    },
  });
  panel.append(back, empty, petals.node);
  container.append(panel);
  return () => petals.dispose();
}
