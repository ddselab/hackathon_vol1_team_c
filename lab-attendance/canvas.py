"""研究室の出席キャンバス: 下駄箱データの読み書きと、attendance.csv → Markdown → Slack キャンバスの自動更新。bot.py から使う。

  python canvas.py demo                 # ダミーデータで ko/ja を描画 + 自己テスト
  python canvas.py create <チャンネルID>  # Bot が持つチャンネルキャンバスを作り、canvas_id を出力
  python canvas.py                      # attendance.csv から描画して CANVAS_ID に反映

環境変数: SLACK_BOT_TOKEN (canvases:write), CANVAS_ID, CANVAS_LANG (ja|ko, 既定 ja)
  APP_HOME_URL（任意）: キャンバスの下に出す「App Home を開く」リンク。キャンバスにはボタンを置けないので登録・変更は App Home で
    例) slack://app?team=T0ANVH4SRHS&id=<アプリ ID A…>&tab=home
slots.json: マス番号が基準。cols = 下駄箱の横のマス数、空きマスは "user": null
  {"cols": 3, "slots": {"1": {"user": "U01AAA", "name": "名札"}, "2": {"user": null}, ...}}
  user/name は bot.py、cell/camera などほかのキーは calibrate.py の担当。write_json はキーをそのまま残す。
attendance.csv: time,user,event（event は in か out）  例) 2026-10-21T09:12:00,U01AAA,in
"""
import csv
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime

T = {
    "ko": {
        "days": "월화수목금토일",
        "summary": "{date} {t} 기준 · 재실 **{n}**/{total}명 · ✅ = 재실",
        "home": "🔘 칸 등록·이름표 변경은 [출석 앱 홈]({url})에서",
    },
    "ja": {
        "days": "月火水木金土日",
        "summary": "{date} {t} 時点 · 在室 **{n}**/{total}人 · ✅ = 在室",
        "home": "🔘 下駄箱の登録・名札の変更は[出席アプリのホーム]({url})から",
    },
}


def today_state(events, now):
    """now と同じ日のイベントから、人ごとの（最後の in/out, 最初の入室時刻, 最後の退室時刻）。"""
    last_kind, first_in, last_out = {}, {}, {}
    for dt, u, kind in events:
        if dt.date() != now.date():
            continue
        last_kind[u] = kind
        if kind == "in":
            first_in.setdefault(u, dt)
        else:
            last_out[u] = dt
    return last_kind, first_in, last_out


def use_certifi():
    """python.org 版の macOS の Python は証明書が空で Slack に接続（HTTPS）できない → certifi の証明書を使わせる。"""
    try:
        import certifi
    except ImportError:
        return  # Raspberry Pi のようにシステムの証明書がある環境ではなくてもよい
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def write_json(path, data):
    tmp = path + ".tmp"  # Bot と calibrate.py が同じファイルに書くので、書きかけのファイルが読まれないよう置き換える
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


SPACE = "　"  # 全角スペース: Slack の表で消されず、見えない（点字の空白 U+2800 は点に見えるので使えない）


def name_width(name):
    """名札の幅の目安（全角 1 文字 = 1）。日中韓の文字は 1、それ以外（英数字・空白）は 0.6。"""
    # ponytail: 文字ごとの目安なので W・M のような幅の広い英字が多いとその列だけ少し広がる。正確にするにはフォントで実際の幅を測る必要がある
    return sum(1 if ord(c) >= 0x2E80 else 0.6 for c in name)


# マスの大きさを固定: 名札はマス内で 1 行の幅 LINE_W（全角 5 = 英字約 8 文字）× NAME_LINES 行に折り、
# 幅合わせの行は常に PAD 文字。登録の前後・名前の長さに関係なく全マスが同じ大きさ（App Home の表とキャンバスの表で共用）
LINE_W, NAME_LINES = 5, 2
PAD = LINE_W + 1


def name_lines(slot):
    """マス内の名札 NAME_LINES 行: まず空白で区切り、1 語が 1 行より長ければ文字単位で。あふれたら最後の行の末尾を …"""
    lines, cur = [], ""
    for word in (slot.get("name") or "?").split():
        joined = f"{cur} {word}" if cur else word
        if name_width(joined) <= LINE_W:
            cur = joined
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if name_width(cur + ch) > LINE_W:
                lines.append(cur)
                cur = ""
            cur += ch
    lines.append(cur)
    if len(lines) > NAME_LINES:
        last = lines[NAME_LINES - 1]
        while name_width(last + "…") > LINE_W:
            last = last[:-1]
        lines = lines[:NAME_LINES - 1] + [last.rstrip() + "…"]
    return lines + [SPACE] * (NAME_LINES - len(lines))


