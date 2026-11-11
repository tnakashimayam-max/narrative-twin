/*
  役割：ブラウザの読み上げ機能で、本人の言葉の引用と前置きを声にする部品。
  4つの極：4. 10年後に代弁する。
  守る原則：9（本人の言葉は本文のまま）、11（推定は読み上げない）、12（本人の声に似せない）。

  呼ぶ側は、前置きと本人の言葉の本文だけを渡す。声の種類は端末に任せ、本人に似せる調整はしない。
*/

import { stampWords } from "./stamps.js";

let speakingButton = null;
let idleLabel = "読み上げる";
let notify = null;

export function stopSpeaking() {
  window.speechSynthesis?.cancel();
  if (speakingButton) {
    speakingButton.textContent = idleLabel;
    speakingButton = null;
    notify?.(false);
    notify = null;
  }
}

/*
  押すと読み上げ、もう一度押すと止める。読み上げ中は、何が起きているかをボタンの言葉で知らせる。
  onChange は読み上げの始まりと終わりを知らせる。だれが読んでいるかを画面に出すときに使う。
*/
export function speak(text, button, { busyLabel = "読み上げ中（押すと止める）", onChange = null } = {}) {
  if (!window.speechSynthesis) {
    return;
  }
  const wasThis = speakingButton === button;
  stopSpeaking();
  if (wasThis) {
    return;
  }
  const voice = new SpeechSynthesisUtterance(stampWords(text));
  voice.lang = "ja-JP";
  voice.onend = () => {
    if (speakingButton === button) {
      stopSpeaking();
    }
  };
  speakingButton = button;
  idleLabel = button.textContent;
  button.textContent = busyLabel;
  notify = onChange;
  notify?.(true);
  window.speechSynthesis.speak(voice);
}
