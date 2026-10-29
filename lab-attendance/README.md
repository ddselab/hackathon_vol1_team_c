# lab-attendance（下駄箱で出席管理する Slack Bot）

研究室の下駄箱をカメラで見て、靴が入っている人を「在室」として Slack の App Home に表示する Bot。
カメラで撮った写真を特定のチャンネルに投稿することもできる。

```
カメラ側 ──POST /event  {"slot":12,"event":"in"}──▶ bot.py ──▶ attendance.csv に記録
         ──POST /events {"slots":{"1":"in",…}}──▶
         ──POST /photo（JPEG）───────────────────▶        ──▶ PHOTO_CHANNEL に投稿
                                                    │
                                                    └──▶ Slack の App Home / キャンバスを描き直す
```

## ファイル

| ファイル | 内容 |
| --- | --- |
| `bot.py` | Bot 本体。Slack の画面と、カメラからのリクエストを受ける HTTP サーバー |
| `manifest.json` | Slack アプリの設定。アプリを作るときに貼り付ける |
| `slots.json` | 下駄箱のデータ（マスの数・並び・誰のマスか） |
| `attendance.csv` | 入室・退室の記録。最初のイベントを受け取ったときに作られる（コミットしない） |
| `requirements.txt` | Python の依存パッケージ |
| `.env.example` | 環境変数ファイルのひな形。コピーして `.env` を作る（`.env` はコミットしない） |
| `lab-attendance.service` | Raspberry Pi で常時実行するための systemd の設定 |

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
python -m pip install -r requirements.txt

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

## Raspberry Pi で常時実行する

