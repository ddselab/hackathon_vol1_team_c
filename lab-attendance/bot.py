"""下駄箱 Slack UI: App Home に下駄箱の表（8×4）を表示し、いくつかのボタンで下駄箱を登録・管理する。

  python bot.py demo           # 自己テスト
  python bot.py preview        # 画面例を Block Kit Builder のリンクで出力（Slack にログインしたブラウザで開く）
  python bot.py init 6 3       # slots.json がないときに空の下駄箱を作る（マス数, 横のマス数）
  python bot.py                # Socket Mode で起動（python -m pip install slack_bolt）

環境変数: SLACK_BOT_TOKEN (xoxb-, users:read · canvases:write), SLACK_APP_TOKEN (xapp-, connections:write)
         CANVAS_ID（任意: 設定すると登録が変わったときにキャンバスも更新）, CANVAS_LANG (ja|ko, 既定 ja)
         PHOTO_CHANNEL（任意: POST /photo で受け取った写真を投稿するチャンネル ID。files:write が必要、Bot をチャンネルに招待しておくこと）
Slack に表示される文言・コードのコメント・ターミナルのメッセージはすべて日本語。
slots.json は canvas.py の形式。この Bot はマスの user/name だけを変更する（cell/camera は calibrate.py の担当）。

App Home の構成: タイトル → 在室人数 → 下駄箱の表（マスごとに番号・名札、在室なら ✅） → ボタン
  下駄箱がない人: [登録する] → 空きマスの選択 + 名札入力のモーダル
  下駄箱がある人: [名札を変更] [場所を移動] [登録解除]
Slack の表（table ブロック）のセルには文字・絵文字しか入らずボタンを置けないので、マスの選択はモーダルのリストで行う。
"""

import json
import os
import re
import sys
import threading
import urllib.parse
from datetime import datetime

import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "slots.json")
LOCK = (
    threading.Lock()
)  # ボタン処理と出席イベント処理（POST /event, /events）が同時にファイルを触らないように
DAYS = "月火水木金土日"
NAME_MAX = (
    16  # 名札の文字数。日本語・英語の名前まで余裕をもって（表ではセル内で折り返される）
)


def button(text, action_id, value="", style=None, confirm=None):
    b = {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "action_id": action_id,
    }
    if value != "":
        b["value"] = str(value)  # Slack は空の value があると画面全体を拒否する
    if style:
        b["style"] = style
    if confirm:
        b["confirm"] = confirm
    return b


def confirm(title, text, ok):
    return {
        "title": {"type": "plain_text", "text": title},
        "text": {"type": "mrkdwn", "text": text},
        "confirm": {"type": "plain_text", "text": ok},
        "deny": {"type": "plain_text", "text": "キャンセル"},
    }


def empty_slots(rack):
    return [n for n, u in canvas.owners(rack) if not u]


def rack_table(rack, last_kind):
    """下駄箱の表: マスの配置どおり 1 行に横 cols マス。マス = 番号 + 名札（在室なら太字 + ✅）。空きマスは番号だけ。"""
    cols = rack.get("cols", 3)
    slots = canvas.owners(rack)

    def cell(n, u):
        parts = [{"type": "text", "text": f"{n:02d}  "}]
        if u:
            name = rack["slots"][str(n)].get("name") or "?"
            if last_kind.get(u) == "in":
                parts += [
                    {"type": "text", "text": name, "style": {"bold": True}},
                    {"type": "text", "text": " "},
                    {"type": "emoji", "name": "white_check_mark"},
                ]
            else:
                parts.append({"type": "text", "text": name})
        return {
            "type": "rich_text",
            "elements": [{"type": "rich_text_section", "elements": parts}],
        }

    rows = [
        [cell(n, u) for n, u in slots[i : i + cols]] for i in range(0, len(slots), cols)
    ]
    blank = {"type": "raw_text", "text": " "}
    rows[-1] += [blank] * (cols - len(rows[-1]))  # 最後の行が足りなければ空セルで埋める
    return {
        "type": "table",
        "block_id": "rack",
        "rows": rows,
        "column_settings": [{"align": "left", "is_wrapped": True}] * cols,
    }


