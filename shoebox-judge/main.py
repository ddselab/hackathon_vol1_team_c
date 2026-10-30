"""靴箱くつ判定ツール コマンドライン版。

使い方:
  python main.py calibrate --image rack.jpg        全マスの四隅をクリックして登録
  python main.py calibrate A3 B5 --image rack.jpg  指定したマスだけ登録し直す
  python main.py set-empty --image empty.jpg       空の状態を基準として登録
  python main.py check A1 --image now.jpg          A1 にくつがあるか判定
  python main.py status --camera 0                 全マスの状態を表示
  python main.py                                   対話モード (A1 と入力して判定)
  python main.py watch                             1分に1回撮って全マスを判定し続ける
  python main.py watch --post                      判定のたびに結果を出席 Bot (POST /events) に送る
  python main.py snapshot rack.jpg                 今のカメラ画像を保存 (マス設定を PC でするとき)
  python main.py gui                               GUI を起動
入力元は --image ファイル / --camera 番号 / --pi (Raspberry Pi のカメラモジュール)。
省略すると、Pi (rpicam-still がある) なら Pi のカメラ、それ以外はカメラ 0 を使います。
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import cv2

from shoebox import (
    CELL_NAMES,
    COLS,
    CORNER_LABELS,
    PI_CAMERA,
    ShoeBoxDetector,
    default_source,
    format_table,
    load_image,
    normalize_cell,
    parse_cell,
    quad_center,
    save_image,
)


# 出席 Bot (lab-attendance/bot.py) の受付先。Bot と同じ Pi で動かす前提
EVENTS_URL = "http://127.0.0.1:8080/events"


def slot_number(name: str) -> int:
    """マス名 → 出席 Bot のマス番号。A1〜A8 = 1〜8、B1 = 9、…、D8 = 32 (左上から右へ、上の段から順)。"""
    row, col = parse_cell(name)
    return row * COLS + col + 1


def post_states(url: str, results: dict[str, tuple[bool, float]], when: datetime) -> dict:
    """全マスの判定結果を出席 Bot の POST /events にまとめて送り、Bot の応答 (JSON) を返す。

    毎回全マスを送ってよい (Bot 側で前回と同じ状態のマスは記録しない)。
    """
    body = {
        "time": when.isoformat(timespec="seconds"),
        "slots": {str(slot_number(name)): "in" if has else "out" for name, (has, _) in results.items()},
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    # Bot は Slack の画面を描き直してから応答するので、少し長めに待つ
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def get_source(args) -> str | int:
    if args.image:
        return args.image
    if args.pi:
        return PI_CAMERA
    if args.camera is not None:
        return args.camera
    return default_source()


def pick_cells(det: ShoeBoxDetector, image, names: list[str]) -> int:
    """OpenCV ウィンドウで各マスの四隅をクリックさせ、1マスごとに保存する。

    右クリックで1点戻す (前のマスにも戻れる)、Esc で中止 (それまでのマスは保存済み)。
    戻り値は設定し終えたマスの数。
    """
    # カメラ画像が画面より大きいとはみ出すので、最大 1200x800 に収まるよう縮小して表示する。
    # image.shape は (高さ, 幅, 3) なので shape[1] が幅、shape[0] が高さ
    scale = min(1.0, 1200 / image.shape[1], 800 / image.shape[0])
    view = cv2.resize(image, None, fx=scale, fy=scale)
    win = "calibrate"
    state = {"idx": 0, "points": []}

    # 元画像の座標 → 縮小表示した画面上の座標 に変換する
    # (逆に、クリック位置は scale で割って元画像の座標に戻してから保存する)
    def sc(p):
        return int(p[0] * scale), int(p[1] * scale)

    def prompt():
        if state["idx"] < len(names):
            name = names[state["idx"]]
            print(f"  [{state['idx'] + 1}/{len(names)}] {name} の "
                  f"{CORNER_LABELS[len(state['points'])]} をクリック")

    def redraw():
        # 表示用画像のコピーに線や文字を描き込んでウィンドウに表示する。
        # (cv2.line などは画像を直接書き換えるので、元の view を汚さないようコピーする)
        # 色の指定は BGR の順: (255, 255, 0) = 水色、(0, 0, 255) = 赤
        canvas = view.copy()
        current = names[state["idx"]] if state["idx"] < len(names) else None
        for name, quad in det.cells.items():
            if name == current:
                continue
            pts = [sc(p) for p in quad]
            for i in range(4):
                cv2.line(canvas, pts[i], pts[(i + 1) % 4], (255, 255, 0), 1)
            cx, cy = sc(quad_center(quad))
            cv2.putText(canvas, name, (cx - 12, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (255, 255, 0), 1, cv2.LINE_AA)
        pts = [sc(p) for p in state["points"]]
        for i, p in enumerate(pts):
            cv2.circle(canvas, p, 4, (0, 0, 255), -1)
            if i:
                cv2.line(canvas, pts[i - 1], p, (0, 0, 255), 2)
        if current:
            cv2.putText(canvas, f"{current}: {len(state['points'])}/4", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.imshow(win, canvas)

    # OpenCV のウィンドウでマウス操作があるたびに呼ばれる関数 (コールバック)。
    # event でクリックの種類、x, y でウィンドウ上の位置がわかる
    def on_mouse(event, x, y, flags, param):
        if state["idx"] >= len(names):
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            state["points"].append([int(x / scale), int(y / scale)])
            if len(state["points"]) == 4:
                name = names[state["idx"]]
                try:
                    det.set_cell(name, state["points"])
                except ValueError as e:
                    print(f"  {name}: {e}")
                    state["points"] = []
                    prompt()
                    redraw()
                    return
                state["points"] = []
                state["idx"] += 1
                if state["idx"] >= len(names):
                    print("  全部そろいました。Enter で終了")
            prompt()
            redraw()
        elif event == cv2.EVENT_RBUTTONDOWN:
            if state["points"]:
                state["points"].pop()
            elif state["idx"] > 0:
                state["idx"] -= 1
                prev = det.cells.get(names[state["idx"]])
                state["points"] = [[int(a), int(b)] for a, b in prev[:3]] if prev else []
            prompt()
            redraw()

    print("各マスの四隅を 左上 → 右上 → 右下 → 左下 の順にクリックしてください")
    print("（右クリック = 1点戻す / Esc = 中止。設定し終えたマスはその都度保存されます）")
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    prompt()
    redraw()
    # cv2.waitKey(30) は 30 ミリ秒キー入力を待つ関数。
    # これを繰り返し呼んでいる間に、ウィンドウの描画やマウス操作が処理される
    # (呼ばないとウィンドウが固まる)。& 0xFF は戻り値の下位8ビット = キーコードを取り出す定番の書き方
    while True:
        key = cv2.waitKey(30) & 0xFF
        if key == 27:  # Esc
            break
        if key in (13, 10) and state["idx"] >= len(names):
            break
    cv2.destroyAllWindows()
    return state["idx"]


def cmd_calibrate(args):
    det = ShoeBoxDetector()
    names = [normalize_cell(c) for c in args.cells] if args.cells else list(CELL_NAMES)
    done = pick_cells(det, load_image(get_source(args)), names)
    print(f"{done}/{len(names)} マスの範囲を config.json に保存しました")
    missing = det.missing_cells()
    if missing:
        print(f"未設定のマス: {' '.join(missing)}")
    elif det.empty is None:
        print("次に set-empty で空の状態を登録してください")


def cmd_set_empty(args):
    det = ShoeBoxDetector()
    det.set_empty(load_image(get_source(args)))
    print("空の状態を empty.png に登録しました")


def cmd_check(args):
    det = ShoeBoxDetector()
    name = normalize_cell(args.cell)
    has, score = det.check(load_image(get_source(args)), name)
    print(f"{name}: {'くつあり' if has else 'くつなし'} (score={score:.2f})")


def cmd_status(args):
    det = ShoeBoxDetector()
    print(format_table(det.detect_all(load_image(get_source(args)))))


def cmd_interactive(args):
    det = ShoeBoxDetector()
    source = get_source(args)
    print("マス名 (例: A1) を入力すると判定します。all = 全表示, q = 終了")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("q", "quit", "exit"):
            break
        try:
            # 入力のたびに最新の画像を取り直す
            image = load_image(source)
            if text.lower() == "all":
                print(format_table(det.detect_all(image)))
            else:
                name = normalize_cell(text)
                has, score = det.check(image, name)
                print(f"{name}: {'くつあり' if has else 'くつなし'} (score={score:.2f})")
        except (ValueError, RuntimeError) as e:
            print(f"エラー: {e}")


def cmd_snapshot(args):
    save_image(args.output, load_image(get_source(args)))
    print(f"カメラ画像を {args.output} に保存しました")


def cmd_watch(args):
    """一定間隔で撮影して全マスを判定し、結果と前回からの変化を表示し続ける。"""
    if args.interval <= 0:
        raise ValueError("--interval は 0 より大きい秒数で指定してください")
    # systemd などから止められたときも、撮影中の rpicam-still を片付けてから終わる
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    source = get_source(args)
    print(f"{args.interval:g} 秒ごとに判定します (Ctrl+C で終了)", flush=True)
    if args.post:
        print(f"判定結果を {args.post} に送ります", flush=True)
    prev = None
    next_at = time.monotonic()
    try:
        while True:
            now = datetime.now()
            stamp = now.strftime("%Y-%m-%d %H:%M:%S")
            try:
                # 毎回読み直すので、動かしたまま「空の状態を登録」やマス設定をし直しても反映される
                det = ShoeBoxDetector()
                results = det.detect_all(load_image(source))
            except (ValueError, RuntimeError) as e:
                # 暗い・カメラが使えないなどは、その回だけ飛ばして次の回にまた試す
                print(f"[{stamp}] 判定できません: {e}", flush=True)
            else:
                count = sum(v[0] for v in results.values())
                print(f"[{stamp}] くつあり {count}/{len(results)} マス", flush=True)
                if prev is None:
                    print(format_table(results), flush=True)
                else:
                    changes = [
                        f"{name} {'くつなし→くつあり' if has else 'くつあり→くつなし'}"
                        for name, (has, _) in results.items()
                        if name in prev and prev[name][0] != has
                    ]
                    if changes:
                        print("  変化: " + ", ".join(changes), flush=True)
                        print(format_table(results), flush=True)
                prev = results
                if args.post:
                    try:
                        reply = post_states(args.post, results, now)
                        if reply.get("recorded"):
                            slots = ", ".join(map(str, reply["recorded"]))
                            print(f"  出席 Bot に記録: {slots} 番", flush=True)
                    except urllib.error.HTTPError as e:
                        # 400 など: Bot が理由を JSON で返すので表示する
                        print(f"  出席 Bot に送れませんでした: {e.code} {e.read().decode(errors='replace')}", flush=True)
                    except (urllib.error.URLError, OSError, ValueError) as e:
                        # Bot が止まっている等。次の回にまた全マスを送るので、そのまま続ける
                        print(f"  出席 Bot に送れませんでした: {e}", flush=True)
            # 撮影・判定にかかった時間を差し引いて、間隔がずれていかないようにする
            next_at = max(next_at + args.interval, time.monotonic())
            time.sleep(next_at - time.monotonic())
    except KeyboardInterrupt:
        print()


def cmd_gui(args):
    import gui

    gui.main(source=get_source(args))


def main(argv=None):
    parser = argparse.ArgumentParser(description="靴箱(4x8)のくつ有無判定ツール")
    src = argparse.ArgumentParser(add_help=False)
    g = src.add_mutually_exclusive_group()
    g.add_argument("--image", help="判定に使う画像ファイル")
    g.add_argument("--camera", type=int, help="カメラ番号 (USB カメラなど)")
    g.add_argument("--pi", action="store_true", help="Raspberry Pi のカメラモジュールを使う")

    parser.add_argument("--image", help=argparse.SUPPRESS)
    parser.add_argument("--camera", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--pi", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("calibrate", parents=[src], help="各マスの四隅を登録 (マス名を付けるとそのマスだけ)")
    p.add_argument("cells", nargs="*", help="設定し直すマス (例: A3 B5)。省略すると全マス")
    p.set_defaults(func=cmd_calibrate)
    sub.add_parser("set-empty", parents=[src], help="空の状態を登録").set_defaults(func=cmd_set_empty)
    p = sub.add_parser("check", parents=[src], help="指定マスを判定 (例: check A1)")
    p.add_argument("cell", help="マス名 (A1〜D8)")
    p.set_defaults(func=cmd_check)
    sub.add_parser("status", parents=[src], help="全マスを表示").set_defaults(func=cmd_status)
    p = sub.add_parser("watch", parents=[src], help="一定間隔で撮影して判定し続ける (既定: 60秒ごと)")
    p.add_argument("--interval", type=float, default=60, help="判定の間隔 (秒、既定: 60)")
    p.add_argument("--post", nargs="?", const=EVENTS_URL, metavar="URL",
                   help=f"判定のたびに結果を出席 Bot に送る (URL 省略時: {EVENTS_URL})")
    p.set_defaults(func=cmd_watch)
    p = sub.add_parser("snapshot", parents=[src], help="今のカメラ画像をファイルに保存")
    p.add_argument("output", help="保存先 (例: rack.jpg)")
    p.set_defaults(func=cmd_snapshot)
    sub.add_parser("gui", parents=[src], help="GUI を起動").set_defaults(func=cmd_gui)

    args = parser.parse_args(argv)
    func = getattr(args, "func", cmd_interactive)
    try:
        func(args)
    except (ValueError, RuntimeError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
