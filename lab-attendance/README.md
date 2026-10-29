# lab-attendance（下駄箱で出席管理する Slack Bot）

研究室の下駄箱をカメラで見て、靴が入っている人を「在室」として Slack の App Home に表示する Bot。
カメラで撮った写真を特定のチャンネルに投稿することもできる。

```
カメラ側 ──POST /event {"slot":12,"event":"in"}──▶ bot.py ──▶ attendance.csv に記録
         ──POST /photo（JPEG）──────────────────▶        ──▶ PHOTO_CHANNEL に投稿
                                                    │
                                                    └──▶ Slack の App Home / キャンバスを描き直す
```

## ファイル

| ファイル | 内容 |
| --- | --- |
| `bot.py` | Bot 本体。Slack の画面と、カメラからのリクエストを受ける HTTP サーバー |
| `manifest.json` | Slack アプリの設定。アプリを作るときに貼り付ける |
| `slots.json` | 下駄箱のデータ（マスの数・並び・誰のマスか） |
| `attendance.csv` | 入室・退室の記録。最初のイベントを受け取ったときに作られる |

> [!WARNING]
> `bot.py` は `canvas.py`（と、`preview` で使う `board.py`）を import しているが、まだリポジトリに入っていない。これがないと起動できない。

## Slack アプリの準備

1. <https://api.slack.com/apps> → **Create New App** → **From a manifest** で `manifest.json` の中身を貼り付ける
2. **OAuth & Permissions** → **Install to Workspace** → `xoxb-` で始まる **Bot User OAuth Token** をコピー
3. **Basic Information** → **App-Level Tokens** → スコープ `connections:write` でトークンを作り、`xapp-` で始まるトークンをコピー
4. 写真を投稿するなら、投稿先のチャンネルで `/invite @getabako` を実行して Bot を招待する

## 環境変数

| 名前 | 必須 | 内容 |
| --- | --- | --- |
| `SLACK_BOT_TOKEN` | ○ | `xoxb-...`（上の手順 2） |
| `SLACK_APP_TOKEN` | ○ | `xapp-...`（上の手順 3）。Socket Mode に使う |
| `PHOTO_CHANNEL` | | 写真の投稿先チャンネル ID（`C0123ABCD` のような形）。チャンネル名を右クリック → 「チャンネル詳細を表示」の一番下にある |
| `CANVAS_ID` | | 設定すると、登録が変わったときにキャンバスも更新する |
| `CANVAS_LANG` | | キャンバスの言語。`ja`（既定）か `ko` |
| `EVENT_PORT` | | HTTP サーバーのポート。既定は `8080` |

トークンはコードに書かず、コミットもしないこと。

## 使い方

```sh
python -m pip install slack_bolt

python bot.py init 32 8   # slots.json がないときだけ: 空の下駄箱を作る（マス数, 横のマス数）
python bot.py demo        # 自己テスト。OK と出れば成功
python bot.py preview     # 画面例を Block Kit Builder の URL で出す
python bot.py             # 起動
```

PowerShell の場合:

```powershell
$env:SLACK_BOT_TOKEN = "xoxb-..."
$env:SLACK_APP_TOKEN = "xapp-..."
$env:PHOTO_CHANNEL   = "C0123ABCD"
$env:PYTHONIOENCODING = "utf-8"   # Windows のターミナルで日本語の出力が文字化け・エラーになるのを防ぐ
python bot.py
```

### Slack での操作

Slack のサイドバーで「出席」アプリを開き、**ホーム** タブを見る。

- 下駄箱がない人: **登録する** → 空いている番号と名札を選ぶ
- 下駄箱がある人: **名札を変更** / **場所を移動** / **登録解除**
- 靴が入っている（在室中の）間は、移動・解除ができない
- 表では、在室中の人の名前が太字になり ✅ が付く

## カメラとの連携（HTTP API）

