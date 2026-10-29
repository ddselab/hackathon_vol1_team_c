"""靴箱(4行x8列)の各マスにくつが入っているかを画像から判定するコア処理。

マスの名前は左上が A1、右上が A8、左下が D1、右下が D8。
各マスの範囲は「マスごとの四隅」を手動で指定する (config.json の "cells")。

------------------------------------------------------------------------
【画像認識が初めての人向け: このファイルの全体像】

このツールは機械学習 (AI に学習させる方式) ではなく、
「空のときの写真」と「今の写真」を比べて、変化があったかどうかで判定する
"差分比較" という古典的な方法を使っています。

判定の流れ:
  1. 準備: 靴箱が空の状態をカメラで撮り、基準画像 (empty.png) として保存しておく
  2. 準備: 写真の中で、各マス (A1〜D8) がどこにあるかを四隅の座標で登録しておく
  3. 判定: 今の写真から各マスの部分を切り出し、基準画像の同じ部分と比べる
  4. 模様や形が大きく違っていれば「くつあり」と判定する

使っているライブラリ:
  - OpenCV (cv2) … 画像の読み込み・変形・ぼかし等を行う定番ライブラリ
  - NumPy (np)   … 数値の配列を扱うライブラリ。OpenCV の画像は NumPy 配列そのもの

【画像はただの数値の配列】
  カラー画像は shape が (高さ, 幅, 3) の NumPy 配列で、
  img[y, x] がその位置の画素 (ピクセル) の色 [青, 緑, 赤] を表します。
  各値は 0(暗い)〜255(明るい) の整数 (dtype=uint8)。
  ※ OpenCV は RGB ではなく「BGR」の順で色を持つ点に注意 (歴史的な理由)。
  ※ 座標は左上が原点 (0, 0) で、x は右向き、y は「下向き」に増える。
  グレースケール (白黒) 画像は shape が (高さ, 幅) の2次元配列で、各値が明るさ。
------------------------------------------------------------------------
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import unicodedata

import cv2
import numpy as np

ROWS = "ABCD"
COLS = 8
CELL_NAMES = [f"{r}{c}" for r in ROWS for c in range(1, COLS + 1)]
CORNER_LABELS = ["左上", "右上", "右下", "左下"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
EMPTY_PATH = os.path.join(BASE_DIR, "empty.png")

# カメラは靴箱を斜めから撮ることが多いので、写真の中のマスは台形などに歪んでいる。
# そこで各マスを「真正面から見た正方形」に変形してから比べる (cell_patch 参照)。
# その正方形の一辺の画素数が PATCH。大きくすると細かく比べられるが処理は遅くなる。
PATCH = 80  # 各マスを正面化したときのサイズ (PATCH x PATCH px)
# マスの縁には靴箱の仕切り板が写り込みやすく、ちょっとした位置ずれで誤判定の
# 原因になる。そこで上下左右それぞれ 10% ずつ削った「内側」だけを比べる。
CELL_MARGIN = 0.10  # マスの枠を避けるため内側から除外する割合

# 構造比較で「同じ」とみなす余裕 (平坦なマス同士のノイズで誤判定しないため)
# ※ _structure_score の計算式で分母・分子に足す小さな定数。
#   模様がほとんどない (のっぺりした) マスだと、カメラのわずかなノイズでも
#   相関が大きくぶれてしまうので、それを抑える「クッション」の役割。
#   画像処理で有名な類似度指標 SSIM でも同じ考え方の定数が使われている。
STRUCT_C = 6.0**2

# Raspberry Pi のカメラモジュール (CSI 接続) を表す入力元。load_image("pi") で撮影する。
# Pi のカメラは OpenCV の VideoCapture では開けないので、rpicam-still を subprocess で呼ぶ
# (picamera2 は OS の Python 用で、venv からは使えないことがあるため使わない)。
PI_CAMERA = "pi"
# 撮影サイズ。OV5647 では 1296x972 が全画角のまま 2x2 で画素をまとめるモードで、
# 暗い所に強く、Pi Zero でも軽い。マスは PATCH 画素に縮めて比べるのでこれで十分。
# 変えたらマス設定と「空の状態を登録」をやり直すこと (座標は画素単位のため)
PI_CAMERA_SIZE = (1296, 972)
# 撮影前に自動露出・ホワイトバランスが落ち着くのを待つ時間 (ミリ秒)
PI_CAMERA_WAIT_MS = 2000

DEFAULT_CONFIG = {
    "cells": {},  # {"A1": [[x,y] 左上, 右上, 右下, 左下], ...} 元画像の座標
    "threshold": 0.6,  # スコアがこれ以上なら「くつあり」
    "min_ratio": 0.30,  # 基準画像(空の状態)と比べてこの割合より暗ければ判定しない
    "min_brightness": 15,  # 明るさ(0-255)がこれ未満なら判定しない
    "min_contrast": 1.5,  # 明るさのばらつきがこれ未満なら判定しない (真っ黒・レンズふさぎ等)
}


class TooDarkError(RuntimeError):
    """暗すぎる(または明るすぎる)ため判定できないときの例外。"""


def parse_cell(text: str) -> tuple[int, int]:
    """'A1' → (0, 0)。小文字・全角も受け付ける。不正なら ValueError。"""
    # NFKC 正規化で全角「Ａ１」→ 半角「A1」に変換している
    s = unicodedata.normalize("NFKC", text).strip().upper()
    m = re.fullmatch(r"([A-Z])\s*(\d+)", s)
    if not m or m.group(1) not in ROWS or not 1 <= int(m.group(2)) <= COLS:
        raise ValueError(
            f"'{text}' は無効なマス名です (A1〜{ROWS[-1]}{COLS} で指定してください)"
        )
    return ROWS.index(m.group(1)), int(m.group(2)) - 1


def cell_name(row: int, col: int) -> str:
    return f"{ROWS[row]}{col + 1}"


def normalize_cell(text: str) -> str:
    return cell_name(*parse_cell(text))


def default_source() -> str | int:
    """入力元を指定しなかったときの既定値。Pi (rpicam-still がある) なら Pi のカメラ、それ以外はカメラ 0。"""
    return PI_CAMERA if shutil.which("rpicam-still") else 0


def capture_pi_camera() -> np.ndarray:
    """rpicam-still で1枚撮り、BGR 画像で返す。

    カメラを使えるのは同時に1プロセスだけなので、撮るたびに起動して終わったら閉じる
    (1分に1回なら常に開きっぱなしにするより CPU も電力も少なくて済む)。
    """
    if not shutil.which("rpicam-still"):
        raise RuntimeError(
            "rpicam-still が見つかりません (Raspberry Pi OS で sudo apt install rpicam-apps)"
        )
    width, height = PI_CAMERA_SIZE
    try:
        # -o - で JPEG を標準出力に書かせ、ファイルを経由せずに受け取る
        result = subprocess.run(
            ["rpicam-still", "-n", "-t", str(PI_CAMERA_WAIT_MS),
             "--width", str(width), "--height", str(height),
             "--encoding", "jpg", "-q", "95", "-o", "-"],
            capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Pi のカメラの撮影が時間切れになりました") from None
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(
            "Pi のカメラで撮影できません (ほかのプログラムがカメラを使っていないか確認してください): "
            + (detail[-1] if detail else f"終了コード {result.returncode}")
        )
    img = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Pi のカメラの画像を読み込めません")
    return img


def load_image(source: str | int) -> np.ndarray:
    """画像ファイルのパス、カメラ番号(int)、または PI_CAMERA から BGR 画像を取得する。"""
    if source == PI_CAMERA:
        return capture_pi_camera()
    if isinstance(source, int):
        # cv2.VideoCapture(番号) で PC につながったカメラを開く。
        # 0 が1台目 (ノートPCの内蔵カメラなど)、1 が2台目…という番号になる。
        cap = cv2.VideoCapture(source)
        try:
            if not cap.isOpened():
                raise RuntimeError(f"カメラ {source} を開けません")
            frame = None
            # 起動直後は露出が安定しないので数フレーム読み捨てる
            # (カメラは周囲の明るさに合わせて自動で露出を調整するが、
            #  起動直後の数枚は暗すぎたり明るすぎたりすることが多い)
            # cap.read() は (成功したか, 画像) のタプルを返す。
            for _ in range(5):
                ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(f"カメラ {source} から画像を取得できません")
            return frame
        finally:
            # カメラは使い終わったら必ず解放する (しないと他のアプリから使えなくなる)
            cap.release()
    # cv2.imread は日本語パスを読めないので numpy 経由で読む
    # 1. np.fromfile でファイルの中身をそのままバイト列として読み込み
    # 2. cv2.imdecode でそのバイト列 (PNG や JPEG の圧縮データ) を画像に展開する
    data = np.fromfile(source, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"画像を読み込めません: {source}")
    return img


def save_image(path: str, img: np.ndarray) -> None:
    # load_image の逆。cv2.imencode で PNG 等の形式に圧縮してから
    # NumPy 経由でファイルに書き出す (これも日本語パス対策)
    ok, buf = cv2.imencode(os.path.splitext(path)[1] or ".png", img)
    if not ok:
        raise RuntimeError(f"画像を保存できません: {path}")
    buf.tofile(path)


def order_corners(points) -> list[list[float]]:
    """4点を 左上→右上→右下→左下 の順に並べ替える (クリック順の間違いを吸収)。"""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) != 4:
        raise ValueError("四隅の座標が4点必要です")
    # 4点の重心 (中心) を求める
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    # 中心から見た各点の角度 (atan2) で並べ替えると、4点が円周上の順に並ぶ。
    # 画像座標(yが下向き)では atan2 の昇順が時計回り
    pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    start = min(range(4), key=lambda i: pts[i][0] + pts[i][1])  # 左上 = x+y 最小
    pts = pts[start:] + pts[:start]
    # 「靴ひも公式 (shoelace formula)」で四角形の面積を計算し、
    # 誤クリックで極端に小さい四角形になっていないかを確認する
    area = 0.0
    for i in range(4):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % 4]
        area += x0 * y1 - x1 * y0
    if abs(area) / 2 < 25:
        raise ValueError("マスの範囲が小さすぎます。四隅を離してクリックしてください")
    return [[x, y] for x, y in pts]


def cell_patch(image: np.ndarray, quad) -> np.ndarray:
    """マスの四隅で囲まれた範囲を正面化し、枠を除いた内側をグレースケールで返す。"""
    # 【射影変換 (ホモグラフィ / パースペクティブ変換)】
    # 斜めから撮った写真では、本当は正方形のマスが台形や歪んだ四角形に写る。
    # 「写真上の四隅 (src)」が「正方形の四隅 (dst)」に移るような変換を求め、
    # それで画像を変形すると、マスを真正面から見たような画像が得られる。
    # (スマホの書類スキャンアプリが紙をまっすぐにするのと同じ仕組み)
    src = np.array(quad, dtype=np.float32)
    dst = np.array(
        [[0, 0], [PATCH - 1, 0], [PATCH - 1, PATCH - 1], [0, PATCH - 1]],
        dtype=np.float32,
    )
    # 4組の対応点から 3x3 の変換行列を計算する
    m = cv2.getPerspectiveTransform(src, dst)
    # その行列で画像全体を変形し、PATCH x PATCH の範囲だけを取り出す。
    # INTER_AREA は縮小時にきれいに平均をとる補間方法 (ギザギザやちらつきを抑える)
    patch = cv2.warpPerspective(image, m, (PATCH, PATCH), flags=cv2.INTER_AREA)
    # カラー (3チャンネル) ならグレースケール (明るさだけ) に変換する。
    # 色の情報を捨てる代わりに、照明の色味の違いに左右されにくくなり計算も軽くなる
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    # 縁 (枠の写り込み) を CELL_MARGIN の割合だけ削る。
    # NumPy のスライス [行の範囲, 列の範囲] = [y の範囲, x の範囲] で切り出せる
    k = int(PATCH * CELL_MARGIN)
    return patch[k : PATCH - k, k : PATCH - k]


def quad_center(quad) -> tuple[int, int]:
    return (
        int(round(sum(p[0] for p in quad) / 4)),
        int(round(sum(p[1] for p in quad) / 4)),
    )


def _structure_score(cur: np.ndarray, ref: np.ndarray) -> float:
    """模様・形の変化 (0=同じ, 1=まったく別物)。

    マスごとに平均を引いて相関をとるので、そのマスだけ暗い・明るい (影やスポット照明)
    といった違いには反応せず、くつが置かれて模様や形が変わったときだけ値が大きくなる。
    """
    # 【なぜ単純な引き算ではダメか】
    # 「今の画像 − 空の画像」の差を見るだけだと、影がかかったり電気の明るさが
    # 変わったりしただけでも差が出てしまい、くつがなくても「あり」と誤判定する。
    # そこで「明るさ」ではなく「模様のパターンが似ているか」を比べる。
    #
    # 1. ガウシアンぼかし: カメラのノイズ (細かいザラザラ) をならす。
    #    (5, 5) はぼかす範囲 (5x5 画素)。0 はぼかし具合を自動で決める指定。
    a = cv2.GaussianBlur(cur, (5, 5), 0)
    b = cv2.GaussianBlur(ref, (5, 5), 0)
    # 2. 各画像から平均の明るさを引く → 「全体が明るい/暗い」という情報を消し、
    #    「平均より明るい所・暗い所の並び方 (= 模様)」だけを残す
    a = a - a.mean()
    b = b - b.mean()
    # 3. 相関係数を計算する (統計で習う相関係数 r と同じもの)
    #    r = 共分散 / (標準偏差a × 標準偏差b)
    #    模様が同じなら r≈1、無関係なら r≈0 になる。
    #    分子・分母に STRUCT_C を足しているのは、のっぺりしたマス (std がほぼ 0)
    #    で 0 に近い数同士の割り算になり値が暴れるのを防ぐため。
    cov = float((a * b).mean())
    r = (cov + STRUCT_C) / (float(a.std()) * float(b.std()) + STRUCT_C)
    # 4. 「似ている度合い」を「変化の度合い」にするため 1 - r とし、0〜1 に収める
    return min(max(1.0 - r, 0.0), 1.0)


class ShoeBoxDetector:
    """設定 (各マスの位置・閾値) と基準画像を持ち、判定を行うクラス。"""

    def __init__(self, config_path: str = CONFIG_PATH, empty_path: str = EMPTY_PATH):
        self.config_path = config_path
        self.empty_path = empty_path
        self.config = dict(DEFAULT_CONFIG)
        self.config["cells"] = {}
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                loaded = json.load(f)
            # 旧形式 (靴箱全体の四隅 "corners" / "pixel_diff") は使わない
            loaded.pop("corners", None)
            loaded.pop("pixel_diff", None)
            self.config.update(loaded)
            self.config["cells"] = dict(self.config.get("cells") or {})
        # 基準画像 (靴箱が空のときの写真)。比較の「お手本」になる
        self.empty = None
        if os.path.exists(empty_path):
            self.empty = load_image(empty_path)

    # ---- 設定 ----
    @property
    def cells(self) -> dict[str, list[list[float]]]:
        return self.config["cells"]

    @property
    def threshold(self) -> float:
        return float(self.config["threshold"])

    @threshold.setter
    def threshold(self, value: float) -> None:
        self.config["threshold"] = float(value)

    def save_config(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def set_cell(self, name: str, corners) -> None:
        """マス1つ分の四隅を登録して保存する。"""
        self.cells[normalize_cell(name)] = order_corners(corners)
        self.save_config()

    def clear_cell(self, name: str) -> None:
        self.cells.pop(normalize_cell(name), None)
        self.save_config()

    def missing_cells(self) -> list[str]:
        return [n for n in CELL_NAMES if n not in self.cells]

    def set_empty(self, image: np.ndarray) -> None:
        """靴箱が空の状態の画像を基準として登録する (カメラ画像をそのまま保存)。"""
        # 暗い写真を基準にしてしまうと以降の判定がすべて狂うので、先にチェックする
        self.check_lighting(image, for_reference=True)
        self.empty = image.copy()
        save_image(self.empty_path, self.empty)

    def _require_ready(self, image: np.ndarray) -> None:
        if not self.cells:
            raise RuntimeError(
                "マスの範囲が未設定です。先に「マス設定」で各マスの四隅を指定してください"
            )
        if self.empty is None:
            raise RuntimeError("空の状態の基準画像が未登録です。先に登録してください")
        # マスの座標は画素単位で登録しているので、カメラの解像度が変わると
        # 座標がずれてしまう。そのため画像サイズ (shape の先頭2つ = 高さ, 幅) を確認
        if self.empty.shape[:2] != image.shape[:2]:
            raise RuntimeError(
                "基準画像とカメラ画像のサイズが違います "
                f"(基準 {self.empty.shape[1]}x{self.empty.shape[0]} / "
                f"現在 {image.shape[1]}x{image.shape[0]})。「空の状態を登録」をやり直してください"
            )

    # ---- 明るさチェック ----
    def lighting(self, image: np.ndarray) -> tuple[float, float, float]:
        """設定済みのマス全体での (平均の明るさ, ばらつき, 白飛び画素の割合)。"""
        quads = list(self.cells.values())
        if quads:
            # 全マスの画素を1本の配列につなげる (ravel = 2次元配列を1次元に平たくする)
            vals = np.concatenate([cell_patch(image, q).ravel() for q in quads])
        else:
            vals = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).ravel()
        # uint8 (0〜255 の整数) のままだと計算であふれることがあるので小数に変換
        vals = vals.astype(np.float32)
        # mean = 平均の明るさ
        # std  = 標準偏差 (明るさのばらつき)。真っ黒な画像などは模様がなく、ほぼ 0 になる
        # (vals >= 250).mean() = 255 近くの真っ白な画素の割合 (= 白飛び。
        #   光が強すぎて画素が最大値に張り付き、模様が失われた状態)
        return float(vals.mean()), float(vals.std()), float((vals >= 250).mean())

    def check_lighting(self, image: np.ndarray, for_reference: bool = False) -> float:
        """暗すぎ・明るすぎなら TooDarkError。問題なければ平均の明るさを返す。"""
        # 暗い写真はノイズだらけで模様が読み取れず、でたらめな判定になる。
        # 間違った答えを返すよりは「判定できない」と伝えるほうが安全、という考え方
        mean, std, sat = self.lighting(image)
        what = "基準画像として登録" if for_reference else "判定"
        need = float(self.config["min_brightness"])
        if not for_reference and self.empty is not None:
            # 空の状態を登録したときの明るさに対する割合で判断する
            need = max(
                need, self.lighting(self.empty)[0] * float(self.config["min_ratio"])
            )
        if mean < need or (
            not for_reference and std < float(self.config["min_contrast"])
        ):
            raise TooDarkError(
                f"暗すぎて{what}できません (明るさ {mean:.0f} / 必要 {need:.0f} 以上)。"
                "部屋の電気をつけてください"
            )
        if sat > 0.5:
            raise TooDarkError(
                f"明るすぎて(白飛びして){what}できません。直射日光などを避けてください"
            )
        return mean

    # ---- 判定 ----
    def detect_all(self, image: np.ndarray) -> dict[str, tuple[bool, float]]:
        """設定済みの全マスの判定結果 {"A1": (くつあり?, スコア), ...} を返す。

        暗すぎるときは TooDarkError を送出する (間違った結果を返さない)。
        """
        self._require_ready(image)
        cur_mean = self.check_lighting(image)
        ref_mean = self.lighting(self.empty)[0]
        # 今の画像の明るさを基準画像に合わせるための倍率。
        # 例: 基準の明るさ 120・今 100 なら gain=1.2 で、今の画像を 1.2 倍明るくして比べる
        gain = ref_mean / max(cur_mean, 1.0)  # 全体の明るさを基準画像にそろえる

        results = {}
        for name in CELL_NAMES:
            quad = self.cells.get(name)
            if quad is None:
                continue
            # 今の画像と基準画像から同じマスを切り出し (正面化+グレースケール)、
            # 模様の違いをスコア (0〜1) にする
            cur = cell_patch(image, quad).astype(np.float32) * gain
            ref = cell_patch(self.empty, quad).astype(np.float32)
            score = _structure_score(cur, ref)
            # スコアが閾値 (threshold) 以上なら「くつあり」。
            # 閾値を下げると敏感に (誤って「あり」が増える)、上げると鈍感になる
            results[name] = (score >= self.threshold, score)
        return results

    def check(self, image: np.ndarray, cell: str) -> tuple[bool, float]:
        name = normalize_cell(cell)
        if name not in self.cells:
            raise RuntimeError(
                f"{name} のマス範囲が未設定です。「マス設定」で四隅を指定してください"
            )
        return self.detect_all(image)[name]


def format_table(results: dict[str, tuple[bool, float]]) -> str:
    """判定結果を 4x8 の表として文字列化する (未設定のマスは －)。"""
    header = "    " + " ".join(f"{c:^4}" for c in range(1, COLS + 1))
    lines = [header]
    for r in ROWS:
        marks = []
        for c in range(1, COLS + 1):
            v = results.get(f"{r}{c}")
            marks.append(f"{'－' if v is None else ('●' if v[0] else '・'):^4}")
        lines.append(f" {r}  {' '.join(marks)}")
    count = sum(v[0] for v in results.values())
    note = (
        f"  未設定 {len(CELL_NAMES) - len(results)} マス"
        if len(results) < len(CELL_NAMES)
        else ""
    )
    lines.append(f"(● = くつあり  {count}/{len(results)} マス{note})")
    return "\n".join(lines)