def build_home(rack, events, viewer, now, notice=None):
    """viewer 1 人に見せる App Home のブロック。"""
    slots = canvas.owners(rack)
    last_kind, _, _ = canvas.today_state(events, now)
    inside = lambda u: last_kind.get(u) == "in"
    members = [u for _, u in slots if u]
    here = sum(map(inside, members))
    mine = next((n for n, u in slots if u == viewer), None)
    has_empty = bool(empty_slots(rack))

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "👟 出席"}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"{now.month}/{now.day} ({DAYS[now.weekday()]}) {now:%H:%M} 時点 · 在室 *{here}*/{len(members)}人 · ✅ = 在室",
                }
            ],
        },
        rack_table(rack, last_kind),
    ]
    if notice:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"⚠️ {notice}"}]}
        )

    if mine:
        name = rack["slots"][str(mine)].get("name") or "名前なし"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"あなたの下駄箱は *{mine}番*、名札は *{name}* です。",
                },
            }
        )
        buttons = [button("名札を変更", "rename")]
        if inside(viewer):
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "靴が入っている間は移動・解除できません。",
                        }
                    ],
                }
            )
        else:
            if has_empty:
                buttons.append(button("場所を移動", "open_claim"))
            buttons.append(
                button(
                    "登録解除",
                    f"release_{mine}",
                    mine,
                    style="danger",
                    confirm=confirm(
                        "登録解除", f"{mine}番の登録を解除しますか？", "解除"
                    ),
                )
            )
        blocks.append({"type": "actions", "elements": buttons})
    elif has_empty:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "まだ下駄箱が登録されていません。"},
                "accessory": button("登録する", "open_claim", style="primary"),
            }
        )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "まだ下駄箱が登録されていません。現在、空いている下駄箱はありません。",
                },
            }
        )
    return blocks


def claim_modal(rack, viewer, default_name=""):
    """空きマスを選ぶモーダル。下駄箱がない人は名札も一緒に入力、ある人は移動。"""
    cols = rack.get("cols", 3)
    moving = any(v.get("user") == viewer for v in rack["slots"].values())
    options = [
        {
            "text": {
                "type": "plain_text",
                "text": f"{n}番（上から{(n - 1) // cols + 1}段目）",
            },
            "value": str(n),
        }
        for n in empty_slots(rack)
    ]
    blocks = [
        {
            "type": "input",
            "block_id": "slot",
            "label": {"type": "plain_text", "text": "空いている番号を選択（図の番号）"},
            "element": {
                "type": "static_select",
                "action_id": "value",
                "options": options[:100],
                "placeholder": {"type": "plain_text", "text": "番号"},
            },
        }
    ]
    if not moving:
        name = {
            "type": "plain_text_input",
            "action_id": "value",
            "max_length": NAME_MAX,
        }
        if default_name[:NAME_MAX].strip():
            name["initial_value"] = default_name[:NAME_MAX]
        blocks.append(
            {
                "type": "input",
                "block_id": "name",
                "element": name,
                "label": {"type": "plain_text", "text": f"名札（{NAME_MAX}文字まで）"},
            }
        )
    title = "場所の移動" if moving else "下駄箱の登録"
    return {
        "type": "modal",
        "callback_id": "claim",
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": "移動" if moving else "登録"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": blocks,
    }


def name_problem(name):
    name = (name or "").strip()
    if not name:
        return "名前を入力してください。"
    if len(name) > NAME_MAX:
        return f"名札は{NAME_MAX}文字までです。"
    return None


