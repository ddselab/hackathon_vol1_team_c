# 研究室在室Bot（SlackBot 担当分）

靴箱カメラの判定結果を受け取り、次の 2 つを行う SlackBot です。

1. 指定チャンネルに「○○ さんが出席しました／退席しました（日時）」と投稿する
2. 本人の Slack ステータスを「🟢 出席中（研究室）」「⚪ 退席中」に切り替える

あわせて、`/lab` コマンドと App Home で現在の在室状況を誰でも確認でき、canvas 班が画像を作るための在室データを JSON で取り出せます。

```
 [カメラ班]                      [このBot]                         [Slack]
 靴箱を撮影・判定  ──POST──▶  /api/presence  ──状態が変わったら──▶  チャンネル投稿
                                    │                              本人のステータス変更
                                    ▼
                               SQLite（現在の状態・履歴）
                                    │
 [canvas班]  ◀──GET── /api/presence, /api/history（JSON）
```

---

## 最重要ポイント：ステータス変更には「本人の許可」が必要

Slack の仕様上、**ボットのトークン（xoxb-）では他人のステータスは変えられません**。ステータス変更 API（`users.profile.set`）は**ユーザートークン（xoxp-）**が必須で、他人のプロフィールを変えられるのは有料プランの管理者だけです。

そこでこの Bot では、各メンバーが `/lab connect` から一度だけ「自分のステータスを変更してよい」と許可し、そのとき発行される本人用トークンを保存して使います。

- 許可していないメンバーも、チャンネルへの出欠投稿は通常どおり行われます（ステータス変更だけスキップ）
- 本人が手動で「会議中」などを設定しているときは上書きしません（`RESPECT_CUSTOM_STATUS=true`）
- 状態が変わったときだけ API を呼ぶので、Slack のレート制限（同じ人のプロフィール更新は 1 分 10 回まで）にもかかりません

---

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `app.py` | 起動スクリプト（HTTP サーバーと Slack 接続を同時に起動） |
| `slack_app.py` | `/lab` コマンド、App Home（Bolt for Python） |
| `presence.py` | 状態変化時のチャンネル投稿・ステータス変更 |
| `web.py` | カメラ班・canvas 班向け API、ステータス許可（OAuth）の受け口 |
| `store.py` | SQLite（メンバー、在室状態、出入り履歴、ユーザートークン） |
| `config.py` | `.env` の読み込み |
| `camera_simulator.py` | カメラ無しで動作確認するためのスクリプト（カメラ班の送信サンプル） |
| `manifest.yml` | Slack App 作成用の設定 |

---

## セットアップ

### 1. Slack App を作る

1. https://api.slack.com/apps →「Create New App」→「From an app manifest」
2. ワークスペースを選び、`manifest.yml` の中身を貼り付ける
   （`redirect_urls` は手順 4 で決まる URL に後で書き換えれば OK）
3. 「Install to Workspace」でインストール

### 2. トークンを集めて `.env` を作る

```bash
cp .env.example .env
```

| 変数 | 取得場所 |
| --- | --- |
| `SLACK_BOT_TOKEN` | OAuth & Permissions → Bot User OAuth Token（`xoxb-`） |
| `SLACK_APP_TOKEN` | Basic Information → App-Level Tokens → Generate（scope は `connections:write`）（`xapp-`） |
| `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` | Basic Information → App Credentials |
| `NOTIFY_CHANNEL_ID` | 投稿先チャンネル名を右クリック →「リンクをコピー」の末尾 `C...` |
| `CAMERA_API_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` で生成し、カメラ班・canvas 班と共有 |

投稿先チャンネルでは `/invite @lab-presence` でボットを招待してください（招待しないと投稿できません）。

### 3. 起動

```bash
pip install -r requirements.txt
python app.py
```

Slack とは Socket Mode で接続するので、**チャンネル投稿・`/lab` コマンドだけなら公開 URL は不要**です。

### 4. ステータス変更の許可用 URL（メンバー登録のときだけ必要）

`/lab connect` の許可画面は Slack から https でアクセスされる必要があります。研究室の PC で動かす場合は ngrok などを使います。

```bash
ngrok http 3000
```

表示された `https://xxxx.ngrok-free.app` を `.env` の `PUBLIC_BASE_URL` に設定し、Slack App の設定画面 → OAuth & Permissions → Redirect URLs に `https://xxxx.ngrok-free.app/slack/oauth_redirect` を追加して、Bot を再起動します。全員の許可が終われば ngrok は止めて構いません（保存したトークンで動き続けます）。

---

## メンバーの使い方

1. `/lab register A3` … 自分の靴箱番号を登録（番号はカメラ班と決めた ID）
2. `/lab connect` … ボタンを押して許可（ステータスも自動で変えたい人だけ）
3. あとは靴を入れる／出すだけ

