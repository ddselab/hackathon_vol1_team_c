"""Pi につないだディスプレイに、ドットでできた目を出してまばたきさせる（カメラは使わない）。

目の開き具合ごとの画面を起動時に RGB565 で作っておき、まばたきの瞬間だけ /dev/fb0 に書く。
それ以外は sleep しているだけなので、CPU はほとんど使わない。
"""

import random
import signal
import sys
import time
from pathlib import Path

import numpy as np

SCREEN_W, SCREEN_H = (int(v) for v in Path("/sys/class/graphics/fb0/virtual_size").read_text().split(","))
DOT = 28  # ドット1個ぶんのマス（px）
GAP = 6  # ドットのあいだの隙間（px）
EYE_COLS, EYE_ROWS = 5, 9  # 目1つのドットの数（横 × 縦）
EYE_SPACING = 9  # 左右の目の中心の間隔（ドット）
COLOR = (255, 255, 255)  # 目の色（RGB）
OPENNESS = [1.0, 0.6, 0.3, 0.0]  # まばたきのコマ（開いている → 閉じている）
FRAME_SECONDS = 0.04  # まばたきの1コマの長さ
BLINK_INTERVAL = (2.0, 6.0)  # まばたきの間隔（秒、この範囲でランダム）
DOUBLE_BLINK = 0.2  # 2回続けてパチパチする確率


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def eye_dots(openness):
    """目1つぶんの、点灯するドットの (列, 行) を返す。閉じると真ん中の1行だけ残る。"""
    rows = max(1, round(EYE_ROWS * openness))
    top = (EYE_ROWS - rows) // 2
    dots = []
    for row in range(top, top + rows):
        for col in range(EYE_COLS):
            # 開いているときは四隅を欠けさせて丸く見せる
            corner = rows > 2 and row in (top, top + rows - 1) and col in (0, EYE_COLS - 1)
            if not corner:
                dots.append((col, row))
    return dots


def render(openness):
    """開き具合 openness の画面を、fb0 にそのまま書ける RGB565 のバイト列にする。"""
    screen = np.zeros((SCREEN_H, SCREEN_W), np.uint16)
    color = rgb565(*COLOR)
    eye_h = EYE_ROWS * DOT
    top = (SCREEN_H - eye_h) // 2
    for center in (-EYE_SPACING / 2, EYE_SPACING / 2):
        left = round(SCREEN_W / 2 + (center - EYE_COLS / 2) * DOT)
        for col, row in eye_dots(openness):
            x, y = left + col * DOT + GAP // 2, top + row * DOT + GAP // 2
            screen[y:y + DOT - GAP, x:x + DOT - GAP] = color
    return screen.astype("<u2").tobytes()


def show(fb, frame):
    fb.seek(0)
    fb.write(frame)
    fb.flush()


def blink(fb, frames):
    # 閉じて、また開く（開いた状態は最後に描く）
    for frame in frames[1:] + frames[-2::-1]:
        show(fb, frame)
        time.sleep(FRAME_SECONDS)


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # 止められたら finally で画面を消す
    frames = [render(openness) for openness in OPENNESS]
    with open("/dev/fb0", "r+b") as fb:
        try:
            show(fb, frames[0])
            while True:
                time.sleep(random.uniform(*BLINK_INTERVAL))
                blink(fb, frames)
                if random.random() < DOUBLE_BLINK:
                    time.sleep(0.15)
                    blink(fb, frames)
        finally:
            show(fb, bytes(SCREEN_W * SCREEN_H * 2))


if __name__ == "__main__":
    main()
