"""
HTTP サーバー（Flask）
  POST /api/presence        カメラ班 → ボット : 靴の有無を送る
  GET  /api/presence        canvas班 → ボット : 現在の在室状況(JSON)
  GET  /api/history?date=   canvas班 → ボット : 指定日の出入り履歴(JSON)
  GET  /slack/install       メンバーが Slack ステータス変更を許可する入口
  GET  /slack/oauth_redirect  Slack から戻ってくる先（ユーザートークンを保存）
"""
import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, request
from markupsafe import escape
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config
import presence
import store

log = logging.getLogger(__name__)
web = Flask(__name__)

USER_SCOPES = ["users.profile:read", "users.profile:write"]
REDIRECT_URI = f"{config.PUBLIC_BASE_URL}/slack/oauth_redirect"


# ---------- 認証（カメラ班・canvas班との共有キー） ----------

def _authorized() -> bool:
    given = request.headers.get("X-API-Key", "")
    return hmac.compare_digest(given, config.CAMERA_API_KEY)


@web.before_request
def _check_api_key():
    if request.path.startswith("/api/") and not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401


# ---------- カメラ班向け ----------

@web.post("/api/presence")
def receive_presence():
    """
    1件:   {"slot": "A3", "present": true}
    まとめ: {"slots": {"A1": true, "A2": false, "A3": true}}
    """
    data = request.get_json(silent=True) or {}
    if isinstance(data.get("slots"), dict):
        updates = list(data["slots"].items())
    elif "slot" in data:
        updates = [(data["slot"], data.get("present"))]
    else:
        return jsonify(ok=False, error="slot または slots が必要です"), 400

    results = {}
    for slot, present in updates:
        slot = str(slot).strip().upper()
        if present not in (True, False, 0, 1):
            results[slot] = "invalid_value"
            continue
        results[slot] = presence.handle_report(slot, bool(present))

    return jsonify(ok=True, results=results)


# ---------- canvas班向け ----------

@web.get("/api/presence")
def current_presence():
    members = [
        {
            "name": m["name"],
            "slack_user_id": m["slack_user_id"],
            "slot": m["slot"],
            "status": {1: "出席中", 0: "退席中"}.get(m["present"], "未確認"),
            "since": m["since"],
        }
        for m in store.list_members()
    ]
    return jsonify(ok=True, generated_at=store.now().isoformat(timespec="seconds"), members=members)


@web.get("/api/history")
def history():
    date_str = request.args.get("date") or store.now().strftime("%Y-%m-%d")
    events = [
        {"name": e["name"], "slot": e["slot"], "status": "出席" if e["present"] else "退席", "at": e["at"]}
        for e in store.events_on(date_str)
    ]
    return jsonify(ok=True, date=date_str, events=events)


# ---------- Slack ステータス変更の許可（OAuth） ----------

_states: dict[str, float] = {}  # state -> 有効期限（CSRF対策）


def _issue_state() -> str:
    now = time.time()
    for s, exp in list(_states.items()):
        if exp < now:
            _states.pop(s, None)
    state = secrets.token_urlsafe(24)
    _states[state] = now + 600  # 10分有効
    return state


def _consume_state(state: str) -> bool:
    exp = _states.pop(state, None)
    return exp is not None and exp >= time.time()


@web.get("/slack/install")
def install():
    if not (config.SLACK_CLIENT_ID and config.SLACK_CLIENT_SECRET and config.PUBLIC_BASE_URL):
        return "OAuth の設定（SLACK_CLIENT_ID / SLACK_CLIENT_SECRET / PUBLIC_BASE_URL）がありません", 500
    params = {
        "client_id": config.SLACK_CLIENT_ID,
        "user_scope": ",".join(USER_SCOPES),  # ボット権限ではなく「本人の権限」を求める
        "redirect_uri": REDIRECT_URI,
        "state": _issue_state(),
    }
    return redirect("https://slack.com/oauth/v2/authorize?" + urlencode(params))


@web.get("/slack/oauth_redirect")
def oauth_redirect():
    if request.args.get("error"):
        return _page("キャンセルされました", f"理由: {escape(request.args['error'])}"), 400
    if not _consume_state(request.args.get("state", "")):
        return _page("やり直してください", "リンクの有効期限が切れています。Slack で <code>/lab connect</code> からもう一度お試しください。"), 400

    try:
        resp = WebClient().oauth_v2_access(
            client_id=config.SLACK_CLIENT_ID,
            client_secret=config.SLACK_CLIENT_SECRET,
            code=request.args.get("code", ""),
            redirect_uri=REDIRECT_URI,
        )
    except SlackApiError as e:
        log.error("oauth.v2.access 失敗: %s", e.response.get("error"))
        return _page("連携に失敗しました", "時間をおいてもう一度お試しください。"), 500

    authed = resp.get("authed_user") or {}
    user_id, token = authed.get("id"), authed.get("access_token")
    if not (user_id and token):
        return _page("連携に失敗しました", "ユーザートークンを取得できませんでした。"), 500

    store.save_user_token(user_id, token)
    presence.sync_status(user_id)  # 今の在室状態をすぐ反映
    log.info("%s がステータス変更を許可しました", user_id)

    msg = "Slack に戻ってください。靴箱の状態に合わせてステータスが変わります。"
    if not store.get_member(user_id):
        msg += "<br>まだ靴箱を登録していない場合は <code>/lab register 靴箱番号</code> を実行してください。"
    return _page("連携が完了しました ✅", msg)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='ja'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title>"
        "<body style='font-family:sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;line-height:1.7'>"
        f"<h1 style='font-size:1.4rem'>{escape(title)}</h1><p>{body}</p></body></html>"
    )
