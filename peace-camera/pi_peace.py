"""Pi Zero 2 W でピースを認識して、つないだディスプレイに映す（GUI なし）。

カメラ映像の表示は rpicam-vid の DRM プレビュー（GPU）に任せ、同じ rpicam-vid の stdout から
フレームを受け取って認識だけする。Python は画面右の余白にカウントダウンと状態を描くだけ。
MediaPipe 1.x は Pi Zero 2 W の CPU（AES 命令なし）で落ちるので、Python 3.12 + mediapipe 0.10.18 で動かす。
"""

import contextlib
import fcntl
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from math import acos, degrees, sqrt
from pathlib import Path

import cv2
import numpy as np

CAPTURE_W, CAPTURE_H = 640, 480  # プレビューにも使うので、小さすぎるとぼやける
WIDTH, HEIGHT = 320, 240  # 認識に使う解像度。小さいほど速い
IDLE_INTERVAL = 1.0  # 手が写っていないときの推論間隔（秒）
PHOTO_WIDTH, PHOTO_HEIGHT = 2592, 1944
COUNTDOWN_SECONDS = 3
MODEL = Path(__file__).parent / "models" / "hand_landmarker.task"
# 撮った写真は保存せず、出席 Bot（lab-attendance/bot.py）の POST /photo に送って Slack に投稿してもらう
PHOTO_URL = os.environ.get("PHOTO_URL", "http://127.0.0.1:8080/photo")
SCREEN_W, SCREEN_H = (int(v) for v in Path("/sys/class/graphics/fb0/virtual_size").read_text().split(","))
PREVIEW_W = SCREEN_H * CAPTURE_W // CAPTURE_H  # 左にプレビュー、右の余白に状態を描く
ACCENT = (81, 0, 255)  # #ff0051（BGR）
# 靴箱判定（~/shoebox）とカメラを譲り合うためのファイル。shoebox.py と同じパスにそろえる。
# カメラを使う間は CAMERA_LOCK を flock で握り、CAMERA_REQUEST が置かれたら映像を止めて譲る
CAMERA_LOCK = "/tmp/pi-camera.lock"
CAMERA_REQUEST = "/tmp/pi-camera.request"
REQUEST_TIMEOUT = 30  # 依頼がこれより長く残っていたら、依頼した側が落ちたとみなして消す（秒）


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
        self.control_lock = threading.Lock()
        self.latest = None
        self.process = None
        self.lock = open(CAMERA_LOCK, "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX)  # 靴箱判定が撮影中なら、終わるまで待つ
        self._start()

    def _start(self):
        self.process = subprocess.Popen(
            ["rpicam-vid", "-t", "0", "--width", str(CAPTURE_W), "--height", str(CAPTURE_H),
             "--framerate", "15", "--codec", "yuv420",
             "--hflip",  # 鏡のように左右反転
             "-p", f"0,0,{PREVIEW_W},{SCREEN_H}", "-o", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()

    def _read(self, process):
        # 変換は認識するフレームだけにしたいので、ここでは生のバイト列を持つだけ
        size = CAPTURE_W * CAPTURE_H * 3 // 2
        while process is self.process \
                and (data := process.stdout.read(size)) and len(data) == size:
            self.latest = data

    def read(self):
        """認識用に縮小した RGB を返す。"""
        if self.latest is None:
            return None
        yuv = np.frombuffer(self.latest, np.uint8).reshape(CAPTURE_H * 3 // 2, CAPTURE_W)
        rgb = cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB_I420)
        return cv2.resize(rgb, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)

    def _stop(self):
        process = self.process
        self.process = None
        self.latest = None  # 止めている間に古い1枚で認識し続けないように
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def capture_photo(self):
        """ライブ映像を一時停止し、最高解像度の JPEG を撮って再開する。ファイルには書かずにバイト列で返す。"""
        with self.control_lock:
            self._stop()
            try:
                result = subprocess.run(
                    ["rpicam-still", "-n", "-t", "500", "--zsl", "--no-raw",
                     "--width", str(PHOTO_WIDTH), "--height", str(PHOTO_HEIGHT),
                     "--encoding", "jpg", "-o", "-"],
                    capture_output=True, timeout=15,
                )
                if result.returncode != 0 or not result.stdout:
                    raise RuntimeError(result.stderr.decode(errors="replace").strip() or "rpicam-still failed")
            finally:
                self._start()
        return result.stdout

    @staticmethod
    def requested():
        return os.path.exists(CAMERA_REQUEST)

    def hand_over(self, stop_event):
        """映像を止めてカメラを譲り、相手が撮り終えてロックを外したら再開する。"""
        with self.control_lock:
            self._stop()
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            give_up_at = time.monotonic() + REQUEST_TIMEOUT
            # 相手はロックを握ってから依頼を消すので、依頼が消えるまではロックを取りに行かない
            while not stop_event.wait(0.1):
                if self.requested():
                    if time.monotonic() < give_up_at:
                        continue
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(CAMERA_REQUEST)
                try:
                    fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                self._start()
                return

    def stop(self):
        with self.control_lock:
            self._stop()


class Recognition:
    """重い手の認識を別スレッドで実行し、最新結果だけを持つ。手がないときは間引く。"""

    def __init__(self, camera, landmarker, mp):
        self.camera = camera
        self.landmarker = landmarker
        self.mp = mp
        self.hand_count = 0
        self.countdown_started = None
        self.armed = True
        self.paused = False  # 靴箱判定にカメラを譲っている間
        self.notice = None
        self.notice_until = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        runs, busy, report_at = 0, 0.0, time.monotonic() + 10
        while not self.stop_event.is_set():
            # 靴箱判定が撮りたがっていたら譲る。カウントダウン中は撮り終えてから譲る
            if self.countdown_started is None and self.camera.requested():
                self.paused = True
                self.notice, self.notice_until = "PAUSE", float("inf")
                self.camera.hand_over(self.stop_event)
                self.notice_until = 0
                self.paused = False
                continue
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
                self.notice = "SHOOT"
                self.notice_until = float("inf")
                try:
                    photo = self.camera.capture_photo()
                    print(f"captured: {len(photo)} bytes", flush=True)
                    self.notice = "SENDING"  # 送り終えるまで出しておく（_send が結果に書き換える）
                    # Bot は Slack に投稿し終えてから応答するので、待つ間も認識を止めないよう別スレッドで送る
                    threading.Thread(target=self._send, args=(photo,), daemon=True).start()
                except Exception as error:
                    print(f"capture failed: {error}", file=sys.stderr, flush=True)
                    self.notice = "ERROR"
                    self.notice_until = time.monotonic() + 2
            else:
                self.countdown_started = None
                self.armed = True
                if self.hand_count == 0:
                    # 誰も写っていないときは推論を間引いて CPU を空ける
                    self.stop_event.wait(max(0, IDLE_INTERVAL - (time.monotonic() - started)))

    def _send(self, photo):
        request = urllib.request.Request(PHOTO_URL, data=photo, headers={"Content-Type": "image/jpeg"})
        try:
            with urllib.request.urlopen(request, timeout=60):
                pass
            print("posted", flush=True)
            notice = "POSTED"
        except urllib.error.HTTPError as error:  # Bot が返したエラー（not_in_channel など）をそのまま出す
            print(f"post failed: {error.code} {error.read().decode(errors='replace')}", file=sys.stderr, flush=True)
            notice = "ERROR"
        except OSError as error:  # Bot が動いていないなど
            print(f"post failed: {error}", file=sys.stderr, flush=True)
            notice = "ERROR"
        # 送っている間に靴箱判定へカメラを譲っていたら、PAUSE の表示を上書きしない（結果はログに出ている）
        if not self.paused:
            self.notice, self.notice_until = notice, time.monotonic() + 2

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


def write(fb, screen, pixels):
    cv2.cvtColor(screen, cv2.COLOR_BGR2BGR565, dst=pixels)
    fb.seek(0)
    fb.write(pixels)  # fb0 は 16bit（RGB565）


def put_centered_text(screen, text, center_x, scale, color, thickness):
    size, _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    position = (center_x - size[0] // 2, (SCREEN_H + size[1]) // 2)
    cv2.putText(screen, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness)


def draw_panel(screen, notice, countdown, hand_count):
    """プレビューの右の余白だけに描く（プレビューの下は GPU の映像で隠れる）。"""
    screen.fill(0)
    center_x = (PREVIEW_W + SCREEN_W) // 2
    if countdown is not None:
        put_centered_text(screen, str(countdown), center_x, 7, (255, 255, 255), 14)
    elif notice:
        put_centered_text(screen, notice, center_x, 1.4, ACCENT, 4)
    else:
        put_centered_text(screen, f"HANDS {hand_count}", center_x, 1, (160, 160, 160), 2)


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # 止められたら finally でカメラも閉じる
    fb = open("/dev/fb0", "r+b")
    screen = np.zeros((SCREEN_H, SCREEN_W, 3), np.uint8)
    pixels = np.empty((SCREEN_H, SCREEN_W, 2), np.uint8)

    cv2.putText(screen, "LOADING...", (40, SCREEN_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 4)
    write(fb, screen, pixels)

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
    shown = None
    try:
        while True:
            # 映像は GPU が描くので、ここは右の余白の表示が変わったときだけ書き直す
            state = (*recognition.status(), recognition.hand_count)
            if state != shown:
                draw_panel(screen, *state)
                write(fb, screen, pixels)
                shown = state
            time.sleep(0.1)
    finally:
        recognition.stop()
        camera.stop()
        landmarker.close()
        fb.close()


if __name__ == "__main__":
    main()
