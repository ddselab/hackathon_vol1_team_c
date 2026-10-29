"""動作確認用の合成画像を作る (少し斜めから撮った靴箱を想定)。

  sample_empty.png   … 空の靴箱
  sample_shoes.png   … A1, B3, D8 にくつが入った靴箱 (少し明るさが違う)
  sample_shadow.png  … 同じくつ + 片側に影がかかった状態
  sample_dark.png    … 部屋の電気が消えている状態 (判定不可になるのが正しい)
  sample_config.json … 各マスの四隅 (config.json にコピーすればマス設定不要)

作り方の流れ:
  1. front_view: 靴箱を真正面から見た絵を NumPy 配列に直接描く
  2. to_photo:   射影変換で「斜めから撮った写真」っぽく歪ませる
                 (shoebox.cell_patch の正面化とちょうど逆の変換)
  3. relight:    明るさやノイズを変えて、照明条件の違いを再現する
"""
import json
import os

import cv2
import numpy as np

from shoebox import BASE_DIR, CELL_NAMES, COLS, ROWS, parse_cell, save_image

SHOES = ["A1", "B3", "D8"]
CW, CH = 100, 100  # 正面図での1マスのサイズ
CORNERS = [[120, 80], [1050, 110], [1010, 560], [150, 590]]  # 写真上の靴箱の四隅
SIZE = (1200, 680)


def front_view(shoes):
    w, h = COLS * CW, len(ROWS) * CH
    # 高さ h, 幅 w, 3チャンネル (BGR) の画像を1色で塗りつぶして作る。
    # (170, 200, 220) は BGR なので、赤が強めのベージュ = 木の板っぽい色
    img = np.full((h, w, 3), (170, 200, 220), np.uint8)  # 木の板っぽい色
    rng = np.random.default_rng(0)
    # 正規分布の乱数を足して木目風のザラつきを付ける。
    # np.clip で 0〜255 からはみ出した値を切り詰めてから uint8 に戻す
    img = np.clip(img + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)
    for r in range(len(ROWS)):
        for c in range(COLS):
            x0, y0 = c * CW, r * CH
            cv2.rectangle(img, (x0 + 6, y0 + 6), (x0 + CW - 6, y0 + CH - 6), (120, 150, 175), -1)
    for i in range(COLS + 1):
        cv2.line(img, (i * CW, 0), (i * CW, h), (60, 80, 100), 8)
    for i in range(len(ROWS) + 1):
        cv2.line(img, (0, i * CH), (w, i * CH), (60, 80, 100), 8)
    for s in shoes:
        r, c = parse_cell(s)
        cx, cy = c * CW + CW // 2, r * CH + CH // 2 + 10
        cv2.ellipse(img, (cx, cy), (36, 22), 0, 0, 360, (40, 40, 160), -1)  # くつ本体
        cv2.ellipse(img, (cx - 8, cy - 6), (14, 8), 0, 0, 360, (230, 230, 230), -1)  # 履き口
    return img


def homography():
    # 正面図の四隅 (src) → 写真上の靴箱の四隅 (CORNERS) へ移す射影変換の行列
    w, h = COLS * CW, len(ROWS) * CH
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    return cv2.getPerspectiveTransform(src, np.float32(CORNERS))


def to_photo(front):
    return cv2.warpPerspective(front, homography(), SIZE, borderValue=(90, 90, 90))


def cell_quads():
    """各マスの内側 (枠の線を除く) の四隅を写真上の座標で返す。"""
    # 正面図でのマスの四隅の座標を、同じ射影変換で写真上の座標に変換する。
    # (画像全体ではなく点だけを変換するときは cv2.perspectiveTransform を使う)
    m = homography()
    out = {}
    for name in CELL_NAMES:
        r, c = parse_cell(name)
        x0, y0, x1, y1 = c * CW + 6, r * CH + 6, (c + 1) * CW - 6, (r + 1) * CH - 6
        pts = np.float32([[[x0, y0]], [[x1, y0]], [[x1, y1]], [[x0, y1]]])
        out[name] = [[round(float(x), 1), round(float(y), 1)]
                     for x, y in cv2.perspectiveTransform(pts, m)[:, 0]]
    return out


def relight(img, factor, noise, rng):
    # 全画素を factor 倍して明るさを変え、noise の強さでノイズを足す。
    # 暗い場所ではカメラが感度を上げるためノイズが増える、という現象の再現にも使う
    f = img.astype(np.float32) * factor + rng.normal(0, noise, img.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    rng = np.random.default_rng(1)
    shoes = to_photo(front_view(SHOES))
    save_image(os.path.join(BASE_DIR, "sample_empty.png"), relight(to_photo(front_view([])), 1.0, 2, rng))
    # 照明が少し違う状態
    save_image(os.path.join(BASE_DIR, "sample_shoes.png"), relight(shoes, 1.1, 2, rng))
    # 右側ほど暗くなる影
    # 左端 1.0 倍 → 右端 0.4 倍 と横方向に変化する倍率を作る。
    # [None, :, None] で shape を (1, 幅, 1) にすると、NumPy のブロードキャストにより
    # (高さ, 幅, 3) の画像の全行・全色にまとめて掛けられる
    shade = np.linspace(1.0, 0.4, SIZE[0], dtype=np.float32)[None, :, None]
    save_image(os.path.join(BASE_DIR, "sample_shadow.png"), relight(shoes, shade, 3, rng))
    # 電気が消えた部屋 (カメラの感度が上がってノイズが多い)
    save_image(os.path.join(BASE_DIR, "sample_dark.png"), relight(shoes, 0.12, 6, rng))
    with open(os.path.join(BASE_DIR, "sample_config.json"), "w", encoding="utf-8") as f:
        json.dump({"cells": cell_quads()}, f, ensure_ascii=False, indent=2)
    print("sample_empty / sample_shoes / sample_shadow / sample_dark (.png) と sample_config.json を作成しました")
    print(f"くつの位置: {', '.join(SHOES)}")