def owners(rack):
    """slots.json → マス番号順の [(番号, user か None)]。"""
    return [(int(n), v.get("user")) for n, v in sorted(rack["slots"].items(), key=lambda x: int(x[0]))]


def render(rack, events, lang, now, home_url=None):
    """App Home（bot.py の build_home）と同じ画面をキャンバスの Markdown に。キャンバスにはボタンを置けないので home_url のリンクで代わりにする。
    rack: slots.json の中身, events: [(datetime, user, "in"|"out")] 時刻順。"""
    s = T[lang]
    slots = owners(rack)
    members = [u for _, u in slots if u]
    last_kind, _, _ = today_state(events, now)
    here = sum(last_kind.get(u) == "in" for u in members)
    date = f"{now.month}/{now.day} ({s['days'][now.weekday()]})"

    # App Home の表と同じマス（番号 / 名札 NAME_LINES 行 / 幅合わせの全角スペース行）。Markdown の表のセルは改行の代わりに <br>
    pad = SPACE * PAD

    def cell(n, u):
        # すべて太字（1 行目のヘッダーと同じ、在室の区別は ✅ だけ）。全角スペースだけの行は ** が効かないのでそのまま
        b = lambda t: t if t == SPACE else f"**{t}**"
        if not u:
            return "<br>".join([b(f"{n:02d}")] + [SPACE] * NAME_LINES + [pad])
        lines = [re.sub(r"([\\|*_~`<])", r"\\\1", l) for l in name_lines(rack["slots"][str(n)])]  # | * < などで表が崩れないように
        return "<br>".join([b(f"{n:02d}") + (" ✅" if last_kind.get(u) == "in" else "")] + [b(l) for l in lines] + [pad])

    # タイトルはキャンバス自体のタイトルが代わりになるので本文には入れない
    out = [s["summary"].format(date=date, t=now.strftime("%H:%M"), n=here, total=len(members)), ""]
    # 下駄箱の表: App Home と同じく 1 行に横 cols マス。Markdown の表はヘッダー行が必要なので 1 行目をヘッダーにする
    cols = rack.get("cols", 3)
    for r in range(0, len(slots), cols):
        row = [cell(n, u) for n, u in slots[r:r + cols]]
        out.append("| " + " | ".join(row + [" "] * (cols - len(row))) + " |")  # 最後の行が足りなければ空セル
        if r == 0:
            out.append("| " + " | ".join(["---"] * cols) + " |")
    if home_url:
        out += ["", s["home"].format(url=home_url)]
    return "\n".join(out)



def slack(method, payload):
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
                 "Content-Type": "application/json; charset=utf-8"})
    r = json.load(urllib.request.urlopen(req, timeout=10))
    if not r.get("ok"):
        raise RuntimeError(f"{method}: {r}")
    return r


def doc(md):
    return {"type": "markdown", "markdown": md}


def update_canvas(md, canvas_id=None):
    """CANVAS_ID のキャンバスの中身を md で丸ごと置き換える（canvases:write が必要）。"""
    slack("canvases.edit", {
        "canvas_id": canvas_id or os.environ["CANVAS_ID"],
        "changes": [{"operation": "replace", "document_content": doc(md)}],  # section_id がなければ全体を置き換え
    })


def load(here=".", day=None):
    """slots.json 全体 + attendance.csv のうち day（既定は今日）の行だけ。画面も判定も今日の状態しか使わないので、
    記録が何年たまってもメモリが増えないよう、ほかの日の行はリストに入れずに読み飛ばす（1 年分を全部持つと約 +4.6MB）。"""
    with open(os.path.join(here, "slots.json"), encoding="utf-8") as f:
        rack = json.load(f)
    path = os.path.join(here, "attendance.csv")
    prefix = (day or date.today()).isoformat()  # time 列は "2026-10-28T09:12:00" の形式
    events = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            events = [(datetime.fromisoformat(r["time"]), r["user"], r["event"])
                      for r in csv.DictReader(f) if r["time"].startswith(prefix)]
    return rack, sorted(events)