> [!IMPORTANT]
> HTTP サーバーは初期状態では **無効**。`bot.py` の `run()` の中にある次の行の先頭の `# ` を消すと有効になる。
>
> ```python
> # start_event_server(lambda: redraw_all(app.client), post_photo if os.environ.get("PHOTO_CHANNEL") else None)
> ```

サーバーは `127.0.0.1` でしか待ち受けないので、**カメラと Bot が同じ機器（Raspberry Pi）で動いている**ことが前提。

### `POST /event` — 靴が入った／出た

カメラ側は「何番のマスで変化があったか」だけを送る。誰のマスかは Bot が `slots.json` から調べる。
チャタリング対策（入室 10 秒・退室 2 分の維持など）はカメラ側で行い、確定した変化だけを送ること。

| キー | 必須 | 内容 |
| --- | --- | --- |
| `slot` | ○ | マスの番号（整数） |
| `event` | ○ | `"in"`（入った）か `"out"`（出た） |
| `time` | | ISO 8601 形式の時刻（例 `"2026-10-26T09:12:00"`）。省略すると受け取った時刻 |

```sh
curl -X POST http://127.0.0.1:8080/event \
  -H "Content-Type: application/json" \
  -d '{"slot": 12, "event": "in"}'
```

```python
import json, urllib.request

def send_event(slot, kind):
    body = json.dumps({"slot": slot, "event": kind}).encode()
    req = urllib.request.Request("http://127.0.0.1:8080/event", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)

send_event(12, "in")
```

応答の例:

```json
{"ok": true, "recorded": true, "note": null}
{"ok": true, "recorded": false, "note": "12番のマスは持ち主がいないので記録しません"}
```

`recorded: false` はエラーではなく、「持ち主がいない」「すでに同じ状態」などの理由で記録しなかったという意味。理由は `note` に入る。

### `GET /slots` — マスの配置と使用中のマス

```sh
curl http://127.0.0.1:8080/slots
```

```json
{"cols": 8, "rows": 4, "used": [1, 3, 12]}
```

名前や Slack ID は返さない。

### `POST /photo` — 写真をチャンネルに投稿

本文には **JPEG ファイルをそのまま** 入れる（JSON や multipart にはしない）。最大 20MB。
`PHOTO_CHANNEL` に「📸 10/29 (水) 14:30」というコメント付きで投稿される。

```sh
curl -X POST http://127.0.0.1:8080/photo \
  -H "Content-Type: image/jpeg" \
  --data-binary @photo.jpg
```

> `-d` ではなく `--data-binary` を使うこと（`-d` だと改行が消えて画像が壊れる）。
> Windows の PowerShell では `curl` が別のコマンドになっているので `curl.exe` と書く。

```python
import urllib.request
from pathlib import Path

def send_photo(path):
    req = urllib.request.Request("http://127.0.0.1:8080/photo", data=Path(path).read_bytes(),
                                 headers={"Content-Type": "image/jpeg"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status

send_photo("photo.jpg")
```

[pi-zero-playground](https://github.com/k4nkan/pi-zero-playground) のピースカメラ（`raspi/pi_peace.py`）から送るなら、`Recognition._run` の中の `print(f"saved: {path}", flush=True)` の直後に次を追加する。

```python
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:8080/photo", data=path.read_bytes(),
        headers={"Content-Type": "image/jpeg"}), timeout=30)
except Exception as error:
    print(f"slack post failed: {error}", file=sys.stderr, flush=True)
```

### 応答コード

| コード | 意味 |
| --- | --- |
| 200 | 成功（`/event` は `recorded` も確認すること） |
| 400 | 送った内容の形式がおかしい（JSON が壊れている、JPEG ではない、サイズが 0 か 20MB 超など） |
| 404 | そのパスはない |
| 502 | Slack への投稿に失敗した。`not_in_channel` なら Bot をチャンネルに招待する |
| 503 | `PHOTO_CHANNEL` が設定されていない |

エラーのときは、どの場合も `{"ok": false, "error": "理由"}` が返る。
