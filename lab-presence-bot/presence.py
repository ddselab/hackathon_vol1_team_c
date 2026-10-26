"""
在室状態が変わったときの処理
  1. Slack チャンネルに「○○さんが出席/退席しました」と投稿（ボットトークン）
  2. 本人の Slack ステータスを変更（本人が許可したユーザートークン）
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config
import store

log = logging.getLogger(__name__)

bot_client = WebClient(token=config.SLACK_BOT_TOKEN)

# Slack への送信は 1 本のワーカーで順番に実行する
#   → カメラへの HTTP 応答を待たせない & 出席→退席の順序が入れ替わらない
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slack-sender")

_BOT_STATUS_TEXTS = {config.STATUS_IN_TEXT, config.STATUS_OUT_TEXT}
_TOKEN_DEAD_ERRORS = {"token_revoked", "invalid_auth", "account_inactive", "token_expired"}


def handle_report(slot: str, present: bool) -> str:
    """カメラからの報告 1 件を処理し、結果文字列を返す"""
    result, member = store.set_presence_if_changed(slot, present)

    if result == "changed":
        _worker.submit(_notify, member, present, post=True)
    elif result == "initial":
        # 初回は「状態が分かっただけ」なのでチャンネルには投稿せず、ステータスだけ合わせる
        _worker.submit(_notify, member, present, post=False)
    elif result == "unknown_slot":
        log.warning("未登録の靴箱 %s の報告を受信しました", slot)

    return result


def sync_status(user_id: str) -> None:
    """現在の在室状態を本人のステータスに反映（連携直後などに使う）"""
    member = store.get_member(user_id)
    if member and member["present"] is not None:
        _worker.submit(update_slack_status, user_id, bool(member["present"]))


def _notify(member: dict, present: bool, post: bool) -> None:
    try:
        if post:
            post_to_channel(member, present)
        update_slack_status(member["slack_user_id"], present)
    except Exception:  # ワーカーが落ちないように
        log.exception("Slack への通知中にエラーが発生しました")


def post_to_channel(member: dict, present: bool) -> None:
    at = datetime.fromisoformat(member["since"])
    when = at.strftime("%Y/%m/%d %H:%M")
    if present:
        icon, verb = ":large_green_circle:", "出席"
    else:
        icon, verb = ":white_circle:", "退席"

    text = f"{icon} {member['name']} さんが{verb}しました（{when}）"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{icon} *{member['name']}* さんが{verb}しました"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{when}　｜　靴箱 {member['slot']}"}]},
    ]
    try:
        bot_client.chat_postMessage(channel=config.NOTIFY_CHANNEL_ID, text=text, blocks=blocks)
    except SlackApiError as e:
        log.error("チャンネル投稿に失敗: %s（ボットをチャンネルに招待しましたか？）", e.response.get("error"))


def update_slack_status(user_id: str, present: bool) -> bool:
    token = store.get_user_token(user_id)
    if not token:
        log.info("%s はステータス変更を許可していないためスキップ（/lab connect で許可）", user_id)
        return False

    client = WebClient(token=token)
    try:
        if config.RESPECT_CUSTOM_STATUS:
            current = client.users_profile_get()["profile"].get("status_text", "")
            if current and current not in _BOT_STATUS_TEXTS:
                log.info("%s は手動ステータス「%s」を設定中のため上書きしません", user_id, current)
                return False

        if present:
            text, emoji = config.STATUS_IN_TEXT, config.STATUS_IN_EMOJI
        else:
            text, emoji = config.STATUS_OUT_TEXT, config.STATUS_OUT_EMOJI

        client.users_profile_set(
            profile={"status_text": text, "status_emoji": emoji, "status_expiration": 0}
        )
        log.info("%s のステータスを「%s」に変更しました", user_id, text)
        return True

    except SlackApiError as e:
        error = e.response.get("error")
        if error in _TOKEN_DEAD_ERRORS:
            store.delete_user_token(user_id)
            log.warning("%s のトークンが無効になったため削除しました（%s）", user_id, error)
        else:
            log.error("%s のステータス変更に失敗: %s", user_id, error)
        return False
