"""연구실 출석 캔버스: attendance.csv → 마크다운 → Slack 캔버스 자동 갱신.

  python canvas.py demo                 # 가짜 데이터로 ko/ja 렌더링 + 자기 점검
  python canvas.py create <채널ID>      # 봇 소유 채널 캔버스 생성, canvas_id 출력
  python canvas.py                      # attendance.csv로 렌더링해서 CANVAS_ID에 반영

환경변수: SLACK_BOT_TOKEN(canvases:write), CANVAS_ID, CANVAS_LANG(ja|ko, 기본 ja)
  APP_HOME_URL(선택): 캔버스 아래 'App Home 열기' 링크. 캔버스엔 버튼을 못 넣어서 등록·변경은 App Home에서
    예) slack://app?team=T0ANVH4SRHS&id=<앱 ID A…>&tab=home
slots.json: 칸 번호 기준. cols = 신발장 가로 칸 수, 빈 칸은 "user": null
  {"cols": 3, "slots": {"1": {"user": "U01AAA", "name": "김OO"}, "2": {"user": null}, ...}}
  (App Home 등록 버튼이 user/name을 바꾸고, 나중에 칸별 카메라 영역도 여기에 붙인다)
attendance.csv: time,user,event   예) 2026-10-21T09:12:00,U01AAA,in
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
    """오늘 사람별 마지막 in/out, 첫 입장 시각, 마지막 퇴실 시각."""
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
    """python.org판 맥 파이썬은 인증서 목록이 비어 있어 Slack 접속(HTTPS)이 실패함 → certifi 목록을 쓰게 함."""
    try:
        import certifi
    except ImportError:
        return  # 라즈베리파이처럼 시스템 인증서가 있는 곳에선 없어도 됨
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def write_json(path, data):
    tmp = path + ".tmp"  # 봇과 calibrate.py가 같은 파일을 쓰므로 반쯤 쓴 파일이 읽히지 않게 바꿔치기
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


SPACE = "　"  # 전각 공백: Slack 표에서 지워지지 않고 안 보임 (점자 공백 U+2800은 점으로 보여서 못 씀)


def name_width(name):
    """이름표 폭 어림값(전각 글자 = 1). 한·중·일 글자 1, 그 밖(영문·숫자·공백) 0.6."""
    # ponytail: 글자별 어림값이라 W·M 같은 넓은 영문자가 많으면 그 열만 살짝 넓어짐. 정확히 하려면 글꼴로 실제 폭을 재야 함
    return sum(1 if ord(c) >= 0x2E80 else 0.6 for c in name)


# 칸 크기 고정: 이름표는 칸 안에서 한 줄 폭 LINE_W(전각 5 = 영문 약 8글자) × NAME_LINES줄로 접어 넣고,
# 폭 맞춤 줄은 늘 PAD칸. 등록 전후·이름 길이와 상관없이 모든 칸이 같은 크기 (App Home 표와 캔버스 표가 같이 씀)
LINE_W, NAME_LINES = 5, 2
PAD = LINE_W + 1


def name_lines(slot):
    """칸 안 이름표 NAME_LINES줄: 띄어쓰기에서 먼저 나누고, 한 단어가 한 줄보다 길면 글자 단위로. 넘치면 마지막 줄 끝을 …"""
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
    """slots.json → 칸 번호 순 [(번호, user 또는 None)]."""
    return [(int(n), v.get("user")) for n, v in sorted(rack["slots"].items(), key=lambda x: int(x[0]))]


def render(rack, events, lang, now, home_url=None):
    """App Home(bot.py build_home)과 같은 화면을 캔버스 마크다운으로. 버튼은 캔버스에 못 넣어서 home_url 링크로 대신함.
    rack: slots.json 내용, events: [(datetime, user, "in"|"out")] 시간순."""
    s = T[lang]
    slots = owners(rack)
    members = [u for _, u in slots if u]
    last_kind, _, _ = today_state(events, now)
    here = sum(last_kind.get(u) == "in" for u in members)
    date = f"{now.month}/{now.day} ({s['days'][now.weekday()]})"

    # App Home 표와 같은 칸(번호 / 이름표 NAME_LINES줄 / 폭 맞춤 전각 공백 줄). 마크다운 표 칸은 줄바꿈 대신 <br>
    pad = SPACE * PAD

    def cell(n, u):
        # 모두 굵게(첫 줄 머리 줄과 같게, 재실 구분은 ✅만). 전각 공백만 있는 줄은 ** 가 안 먹혀서 그대로
        b = lambda t: t if t == SPACE else f"**{t}**"
        if not u:
            return "<br>".join([b(f"{n:02d}")] + [SPACE] * NAME_LINES + [pad])
        lines = [re.sub(r"([\\|*_~`<])", r"\\\1", l) for l in name_lines(rack["slots"][str(n)])]  # | * < 등이 표를 깨지 않게
        return "<br>".join([b(f"{n:02d}") + (" ✅" if last_kind.get(u) == "in" else "")] + [b(l) for l in lines] + [pad])

    # 제목은 캔버스 자체 제목이 대신하므로 본문에 넣지 않음
    out = [s["summary"].format(date=date, t=now.strftime("%H:%M"), n=here, total=len(members)), ""]
    # 신발장 표: App Home처럼 한 줄에 가로 cols칸. 마크다운 표는 머리 줄이 필요해서 첫 줄을 머리 줄로 씀
    cols = rack.get("cols", 3)
    for r in range(0, len(slots), cols):
        row = [cell(n, u) for n, u in slots[r:r + cols]]
        out.append("| " + " | ".join(row + [" "] * (cols - len(row))) + " |")  # 마지막 줄이 모자라면 빈 셀
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
    """main.py에서 출석/퇴실이 확정될 때마다 호출."""
    slack("canvases.edit", {
        "canvas_id": canvas_id or os.environ["CANVAS_ID"],
        "changes": [{"operation": "replace", "document_content": doc(md)}],  # section_id 없으면 전체 교체
    })


def load(here=".", day=None):
    """slots.json 전체 + attendance.csv에서 day(기본 오늘) 줄만. 화면·판단은 모두 오늘 상태만 쓰므로,
    기록이 몇 년 쌓여도 메모리가 늘지 않게 다른 날 줄은 리스트에 담지 않고 흘려보냄 (1년치 전부 담으면 약 +4.6MB)."""
    with open(os.path.join(here, "slots.json"), encoding="utf-8") as f:
        rack = json.load(f)
    path = os.path.join(here, "attendance.csv")
    prefix = (day or date.today()).isoformat()  # time 열은 "2026-10-28T09:12:00" 형식
    events = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            events = [(datetime.fromisoformat(r["time"]), r["user"], r["event"])
                      for r in csv.DictReader(f) if r["time"].startswith(prefix)]
    return rack, sorted(events)


def demo():
    # bot.py sample()과 같은 신발장: 1번 김민준(재실), 2번 이서연(퇴실), 4번 최유찬, 3·5·6번 빈 칸
    rack = {"cols": 3, "slots": {"1": {"user": "UA", "name": "김민준"}, "2": {"user": "UB", "name": "이서연"},
                                 "3": {"user": None}, "4": {"user": "UC", "name": "최유찬"},
                                 "5": {"user": None}, "6": {"user": None}}}
    d = lambda h, m: datetime(2026, 10, 26, h, m)
    ev = [(datetime(2026, 10, 25, 9, 0), "UC", "in"),  # 어제 들어오고 안 나간 기록은 오늘 재실이 아님
          (d(8, 40), "UA", "in"), (d(9, 0), "UB", "in"), (d(12, 10), "UB", "out")]
    now = d(14, 30)
    url = "slack://app?team=T1&id=A1&tab=home"
    ko = render(rack, ev, "ko", now, url)
    ja = render(rack, ev, "ja", now)
    print(ko, "\n\n" + "=" * 40 + "\n\n" + ja)

    assert ja.splitlines()[0] == "10/26 (月) 14:30 時点 · 在室 **1**/3人 · ✅ = 在室"
    S, pad = SPACE, SPACE * PAD  # 칸 폭은 이름과 상관없이 고정
    assert [l for l in ja.splitlines() if l.startswith("|")] == [
        f"| **01** ✅<br>**김민준**<br>{S}<br>{pad} | **02**<br>**이서연**<br>{S}<br>{pad} | **03**<br>{S}<br>{S}<br>{pad} |",
        "| --- | --- | --- |",
        f"| **04**<br>**최유찬**<br>{S}<br>{pad} | **05**<br>{S}<br>{S}<br>{pad} | **06**<br>{S}<br>{S}<br>{pad} |",
    ]  # App Home 표와 같은 4줄 칸
    assert "[出席アプリのホーム]" not in ja and ko.endswith(f"[출석 앱 홈]({url})에서")
    assert "재실 **1**/3명" in ko
    five = render({"cols": 3, "slots": {str(n): {"user": None} for n in range(1, 6)}}, [], "ja", now)
    assert five.endswith(f"| **04**<br>{S}<br>{S}<br>{pad} | **05**<br>{S}<br>{S}<br>{pad} |   |")  # 모자란 마지막 줄은 빈 셀
    odd = render({"cols": 1, "slots": {"1": {"user": "UA", "name": "a|*<br"}}}, [], "ja", now)
    assert "| **01**<br>**a\\|\\*\\<br**<br>" in odd  # 이름표의 | * < 는 이스케이프 (이름표로 줄바꿈을 끼워 넣지 못하게)
    # 칸 안 이름표: 띄어쓰기에서 줄바꿈, 긴 단어는 글자 단위, 2줄 넘으면 …, 모든 줄이 한 줄 폭 안
    assert name_lines({"name": "Choi Youchan"}) == ["Choi", "Youchan"]
    assert name_lines({"name": "山田 太郎"}) == ["山田 太郎", S] and name_lines({"name": "윤도현"}) == ["윤도현", S]
    assert name_lines({"name": "Alexander Smith"}) == ["Alexande", "r Smith"]
    assert name_lines({"name": "가나다라마바사아자차카타파하"}) == ["가나다라마", "바사아자…"]
    for nm in ("Choi Youchan", "Alexander Smith-Johnson", "가나다라마바사아자차카타파하", "a b c d e f g h i j k", "?"):
        assert all(name_width(l) <= LINE_W for l in name_lines({"name": nm})) and len(name_lines({"name": nm})) == NAME_LINES, nm

    # load: attendance.csv에서 지정한 날(기본 오늘) 줄만 읽음
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
