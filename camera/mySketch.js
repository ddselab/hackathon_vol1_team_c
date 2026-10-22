/*
By Okazz 2026
*/

const VIDEO_WIDTH = 640;
const VIDEO_HEIGHT = 480;
// HandPoseの21点: 0=手首, 各指は付け根→先端の順に4点ずつ
const FINGERS = [
  [0, 1, 2, 3, 4], // 親指
  [0, 5, 6, 7, 8], // 人差し指
  [0, 9, 10, 11, 12], // 中指
  [0, 13, 14, 15, 16], // 薬指
  [0, 17, 18, 19, 20], // 小指
];
const FINGERTIP_INDICES = [4, 8, 12, 16, 20];
const JOINT_SIZE = 10;
const DOT_SIZE = 20;
const PHOTO_COUNTDOWN = 3000; // 両手ピースから撮影までの時間（ミリ秒）
const PEACE_GRACE = 300; // 認識が一瞬途切れてもカウントダウンを続ける時間（ミリ秒）

let video;
let handPose;
let hands = [];
let peaceStartTime = null; // 両手ピースが始まった時刻。nullならカウントダウンしていない
let lastPeaceTime = -Infinity;
let photoTaken = false; // 撮影後、ピースをやめるまで次の撮影をしない
let flashStartTime = -Infinity;

async function setup() {
  createCanvas(windowWidth, windowHeight);

  video = createCapture(VIDEO);
  video.size(VIDEO_WIDTH, VIDEO_HEIGHT);
  video.hide();

  handPose = await ml5.handPose({ maxHands: 2 });
  handPose.detectStart(video, gotHands);
}

function draw() {
  background(0);

  let view = getVideoView();
  drawMirroredVideo(view);

  // 手を描く前に撮影して、写真には映像だけが写るようにする
  let peace = hands.length >= 2 && hands.every(isPeace);
  let countdown = updatePhotoCountdown(peace);
  if (countdown === 0) {
    savePhoto(view);
    flashStartTime = millis();
  }

  for (let hand of hands) {
    let points = hand.keypoints.map((point) => videoPointToScreen(point, view));
    drawHand(points);
  }

  if (countdown > 0) drawCountdown(countdown, view);
  drawFlash(view);
}

function gotHands(results) {
  hands = results;
}

// 指先が第2関節より手首に近ければ、その指は曲がっているとみなす
function isFingerFolded(hand, tipIndex, jointIndex) {
  let wrist = hand.keypoints[0];
  let tip = hand.keypoints[tipIndex];
  let joint = hand.keypoints[jointIndex];
  return (
    dist(tip.x, tip.y, wrist.x, wrist.y) <
    dist(joint.x, joint.y, wrist.x, wrist.y)
  );
}

// 人差し指と中指が伸びて、薬指と小指が曲がっていればピース
function isPeace(hand) {
  return (
    !isFingerFolded(hand, 8, 6) && // 人差し指 [先端, 第2関節]
    !isFingerFolded(hand, 12, 10) && // 中指
    isFingerFolded(hand, 16, 14) && // 薬指
    isFingerFolded(hand, 20, 18) // 小指
  );
}

// 撮影までの残り時間（ミリ秒）を返す。撮影する瞬間は0、カウントダウンしていなければnull
function updatePhotoCountdown(peace) {
  let now = millis();
  if (peace) lastPeaceTime = now;

  if (now - lastPeaceTime > PEACE_GRACE) {
    peaceStartTime = null;
    photoTaken = false;
    return null;
  }
  if (photoTaken) return null;

  if (peaceStartTime === null) peaceStartTime = now;
  let remaining = PHOTO_COUNTDOWN - (now - peaceStartTime);
  if (remaining > 0) return remaining;

  photoTaken = true;
  return 0;
}

// 映像の部分だけを切り出してPNGで保存する
function savePhoto(view) {
  let density = pixelDensity();
  let photo = document.createElement("canvas");
  photo.width = view.width * density;
  photo.height = view.height * density;
  photo
    .getContext("2d")
    .drawImage(
      drawingContext.canvas,
      view.x * density,
      view.y * density,
      photo.width,
      photo.height,
      0,
      0,
      photo.width,
      photo.height,
    );

  let link = document.createElement("a");
  let timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  link.download = `photo-${timestamp}.png`;
  link.href = photo.toDataURL("image/png");
  link.click();
}

