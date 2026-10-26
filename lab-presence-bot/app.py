"""起動スクリプト: python app.py"""
import logging
import threading

from slack_bolt.adapter.socket_mode import SocketModeHandler

import config
import store
from slack_app import app
from web import web


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    store.init_db()

    # カメラ班・canvas班・OAuth 用の HTTP サーバー（別スレッド）
    threading.Thread(
        target=lambda: web.run(host=config.HTTP_HOST, port=config.HTTP_PORT, use_reloader=False),
        daemon=True,
        name="http-server",
    ).start()
    logging.info("HTTP API: http://%s:%s", config.HTTP_HOST, config.HTTP_PORT)

    # Slack とは Socket Mode で接続（公開URL不要）
    SocketModeHandler(app, config.SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()