[pi-zero-playground](https://github.com/k4nkan/pi-zero-playground) と同じ Raspberry Pi（Pi Zero 2 W、Raspberry Pi OS Lite 64bit、GUI なし）で動かす前提で書いている。
Bot は Socket Mode で Slack に自分から接続するので、ルーターのポート開放や固定 IP はいらない。Wi-Fi（2.4GHz）でインターネットにつながっていれば動く。

以下の `$PI` は自分の Pi の `ユーザー名@ホスト名` に置き換える（例: `pi@raspberrypi.local`）。

### 1. SSH でつながるようにする（最初の 1 回）

パスワードを毎回聞かれないように、PC の公開鍵を Pi に登録しておく。

```sh
# Mac / Linux / Git Bash
ssh-keygen -t ed25519            # 鍵がまだない場合だけ。質問はすべて Enter でよい
ssh-copy-id "$PI"
ssh "$PI" 'echo OK'              # パスワードを聞かれずに OK と出れば成功
```

```powershell
# Windows の PowerShell（ssh-copy-id がないので代わりにこう書く）
$PI = "pi@raspberrypi.local"
ssh-keygen -t ed25519
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | ssh $PI "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
ssh $PI "echo OK"
```

### 2. ファイルを Pi に送る

リポジトリのルートで実行する。Pi 側には `~/lab-attendance/` を作ってそこに置く。

```sh
# Mac / Linux（rsync）
ssh "$PI" 'mkdir -p ~/lab-attendance'
rsync -av --exclude slots.json --exclude attendance.csv --exclude .env --exclude __pycache__ \
  lab-attendance/ "$PI":lab-attendance/
```

```powershell
# Windows の PowerShell（scp。Windows 10 以降なら最初から入っている）
ssh $PI "mkdir -p ~/lab-attendance"
scp lab-attendance/bot.py lab-attendance/canvas.py lab-attendance/requirements.txt `
    lab-attendance/.env.example lab-attendance/lab-attendance.service "${PI}:lab-attendance/"
```

`canvas.py` はまだリポジトリにないので、追加されるまでは scp がこのファイルだけエラーになる（ほかのファイルは送られる）。

`slots.json` は **最初の 1 回だけ** 送る（または Pi 上で `python bot.py init 32 8` を実行して作る）。

```sh
scp lab-attendance/slots.json "$PI":lab-attendance/
```

> [!CAUTION]
> 運用を始めると、Slack での登録内容は Pi 上の `slots.json` に、出席記録は Pi 上の `attendance.csv` に保存される。PC からこの 2 つを送ると **Pi の登録や記録が上書きされて消える** ので、コードを更新するときは `.py` ファイルだけを送ること。
> 逆に Pi のデータを PC に取ってくるときは `scp "$PI":lab-attendance/attendance.csv .` のようにする。

### 3. Pi に依存パッケージを入れる

venv は作らず、OS の Python（3.13）にそのまま入れる。

```sh
ssh "$PI"
cd ~/lab-attendance
sudo apt install -y python3-pip                                   # Lite には pip が入っていないことがある
pip install --user --break-system-packages -r requirements.txt   # ~/.local に入る
```

> Raspberry Pi OS（Debian 13）は、apt 以外で OS の Python にパッケージを入れようとすると `externally-managed-environment` というエラーで止める。
> `--break-system-packages` はそれを解除するオプション。`--user` を付けているので、入るのは自分のホーム（`~/.local`）だけで、apt のパッケージは上書きしない。
> ピースカメラは別の Python 3.12 の venv（`~/hand-landmarker/.venv`）で動いているので、こちらの影響は受けない。

### 4. 環境変数を設定する

トークンは `.env` ファイルに書き、自分だけが読めるようにする。

```sh
cp .env.example .env
nano .env          # SLACK_BOT_TOKEN などを書く。Ctrl+O → Enter で保存、Ctrl+X で終了
chmod 600 .env     # 自分以外が読めないようにする
```

`.env` の中身はこういう形にする。systemd が読むので、`export` や引用符（`"`）は付けない。

```ini
SLACK_BOT_TOKEN=xoxb-1234...
SLACK_APP_TOKEN=xapp-1-ABCD...
PHOTO_CHANNEL=C0123ABCD
```

まず手で起動して、エラーが出ないか確かめる（止めるときは Ctrl+C）。

```sh
set -a; . ./.env; set +a     # .env の中身を環境変数として読み込む
python3 bot.py
```

### 5. 常時実行する（systemd）

`lab-attendance.service` を systemd に登録すると、Pi の電源を入れたら自動で起動し、落ちても 10 秒後に自動で再起動する。

```sh
mkdir -p ~/.config/systemd/user
cp ~/lab-attendance/lab-attendance.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lab-attendance   # 今すぐ起動し、次回以降も自動で起動する
sudo loginctl enable-linger "$USER"            # SSH でログインしていなくても動かし続ける（最初の 1 回だけ）
```

> `enable-linger` を忘れると、SSH を切った瞬間や再起動後に Bot が止まる。

よく使うコマンド:

| やりたいこと | コマンド |
| --- | --- |
| 動いているか確認 | `systemctl --user status lab-attendance` |
| ログをリアルタイムで見る（Ctrl+C で抜ける） | `journalctl --user -u lab-attendance -f` |
| 再起動（コードや `.env` を変えたあと） | `systemctl --user restart lab-attendance` |
| 止める | `systemctl --user stop lab-attendance` |
| 自動起動をやめる | `systemctl --user disable --now lab-attendance` |

### コードを更新するとき

PC で `bot.py` を直したら、送って再起動するだけでよい。

```sh
scp lab-attendance/bot.py "$PI":lab-attendance/
ssh "$PI" 'systemctl --user restart lab-attendance'
```

### Pi で動かすときの注意

- Pi Zero 2 W はメモリが 512MB しかない。Bot 自体は軽いが、同じ Pi でピースカメラ（MediaPipe）も動かすと余裕が少ないので、`free -h` で空きを確認しておく。
- Pi には時計の電池がないので、時刻はインターネット（NTP）に合わせている。出席の時刻がずれていたら `timedatectl` で `System clock synchronized: yes` になっているか確認する。
- 急に電源を抜くと microSD が壊れることがある。止めるときは `sudo poweroff` してから抜く。

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

### `POST /events` — 複数のマスをまとめて送る

1〜32 番のマスの状態を、1 回のリクエストでまとめて送れる。カメラが全マスを一度に判定するならこちらが便利。

| キー | 必須 | 内容 |
| --- | --- | --- |
| `slots` | ○ | `{"マス番号": "in" か "out"}` の形のオブジェクト。全マス分でも、変わったマスだけでもよい |
| `time` | | ISO 8601 形式の時刻。全マス共通。省略すると受け取った時刻 |

- 前の状態と同じマスや、持ち主がいないマスは記録されないだけでエラーにはならない。なので **毎回 32 マス全部を送ってよい**（記録されるのは変化したマスだけ）。
- 値に `in` / `out` 以外が 1 つでも混ざっていると、400 を返して **どのマスも記録しない**。
- 何マス変わっても、Slack の表の描き直しは 1 回だけ。

```sh
curl -X POST http://127.0.0.1:8080/events \
  -H "Content-Type: application/json" \
  -d '{"slots": {"1": "in", "2": "out", "3": "in", "32": "out"}}'
```

```python
import json, urllib.request

def send_states(occupied, total=32):
    """occupied = 靴が入っているマス番号の集合。1〜total 番の状態をまとめて送る。"""
    slots = {str(n): "in" if n in occupied else "out" for n in range(1, total + 1)}
    req = urllib.request.Request("http://127.0.0.1:8080/events",
                                 data=json.dumps({"slots": slots}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)

send_states({1, 3, 12})
```

応答の例（`recorded` は記録したマス番号、`notes` は記録しなかったマスとその理由）:

```json
{
  "ok": true,
  "recorded": [1, 12],
  "notes": {
    "2": "すでに同じ状態なので記録しません",
    "3": "3番のマスは持ち主がいないので記録しません"
  }
}
```

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
| 400 | 送った内容の形式がおかしい（JSON が壊れている、`/events` の値に in/out 以外がある、JPEG ではない、サイズが 0 か 20MB 超など） |
| 404 | そのパスはない |
| 502 | Slack への投稿に失敗した。`not_in_channel` なら Bot をチャンネルに招待する |
| 503 | `PHOTO_CHANNEL` が設定されていない |

エラーのときは、どの場合も `{"ok": false, "error": "理由"}` が返る。
