"""
カメラ班の実装が無くても動作確認できるスクリプト（カメラ班の送信サンプルも兼ねる）

  python camera_simulator.py A3 in     # 靴箱A3に靴がある → 出席
  python camera_simulator.py A3 out    # 靴箱A3に靴がない → 退席
  python camera_simulator.py status    # 現在の在室状況(JSON)を表示
"""
import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()
BASE = os.environ.get("API_BASE_URL", f"http://127.0.0.1:{os.environ.get('HTTP_PORT', '3000')}")
KEY = os.environ["CAMERA_API_KEY"]


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        return {"http_status": e.code, "body": e.read().decode()}


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "status":
        print(json.dumps(call("GET", "/api/presence"), ensure_ascii=False, indent=2))
    elif len(sys.argv) == 3 and sys.argv[2] in ("in", "out"):
        result = call("POST", "/api/presence", {"slot": sys.argv[1], "present": sys.argv[2] == "in"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(__doc__)
