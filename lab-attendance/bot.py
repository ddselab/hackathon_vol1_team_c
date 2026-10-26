"""신발장 Slack UI: App Home에 신발장 표(8×4)를 보여주고, 버튼 몇 개로 칸을 등록·관리한다.

  python bot.py demo           # 자기 점검
  python bot.py preview        # 예시 화면을 Block Kit Builder 링크로 출력 (Slack에 로그인된 브라우저에서 열기)
  python bot.py init 6 3       # slots.json이 없을 때 빈 신발장 만들기 (칸 수, 가로 칸 수)
  python bot.py                # Socket Mode로 실행 (python -m pip install slack_bolt)

환경변수: SLACK_BOT_TOKEN (xoxb-, users:read · canvases:write), SLACK_APP_TOKEN (xapp-, connections:write)
         CANVAS_ID (선택: 있으면 등록이 바뀔 때 캔버스도 갱신), CANVAS_LANG (ja|ko, 기본 ja)
Slack에 보이는 글(App Home·창·오류 문구)은 모두 일본어. 코드 주석과 터미널 메시지는 한국어.
slots.json은 canvas.py 형식. 이 봇은 칸의 user/name만 바꾼다 (cell/camera는 calibrate.py 몫).

App Home 구성: 제목 → 연구실에 있는 인원 → 신발장 표(칸마다 번호·이름표, 연구실에 있으면 ✅) → 버튼
  칸이 없는 사람: [칸 등록] → 빈 칸 고르기 + 이름표 입력 창
  칸이 있는 사람: [이름표 바꾸기] [칸 옮기기] [칸 해제]
Slack 표(table 블록) 칸에는 글자·이모지만 들어가고 버튼은 못 넣어서, 칸 고르기는 창의 목록으로 한다.
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
LOCK = threading.Lock()  # 버튼 처리와 출석 이벤트 처리(POST /event)가 동시에 파일을 만지지 않게
DAYS = "月火水木金土日"
NAME_MAX = 16  # 이름표 글자 수. 일본어·영어 이름까지 넉넉하게 (표에서는 칸 안에서 줄바꿈됨)


def button(text, action_id, value="", style=None, confirm=None):
    b = {"type": "button", "text": {"type": "plain_text", "text": text}, "action_id": action_id}
    if value != "":
        b["value"] = str(value)  # Slack은 빈 value를 넣으면 화면 전체를 거절함
    if style:
        b["style"] = style
    if confirm:
        b["confirm"] = confirm
    return b


def confirm(title, text, ok):
    return {"title": {"type": "plain_text", "text": title}, "text": {"type": "mrkdwn", "text": text},
            "confirm": {"type": "plain_text", "text": ok}, "deny": {"type": "plain_text", "text": "キャンセル"}}


def empty_slots(rack):
    return [n for n, u in canvas.owners(rack) if not u]


def rack_table(rack, last_kind):
    """신발장 표: 칸 배치 그대로 한 줄에 가로 cols칸. 칸 = 번호 + 이름표(있으면 굵게 + ✅). 빈 칸은 번호만."""
    cols = rack.get("cols", 3)
    slots = canvas.owners(rack)

    def cell(n, u):
        parts = [{"type": "text", "text": f"{n:02d}  "}]
        if u:
            name = rack["slots"][str(n)].get("name") or "?"
            if last_kind.get(u) == "in":
                parts += [{"type": "text", "text": name, "style": {"bold": True}}, {"type": "text", "text": " "},
                          {"type": "emoji", "name": "white_check_mark"}]
            else:
                parts.append({"type": "text", "text": name})
        return {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": parts}]}

    rows = [[cell(n, u) for n, u in slots[i:i + cols]] for i in range(0, len(slots), cols)]
    blank = {"type": "raw_text", "text": " "}
    rows[-1] += [blank] * (cols - len(rows[-1]))  # 마지막 줄이 모자라면 빈 셀로 채움
    return {"type": "table", "block_id": "rack", "rows": rows,
            "column_settings": [{"align": "left", "is_wrapped": True}] * cols}


def build_home(rack, events, viewer, now, notice=None):
    """viewer 한 사람에게 보여줄 App Home 블록."""
    slots = canvas.owners(rack)
    last_kind, _, _ = canvas.today_state(events, now)
    inside = lambda u: last_kind.get(u) == "in"
    members = [u for _, u in slots if u]
    here = sum(map(inside, members))
    mine = next((n for n, u in slots if u == viewer), None)
    has_empty = bool(empty_slots(rack))

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "👟 出席"}},
              {"type": "context", "elements": [{"type": "mrkdwn", "text":
                  f"{now.month}/{now.day} ({DAYS[now.weekday()]}) {now:%H:%M} 時点 · 在室 *{here}*/{len(members)}人 · ✅ = 在室"}]},
              rack_table(rack, last_kind)]
    if notice:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"⚠️ {notice}"}]})

    if mine:
        name = rack["slots"][str(mine)].get("name") or "名前なし"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"あなたの下駄箱は *{mine}番*、名札は *{name}* です。"}})
        buttons = [button("名札を変更", "rename")]
        if inside(viewer):
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "靴が入っている間は移動・解除できません。"}]})
        else:
            if has_empty:
                buttons.append(button("場所を移動", "open_claim"))
            buttons.append(button("登録解除", f"release_{mine}", mine, style="danger",
                                  confirm=confirm("登録解除", f"{mine}番の登録を解除しますか？", "解除")))
        blocks.append({"type": "actions", "elements": buttons})
    elif has_empty:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "まだ下駄箱が登録されていません。"},
                       "accessory": button("登録する", "open_claim", style="primary")})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "まだ下駄箱が登録されていません。現在、空いている下駄箱はありません。"}})
    return blocks


def claim_modal(rack, viewer, default_name=""):
    """빈 칸을 고르는 창. 칸이 없는 사람은 이름표도 같이 입력, 칸이 있는 사람은 옮기기."""
    cols = rack.get("cols", 3)
    moving = any(v.get("user") == viewer for v in rack["slots"].values())
    options = [{"text": {"type": "plain_text", "text": f"{n}番（上から{(n - 1) // cols + 1}段目）"}, "value": str(n)}
               for n in empty_slots(rack)]
    blocks = [{"type": "input", "block_id": "slot",
               "label": {"type": "plain_text", "text": "空いている番号を選択（図の番号）"},
               "element": {"type": "static_select", "action_id": "value", "options": options[:100],
                           "placeholder": {"type": "plain_text", "text": "番号"}}}]
    if not moving:
        name = {"type": "plain_text_input", "action_id": "value", "max_length": NAME_MAX}
        if default_name[:NAME_MAX].strip():
            name["initial_value"] = default_name[:NAME_MAX]
        blocks.append({"type": "input", "block_id": "name", "element": name,
                       "label": {"type": "plain_text", "text": f"名札（{NAME_MAX}文字まで）"}})
    title = "場所の移動" if moving else "下駄箱の登録"
    return {"type": "modal", "callback_id": "claim", "title": {"type": "plain_text", "text": title},
            "submit": {"type": "plain_text", "text": "移動" if moving else "登録"},
            "close": {"type": "plain_text", "text": "キャンセル"}, "blocks": blocks}


def name_problem(name):
    name = (name or "").strip()
    if not name:
        return "名前を入力してください。"
    if len(name) > NAME_MAX:
        return f"名札は{NAME_MAX}文字までです。"
    return None


def change(rack, events, now, user, name, action, n):
    """칸 등록·옮기기·해제. rack을 바꾸고 None을 돌려주거나, 막히면 이유를 돌려준다."""
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
            name = slots[mine].get("name") or name  # 옮겨도 이름표는 그대로
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
    """이름표 바꾸기. 바꾸면 None, 막히면 이유."""
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
    return {"type": "modal", "callback_id": "rename",
            "title": {"type": "plain_text", "text": "名札の変更"},
            "submit": {"type": "plain_text", "text": "保存"}, "close": {"type": "plain_text", "text": "キャンセル"},
            "blocks": [{"type": "input", "block_id": "name", "element": element,
                        "label": {"type": "plain_text", "text": f"下駄箱に表示する名前（{NAME_MAX}文字まで）"}}]}


def submit_claim(rack, events, now, user, values):
    """칸 등록 창의 입력값 처리. (오류가 난 입력칸 이름, 이유) 또는 None."""
    n = int(values["slot"]["value"]["selected_option"]["value"])
    name = values.get("name", {}).get("value", {}).get("value")
    if "name" in values:
        why = name_problem(name)
        if why:
            return "name", why
    why = change(rack, events, now, user, name or "", "claim", n)
    return ("slot", why) if why else None



# ── 카메라 쪽에서 출석 정보 받기 ────────────────────────────────────────────
# 약속: 카메라 쪽은 '몇 번 칸에 신발이 들어왔다/나갔다'만 보낸다. 누구 칸인지는 이 봇이 slots.json에서 찾는다.
#   POST /event  {"slot": 12, "event": "in", "time": "2026-10-26T09:12:00"}   time은 빼도 됨 → 받은 시각으로 기록
#   GET  /slots  {"cols": 8, "rows": 4, "used": [1, 3, 12]}                    이름·Slack ID는 주지 않음
# 흔들림 방지(입장 10초·퇴실 2분 유지)는 카메라 쪽 몫. 이 봇은 확정된 변화만 받는다고 가정한다.
CSV = os.path.join(HERE, "attendance.csv")
EVENT_PORT = int(os.environ.get("EVENT_PORT", "8080"))


def apply_event(rack, events, slot, kind, when):
    """출석 이벤트 하나를 판단. (기록할 사람 ID, None) 또는 (None, 기록하지 않는 이유)."""
    if kind not in ("in", "out"):
        return None, "event는 in 또는 out이어야 해요"
    key = str(slot)
    if key not in rack["slots"]:
        return None, f"{slot}번 칸은 없어요"
    user = rack["slots"][key].get("user")
    if not user:
        return None, f"{slot}번 칸은 주인이 없어서 기록하지 않아요"
    last_kind, _, _ = canvas.today_state(events, when)
    if last_kind.get(user, "out") == kind:  # 이미 같은 상태 (예: 오늘 기록 없는 사람에게 또 out)
        return None, "이미 같은 상태라 기록하지 않아요"
    return user, None


def append_attendance(path, when, user, kind):
    """attendance.csv 끝에 한 줄 추가. 파일이 없으면 머리줄부터 만든다."""
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        if new:
            f.write("time,user,event\n")
        f.write(f"{when.isoformat(timespec='seconds')},{user},{kind}\n")


def slots_summary(rack):
    """GET /slots 응답: 칸 배치와 '주인 있는 칸 번호'만 (이름·ID는 카메라 쪽에 필요 없음)."""
    slots = canvas.owners(rack)
    cols = rack.get("cols", 3)
    return {"cols": cols, "rows": -(-len(slots) // cols), "used": [n for n, u in slots if u]}


def start_event_server(on_change, port=EVENT_PORT):
    """카메라 쪽 요청을 받는 작은 HTTP 서버를 봇과 함께 돌린다. 127.0.0.1 = 같은 기기(라즈베리파이) 안에서만 접속 가능."""
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
                return self.reply(404, {"ok": False, "error": "GET은 /slots만 있어요"})
            rack, _ = canvas.load(HERE)
            self.reply(200, slots_summary(rack))

        def do_POST(self):
            if self.path != "/event":
                return self.reply(404, {"ok": False, "error": "POST는 /event만 있어요"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                slot, kind = int(body["slot"]), body["event"]
                when = datetime.fromisoformat(body["time"]) if body.get("time") else datetime.now()
            except (ValueError, KeyError, TypeError) as e:
                return self.reply(400, {"ok": False, "error": f"형식이 틀렸어요: {e!r}"})
            with LOCK:  # 버튼 처리와 동시에 파일을 만지지 않게
                rack, events = canvas.load(HERE)
                user, why = apply_event(rack, events, slot, kind, when)
                if user:
                    append_attendance(CSV, when, user, kind)
            if user:
                on_change()  # 표 다시 그리기
            self.reply(200, {"ok": True, "recorded": bool(user), "note": why})

        def log_message(self, *args):
            pass  # 요청마다 터미널에 찍히는 접속 기록은 끔

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()  # 봇과 따로 돌도록 별도 스레드
    print(f"출석 정보 받는 중: http://127.0.0.1:{port}/event")
    return server


def run():
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    if not os.path.exists(PATH):
        sys.exit("slots.json이 없어요. 먼저 python bot.py init <칸 수> <가로 칸 수> 를 실행하세요.")
    canvas.use_certifi()
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    # ponytail: App Home을 연 사람 목록은 메모리에만 둠. 재시작하면 각자 다시 열 때 채워짐
    seen = set()

    def publish(client, user, notice=None):
        rack, events = canvas.load(HERE)
        client.views_publish(user_id=user, view={"type": "home", "blocks": build_home(rack, events, user, datetime.now(), notice)})

    def redraw_all(client, user=None, why=None):
        """캔버스를 새로 하고, 열어 본 사람 모두의 홈 화면을 다시 그림. why(거절 이유)는 누른 사람 화면에만."""
        if not why and os.environ.get("CANVAS_ID"):
            rack, events = canvas.load(HERE)
            canvas.update_canvas(canvas.render(rack, events, os.environ.get("CANVAS_LANG", "ja"), datetime.now()))
        if user:
            seen.add(user)
        for u in seen:
            publish(client, u, why if u == user else None)

    def after_change(client, user, why):
        """버튼으로 등록이 바뀌었을 때 (빈 칸 여부가 달라지므로 모두 다시 그림)."""
        redraw_all(client, user, why)

    def display_name(client, user):
        profile = client.users_info(user=user)["user"]
        return profile["profile"].get("display_name") or profile.get("real_name") or ""

    # 카메라 쪽 출석 정보 받기 (POST /event, GET /slots). 카메라와 연결할 때 아래 줄 맨 앞의 "# "를 지우면 켜진다.
    # start_event_server(lambda: redraw_all(app.client))

    @app.event("app_home_opened")
    def opened(event, client):
        if event.get("tab") == "home":
            seen.add(event["user"])
            publish(client, event["user"])

    @app.action("open_claim")
    def open_claim(ack, body, client):
        ack()  # 3초 안에 응답해야 하므로 먼저 받았다고 알림
        user = body["user"]["id"]
        rack, _ = canvas.load(HERE)
        if not empty_slots(rack):
            publish(client, user, "現在、空いている下駄箱はありません。")
            return
        client.views_open(trigger_id=body["trigger_id"], view=claim_modal(rack, user, display_name(client, user)))

    @app.view("claim")
    def save_claim(ack, body, view, client):
        user = body["user"]["id"]
        with LOCK:
            rack, events = canvas.load(HERE)  # 쓰기 직전에 다시 읽어 calibrate.py가 바꾼 cell을 지우지 않음
            problem = submit_claim(rack, events, datetime.now(), user, view["state"]["values"])
            if not problem:
                canvas.write_json(PATH, rack)
        if problem:
            ack(response_action="errors", errors={problem[0]: problem[1]})  # 입력칸 아래에 이유 표시, 창은 그대로
            return
        ack()
        after_change(client, user, None)

    @app.action(re.compile(r"^release_\d+$"))
    def release(ack, body, action, client):
        ack()
        user = body["user"]["id"]
        with LOCK:
            rack, events = canvas.load(HERE)
            why = change(rack, events, datetime.now(), user, "", "release", int(action["value"]))
            if not why:
                canvas.write_json(PATH, rack)
        after_change(client, user, why)

    @app.action("rename")
    def open_rename(ack, body, client):
        ack()
        rack, _ = canvas.load(HERE)
        current_name = next((v.get("name", "") for v in rack["slots"].values() if v.get("user") == body["user"]["id"]), "")
        client.views_open(trigger_id=body["trigger_id"], view=rename_modal(current_name))

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
    """예시 신발장: 1번 김민준(재실), 2번 이서연(퇴실), 4번 최유찬, 3·5·6번 빈 칸."""
    rack = {"cols": 3, "slots": {"1": {"user": "UA", "name": "김민준"}, "2": {"user": "UB", "name": "이서연"},
                                 "3": {"user": None}, "4": {"user": "U0BGU875SSJ", "name": "최유찬"},
                                 "5": {"user": None}, "6": {"user": None}}}
    d = lambda h, m: datetime(2026, 10, 26, h, m)
    events = [(d(8, 40), "UA", "in"), (d(9, 0), "UB", "in"), (d(12, 10), "UB", "out")]
    return rack, events, d(14, 30)


def preview():
    import board
    rack, events, now = board.sample()
    names = {v["user"]: v["name"] for v in rack["slots"].values() if v.get("user")}
    for title, viewer in [("칸이 있는 사람(윤도현)", "UE"), ("아직 칸이 없는 사람", "UX")]:
        text = json.dumps({"type": "home", "blocks": build_home(rack, events, viewer, now)}, ensure_ascii=False)
        for u, nm in names.items():
            text = text.replace(f"<@{u}>", nm)
        print(f"\n[{title}]\nhttps://app.slack.com/block-kit-builder/T0ANVH4SRHS#{urllib.parse.quote(text)}")


def demo():
    rack, events, now = sample()
    me = "U0BGU875SSJ"
    ids = lambda blocks: [e["action_id"] for b in blocks
                          for e in (b["elements"] if b["type"] == "actions" else [b["accessory"]] if "accessory" in b else [])]
    text = lambda blocks: json.dumps(blocks, ensure_ascii=False)

    # 칸이 없는 사람: 신발장 표 + [칸 등록] 버튼 하나뿐
    home = build_home(rack, events, "UX", now)
    assert "在室 *1*/3人 · ✅ = 在室" in text(home)
    table = home[2]
    assert table["type"] == "table" and len(table["rows"]) == 2 and all(len(r) == 3 for r in table["rows"])
    cell_text = lambda c: "".join(e.get("text", ":" + e.get("name", "") + ":") for e in c["elements"][0]["elements"])
    assert [cell_text(c) for c in table["rows"][0]] == ["01  김민준 :white_check_mark:", "02  이서연", "03  "]  # 1번만 재실
    assert table["rows"][0][0]["elements"][0]["elements"][1]["style"] == {"bold": True}  # 재실이면 이름 굵게
    assert [cell_text(c) for c in table["rows"][1]] == ["04  최유찬", "05  ", "06  "]
    assert len(json.dumps(table, ensure_ascii=False)) < 10000  # Slack 표는 칸 글자 합계 1만 자 제한
    five = build_home({"cols": 3, "slots": {str(k): {"user": None} for k in range(1, 6)}}, [], "UX", now)[2]
    assert five["rows"][1][2] == {"type": "raw_text", "text": " "}  # 칸이 모자란 마지막 줄은 빈 셀로 채움
    assert ids(home) == ["open_claim"] and home[-1]["accessory"]["style"] == "primary"
    # Slack은 빈 글자 value가 하나라도 있으면 화면 전체를 거절함 (실제로 views.publish가 invalid_arguments로 실패했음)
    full_rack = {"cols": 2, "slots": {"1": {"user": "UA", "name": "A"}, "2": {"user": "UB", "name": "B"}}}
    for viewer, r in [("UX", rack), (me, rack), ("UA", rack), ("UX", full_rack), ("UB", full_rack)]:
        assert '"value": ""' not in text(build_home(r, events, viewer, now)), viewer
    assert not re.search(r"\d\d:\d\d[~]|~\d\d:\d\d", text(home))  # 입장·퇴실 시각 없음

    # 칸이 있는 사람: 이름표 바꾸기 / 칸 옮기기 / 칸 해제(확인창)
    home = build_home(rack, events, me, now)
    assert "あなたの下駄箱は *4番*、名札は *최유찬*" in text(home)
    assert ids(home) == ["rename", "open_claim", "release_4"] and "confirm" in home[-1]["elements"][2]

    # 신발이 칸에 있는 사람: 이름표 바꾸기만
    home = build_home(rack, events, "UA", now)
    assert ids(home) == ["rename"] and "移動・解除できません" in text(home)

    # 빈 칸이 없으면 등록·옮기기 버튼 없음
    full = {"cols": 2, "slots": {"1": {"user": "UA", "name": "A"}, "2": {"user": "UB", "name": "B"}}}
    assert ids(build_home(full, [], "UX", now)) == [] and "空いている下駄箱はありません" in text(build_home(full, [], "UX", now))
    assert ids(build_home(full, [], "UB", now)) == ["rename", "release_2"]

    # 칸 고르기 창: 빈 칸만 목록에, 처음 등록이면 이름표 입력칸(Slack 이름을 16글자까지 미리 채움) 포함
    modal = claim_modal(rack, "UX", "Alexander Smith-Johnson")
    opts = modal["blocks"][0]["element"]["options"]
    assert [o["value"] for o in opts] == ["3", "5", "6"] and opts[1]["text"]["text"] == "5番（上から2段目）"
    assert modal["blocks"][1]["element"]["initial_value"] == "Alexander Smith-" and modal["title"]["text"] == "下駄箱の登録"
    assert modal["blocks"][1]["element"]["max_length"] == 16
    assert "initial_value" not in claim_modal(rack, "UX", "")["blocks"][1]["element"]  # Slack은 빈 initial_value를 거절함
    move = claim_modal(rack, me)
    assert move["title"]["text"] == "場所の移動" and len(move["blocks"]) == 1  # 옮길 땐 이름표 입력 없음

    # 창 제출 처리
    pick = lambda n, name=None: {"slot": {"value": {"selected_option": {"value": str(n)}}},
                                 **({"name": {"value": {"value": name}}} if name is not None else {})}
    rack["slots"]["3"]["cell"] = [400, 0, 200, 200]  # calibrate.py가 붙인 값은 그대로 남아야 함
    assert submit_claim(rack, events, now, "UX", pick(3, " 윤 ")) is None
    assert rack["slots"]["3"] == {"user": "UX", "name": "윤", "cell": [400, 0, 200, 200]}
    assert submit_claim(rack, events, now, "UY", pick(3, "누군가")) == ("slot", "すでに他の人が登録しています。別の番号を選んでください。")
    assert submit_claim(rack, events, now, "UY", pick(5, "")) == ("name", "名前を入力してください。")
    assert rack["slots"]["5"] == {"user": None}  # 이름이 틀리면 칸도 바뀌지 않음
    assert rename(rack, me, "유찬") is None
    assert submit_claim(rack, events, now, me, pick(5)) is None  # 4번 → 5번으로 옮김, 바꾼 이름표 유지
    assert rack["slots"]["4"] == {"user": None} and rack["slots"]["5"] == {"user": me, "name": "유찬"}
    assert submit_claim(rack, events, now, "UA", pick(6)) == ("slot", "靴が入っている間は移動できません。")

    # 해제
    assert change(rack, events, now, "UA", "", "release", 1) == "靴が入っている間は解除できません。"
    assert change(rack, events, now, "UB", "", "release", 1) == "自分の下駄箱のみ解除できます。"
    assert change(rack, events, now, "UB", "", "release", 2) is None and rack["slots"]["2"] == {"user": None}

    # 이름표 바꾸기
    assert rename(rack, me, "  ") == "名前を入力してください。"
    assert rename(rack, me, "A" * 17) == "名札は16文字までです。"
    assert rename(rack, me, "山田 太郎") is None and rename(rack, me, "Alexander Smith") is None  # 일본어·영어 이름
    assert rename(rack, "UZ", "누구") == "先に下駄箱を登録してください。"
    assert rename(rack, me, " 최 ") is None and rack["slots"]["5"]["name"] == "최"
    assert "initial_value" not in rename_modal("")["blocks"][0]["element"]

    # 카메라 출석 이벤트 (rack은 위에서 바뀐 상태: 1번 UA 재실, 3번 UX, 5번 me)
    t = now.replace(hour=15)
    assert apply_event(rack, events, 5, "in", t) == (me, None)                 # 주인 있는 칸 → 그 사람 기록
    assert apply_event(rack, events, 1, "in", t) == (None, "이미 같은 상태라 기록하지 않아요")  # 이미 재실
    assert apply_event(rack, events, 1, "out", t) == ("UA", None)
    assert apply_event(rack, events, 4, "in", t)[0] is None                    # 주인 없는 칸은 무시
    assert apply_event(rack, events, 99, "in", t)[0] is None and apply_event(rack, events, 5, "x", t)[0] is None
    assert slots_summary(rack) == {"cols": 3, "rows": 2, "used": [1, 3, 5]}   # 이름·ID는 안 줌
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "attendance.csv")
        append_attendance(p, t, me, "in")
        append_attendance(p, t.replace(hour=18), me, "out")
        assert open(p, encoding="utf-8").read() == (
            "time,user,event\n2026-10-26T15:30:00,U0BGU875SSJ,in\n2026-10-26T18:30:00,U0BGU875SSJ,out\n")
    print("OK")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "demo":
        demo()
    elif cmd == "preview":
        preview()
    elif cmd == "init":
        if os.path.exists(PATH):
            sys.exit("slots.json이 이미 있어요. 덮어쓰지 않았어요.")
        n, cols = int(sys.argv[2]), int(sys.argv[3])
        canvas.write_json(PATH, {"cols": cols, "slots": {str(k): {"user": None} for k in range(1, n + 1)}})
        print(f"빈 신발장을 만들었어요: 칸 {n}개, 가로 {cols}칸 → slots.json")
    else:
        run()