def change(rack, events, now, user, name, action, n):
    """下駄箱の登録・移動・解除。rack を変更して None を返すか、できなければ理由を返す。"""
    slots, key = rack["slots"], str(n)
    if key not in slots:
        return "存在しない番号です。"
    last_kind, _, _ = canvas.today_state(events, now)
    inside = last_kind.get(user) == "in"
    mine = next((k for k, v in slots.items() if v.get("user") == user), None)
    if action == "claim":
        if slots[key].get("user"):
            return "すでに他の人が登録しています。別の番号を選んでください。"
        if mine and inside:
            return "靴が入っている間は移動できません。"
        if mine:
            name = slots[mine].get("name") or name  # 移動しても名札はそのまま
            slots[mine]["user"] = None
            slots[mine].pop("name", None)
        slots[key].update(user=user, name=name.strip()[:NAME_MAX])
    elif action == "release":
        if slots[key].get("user") != user:
            return "自分の下駄箱のみ解除できます。"
        if inside:
            return "靴が入っている間は解除できません。"
        slots[key]["user"] = None
        slots[key].pop("name", None)
    return None


def rename(rack, user, name):
    """名札の変更。変更できたら None、できなければ理由。"""
    why = name_problem(name)
    if why:
        return why
    mine = next((v for v in rack["slots"].values() if v.get("user") == user), None)
    if not mine:
        return "先に下駄箱を登録してください。"
    mine["name"] = name.strip()
    return None


def rename_modal(current):
    element = {"type": "plain_text_input", "action_id": "value", "max_length": NAME_MAX}
    if current:
        element["initial_value"] = current
    return {
        "type": "modal",
        "callback_id": "rename",
        "title": {"type": "plain_text", "text": "名札の変更"},
        "submit": {"type": "plain_text", "text": "保存"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": [
            {
                "type": "input",
                "block_id": "name",
                "element": element,
                "label": {
                    "type": "plain_text",
                    "text": f"下駄箱に表示する名前（{NAME_MAX}文字まで）",
                },
            }
        ],
    }


def submit_claim(rack, events, now, user, values):
    """登録モーダルの入力値を処理。（エラーになった入力欄の名前, 理由）または None。"""
    n = int(values["slot"]["value"]["selected_option"]["value"])
    name = values.get("name", {}).get("value", {}).get("value")
    if "name" in values:
        why = name_problem(name)
        if why:
            return "name", why
    why = change(rack, events, now, user, name or "", "claim", n)
    return ("slot", why) if why else None


# ── カメラ側から出席情報を受け取る ──────────────────────────────────────────
# 取り決め: カメラ側は「何番のマスに靴が入った／出た」だけを送る。誰のマスかはこの Bot が slots.json から調べる。
#   POST /event  {"slot": 12, "event": "in", "time": "2026-10-26T09:12:00"}   time は省略可 → 受け取った時刻で記録
#   POST /events {"slots": {"1": "in", "2": "out", ..., "32": "out"}, "time": ...}   複数マスを 1 回でまとめて送る
#   GET  /slots  {"cols": 8, "rows": 4, "used": [1, 3, 12]}                    名前・Slack ID は返さない
#   POST /photo  本文 = JPEG そのまま（Content-Type: image/jpeg）              PHOTO_CHANNEL のチャンネルに投稿
# チャタリング対策（入室 10 秒・退室 2 分の維持）はカメラ側の担当。この Bot は確定した変化だけを受け取る前提。
CSV = os.path.join(HERE, "attendance.csv")
EVENT_PORT = int(os.environ.get("EVENT_PORT", "8080"))
PHOTO_MAX = (
    20 * 1024 * 1024
)  # 2592×1944 の JPEG は普通 2〜4MB。これより大きければ誤送信とみなして拒否


def apply_event(rack, events, slot, kind, when):
    """出席イベント 1 件を判定。（記録する人の ID, None）または（None, 記録しない理由）。"""
    if kind not in ("in", "out"):
        return None, "event は in か out にしてください"
    key = str(slot)
    if key not in rack["slots"]:
        return None, f"{slot}番のマスはありません"
    user = rack["slots"][key].get("user")
    if not user:
        return None, f"{slot}番のマスは持ち主がいないので記録しません"
    last_kind, _, _ = canvas.today_state(events, when)
    if (
        last_kind.get(user, "out") == kind
    ):  # すでに同じ状態（例: 今日記録のない人にまた out）
        return None, "すでに同じ状態なので記録しません"
    return user, None


