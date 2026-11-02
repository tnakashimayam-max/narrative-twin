/*
  役割：写真を送る前に、画面の側で縮める。
  4つの極：1. 書く・話す。
  守る原則：8（手元の写真の情報を広げない）、10（撮影の位置などを送らない）。

  長い辺を1568pxに縮め、canvas で描き直して JPEG にする。描き直すと撮影の位置などの情報は残らない。
  スマホの写真は大きいので、送る時間と費用を減らす目的もある。
  サーバーでも同じ処理をする（app/images.py）。ここを通らずに届いた写真でも、情報を残さないため。
*/

export const UNREADABLE_PHOTO = "この形式の写真は読めません。JPEG か PNG に変えてから、お送りください。";

const MAX_LONG_EDGE = 1568;
const QUALITY = 0.88;

async function decode(file) {
  if ("createImageBitmap" in window) {
    return createImageBitmap(file, { imageOrientation: "from-image" });
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } finally {
    URL.revokeObjectURL(url);
  }
}

/* 写真を縮めて JPEG の Blob を返す。読めなければ、分かる言葉のエラーにする。 */
export async function shrinkPhoto(file) {
  let picture;
  try {
    picture = await decode(file);
  } catch {
    throw new Error(UNREADABLE_PHOTO);
  }
  const width = picture.width;
  const height = picture.height;
  if (!width || !height) {
    throw new Error(UNREADABLE_PHOTO);
  }
  const scale = Math.min(1, MAX_LONG_EDGE / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext("2d");
  // 透明な部分が黒くならないように、先に白で塗る
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(picture, 0, 0, canvas.width, canvas.height);
  if (picture.close) {
    picture.close();
  }
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error(UNREADABLE_PHOTO))),
      "image/jpeg",
      QUALITY
    );
  });
}