| コマンド | 内容 |
| --- | --- |
| `/lab` | 在室状況を表示（自分にだけ見える） |
| `/lab register 番号` | 靴箱を登録・変更 |
| `/lab unregister` | 登録解除 |
| `/lab connect` / `/lab disconnect` | ステータス自動変更の許可／取り消し |
| `/lab help` | 使い方 |

Bot の「ホーム」タブを開くと、在室状況が一覧で見られます。

---

## カメラ班向け API 仕様

すべてのリクエストにヘッダー `X-API-Key: <CAMERA_API_KEY>` を付けてください。

### `POST /api/presence`

1 件ずつ送る場合:

```json
{"slot": "A3", "present": true}
```

靴箱全体をまとめて送る場合（1 フレームの判定結果をそのまま送れます）:

```json
{"slots": {"A1": true, "A2": false, "A3": true}}
```

- `present`: 靴がある＝`true`（出席）、ない＝`false`（退席）
- 同じ状態を何度送っても、**状態が変わったときだけ** Slack に通知します。定期送信（例: 10 秒ごと）でも変化時だけの送信でも OK
- 靴箱番号は大文字小文字を区別しません

レスポンス例:

```json
{"ok": true, "results": {"A1": "no_change", "A2": "changed", "A3": "unknown_slot"}}
```

| 結果 | 意味 |
| --- | --- |
| `changed` | 状態が変わった（チャンネル投稿＋ステータス変更） |
| `initial` | 登録後はじめての報告（ステータスだけ合わせ、投稿はしない） |
| `no_change` | 変化なし |
| `unknown_slot` | `/lab register` されていない靴箱 |
| `invalid_value` | `present` が true/false ではない |

**お願い:** 人が靴を出し入れする瞬間の誤判定（チャタリング）は、カメラ側で「同じ判定が数回続いたら確定」としてから送ってもらえると、チャンネルに出席→退席→出席…と連投されずに済みます。

Python から送る例は `camera_simulator.py` の `call()` 関数を参照してください。

---

## canvas 班向け API 仕様

ヘッダー `X-API-Key` が必要です。

### `GET /api/presence` … 現在の在室状況

```json
{
  "ok": true,
  "generated_at": "2026-10-26T14:05:00+09:00",
  "members": [
    {"name": "山田 太郎", "slack_user_id": "U0123", "slot": "A3", "status": "出席中", "since": "2026-10-26T09:12:30+09:00"}
  ]
}
```

`status` は `出席中` / `退席中` / `未確認` のいずれかです。

### `GET /api/history?date=2026-10-26` … その日の出入り履歴

```json
{"ok": true, "date": "2026-10-26", "events": [{"name": "山田 太郎", "slot": "A3", "status": "出席", "at": "2026-10-26T09:12:30+09:00"}]}
```

`date` を省略すると今日の履歴を返します。

---

## チームで決めておくこと

- **Slack App は 1 つにまとめるのがおすすめ。** 同じ App のトークンで Socket Mode 接続を複数のプログラムから張ると、ボタン操作などのイベントがどちらか一方にランダムに届いてしまいます。canvas 班のボタン処理は `slack_app.py` に `@app.action(...)` を追加する形で同じプロセスに載せるのが確実です（別プロセスにしたい場合は Slack App を分けてください）。
- canvas 班が画像を Slack にアップロードする場合、旧 `files.upload` は廃止済みなので `slack_sdk` の `files_upload_v2` を使ってください。ボットスコープに `files:write` の追加が必要です。
- 靴箱番号（`A1`, `A2`, …）の付け方はカメラ班と統一してください。

---

## カメラ無しでの動作確認

Bot を起動した状態で別のターミナルから:

```bash
python camera_simulator.py A3 in     # 出席
python camera_simulator.py A3 out    # 退席
python camera_simulator.py status    # 在室状況(JSON)
```

先に Slack で `/lab register A3` をしておくと、チャンネル投稿とステータス変更を確認できます（登録直後の 1 回目は `initial` になり投稿されないので、in → out → in の順で試してください）。

---

## うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| チャンネルに投稿されない | ボットをチャンネルに招待したか／`NOTIFY_CHANNEL_ID` がチャンネル名ではなく `C...` の ID か |
| `/lab` が「dispatch_failed」になる | `python app.py` が起動しているか／`SLACK_APP_TOKEN` が正しいか |
| ステータスが変わらない | 本人が `/lab connect` で許可したか／手動ステータスを設定中でないか（ログに理由が出ます） |
| 許可画面で redirect_uri エラー | Slack App の Redirect URLs と `PUBLIC_BASE_URL` が一致しているか（ngrok の URL は再起動で変わります） |
| カメラから 401 が返る | `X-API-Key` が `.env` の `CAMERA_API_KEY` と一致しているか |
