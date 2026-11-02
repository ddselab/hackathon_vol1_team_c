"""Pi Zero 2 W でピースを認識して、つないだディスプレイに映す（GUI なし）。

カメラは rpicam-vid を 1 本だけ開きっぱなしにして、ピースの認識・撮影・靴箱判定のすべてに同じ映像を使う。
カメラを開き直さないので、撮影中も靴箱判定中も映像は止まらない。
映像の表示は rpicam-vid の DRM プレビュー（GPU）に任せ、Python は映像のまわりの余白に
状態（上）・日時（下）・カウントダウン（左）を描くだけ。
MediaPipe 1.x は Pi Zero 2 W の CPU（AES 命令なし）で落ちるので、Python 3.12 + mediapipe 0.10.18 で動かす。
"""

import fcntl
import functools
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from math import acos, degrees, sqrt
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 撮影・靴箱判定・プレビュー・認識のすべてに使う映像。
# ディスプレイ（1024×600）は画素が少し横長なので、1280×748 で見た目がちょうど 16:9 になる。
# rpicam-vid は幅が 64 の倍数でないと 1 行ごとに詰め物を入れて出力するので、幅は 1280 にする
CAPTURE_W, CAPTURE_H = 1280, 748
FRAMERATE = 15
# 横長を頼むとカメラは画角の狭い 1920×1080 のモードを選ぶので、靴箱判定と同じ 4:3 全体のモードに固定し、
# そこから上下を切り取る。靴箱は画面の下端まであるので、上（天井側）だけ削る
SENSOR_MODE, SENSOR_W, SENSOR_H = "1296:972:10:P", 1296, 972
CROP_H = CAPTURE_H * SENSOR_W / (CAPTURE_W * SENSOR_H)  # 4:3 全体のうち使う高さの割合
CROP_Y = 1 - CROP_H
WIDTH, HEIGHT = 320, 188  # 認識に使う解像度。小さいほど速い（YUV420 なので偶数）
IDLE_INTERVAL = 1.0  # 手が写っていないときの推論間隔（秒）
COUNTDOWN_SECONDS = 3
MODEL = Path(__file__).parent / "models" / "hand_landmarker.task"
# 撮った写真は保存せず、出席 Bot（lab-attendance/bot.py）の POST /photo に送って Slack に投稿してもらう
PHOTO_URL = os.environ.get("PHOTO_URL", "http://127.0.0.1:8080/photo")
PHOTO_QUALITY = 90

# 靴箱判定（shoebox-judge）。マス設定と基準画像は SHOEBOX_DIR の config.json / empty.png をそのまま読む。
# 動いている間は CAMERA_LOCK を握り続けるので、shoebox-judge の watch は自分では撮らずに休む
SHOEBOX_DIR = Path(os.environ.get("SHOEBOX_DIR", Path.home() / "shoebox"))
SHOEBOX_URL = os.environ.get("SHOEBOX_URL", "http://127.0.0.1:8080/events")  # 空にすると送らない
SHOEBOX_INTERVAL = 60  # 判定の間隔（秒）
SHOEBOX_WAIT = 30  # 手が写っている（人が前にいる）間は判定を待つ。これより長く続いたらその回は飛ばす（秒）
CAMERA_LOCK = "/tmp/pi-camera.lock"

SCREEN_W, SCREEN_H = (int(v) for v in Path("/sys/class/graphics/fb0/virtual_size").read_text().split(","))
# 映像は真ん中に置き、上下左右の余白に文字を描く
PREVIEW_H = SCREEN_H * 5 // 6
PREVIEW_W = PREVIEW_H * CAPTURE_W // CAPTURE_H
PREVIEW_X, PREVIEW_Y = (SCREEN_W - PREVIEW_W) // 2, (SCREEN_H - PREVIEW_H) // 2
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ACCENT = (255, 0, 81)  # #ff0051（RGB）
WHITE = (255, 255, 255)
GRAY = (150, 150, 150)


@functools.cache  # 表示は毎秒描き直すので、同じ大きさのフォントは読み直さない
def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:  # DejaVu が入っていない環境
        return ImageFont.load_default(size)