def apply_events(rack, events, states, when):
    """複数マスの状態をまとめて判定。（記録する [(マス番号, 人の ID, in/out)], {マス番号: 記録しない理由}）。"""
    recorded, notes = [], {}
    for slot, kind in sorted(states.items()):
        user, why = apply_event(rack, events, slot, kind, when)
        if user:
            recorded.append((slot, user, kind))
        else:
            notes[slot] = why
    return recorded, notes


def parse_states(body):
    """POST /events の本文から {マス番号: "in" か "out"} を取り出す。形式が違えば ValueError。"""
    slots = body.get("slots")
    if not isinstance(slots, dict) or not slots:
        raise ValueError('slots に {"マス番号": "in" か "out"} を入れてください')
    states = {int(k): v for k, v in slots.items()}
    bad = [k for k, v in states.items() if v not in ("in", "out")]
    if bad:  # 1 つでもおかしければ、どのマスも記録しない
        raise ValueError(f"{bad}番の値は in か out にしてください")
    return states


def append_attendance(path, when, user, kind):
    """attendance.csv の末尾に 1 行追加。ファイルがなければヘッダー行から作る。"""
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        if new:
            f.write("time,user,event\n")
        f.write(f"{when.isoformat(timespec='seconds')},{user},{kind}\n")