def demo():
    # bot.py の sample() と同じ下駄箱: 1番 佐藤健（在室）、2番 鈴木花子（退室）、4番 田中翔、3・5・6番は空き
    rack = {"cols": 3, "slots": {"1": {"user": "UA", "name": "佐藤健"}, "2": {"user": "UB", "name": "鈴木花子"},
                                 "3": {"user": None}, "4": {"user": "UC", "name": "田中翔"},
                                 "5": {"user": None}, "6": {"user": None}}}
    d = lambda h, m: datetime(2026, 10, 26, h, m)
    ev = [(datetime(2026, 10, 25, 9, 0), "UC", "in"),  # 昨日入って出ていない記録は、今日は在室にならない
          (d(8, 40), "UA", "in"), (d(9, 0), "UB", "in"), (d(12, 10), "UB", "out")]
    now = d(14, 30)
    url = "slack://app?team=T1&id=A1&tab=home"
    ko = render(rack, ev, "ko", now, url)
    ja = render(rack, ev, "ja", now)
    print(ko, "\n\n" + "=" * 40 + "\n\n" + ja)

    assert ja.splitlines()[0] == "10/26 (月) 14:30 時点 · 在室 **1**/3人 · ✅ = 在室"
    S, pad = SPACE, SPACE * PAD  # マスの幅は名前に関係なく固定
    assert [l for l in ja.splitlines() if l.startswith("|")] == [
        f"| **01** ✅<br>**佐藤健**<br>{S}<br>{pad} | **02**<br>**鈴木花子**<br>{S}<br>{pad} | **03**<br>{S}<br>{S}<br>{pad} |",
        "| --- | --- | --- |",
        f"| **04**<br>**田中翔**<br>{S}<br>{pad} | **05**<br>{S}<br>{S}<br>{pad} | **06**<br>{S}<br>{S}<br>{pad} |",
    ]  # App Home の表と同じ 4 行のマス
    assert "[出席アプリのホーム]" not in ja and ko.endswith(f"[출석 앱 홈]({url})에서")
    assert "재실 **1**/3명" in ko
    five = render({"cols": 3, "slots": {str(n): {"user": None} for n in range(1, 6)}}, [], "ja", now)
    assert five.endswith(f"| **04**<br>{S}<br>{S}<br>{pad} | **05**<br>{S}<br>{S}<br>{pad} |   |")  # 足りない最後の行は空セル
    odd = render({"cols": 1, "slots": {"1": {"user": "UA", "name": "a|*<br"}}}, [], "ja", now)
    assert "| **01**<br>**a\\|\\*\\<br**<br>" in odd  # 名札の | * < はエスケープ（名札で改行を差し込めないように）
    # マス内の名札: 空白で改行、長い語は文字単位、2 行を超えたら …、どの行も 1 行の幅に収まる
    assert name_lines({"name": "Choi Youchan"}) == ["Choi", "Youchan"]
    assert name_lines({"name": "山田 太郎"}) == ["山田 太郎", S] and name_lines({"name": "山本"}) == ["山本", S]
    assert name_lines({"name": "Alexander Smith"}) == ["Alexande", "r Smith"]
    assert name_lines({"name": "あいうえおかきくけこさしすせ"}) == ["あいうえお", "かきくけ…"]
    for nm in ("Choi Youchan", "Alexander Smith-Johnson", "あいうえおかきくけこさしすせ", "a b c d e f g h i j k", "?"):
        assert all(name_width(l) <= LINE_W for l in name_lines({"name": nm})) and len(name_lines({"name": nm})) == NAME_LINES, nm

    # load: attendance.csv から指定した日（既定は今日）の行だけ読む
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        write_json(os.path.join(tmp, "slots.json"), rack)
        with open(os.path.join(tmp, "attendance.csv"), "w", encoding="utf-8") as f:
            f.write("time,user,event\n2026-10-25T09:00:00,UC,in\n2026-10-26T08:40:00,UA,in\n"
                    f"{date.today().isoformat()}T07:00:00,UB,in\n")
        assert load(tmp, date(2026, 10, 26))[1] == [(d(8, 40), "UA", "in")]
        assert [e[1] for e in load(tmp)[1]] == ["UB"]
    print("\nOK")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "push"
    if cmd == "demo":
        demo()
    else:
        use_certifi()
        rack, events = load(os.path.dirname(os.path.abspath(__file__)))
        md = render(rack, events, os.environ.get("CANVAS_LANG", "ja"), datetime.now(), os.environ.get("APP_HOME_URL"))
        if cmd == "create":
            r = slack("conversations.canvases.create", {"channel_id": sys.argv[2], "document_content": doc(md)})
            print("CANVAS_ID =", r["canvas_id"])
        else:
            update_canvas(md)
