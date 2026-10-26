"""
Slack 側の操作（Bolt for Python / Socket Mode）
  /lab                  在室状況を表示（自分にだけ見える）
  /lab register A3      自分の靴箱番号を登録
  /lab unregister       登録解除
  /lab connect          Slackステータス自動変更を許可する
  /lab disconnect       許可を取り消す
  /lab help             使い方
  App Home タブ         在室状況を常時表示
"""
import logging
from datetime import datetime

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config
import store

log = logging.getLogger(__name__)

app = App(token=config.SLACK_BOT_TOKEN)

HELP_TEXT = (
    "*研究室在室Bot の使い方*\n"
    "• `/lab` … 今の在室状況を表示\n"
    "• `/lab register 靴箱番号` … 自分の靴箱を登録（例: `/lab register A3`）\n"
    "• `/lab unregister` … 登録を解除\n"
    "• `/lab connect` … Slackステータスの自動変更を許可\n"
    "• `/lab disconnect` … 自動変更の許可を取り消す"
)


# ---------- 表示用 ----------

def _fmt_since(iso: str | None) -> str:
    if not iso:
        return "未確認"
    return datetime.fromisoformat(iso).strftime("%m/%d %H:%M") + "〜"


def build_status_blocks() -> list[dict]:
    members = store.list_members()
    ins = [m for m in members if m["present"] == 1]
    outs = [m for m in members if m["present"] != 1]
    stamp = store.now().strftime("%Y/%m/%d %H:%M")

    def lines(ms: list[dict]) -> str:
        return "\n".join(f"• {m['name']}（{_fmt_since(m['since'])}）" for m in ms) or "（なし）"

    return [
        {"type": "header", "text": {"type": "plain_text", "text": "研究室の在室状況"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{stamp} 時点"}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f":large_green_circle: *出席中（{len(ins)}人）*\n{lines(ins)}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f":white_circle: *退席中（{len(outs)}人）*\n{lines(outs)}"}},
    ]


def _display_name(client: WebClient, user_id: str) -> str:
    try:
        profile = client.users_info(user=user_id)["user"]["profile"]
        return profile.get("display_name") or profile.get("real_name") or user_id
    except SlackApiError:
        return user_id


# ---------- スラッシュコマンド ----------

@app.command("/lab")
def lab_command(ack, command, respond, client):
    ack()  # 3秒以内に応答が必要なので最初に返す

    args = (command.get("text") or "").split()
    sub = args[0].lower() if args else "status"
    user_id = command["user_id"]

    if sub in ("status", "list"):
        respond(blocks=build_status_blocks(), text="研究室の在室状況")

    elif sub == "register":
        if len(args) < 2:
            respond("使い方: `/lab register 靴箱番号`（例: `/lab register A3`）")
            return
        slot = args[1].strip().upper()
        name = _display_name(client, user_id)
        try:
            store.register_member(user_id, slot, name)
        except ValueError as e:
            respond(f":warning: {e}")
            return
        msg = f":white_check_mark: 靴箱 *{slot}* を *{name}* さんとして登録しました。"
        if not store.get_user_token(user_id):
            msg += "\nSlackステータスも自動で変えたい場合は `/lab connect` で許可してください。"
        respond(msg)

    elif sub == "unregister":
        ok = store.unregister_member(user_id)
        respond("登録を解除しました。" if ok else "登録されていません。")

    elif sub == "connect":
        if not (config.SLACK_CLIENT_ID and config.PUBLIC_BASE_URL):
            respond(":warning: 管理者がステータス連携の設定（SLACK_CLIENT_ID / PUBLIC_BASE_URL）をしていません。")
            return
        respond(
            text="Slackステータスの自動変更を許可してください",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text":
                    "靴箱の状態に合わせて、あなたの Slack ステータスを自動で変更します。\n"
                    "下のボタンから許可してください（変更されるのはあなた自身のステータスだけです）。"}},
                {"type": "actions", "elements": [{
                    "type": "button", "action_id": "open_install_link", "style": "primary",
                    "text": {"type": "plain_text", "text": "ステータス変更を許可する"},
                    "url": f"{config.PUBLIC_BASE_URL}/slack/install",
                }]},
            ],
        )

    elif sub == "disconnect":
        token = store.get_user_token(user_id)
        if not token:
            respond("ステータス連携はされていません。")
            return
        try:
            WebClient(token=token).auth_revoke()
        except SlackApiError:
            pass  # すでに無効でも DB からは消す
        store.delete_user_token(user_id)
        respond("ステータスの自動変更を停止しました。")

    else:
        respond(HELP_TEXT)


@app.action("open_install_link")
def open_install_link(ack):
    # URL ボタンでも Slack から通知が来るので ack だけ返す（返さないと警告アイコンが出る）
    ack()


# ---------- App Home ----------

@app.event("app_home_opened")
def show_home(client, event):
    if event.get("tab") != "home":
        return
    blocks = build_status_blocks() + [
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": HELP_TEXT}},
    ]
    client.views_publish(user_id=event["user"], view={"type": "home", "blocks": blocks})