def slots_summary(rack):
    """GET /slots の応答: マスの配置と「持ち主がいるマスの番号」だけ（名前・ID はカメラ側に不要）。"""
    slots = canvas.owners(rack)
    cols = rack.get("cols", 3)
    return {
        "cols": cols,
        "rows": -(-len(slots) // cols),
        "used": [n for n, u in slots if u],
    }


def start_event_server(on_change, on_photo=None, port=EVENT_PORT):
    """カメラ側のリクエストを受ける小さな HTTP サーバーを Bot と一緒に動かす。127.0.0.1 = 同じ機器（Raspberry Pi）の中からだけ接続できる。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != "/slots":
                return self.reply(404, {"ok": False, "error": "GET は /slots だけです"})
            rack, _ = canvas.load(HERE)
            self.reply(200, slots_summary(rack))

        def do_POST(self):
            if self.path == "/photo":
                return self.photo()
            if self.path not in ("/event", "/events"):
                return self.reply(
                    404,
                    {"ok": False, "error": "POST は /event, /events, /photo だけです"},
                )
            single = self.path == "/event"
            try:
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"
                )
                states = (
                    {int(body["slot"]): body["event"]} if single else parse_states(body)
                )
                when = (
                    datetime.fromisoformat(body["time"])
                    if body.get("time")
                    else datetime.now()
                )
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                return self.reply(
                    400, {"ok": False, "error": f"形式が正しくありません: {e!r}"}
                )
            with LOCK:  # ボタン処理と同時にファイルを触らないように
                rack, events = canvas.load(HERE)
                recorded, notes = apply_events(rack, events, states, when)
                for _, user, kind in recorded:
                    append_attendance(CSV, when, user, kind)
            if recorded:
                on_change()  # 何マス変わっても表の描き直しは 1 回だけ
            if single:
                note = next(iter(notes.values()), None)
                return self.reply(
                    200, {"ok": True, "recorded": bool(recorded), "note": note}
                )
            self.reply(
                200,
                {
                    "ok": True,
                    "recorded": [slot for slot, _, _ in recorded],
                    "notes": {str(slot): why for slot, why in notes.items()},
                },
            )

        def photo(self):
            if not on_photo:
                return self.reply(
                    503, {"ok": False, "error": "PHOTO_CHANNEL が設定されていません"}
                )
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= PHOTO_MAX:
                return self.reply(
                    400,
                    {"ok": False, "error": f"写真のサイズがおかしいです: {size}バイト"},
                )
            data = self.rfile.read(size)
            if not data.startswith(b"\xff\xd8"):  # JPEG の先頭マーカー
                return self.reply(400, {"ok": False, "error": "JPEG ではありません"})
            try:
                on_photo(data)
            except (
                Exception
            ) as e:  # Slack 側のエラー（not_in_channel など）はそのままカメラ側に返す
                print(f"写真の投稿に失敗: {e!r}")
                return self.reply(
                    502, {"ok": False, "error": f"Slack に投稿できませんでした: {e!r}"}
                )
            self.reply(200, {"ok": True})

        def log_message(self, *args):
            pass  # リクエストごとにターミナルへ出るアクセスログは止める

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(
        target=server.serve_forever, daemon=True
    ).start()  # Bot とは別スレッドで動かす
    print(f"出席情報の受付中: http://127.0.0.1:{port}/event")
    return server


def run():
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    if not os.path.exists(PATH):
        sys.exit(
            "slots.json がありません。先に python bot.py init <マス数> <横のマス数> を実行してください。"
        )
    canvas.use_certifi()
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    # ponytail: App Home を開いた人の一覧はメモリにだけ持つ。再起動したら各自が開き直したときに埋まる
    seen = set()

    def publish(client, user, notice=None):
        rack, events = canvas.load(HERE)
        client.views_publish(
            user_id=user,
            view={
                "type": "home",
                "blocks": build_home(rack, events, user, datetime.now(), notice),
            },
        )

    def redraw_all(client, user=None, why=None):
        """キャンバスを更新し、開いたことのある全員のホーム画面を描き直す。why（拒否理由）は押した人の画面にだけ出す。"""
        if not why and os.environ.get("CANVAS_ID"):
            rack, events = canvas.load(HERE)
            canvas.update_canvas(
                canvas.render(
                    rack, events, os.environ.get("CANVAS_LANG", "ja"), datetime.now()
                )
            )
        if user:
            seen.add(user)
        for u in seen:
            publish(client, u, why if u == user else None)

    def after_change(client, user, why):
        """ボタンで登録が変わったとき（空きマスの有無が変わるので全員分描き直す）。"""
        redraw_all(client, user, why)

    def display_name(client, user):
        profile = client.users_info(user=user)["user"]
        return profile["profile"].get("display_name") or profile.get("real_name") or ""

    def post_photo(data):
        """カメラで撮った写真を PHOTO_CHANNEL に投稿する。"""
        now = datetime.now()
        app.client.files_upload_v2(
            channel=os.environ["PHOTO_CHANNEL"],
            file=data,
            filename=f"photo_{now:%Y%m%d_%H%M%S}.jpg",
            initial_comment=f"📸 {now.month}/{now.day} ({DAYS[now.weekday()]}) {now:%H:%M}",
            request_file_info=False,
        )  # True だと files:read 権限も必要になる

    # カメラ側から出席情報・写真を受け取る（POST /event, POST /events, GET /slots, POST /photo）。カメラとつなぐときに下の行頭の "# " を消すと有効になる。
    start_event_server(
        lambda: redraw_all(app.client),
        post_photo if os.environ.get("PHOTO_CHANNEL") else None,
    )

    @app.event("app_home_opened")
    def opened(event, client):
        if event.get("tab") == "home":
            seen.add(event["user"])
            publish(client, event["user"])

    @app.action("open_claim")
    def open_claim(ack, body, client):
        ack()  # 3 秒以内に応答する必要があるので、先に受け取ったことを知らせる
        user = body["user"]["id"]
        rack, _ = canvas.load(HERE)
        if not empty_slots(rack):
            publish(client, user, "現在、空いている下駄箱はありません。")
            return
        client.views_open(
            trigger_id=body["trigger_id"],
            view=claim_modal(rack, user, display_name(client, user)),
        )

    @app.view("claim")
    def save_claim(ack, body, view, client):
        user = body["user"]["id"]
        with LOCK:
            rack, events = canvas.load(
                HERE
            )  # 書き込む直前に読み直して、calibrate.py が変えた cell を消さない
            problem = submit_claim(
                rack, events, datetime.now(), user, view["state"]["values"]
            )
            if not problem:
                canvas.write_json(PATH, rack)
        if problem:
            ack(
                response_action="errors", errors={problem[0]: problem[1]}
            )  # 入力欄の下に理由を表示、モーダルはそのまま
            return
        ack()
        after_change(client, user, None)

    @app.action(re.compile(r"^release_\d+$"))
    def release(ack, body, action, client):
        ack()
        user = body["user"]["id"]
        with LOCK:
            rack, events = canvas.load(HERE)
            why = change(
                rack, events, datetime.now(), user, "", "release", int(action["value"])
            )
            if not why:
                canvas.write_json(PATH, rack)
        after_change(client, user, why)

    @app.action("rename")
    def open_rename(ack, body, client):
        ack()
        rack, _ = canvas.load(HERE)
        current_name = next(
            (
                v.get("name", "")
                for v in rack["slots"].values()
                if v.get("user") == body["user"]["id"]
            ),
            "",
        )
        client.views_open(
            trigger_id=body["trigger_id"], view=rename_modal(current_name)
        )

    @app.view("rename")
    def save_rename(ack, body, view, client):
        user = body["user"]["id"]
        with LOCK:
            rack, _ = canvas.load(HERE)
            why = rename(rack, user, view["state"]["values"]["name"]["value"]["value"])
            if not why:
                canvas.write_json(PATH, rack)
        if why:
            ack(response_action="errors", errors={"name": why})
            return
        ack()
        after_change(client, user, None)

    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


def sample():
    """下駄箱の例: 1番 佐藤健（在室）、2番 鈴木花子（退室）、4番 Choi Youchan、3・5・6番は空き。"""
    rack = {
        "cols": 3,
        "slots": {
            "1": {"user": "UA", "name": "佐藤健"},
            "2": {"user": "UB", "name": "鈴木花子"},
            "3": {"user": None},
            "4": {"user": "U0BGU875SSJ", "name": "Choi Youchan"},
            "5": {"user": None},
            "6": {"user": None},
        },
    }
    d = lambda h, m: datetime(2026, 10, 26, h, m)
    events = [(d(8, 40), "UA", "in"), (d(9, 0), "UB", "in"), (d(12, 10), "UB", "out")]
    return rack, events, d(14, 30)


def preview():
    import board

    rack, events, now = board.sample()
    names = {v["user"]: v["name"] for v in rack["slots"].values() if v.get("user")}
    for title, viewer in [("下駄箱がある人", "UE"), ("まだ下駄箱がない人", "UX")]:
        text = json.dumps(
            {"type": "home", "blocks": build_home(rack, events, viewer, now)},
            ensure_ascii=False,
        )
        for u, nm in names.items():
            text = text.replace(f"<@{u}>", nm)
        print(
            f"\n[{title}]\nhttps://app.slack.com/block-kit-builder/T0ANVH4SRHS#{urllib.parse.quote(text)}"
        )


def demo():
    rack, events, now = sample()
    me = "U0BGU875SSJ"
    ids = lambda blocks: [
        e["action_id"]
        for b in blocks
        for e in (
            b["elements"]
            if b["type"] == "actions"
            else [b["accessory"]]
            if "accessory" in b
            else []
        )
    ]
    text = lambda blocks: json.dumps(blocks, ensure_ascii=False)

    # 下駄箱がない人: 下駄箱の表 + [登録する] ボタン 1 つだけ
    home = build_home(rack, events, "UX", now)
    assert "在室 *1*/3人 · ✅ = 在室" in text(home)
    table = home[2]
    assert (
        table["type"] == "table"
        and len(table["rows"]) == 2
        and all(len(r) == 3 for r in table["rows"])
    )
    cell_text = lambda c: "".join(
        e.get("text", ":" + e.get("name", "") + ":")
        for e in c["elements"][0]["elements"]
    )
    assert [cell_text(c) for c in table["rows"][0]] == [
        "01  佐藤健 :white_check_mark:",
        "02  鈴木花子",
        "03  ",
    ]  # 1番だけ在室
    assert table["rows"][0][0]["elements"][0]["elements"][1]["style"] == {
        "bold": True
    }  # 在室なら名前を太字
    assert [cell_text(c) for c in table["rows"][1]] == [
        "04  Choi Youchan",
        "05  ",
        "06  ",
    ]
    assert (
        len(json.dumps(table, ensure_ascii=False)) < 10000
    )  # Slack の表はセルの文字数合計 1 万字まで
    five = build_home(
        {"cols": 3, "slots": {str(k): {"user": None} for k in range(1, 6)}},
        [],
        "UX",
        now,
    )[2]
    assert five["rows"][1][2] == {
        "type": "raw_text",
        "text": " ",
    }  # マスが足りない最後の行は空セルで埋める
    assert ids(home) == ["open_claim"] and home[-1]["accessory"]["style"] == "primary"
    # Slack は空文字の value が 1 つでもあると画面全体を拒否する（実際に views.publish が invalid_arguments で失敗した）
    full_rack = {
        "cols": 2,
        "slots": {"1": {"user": "UA", "name": "A"}, "2": {"user": "UB", "name": "B"}},
    }
    for viewer, r in [
        ("UX", rack),
        (me, rack),
        ("UA", rack),
        ("UX", full_rack),
        ("UB", full_rack),
    ]:
        assert '"value": ""' not in text(build_home(r, events, viewer, now)), viewer
    assert not re.search(
        r"\d\d:\d\d[~]|~\d\d:\d\d", text(home)
    )  # 入室・退室時刻は出さない

    # 下駄箱がある人: 名札を変更 / 場所を移動 / 登録解除（確認ダイアログ）
    home = build_home(rack, events, me, now)
    assert "あなたの下駄箱は *4番*、名札は *Choi Youchan*" in text(home)
    assert (
        ids(home) == ["rename", "open_claim", "release_4"]
        and "confirm" in home[-1]["elements"][2]
    )

    # 靴が入っている人: 名札の変更だけ
    home = build_home(rack, events, "UA", now)
    assert ids(home) == ["rename"] and "移動・解除できません" in text(home)

    # 空きマスがなければ登録・移動ボタンなし
    full = {
        "cols": 2,
        "slots": {"1": {"user": "UA", "name": "A"}, "2": {"user": "UB", "name": "B"}},
    }
    assert ids(
        build_home(full, [], "UX", now)
    ) == [] and "空いている下駄箱はありません" in text(build_home(full, [], "UX", now))
    assert ids(build_home(full, [], "UB", now)) == ["rename", "release_2"]

    # マス選択モーダル: 空きマスだけをリストに。初回登録なら名札入力欄（Slack の名前を 16 文字まで事前入力）を含む
    modal = claim_modal(rack, "UX", "Alexander Smith-Johnson")
    opts = modal["blocks"][0]["element"]["options"]
    assert [o["value"] for o in opts] == ["3", "5", "6"] and opts[1]["text"][
        "text"
    ] == "5番（上から2段目）"
    assert (
        modal["blocks"][1]["element"]["initial_value"] == "Alexander Smith-"
        and modal["title"]["text"] == "下駄箱の登録"
    )
    assert modal["blocks"][1]["element"]["max_length"] == 16
    assert (
        "initial_value" not in claim_modal(rack, "UX", "")["blocks"][1]["element"]
    )  # Slack は空の initial_value を拒否する
    move = claim_modal(rack, me)
    assert (
        move["title"]["text"] == "場所の移動" and len(move["blocks"]) == 1
    )  # 移動時は名札入力なし

    # モーダル送信の処理
    pick = lambda n, name=None: {
        "slot": {"value": {"selected_option": {"value": str(n)}}},
        **({"name": {"value": {"value": name}}} if name is not None else {}),
    }
    rack["slots"]["3"]["cell"] = [
        400,
        0,
        200,
        200,
    ]  # calibrate.py が付けた値はそのまま残る必要がある
    assert submit_claim(rack, events, now, "UX", pick(3, " 田中 ")) is None
    assert rack["slots"]["3"] == {
        "user": "UX",
        "name": "田中",
        "cell": [400, 0, 200, 200],
    }
    assert submit_claim(rack, events, now, "UY", pick(3, "だれか")) == (
        "slot",
        "すでに他の人が登録しています。別の番号を選んでください。",
    )
    assert submit_claim(rack, events, now, "UY", pick(5, "")) == (
        "name",
        "名前を入力してください。",
    )
    assert rack["slots"]["5"] == {"user": None}  # 名前が不正ならマスも変わらない
    assert rename(rack, me, "ユチャン") is None
    assert (
        submit_claim(rack, events, now, me, pick(5)) is None
    )  # 4番 → 5番に移動、変更した名札は維持
    assert rack["slots"]["4"] == {"user": None} and rack["slots"]["5"] == {
        "user": me,
        "name": "ユチャン",
    }
    assert submit_claim(rack, events, now, "UA", pick(6)) == (
        "slot",
        "靴が入っている間は移動できません。",
    )

    # 登録解除
    assert (
        change(rack, events, now, "UA", "", "release", 1)
        == "靴が入っている間は解除できません。"
    )
    assert (
        change(rack, events, now, "UB", "", "release", 1)
        == "自分の下駄箱のみ解除できます。"
    )
    assert change(rack, events, now, "UB", "", "release", 2) is None and rack["slots"][
        "2"
    ] == {"user": None}

    # 名札の変更
    assert rename(rack, me, "  ") == "名前を入力してください。"
    assert rename(rack, me, "A" * 17) == "名札は16文字までです。"
    assert (
        rename(rack, me, "山田 太郎") is None
        and rename(rack, me, "Alexander Smith") is None
    )  # 日本語・英語の名前
    assert rename(rack, "UZ", "だれ") == "先に下駄箱を登録してください。"
    assert rename(rack, me, " チェ ") is None and rack["slots"]["5"]["name"] == "チェ"
    assert "initial_value" not in rename_modal("")["blocks"][0]["element"]

    # カメラの出席イベント（rack は上で変わった状態: 1番 UA 在室、3番 UX、5番 me）
    t = now.replace(hour=15)
    assert apply_event(rack, events, 5, "in", t) == (
        me,
        None,
    )  # 持ち主がいるマス → その人を記録
    assert apply_event(rack, events, 1, "in", t) == (
        None,
        "すでに同じ状態なので記録しません",
    )  # すでに在室
    assert apply_event(rack, events, 1, "out", t) == ("UA", None)
    assert apply_event(rack, events, 4, "in", t)[0] is None  # 持ち主がいないマスは無視
    assert (
        apply_event(rack, events, 99, "in", t)[0] is None
        and apply_event(rack, events, 5, "x", t)[0] is None
    )
    # まとめて送る（POST /events）: 変化したマスだけ記録し、ほかは理由を返す
    recorded, notes = apply_events(
        rack, events, {1: "out", 3: "in", 4: "in", 5: "out"}, t
    )
    assert recorded == [(1, "UA", "out"), (3, "UX", "in")]
    assert notes == {
        4: "4番のマスは持ち主がいないので記録しません",
        5: "すでに同じ状態なので記録しません",
    }
    assert parse_states({"slots": {"1": "in", "32": "out"}}) == {1: "in", 32: "out"}
    for bad in [{}, {"slots": {}}, {"slots": [1, 2]}, {"slots": {"x": "in"}}, {"slots": {"1": "in", "2": "maybe"}}]:
        try:
            parse_states(bad)
            assert False, bad
        except ValueError:
            pass
    assert slots_summary(rack) == {
        "cols": 3,
        "rows": 2,
        "used": [1, 3, 5],
    }  # 名前・ID は返さない
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "attendance.csv")
        append_attendance(p, t, me, "in")
        append_attendance(p, t.replace(hour=18), me, "out")
        assert open(p, encoding="utf-8").read() == (
            "time,user,event\n2026-10-26T15:30:00,U0BGU875SSJ,in\n2026-10-26T18:30:00,U0BGU875SSJ,out\n"
        )
    print("OK")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "demo":
        demo()
    elif cmd == "preview":
        preview()
    elif cmd == "init":
        if os.path.exists(PATH):
            sys.exit("slots.json はすでにあります。上書きしませんでした。")
        n, cols = int(sys.argv[2]), int(sys.argv[3])
        canvas.write_json(
            PATH,
            {"cols": cols, "slots": {str(k): {"user": None} for k in range(1, n + 1)}},
        )
        print(f"空の下駄箱を作りました: マス {n} 個、横 {cols} マス → slots.json")
    else:
        run()
