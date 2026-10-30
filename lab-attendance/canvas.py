"""下駄箱データの読み書きと、Slack キャンバスへの出席表の書き出し。bot.py から使う。

slots.json の形式:
  {"cols": 8, "groups": [4, 4], "slots": {"1": {"user": "U...", "name": "名札", ...}, "2": {"user": null}, ...}}
  user/name は bot.py、cell/camera などほかのキーは calibrate.py の担当。write_json はキーをそのまま残す。
attendance.csv の形式: time,user,event（event は in か out）。1 行 = 1 件の出入り。
"""

import csv
import json
import os
from datetime import datetime

DAYS = {"ja": "月火水木金土日", "ko": "월화수목금토일"}
TEXT = {
    "ja": {
        "title": "👟 出席",
        "at": "時点",
        "here": "在室",
        "people": "人",
        "slot": "番号",
        "name": "名札",
        "state": "状態",
        "in": "✅ 在室",
        "out": "—",
        "empty": "（空き）",
    },
    "ko": {
        "title": "👟 출석",
        "at": "기준",
        "here": "재실",
        "people": "명",
        "slot": "번호",
        "name": "이름",
        "state": "상태",
        "in": "✅ 재실",
        "out": "—",
        "empty": "（비어 있음）",
    },
}


def use_certifi():
    """macOS の Python などで証明書が見つからず Slack に接続できない問題を避ける。certifi がなければ何もしない。"""
    try:
        import certifi
    except ImportError:
        return
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def read_events(path):
    """attendance.csv を [(時刻, 人の ID, in/out)] にする。ファイルがなければ空。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [
            (datetime.fromisoformat(r["time"]), r["user"], r["event"])
            for r in csv.DictReader(f)
            if r.get("time") and r.get("user") and r.get("event") in ("in", "out")
        ]


def load(here):
    """here にある slots.json と attendance.csv を読む。（rack, events）を返す。"""
    with open(os.path.join(here, "slots.json"), encoding="utf-8-sig") as f:
        rack = json.load(f)
    return rack, read_events(os.path.join(here, "attendance.csv"))


def write_json(path, data):
    """一時ファイルに書いてから置き換える（書き込み途中で止まっても slots.json が壊れないように）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def owners(rack):
    """[(マス番号, 持ち主の ID か None)] をマス番号順に。"""
    return sorted((int(k), v.get("user")) for k, v in rack["slots"].items())


def today_state(events, now):
    """now と同じ日の now までのイベントから、人ごとの（最後の in/out, 最初の入室時刻, 最後の退室時刻）。"""
    last_kind, first_in, last_out = {}, {}, {}
    for when, user, kind in sorted(events):
        if when.date() != now.date() or when > now:
            continue
        last_kind[user] = kind
        if kind == "in":
            first_in.setdefault(user, when)
        else:
            last_out[user] = when
    return last_kind, first_in, last_out


def render(rack, events, lang, now):
    """キャンバスに書く Markdown。マス番号順に 番号 · 名札 · 在室かどうか の表。"""
    t = TEXT.get(lang, TEXT["ja"])
    days = DAYS.get(lang, DAYS["ja"])
    last_kind, _, _ = today_state(events, now)
    slots = owners(rack)
    members = [u for _, u in slots if u]
    here = sum(last_kind.get(u) == "in" for u in members)
    lines = [
        f"# {t['title']}",
        "",
        f"{now.month}/{now.day} ({days[now.weekday()]}) {now:%H:%M} {t['at']} · "
        f"{t['here']} **{here}**/{len(members)}{t['people']}",
        "",
        f"| {t['slot']} | {t['name']} | {t['state']} |",
        "|---|---|---|",
    ]
    for n, u in slots:
        if u:
            name = (rack["slots"][str(n)].get("name") or "?").replace("|", "｜")
            state = t["in"] if last_kind.get(u) == "in" else t["out"]
        else:
            name, state = t["empty"], ""
        lines.append(f"| {n:02d} | {name} | {state} |")
    return "\n".join(lines) + "\n"


def update_canvas(markdown, canvas_id=None, token=None):
    """CANVAS_ID のキャンバスの中身を markdown で丸ごと置き換える（canvases:write が必要）。"""
    from slack_sdk import WebClient

    client = WebClient(token=token or os.environ["SLACK_BOT_TOKEN"])
    client.canvases_edit(
        canvas_id=canvas_id or os.environ["CANVAS_ID"],
        changes=[
            {
                "operation": "replace",
                "document_content": {"type": "markdown", "markdown": markdown},
            }
        ],
    )
