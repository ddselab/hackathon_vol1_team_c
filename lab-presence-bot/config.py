"""設定値の読み込み（.env または環境変数）"""
import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません（.env を確認してください）")
    return value


# --- Slack ---
SLACK_BOT_TOKEN = _required("SLACK_BOT_TOKEN")        # xoxb-...  投稿・コマンド用
SLACK_APP_TOKEN = _required("SLACK_APP_TOKEN")        # xapp-...  Socket Mode 用
NOTIFY_CHANNEL_ID = _required("NOTIFY_CHANNEL_ID")    # 出欠を投稿するチャンネルID (C...)

# ステータス変更の許可（OAuth）用。未設定でもチャンネル投稿だけなら動く
SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "").strip()
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")  # 例: https://xxxx.ngrok-free.app

# --- カメラ班・canvas班向け HTTP API ---
CAMERA_API_KEY = _required("CAMERA_API_KEY")
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "3000"))

# --- その他 ---
DB_PATH = os.environ.get("DB_PATH", "lab_presence.db")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tokyo")

# Slackステータスの表示内容
STATUS_IN_TEXT = os.environ.get("STATUS_IN_TEXT", "出席中（研究室）")
STATUS_IN_EMOJI = os.environ.get("STATUS_IN_EMOJI", ":large_green_circle:")
STATUS_OUT_TEXT = os.environ.get("STATUS_OUT_TEXT", "退席中")
STATUS_OUT_EMOJI = os.environ.get("STATUS_OUT_EMOJI", ":white_circle:")

# true のとき、本人が手動で設定したステータス（「会議中」など）は上書きしない
RESPECT_CUSTOM_STATUS = os.environ.get("RESPECT_CUSTOM_STATUS", "true").lower() == "true"