def joint_angle(hand, mcp, pip, tip):
    """指の付け根・第2関節・指先が作る角度。手の表裏に左右されない。"""
    a = tuple(getattr(hand[mcp], axis) - getattr(hand[pip], axis) for axis in ("x", "y", "z"))
    b = tuple(getattr(hand[tip], axis) - getattr(hand[pip], axis) for axis in ("x", "y", "z"))
    length_a = sqrt(sum(value * value for value in a))
    length_b = sqrt(sum(value * value for value in b))
    if not length_a or not length_b:
        return 0
    cosine = sum(x * y for x, y in zip(a, b)) / (length_a * length_b)
    return degrees(acos(max(-1, min(1, cosine))))


def extended(hand, mcp, pip, tip):
    return joint_angle(hand, mcp, pip, tip) >= 150


def is_peace(hand):
    # 人差し指と中指が伸び、薬指と小指が曲がっている
    return extended(hand, 5, 6, 8) and extended(hand, 9, 10, 12) \
        and not extended(hand, 13, 14, 16) and not extended(hand, 17, 18, 20)


class Camera:
    """rpicam-vid でプレビューを出しつつ、YUV420 の stdout を別スレッドで読み続けて最新の1枚だけ持つ。"""

    def __init__(self):
        self.lock = open(CAMERA_LOCK, "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX)  # shoebox-judge の watch が撮影中なら、終わるまで待つ
        self.latest = None
        self.process = subprocess.Popen(
            ["rpicam-vid", "-t", "0", "--width", str(CAPTURE_W), "--height", str(CAPTURE_H),
             "--framerate", str(FRAMERATE), "--codec", "yuv420",
             "--mode", SENSOR_MODE, "--roi", f"0,{CROP_Y:.6f},1,{CROP_H:.6f}",
             "--hflip",  # 鏡のように左右反転
             "-p", f"{PREVIEW_X},{PREVIEW_Y},{PREVIEW_W},{PREVIEW_H}", "-o", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        # 変換は使うフレームだけにしたいので、ここでは生のバイト列を持つだけ
        size = CAPTURE_W * CAPTURE_H * 3 // 2
        while (data := self.process.stdout.read(size)) and len(data) == size:
            self.latest = data

    def snapshot(self):
        """今の1枚（YUV420 のバイト列）。撮影と靴箱判定はこれを to_bgr で変換して使う。"""
        return self.latest

    def read(self):
        """認識用に縮小した RGB を返す（プレビューと同じく左右反転したまま）。"""
        data = self.latest
        if data is None:
            return None
        # 全体を RGB にしてから縮めると重いので、Y・U・V の面ごとに縮めてから変換する
        yuv = np.frombuffer(data, np.uint8)
        y_size, uv_size = CAPTURE_W * CAPTURE_H, CAPTURE_W * CAPTURE_H // 4
        planes = (
            (yuv[:y_size].reshape(CAPTURE_H, CAPTURE_W), (WIDTH, HEIGHT)),
            (yuv[y_size:y_size + uv_size].reshape(CAPTURE_H // 2, CAPTURE_W // 2), (WIDTH // 2, HEIGHT // 2)),
            (yuv[y_size + uv_size:].reshape(CAPTURE_H // 2, CAPTURE_W // 2), (WIDTH // 2, HEIGHT // 2)),
        )
        small = np.concatenate([cv2.resize(plane, size, interpolation=cv2.INTER_AREA).ravel()
                                for plane, size in planes])
        return cv2.cvtColor(small.reshape(HEIGHT * 3 // 2, WIDTH), cv2.COLOR_YUV2RGB_I420)

    @staticmethod
    def to_bgr(data):
        """snapshot の1枚を、撮影サイズの BGR にする。プレビュー用の左右反転は戻す。"""
        yuv = np.frombuffer(data, np.uint8).reshape(CAPTURE_H * 3 // 2, CAPTURE_W)
        return cv2.flip(cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420), 1)

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def make_photo(data):
    """撮った1枚を JPEG にする。"""
    _, jpeg = cv2.imencode(".jpg", Camera.to_bgr(data), [cv2.IMWRITE_JPEG_QUALITY, PHOTO_QUALITY])
    return jpeg.tobytes()


class Recognition:
    """重い手の認識を別スレッドで実行し、最新結果だけを持つ。手がないときは間引く。"""

    def __init__(self, camera, landmarker, mp):
        self.camera = camera
        self.landmarker = landmarker
        self.mp = mp
        self.hand_count = 0
        self.countdown_started = None
        self.armed = True
        self.notice = None
        self.notice_until = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        runs, busy, report_at = 0, 0.0, time.monotonic() + 10
        while not self.stop_event.is_set():
            started = time.monotonic()
            frame = self.camera.read()
            if frame is None:
                time.sleep(0.01)
                continue
            image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=frame)
            result = self.landmarker.detect_for_video(image, int(started * 1000))
            self.hand_count = len(result.hand_landmarks)
            peace_count = sum(is_peace(hand) for hand in result.hand_landmarks)
            now = time.monotonic()

            runs += 1
            busy += now - started
            if now >= report_at:
                print(f"recognition: {runs / 10:.1f}/s, {1000 * busy / max(runs, 1):.0f}ms each", flush=True)
                runs, busy, report_at = 0, 0.0, now + 10

            if peace_count >= 2:
                if not self.armed:
                    continue
                if self.countdown_started is None:
                    self.countdown_started = now
                if now - self.countdown_started < COUNTDOWN_SECONDS:
                    continue

                self.armed = False
                self.countdown_started = None
                # カメラは止めず、今の1枚をそのまま使う。
                # Bot は Slack に投稿し終えてから応答するので、待つ間も認識を止めないよう別スレッドで送る
                data = self.camera.snapshot()
                print("captured", flush=True)
                self.notice, self.notice_until = "SENDING", float("inf")  # _send が結果に書き換える
                threading.Thread(target=self._send, args=(data,), daemon=True).start()
            else:
                self.countdown_started = None
                self.armed = True
                if self.hand_count == 0:
                    # 誰も写っていないときは推論を間引いて CPU を空ける
                    self.stop_event.wait(max(0, IDLE_INTERVAL - (time.monotonic() - started)))

    def _send(self, data):
        try:
            request = urllib.request.Request(
                PHOTO_URL, data=make_photo(data), headers={"Content-Type": "image/jpeg"})
            with urllib.request.urlopen(request, timeout=60):
                pass
            print("posted", flush=True)
            self.notice = "POSTED"
        except urllib.error.HTTPError as error:  # Bot が返したエラー（not_in_channel など）をそのまま出す
            print(f"post failed: {error.code} {error.read().decode(errors='replace')}", file=sys.stderr, flush=True)
            self.notice = "ERROR"
        except Exception as error:  # Bot が動いていない、JPEG にできないなど
            print(f"post failed: {error!r}", file=sys.stderr, flush=True)
            self.notice = "ERROR"
        self.notice_until = time.monotonic() + 2

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=20)

    def status(self):
        if time.monotonic() < self.notice_until:
            return self.notice, None
        if self.armed and self.countdown_started is not None:
            elapsed = time.monotonic() - self.countdown_started
            return None, max(1, COUNTDOWN_SECONDS - int(elapsed))
        return None, None


def fit_to_frame(detector):
    """マス設定と基準画像は shoebox-judge が rpicam-still（4:3 全体、1296×972）で作っているので、
    映像と同じように上を切り取って縮める。ファイルは書き換えない。"""
    if detector.empty is None:
        return
    height, width = detector.empty.shape[:2]
    if (width, height) == (CAPTURE_W, CAPTURE_H):
        return
    top = CROP_Y * height
    sx, sy = CAPTURE_W / width, CAPTURE_H / (CROP_H * height)
    detector.config["cells"] = {
        name: [[x * sx, (y - top) * sy] for x, y in quad] for name, quad in detector.cells.items()
    }
    detector.empty = cv2.resize(detector.empty[round(top):], (CAPTURE_W, CAPTURE_H), interpolation=cv2.INTER_AREA)


class Shoebox:
    """1分ごとに映像の今の1枚で靴箱を判定し、出席 Bot に送る（shoebox-judge の watch --post の代わり）。"""

    def __init__(self, camera, recognition):
        self.camera = camera
        self.recognition = recognition
        self.stop_event = threading.Event()
        try:
            sys.path.insert(0, str(SHOEBOX_DIR))
            import main as shoebox_main  # shoebox-judge/main.py（post_states を使う）
            from shoebox import ShoeBoxDetector
        except ImportError as error:
            print(f"shoebox: {SHOEBOX_DIR} を読み込めないので、靴箱判定はしません: {error}", file=sys.stderr, flush=True)
            self.thread = None
            return
        self.post_states, self.detector = shoebox_main.post_states, ShoeBoxDetector
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        next_at = time.monotonic()
        while not self.stop_event.wait(max(0, next_at - time.monotonic())):
            # 判定にかかった時間を差し引いて、間隔がずれていかないようにする
            next_at = max(next_at + SHOEBOX_INTERVAL, time.monotonic())
            # 手が写っている間は靴箱の前に人がいて正しく判定できないので、いなくなるまで待つ
            give_up_at = time.monotonic() + SHOEBOX_WAIT
            while self.recognition.hand_count and time.monotonic() < give_up_at:
                if self.stop_event.wait(1):
                    return
            if self.recognition.hand_count:
                print("shoebox: 手が写り続けているので、この回は飛ばします", flush=True)
                continue
            data = self.camera.snapshot()
            if data is None:
                continue
            self._judge(data, datetime.now())

    def _judge(self, data, now):
        try:
            # 毎回読み直すので、動かしたまま「空の状態を登録」やマス設定をし直しても反映される
            detector = self.detector()
            fit_to_frame(detector)
            results = detector.detect_all(Camera.to_bgr(data))
        except (ValueError, RuntimeError) as error:
            # 暗いなどは、その回だけ飛ばして次の回にまた試す
            print(f"shoebox: 判定できません: {error}", flush=True)
            return
        print(f"shoebox: くつあり {sum(has for has, _ in results.values())}/{len(results)} マス", flush=True)
        if not SHOEBOX_URL:
            return
        try:
            reply = self.post_states(SHOEBOX_URL, results, now)
            if reply.get("recorded"):
                print(f"shoebox: 出席 Bot に記録: {', '.join(map(str, reply['recorded']))} 番", flush=True)
        except urllib.error.HTTPError as error:
            print(f"shoebox: 出席 Bot に送れませんでした: {error.code} {error.read().decode(errors='replace')}",
                  flush=True)
        except (urllib.error.URLError, OSError, ValueError) as error:
            # Bot が止まっている等。次の回にまた全マスを送るので、そのまま続ける
            print(f"shoebox: 出席 Bot に送れませんでした: {error}", flush=True)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=20)


def write(fb, image):
    fb.seek(0)
    fb.write(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR565).tobytes())  # fb0 は 16bit（RGB565）


def draw_screen(notice, countdown, hand_count, clock):
    """映像のまわりの余白に描く（映像の下になる部分は GPU の映像で隠れる）。"""
    image = Image.new("RGB", (SCREEN_W, SCREEN_H))
    draw = ImageDraw.Draw(image)
    top, bottom = (SCREEN_W // 2, PREVIEW_Y // 2), (SCREEN_W // 2, SCREEN_H - PREVIEW_Y // 2)  # 上下の余白の真ん中
    small = font(PREVIEW_Y * 2 // 5)
    if countdown is not None:
        # 上下の余白には収まらないので、左の余白の上のほうに帯の幅いっぱいの大きさで出す
        draw.text((PREVIEW_X // 2, SCREEN_H // 4), str(countdown), font=font(PREVIEW_X * 7 // 5),
                  fill=WHITE, anchor="mm")
    elif notice:
        draw.text(top, notice, font=font(PREVIEW_Y * 3 // 5), fill=ACCENT, anchor="mm")
    else:
        draw.text(top, f"HANDS {hand_count}", font=small, fill=GRAY, anchor="mm")
    draw.text(bottom, clock, font=small, fill=GRAY, anchor="mm")
    return image


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # 止められたら finally でカメラも閉じる
    fb = open("/dev/fb0", "r+b")
    loading = Image.new("RGB", (SCREEN_W, SCREEN_H))
    ImageDraw.Draw(loading).text((SCREEN_W // 2, SCREEN_H // 2), "LOADING...", font=font(SCREEN_H // 10),
                                 fill=WHITE, anchor="mm")
    write(fb, loading)

    # mediapipe の読み込みに 30 秒近くかかるので、先に LOADING を出してから読む
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.4,
        min_hand_presence_confidence=0.4,
        min_tracking_confidence=0.4,
    ))
    camera = Camera()
    recognition = Recognition(camera, landmarker, mp)
    shoebox = Shoebox(camera, recognition)
    shown = None
    try:
        while True:
            # 映像は GPU が描くので、ここは余白の表示が変わったとき（少なくとも毎秒、時計）だけ書き直す
            state = (*recognition.status(), recognition.hand_count, time.strftime("%Y/%m/%d %H:%M:%S"))
            if state != shown:
                write(fb, draw_screen(*state))
                shown = state
            time.sleep(0.1)
    finally:
        shoebox.stop()
        recognition.stop()
        camera.stop()
        landmarker.close()
        fb.close()


if __name__ == "__main__":
    main()
