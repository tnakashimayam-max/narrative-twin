/*
  役割：発言の中のスタンプを、絵と言葉に読み替える部品。
  4つの極：2. 分かち合う。
  守る原則：9（本人の言葉は本文のまま。保存した文は変えない）。

  スタンプは発言の本文に「（スタンプ：太陽）」の形で入っている。サーバーとツインは、ふつうの文字として読む。
  画面に出すときだけ絵にする。別の入れ物を作らなかったのは、探す機能やツインの読みをそのまま使えるため。
  絵があるのは太陽、星、花、月だけ。ほかの名前は人が書いた文として、そのまま出す。
  どの画面の言葉も el を通るので、絵にするのは static/ui.js の el の1か所で済む。
  いいねのような反応や数ではなく、1つの発言として扱う（原則14）。
*/

const FILES = { 太陽: "sun", 星: "star", 花: "flower", 月: "moon" };
const STAMP = /（スタンプ：(太陽|星|花|月)）/;
const STAMP_RUN = /（スタンプ：(太陽|星|花|月)）(?:（スタンプ：\1）)*/g;
// 部屋の一覧の文はサーバーが字数で切るので（app/rooms.py）、終わりに切れたスタンプが残ることがある
const CUT_STAMP = /（(?:ス(?:タ(?:ン(?:プ(?:：[^）]*)?)?)?)?)?$/;

export const STAMP_NAMES = Object.keys(FILES);

/* 発言に保存する形。 */
export function stampText(name) {
  return `（スタンプ：${name}）`;
}

export function hasStamp(words) {
  return typeof words === "string" && STAMP.test(words);
}

export function stampImage(name) {
  const image = document.createElement("img");
  image.className = "stamp";
  image.src = `stamps/${FILES[name]}.svg`;
  image.alt = `${name}のスタンプ`;
  image.width = 28;
  image.height = 28;
  image.draggable = false;
  return image;
}

/* 文を、文字と絵の並びにする。split は名前の部分も返すので、奇数番目が名前になる。 */
export function stampParts(words) {
  return words
    .split(STAMP)
    .map((part, index) => (index % 2 === 1 ? stampImage(part) : part))
    .filter((part) => part !== "");
}

/* 絵を出せない所（読み上げ、探す機能）で使う言葉。「太陽のスタンプ5個」のように読む。 */
export function stampWords(words) {
  return words.replace(STAMP_RUN, (run, name) => {
    const count = run.length / stampText(name).length;
    return count > 1 ? `${name}のスタンプ${count}個` : `${name}のスタンプ`;
  });
}

/* スタンプだけの発言に足す印。吹き出しをやめて絵を大きく出す。数が多いほど小さくして折り返す。 */
export function stampOnlyClass(words) {
  if (!hasStamp(words)) {
    return "";
  }
  const pieces = words.split(STAMP);
  if (pieces.some((piece, index) => index % 2 === 0 && piece.trim())) {
    return "";
  }
  const count = (pieces.length - 1) / 2;
  return count === 1 ? " stamp-only stamp-one" : count <= 3 ? " stamp-only stamp-few" : " stamp-only stamp-many";
}

export function withoutCutStamp(words) {
  return words.replace(CUT_STAMP, "");
}