function drawHand(points) {
  // 骨（関節同士をつなぐ線）
  stroke(255);
  strokeWeight(3);
  for (let finger of FINGERS) {
    for (let i = 0; i < finger.length - 1; i++) {
      let a = points[finger[i]];
      let b = points[finger[i + 1]];
      line(a.x, a.y, b.x, b.y);
    }
  }

  // 関節
  noStroke();
  fill(255);
  for (let point of points) {
    circle(point.x, point.y, JOINT_SIZE);
  }

  // 指先
  fill(255, 0, 80);
  for (let index of FINGERTIP_INDICES) {
    circle(points[index].x, points[index].y, DOT_SIZE);
  }
}

// 残り秒数と、経過を表す円を画面中央に描く
function drawCountdown(remaining, view) {
  let centerX = view.x + view.width / 2;
  let centerY = view.y + view.height / 2;
  let progress = 1 - remaining / PHOTO_COUNTDOWN;

  noStroke();
  fill(0, 120);
  circle(centerX, centerY, 180);

  noFill();
  stroke(255);
  strokeWeight(8);
  arc(centerX, centerY, 160, 160, -HALF_PI, -HALF_PI + TWO_PI * progress);

  // 数字の形そのものの上下左右の端を測って、見た目の中心を円の中心に合わせる
  let label = String(Math.ceil(remaining / 1000));
  let context = drawingContext;
  context.save(); // p5が覚えている塗り色とずれないよう、Canvasの状態を後で戻す
  context.font = "96px sans-serif";
  context.textAlign = "left";
  context.textBaseline = "alphabetic";
  let bounds = context.measureText(label);
  let glyphWidth = bounds.actualBoundingBoxLeft + bounds.actualBoundingBoxRight;
  let glyphHeight = bounds.actualBoundingBoxAscent + bounds.actualBoundingBoxDescent;
  context.fillStyle = "#fff";
  context.fillText(
    label,
    centerX - glyphWidth / 2 + bounds.actualBoundingBoxLeft,
    centerY + glyphHeight / 2 - bounds.actualBoundingBoxDescent,
  );
  context.restore();
}

// 撮影直後に白く光らせ、保存したことを表示する
function drawFlash(view) {
  let elapsed = millis() - flashStartTime;
  if (elapsed > 1500) return;

  noStroke();
  fill(255, map(elapsed, 0, 400, 255, 0, true));
  rect(view.x, view.y, view.width, view.height);

  let label = "保存しました";
  textSize(18);
  textAlign(CENTER, CENTER);
  let labelWidth = textWidth(label) + 32;
  fill(0, 160);
  rect(
    view.x + (view.width - labelWidth) / 2,
    view.y + view.height - 64,
    labelWidth,
    40,
    20,
  );
  fill(255);
  text(label, view.x + view.width / 2, view.y + view.height - 44);
}

// 映像を画面に収まる最大サイズで中央に表示する
function getVideoView() {
  let viewScale = min(width / VIDEO_WIDTH, height / VIDEO_HEIGHT);
  let displayWidth = VIDEO_WIDTH * viewScale;
  let displayHeight = VIDEO_HEIGHT * viewScale;

  return {
    x: (width - displayWidth) / 2,
    y: (height - displayHeight) / 2,
    width: displayWidth,
    height: displayHeight,
    scale: viewScale,
  };
}

// 鏡のように左右反転して表示する
function drawMirroredVideo(view) {
  push();
  translate(view.x + view.width, view.y);
  scale(-1, 1);
  image(video, 0, 0, view.width, view.height);
  pop();
}

function videoPointToScreen(point, view) {
  return {
    x: view.x + (VIDEO_WIDTH - point.x) * view.scale,
    y: view.y + point.y * view.scale,
  };
}

function windowResized() {
  resizeCanvas(windowWidth, windowHeight);
}
