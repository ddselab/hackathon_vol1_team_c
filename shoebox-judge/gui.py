"""靴箱くつ判定ツール GUI 版 (Tkinter)。

画像処理の中身は shoebox.py にあり、このファイルは画面表示と操作だけを担当する。
画像まわりで知っておくとよいこと:
  - OpenCV の画像 (NumPy 配列, BGR 順) は Tkinter にそのまま表示できない。
    BGR → RGB に並べ替え → Pillow の Image に変換 → ImageTk.PhotoImage に変換、
    という手順で表示している (draw 参照)。
  - 画面のプレビューは縮小表示なので、クリック位置 (画面座標) を
    元画像の座標に戻す計算が必要になる (to_image_xy 参照)。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from shoebox import (
    CELL_NAMES,
    COLS,
    CORNER_LABELS,
    PI_CAMERA,
    ROWS,
    ShoeBoxDetector,
    TooDarkError,
    cell_name,
    load_image,
    parse_cell,
    quad_center,
)

PREVIEW_W, PREVIEW_H = 900, 560
ZOOM_SRC, ZOOM_SCALE = 36, 4  # ルーペ: 元画像の 36x36px を 4 倍で表示
COLOR_ON = "#4caf50"
COLOR_OFF = "#d0d0d0"
COLOR_UNSET = "#f4f4f4"
COLOR_HL = "#ff9800"


class App:
    def __init__(self, root: tk.Tk, source: str | int = 0):
        self.root = root
        self.det = ShoeBoxDetector()
        self.image = None  # 最新の元画像 (BGR)
        self.results: dict[str, tuple[bool, float]] = {}
        self.result_error = ""  # 暗すぎる等で判定できなかったときの理由
        self.scale = 1.0
        self.highlight: str | None = None
        self._photo = None
        self._zoom_photo = None
        self._rendered = None
        # マス設定中のみ使う
        self.setup_queue: list[str] | None = None
        self.setup_idx = 0
        self.points: list[list[int]] = []
        self.mouse: tuple[int, int] | None = None

        root.title("靴箱 くつ判定")

        # ---- 上部: 入力元と操作ボタン ----
        top = ttk.Frame(root, padding=6)
        top.pack(fill="x")
        self.src_mode = tk.StringVar(
            value="pi" if source == PI_CAMERA else "file" if isinstance(source, str) else "camera")
        self.cam_no = tk.IntVar(value=source if isinstance(source, int) else 0)
        self.file_path = tk.StringVar(value=source if isinstance(source, str) and source != PI_CAMERA else "")
        ttk.Radiobutton(top, text="カメラ", variable=self.src_mode, value="camera").pack(side="left")
        ttk.Spinbox(top, from_=0, to=9, width=3, textvariable=self.cam_no).pack(side="left")
        ttk.Radiobutton(top, text="Piカメラ", variable=self.src_mode, value="pi").pack(side="left", padx=(10, 0))
        ttk.Radiobutton(top, text="画像ファイル", variable=self.src_mode, value="file").pack(side="left", padx=(10, 0))
        ttk.Entry(top, textvariable=self.file_path, width=28).pack(side="left")
        ttk.Button(top, text="参照…", command=self.browse).pack(side="left")

        btns = ttk.Frame(root, padding=(6, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="更新", command=self.refresh).pack(side="left")
        ttk.Button(btns, text="マス設定", command=self.start_setup_all).pack(side="left", padx=(4, 0))
        ttk.Button(btns, text="選択マスを設定し直す", command=self.start_setup_selected).pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btns, text="設定をやめる", command=self.stop_setup, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(btns, text="空の状態を登録", command=self.register_empty).pack(side="left", padx=4)
        self.auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="自動更新(1秒)", variable=self.auto, command=self.auto_loop).pack(side="left", padx=10)
        ttk.Label(btns, text="閾値").pack(side="left")
        self.th = tk.DoubleVar(value=self.det.threshold)
        ttk.Scale(btns, from_=0.02, to=0.6, variable=self.th, length=120,
                  command=self.on_threshold).pack(side="left")
        self.th_label = ttk.Label(btns, text=f"{self.det.threshold:.2f}", width=5)
        self.th_label.pack(side="left")

        # ---- 中央: プレビューとマス一覧 ----
        mid = ttk.Frame(root, padding=6)
        mid.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(mid, width=PREVIEW_W, height=PREVIEW_H, bg="#222",
                                cursor="crosshair", highlightthickness=0)
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-3>", self.on_canvas_undo)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<Leave>", self.on_canvas_leave)
        root.bind("<Escape>", lambda e: self.stop_setup())

        grid = ttk.Frame(mid, padding=(10, 0))
        grid.pack(side="left", anchor="n")
        self.cells: dict[str, tk.Label] = {}
        for c in range(COLS):
            ttk.Label(grid, text=str(c + 1), anchor="center").grid(row=0, column=c + 1)
        for r in range(len(ROWS)):
            ttk.Label(grid, text=ROWS[r]).grid(row=r + 1, column=0, padx=(0, 4))
            for c in range(COLS):
                name = cell_name(r, c)
                lb = tk.Label(grid, text=name, width=4, height=2, bg=COLOR_OFF,
                              relief="ridge", bd=2)
                lb.grid(row=r + 1, column=c + 1, padx=1, pady=1)
                lb.bind("<Button-1>", lambda e, n=name: self.show_cell(n))
                self.cells[name] = lb
        self.count_label = ttk.Label(grid, text="", wraplength=300)
        self.count_label.grid(row=len(ROWS) + 1, column=0, columnspan=COLS + 1, pady=6)
        ttk.Label(grid, text="緑 = くつあり / 灰 = くつなし\n薄い文字 = マス範囲が未設定 / 赤っぽい = 判定不可",
                  foreground="#777").grid(row=len(ROWS) + 2, column=0, columnspan=COLS + 1)

        # ---- 下部: マス名入力 ----
        bottom = ttk.Frame(root, padding=6)
        bottom.pack(fill="x")
        ttk.Label(bottom, text="マス名:").pack(side="left")
        self.query = tk.StringVar()
        ent = ttk.Entry(bottom, textvariable=self.query, width=6, font=("", 14))
        ent.pack(side="left")
        ent.bind("<Return>", lambda e: self.ask())
        ttk.Button(bottom, text="確認", command=self.ask).pack(side="left", padx=4)
        self.answer = ttk.Label(bottom, text="", font=("", 16, "bold"))
        self.answer.pack(side="left", padx=10)

        self.status = ttk.Label(root, text="", foreground="#555", padding=(6, 0, 6, 6))
        self.status.pack(fill="x")

        ent.focus_set()
        root.after(100, self.refresh)

    # ---- 入力元 ----
    def source(self) -> str | int:
        if self.src_mode.get() == "file":
            if not self.file_path.get():
                raise RuntimeError("画像ファイルを選択してください")
            return self.file_path.get()
        if self.src_mode.get() == "pi":
            return PI_CAMERA
        return int(self.cam_no.get())

    def browse(self):
        path = filedialog.askopenfilename(
            filetypes=[("画像", "*.png *.jpg *.jpeg *.bmp"), ("すべて", "*.*")])
        if path:
            self.file_path.set(path)
            self.src_mode.set("file")
            self.refresh()

    # ---- 更新・判定 ----
    def in_setup(self) -> bool:
        return self.setup_queue is not None

    def refresh(self):
        if self.in_setup():
            return  # 設定中は画像を差し替えない (クリック位置がずれるため)
        try:
            self.image = load_image(self.source())
        except (RuntimeError, ValueError) as e:
            self.set_status(str(e))
            return
        self.detect()
        self.draw()
        if self.query.get().strip() and (self.results or self.result_error):
            self.ask(quiet=True)

    def detect(self):
        self.results, self.result_error = {}, ""
        try:
            self.results = self.det.detect_all(self.image)
            missing = self.det.missing_cells()
            self.set_status("判定しました" + (f"（未設定のマス: {len(missing)}）" if missing else ""))
        except TooDarkError as e:
            self.result_error = str(e)
            self.set_status(str(e))
        except RuntimeError as e:
            self.set_status(str(e))
        self.update_cells()

    def auto_loop(self):
        if self.auto.get():
            self.refresh()
            self.root.after(1000, self.auto_loop)

    def on_threshold(self, _=None):
        self.det.threshold = round(self.th.get(), 2)
        self.th_label.config(text=f"{self.det.threshold:.2f}")
        self.det.save_config()
        if self.image is not None and not self.in_setup():
            self.detect()
            self.draw()

    def update_cells(self):
        n = 0
        for name, lb in self.cells.items():
            is_set = name in self.det.cells
            has = self.results.get(name, (False, 0.0))[0]
            n += has
            if not is_set:
                bg, fg, relief = COLOR_UNSET, "#aaa", "groove"
            elif self.result_error:
                bg, fg, relief = "#fbe9e7", "#b0897f", "ridge"  # 判定不可
            else:
                bg, fg, relief = (COLOR_ON if has else COLOR_OFF), "black", "ridge"
            if name == self.highlight:
                bg, fg, relief = ("#2e7d32" if has else COLOR_HL), "white", "solid"
            lb.config(bg=bg, fg=fg, relief=relief)
        if self.in_setup():
            text = "マス設定中"
        elif self.result_error:
            text = "⚠ 暗すぎて判定できません"
        elif self.results:
            text = f"くつあり: {n} / {len(self.results)} マス"
        else:
            text = "未判定"
        self.count_label.config(text=text, foreground="#c62828" if self.result_error else "")

    def ask(self, quiet: bool = False):
        try:
            name = cell_name(*parse_cell(self.query.get()))
        except ValueError as e:
            if not quiet:
                self.answer.config(text=str(e), foreground="red")
            return
        self.show_cell(name)

    def show_cell(self, name: str):
        self.highlight = name
        if self.in_setup():
            pass
        elif name not in self.det.cells:
            self.answer.config(text=f"{name}：マス範囲が未設定です", foreground="red")
        elif self.result_error:
            self.answer.config(text=f"{name}：判定できません（暗すぎます）", foreground="#c62828")
        elif not self.results:
            self.answer.config(text=f"{name}: 未判定", foreground="red")
        else:
            has, score = self.results[name]
            self.answer.config(
                text=f"{name}：{'くつあり' if has else 'くつなし'}  (score={score:.2f})",
                foreground="#2e7d32" if has else "#555")
        self.update_cells()
        self.draw()

    # ---- マス設定 (各マスの四隅をクリック) ----
    def start_setup_all(self):
        missing = self.det.missing_cells()
        queue = CELL_NAMES
        if self.det.cells and missing:
            ans = messagebox.askyesnocancel(
                "マス設定",
                f"未設定のマスが {len(missing)} 個あります。\n\n"
                "はい … 未設定のマスだけ設定する\nいいえ … A1 から全部やり直す")
            if ans is None:
                return
            queue = missing if ans else CELL_NAMES
        elif self.det.cells and not messagebox.askokcancel(
                "マス設定", "A1 から全部のマスを設定し直します。よろしいですか？\n"
                "（1マスだけ直すときは、右の一覧でマスを選んで「選択マスを設定し直す」）"):
            return
        self.begin_setup(list(queue))

    def start_setup_selected(self):
        if not self.highlight:
            messagebox.showinfo("マス設定", "右の一覧で設定し直すマスをクリックしてから押してください")
            return
        self.begin_setup([self.highlight])

    def begin_setup(self, queue: list[str]):
        self.refresh()
        if self.image is None:
            return
        self.setup_queue, self.setup_idx, self.points = queue, 0, []
        self.results, self.result_error = {}, ""
        self.stop_btn.config(state="normal")
        self.auto.set(False)
        self.update_setup_status()

    def stop_setup(self, finished: bool = False):
        if not self.in_setup():
            return
        self.setup_queue, self.points = None, []
        self.stop_btn.config(state="disabled")
        self.draw_zoom(clear=True)
        if finished:
            msg = "マスの範囲を保存しました。"
            if self.det.empty is None:
                msg += "\n靴箱が空の状態で「空の状態を登録」を押してください。"
            messagebox.showinfo("マス設定", msg)
        self.refresh()

    def current_cell(self) -> str | None:
        if not self.in_setup() or self.setup_idx >= len(self.setup_queue):
            return None
        return self.setup_queue[self.setup_idx]

    def update_setup_status(self):
        name = self.current_cell()
        if name is None:
            return
        self.highlight = name
        total = len(self.setup_queue)
        self.set_status(
            f"[{self.setup_idx + 1}/{total}]  {name} の {CORNER_LABELS[len(self.points)]} をクリック"
            "　（右クリック = 1点戻す / Esc = やめる・ここまでは保存済み）")
        self.update_cells()
        self.draw()

    def to_image_xy(self, e) -> tuple[int, int] | None:
        if self.image is None:
            return None
        h, w = self.image.shape[:2]
        # 画面上のクリック位置 (縮小表示の座標) を縮小率で割り、元画像の座標に戻す
        x, y = int(e.x / self.scale), int(e.y / self.scale)
        if not (0 <= x < w and 0 <= y < h):
            return None
        return x, y

    def on_canvas_click(self, e):
        name = self.current_cell()
        p = self.to_image_xy(e)
        if name is None or p is None:
            return
        self.points.append(list(p))
        if len(self.points) == 4:
            try:
                self.det.set_cell(name, self.points)
            except ValueError as err:
                self.points = []
                messagebox.showwarning("マス設定", f"{name}: {err}")
                self.update_setup_status()
                return
            self.points = []
            self.setup_idx += 1
            if self.setup_idx >= len(self.setup_queue):
                self.stop_setup(finished=True)
                return
        self.update_setup_status()

    def on_canvas_undo(self, _e):
        if not self.in_setup():
            return
        if self.points:
            self.points.pop()
        elif self.setup_idx > 0:
            # 1つ前のマスに戻り、最後の1点だけ取り消した状態にする
            self.setup_idx -= 1
            prev = self.det.cells.get(self.setup_queue[self.setup_idx])
            self.points = [[int(x), int(y)] for x, y in prev[:3]] if prev else []
        self.update_setup_status()

    def on_canvas_motion(self, e):
        if not self.in_setup():
            return
        self.mouse = self.to_image_xy(e)
        self.draw_zoom()

    def on_canvas_leave(self, _e):
        self.mouse = None
        self.draw_zoom(clear=True)

    def register_empty(self):
        if self.in_setup():
            return
        try:
            self.image = load_image(self.source())
            self.det.set_empty(self.image)
        except (RuntimeError, ValueError) as e:
            messagebox.showerror("エラー", str(e))
            return
        self.set_status("空の状態を登録しました")
        self.refresh()

    # ---- 描画 ----
    def render(self):
        """マス範囲・判定結果・設定中の点を描いた元画像サイズの画像を返す。"""
        # 元画像を直接書き換えないようコピーに描き込む
        img = self.image.copy()
        h, w = img.shape[:2]
        # プレビュー枠 (900x560) に収まる縮小率。縦横比を保つため小さいほうに合わせる
        self.scale = min(PREVIEW_W / w, PREVIEW_H / h)
        # 元画像サイズで描いてから縮小するので、線の太さ・文字の大きさは
        # 縮小後にちょうどよく見えるよう 1/scale 倍しておく
        # (色は BGR 順: (255, 255, 0) = 水色, (0, 165, 255) = オレンジ, (0, 0, 255) = 赤)
        thick = max(1, int(round(1.5 / self.scale)))
        font = 0.45 / self.scale
        current = self.current_cell()
        for name, quad in self.det.cells.items():
            if name == current:
                continue  # 設定中のマスは古い枠を描かない
            pts = [tuple(int(v) for v in p) for p in quad]
            color = (0, 165, 255) if name == self.highlight else (255, 255, 0)
            for i in range(4):
                cv2.line(img, pts[i], pts[(i + 1) % 4], color, thick)
            cx, cy = quad_center(quad)
            if self.results.get(name):
                has = self.results[name][0]
                col = (0, 165, 255) if name == self.highlight else ((80, 200, 80) if has else (160, 160, 160))
                cv2.circle(img, (cx, cy), int(7 * thick), col, -1 if has else thick)
            else:
                cv2.putText(img, name, (cx - int(14 / self.scale), cy + int(5 / self.scale)),
                            cv2.FONT_HERSHEY_SIMPLEX, font, (255, 255, 0), thick, cv2.LINE_AA)
        if current:
            pts = [tuple(p) for p in self.points]
            for i, p in enumerate(pts):
                cv2.circle(img, p, int(4 * thick), (0, 0, 255), -1)
                if i:
                    cv2.line(img, pts[i - 1], p, (0, 0, 255), thick)
        return img

    def draw(self):
        if self.image is None:
            return
        img = self._rendered = self.render()
        h, w = img.shape[:2]
        img = cv2.resize(img, (int(w * self.scale), int(h * self.scale)), interpolation=cv2.INTER_AREA)
        # OpenCV (BGR) → RGB に変換 → Pillow 画像 → Tkinter 用画像。
        # self._photo に保持しておかないと、Python のガベージコレクションで
        # 画像が消えて画面が真っ白になる (Tkinter のよくある落とし穴)
        self._photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.draw_zoom()

    def draw_zoom(self, clear: bool = False):
        """設定中、カーソル付近を拡大して右上に表示する (細かい位置合わせ用)。"""
        self.canvas.delete("zoom", "rubber")
        if clear or not self.in_setup() or self.mouse is None or self.image is None:
            return
        x, y = self.mouse
        h, w = self.image.shape[:2]
        r = ZOOM_SRC // 2
        img = self._rendered
        if self.points:  # 最後の点からカーソルまでの線
            px, py = self.points[-1]
            s = self.scale
            self.canvas.create_line(px * s, py * s, x * s, y * s, fill="red", dash=(3, 2), tags="rubber")
        # 画像の端の近くでも切り出せるよう、周囲に r 画素ぶんの灰色の余白を足す。
        # 余白を足したので、元の (x, y) を中心とする範囲は pad 上では
        # [y : y+2r, x : x+2r] になる (NumPy のスライスは [y, x] の順)
        pad = cv2.copyMakeBorder(img, r, r, r, r, cv2.BORDER_CONSTANT, value=(40, 40, 40))
        crop = pad[y : y + 2 * r, x : x + 2 * r]
        size = ZOOM_SRC * ZOOM_SCALE
        # INTER_NEAREST (最近傍補間) で拡大すると、画素がぼやけずドット絵のように
        # くっきり見えるので、どの画素をクリックするか判断しやすい
        crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_NEAREST)
        c = size // 2
        cv2.line(crop, (c, 0), (c, size), (0, 0, 255), 1)
        cv2.line(crop, (0, c), (size, c), (0, 0, 255), 1)
        self._zoom_photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
        # カーソルと重ならない側の上隅に表示
        cw = int(w * self.scale)
        zx = 4 if x * self.scale > cw / 2 else cw - size - 4
        self.canvas.create_image(zx, 4, anchor="nw", image=self._zoom_photo, tags="zoom")
        self.canvas.create_rectangle(zx, 4, zx + size, 4 + size, outline="white", tags="zoom")

    def set_status(self, text: str):
        self.status.config(text=text)


def main(source: str | int = 0):
    root = tk.Tk()
    App(root, source)
    root.mainloop()


if __name__ == "__main__":
    main()
