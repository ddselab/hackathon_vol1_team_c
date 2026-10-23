# トークンの扱い

トークンは環境変数として扱う。

## ローカルの場合

| 環境        | 設定方法                           |
| ----------- | ---------------------------------- |
| Mac / Linux | `export SLACK_BOT_TOKEN="xoxb-...` |
| Windows     | `$env:SLACK_BOT_TOKEN = "xoxb-..." |

# CanvasのIDの調査

Canvasのリンクからコピーする。

1. チャンネルの **Canvas** タブを開く
1. 右上の **⋮** からリンクをコピー
1. `https://~slack.com/docs/T.../F...` の末尾 `F...` がCanvas ID

# Canvasを読み取る(HTTPS API)

### 手順

1. `file.info` APIでCanvas情報を取得
1. 戻り値の `url_private` に、Botトークン付きでアクセスして本文(html)を取得

```powershell
# 情報取得(url_privateのURLを次で利用します)
curl.exe -s -H "Authorization: Bearer $env:SLACK_BOT_TOKEN" "https://slack.com/api/file_info?file=FキャンバスID"
# 本文をhtmlで出力
curl.exe -s -H "Authorization: Bearer $env:SLACK_BOT_TOKEN" "url_privateの値(https://files.slack.com/files-pri/...)"
```

```bash
# 情報取得(url_privateのURLを次で利用します)
curl -s -H "Authorization: Bearer $env:SLACK_BOT_TOKEN" "https://slack.com/api/file_info?file=FキャンバスID"
# 本文をhtmlで出力
curl -s -H "Authorization: Bearer $env:SLACK_BOT_TOKEN" "url_privateの値(https://files.slack.com/files-pri/...)"
```

# Canvasに書き込む(HTTPS API)

`canvases.edit` を利用する

```powershell
$body = @{
  canvas_id = "キャンバスID"
  changes   = @(
    @{
      operation        = "insert_at_end"
      document_content = @{
        type     = "markdown"
        markdown = "Botからの書き込みテスト"
      }
    }
  )
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Uri "https://slack.com/api/canvases.edit" `
  -Method Post `
  -Headers @{ Authorization = "Bearer xoxb-自分のトークン" } `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

```bash
curl -s -X POST "https://slack.com/api/canvases.edit" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "canvas_id": "キャンバスID",
    "changes": [
      {
        "operation": "insert_at_end",
        "document_content": {
          "type": "markdown",
          "markdown": "Botからの書き込みテスト"
        }
      }
    ]
  }'
```

## operation 一覧

| operation                        | 内容                                                        |
| -------------------------------- | ----------------------------------------------------------- |
| `insert_at_end`                  | 末尾に追加                                                  |
| `insert_at_start`                | 先頭に追加                                                  |
| `insert_after` / `insert_before` | 指定セクションの前後に挿入(`section_id` が必要)             |
| `replace`                        | セクションを置き換え。`section_id` を省くとCanvas全体を置換 |
| `delete`                         | セクションを削除(`section_id` が必要)                       |
| `rename`                         | タイトル変更(`title_content` を使う)                        |

# pythonで行う場合

### 準備

```bash
pip install slack_sdk requests
```

### サンプルコード

```python
import os
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

TOKEN = os.environ["SLACK_BOT_TOKEN"]
CANVAS_ID = "F0CAHQA3K7H"

client = WebClient(token=TOKEN)


def read_canvas(canvas_id: str) -> str:
    """Canvasの本文(HTML)を取得する"""
    info = client.files_info(file=canvas_id)
    url = info["file"]["url_private"]
    res = requests.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
    res.raise_for_status()
    res.encoding = "utf-8"
    return res.text


def append_to_canvas(canvas_id: str, markdown: str):
    """Canvasの末尾にMarkdownを追記する"""
    return client.api_call(
        "canvases.edit",
        json={
            "canvas_id": canvas_id,
            "changes": [
                {
                    "operation": "insert_at_end",
                    "document_content": {
                        "type": "markdown",
                        "markdown": markdown,
                    },
                }
            ],
        },
    )


if __name__ == "__main__":
    try:
        print("--- 書き込み前 ---")
        print(read_canvas(CANVAS_ID))

        append_to_canvas(CANVAS_ID, "Pythonからの書き込みテスト")

        print("--- 書き込み後 ---")
        print(read_canvas(CANVAS_ID))
    except SlackApiError as e:
        print("Slack APIエラー:", e.response["error"])
```

### 全体を置き換える

```python
client.api_call("canvases.edit", json={
    "canvas_id": CANVAS_ID,
    "changes": [{
        "operation": "replace",
        "document_content": {"type": "markdown", "markdown": "# 新しい内容\n本文"},
    }],
})
```

### セクションを探して後ろに挿入する

```python
r = client.api_call("canvases.sections.lookup", json={
    "canvas_id": CANVAS_ID,
    "criteria": {"contains_text": "探したい文字"},
})
section_id = r["sections"][0]["id"]

client.api_call("canvases.edit", json={
    "canvas_id": CANVAS_ID,
    "changes": [{
        "operation": "insert_after",
        "section_id": section_id,
        "document_content": {"type": "markdown", "markdown": "挿入する文章"},
    }],
})
```

### HTMLから文字だけ取り出す

```python
from bs4 import BeautifulSoup  # pip install beautifulsoup4

text = BeautifulSoup(html, "html.parser").get_text()
```

# 画像を投稿する(ローカル画像)

旧 `files.upload` は廃止済みのため、以下の3ステップでアップロードする。

1. `files.getUploadURLExternal` でアップロード先URLと `file_id` を取得
1. 取得したURLに画像ファイルを送信
1. `files.completeUploadExternal` でチャンネルに共有

### 事前準備

- Botに `files:write` スコープを付与する(付与後はアプリの再インストールが必要)
- 投稿先チャンネルにBotを招待しておく(`/invite @Bot名`)
- チャンネルIDを調べる: チャンネル名をクリック → 詳細画面の一番下にある `C...` がチャンネルID

### HTTPS API

```powershell
$path      = ".\image.png"
$channelId = "チャンネルID"
$headers   = @{ Authorization = "Bearer $env:SLACK_BOT_TOKEN" }

# 1. アップロード先URLを取得
$file = Get-Item $path
$res1 = Invoke-RestMethod -Uri "https://slack.com/api/files.getUploadURLExternal" `
  -Method Post -Headers $headers `
  -Body @{ filename = $file.Name; length = $file.Length }

# 2. 画像を送信
Invoke-RestMethod -Uri $res1.upload_url -Method Post -InFile $file.FullName

# 3. チャンネルに共有
$body = @{
  files           = @(@{ id = $res1.file_id; title = $file.Name })
  channel_id      = $channelId
  initial_comment = "Botからの画像投稿テスト"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Uri "https://slack.com/api/files.completeUploadExternal" `
  -Method Post -Headers $headers `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

```bash
FILE_PATH="./image.png"
CHANNEL_ID="チャンネルID"

# 1. アップロード先URLを取得(upload_url と file_id が返る)
curl -s -X POST "https://slack.com/api/files.getUploadURLExternal" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -d "filename=$(basename "$FILE_PATH")" \
  -d "length=$(wc -c < "$FILE_PATH" | tr -d ' ')"

# 2. 画像を送信(1で返った upload_url を使う)
curl -s -X POST "upload_urlの値" -F "file=@$FILE_PATH"

# 3. チャンネルに共有(1で返った file_id を使う)
curl -s -X POST "https://slack.com/api/files.completeUploadExternal" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "files": [{ "id": "file_idの値", "title": "image.png" }],
    "channel_id": "'"$CHANNEL_ID"'",
    "initial_comment": "Botからの画像投稿テスト"
  }'
```

### Python

`slack_sdk` の `files_upload_v2` を使うと、上記3ステップをまとめて実行してくれる。

```python
import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
CHANNEL_ID = "チャンネルID"


def post_image(channel_id: str, path: str, comment: str = ""):
    """ローカル画像をチャンネルに投稿する"""
    return client.files_upload_v2(
        channel=channel_id,
        file=path,
        title=os.path.basename(path),
        initial_comment=comment,
    )


if __name__ == "__main__":
    try:
        post_image(CHANNEL_ID, "./image.png", "Pythonからの画像投稿テスト")
    except SlackApiError as e:
        print("Slack APIエラー:", e.response["error"])
```

スレッドに返信として投稿したい場合は `thread_ts="親メッセージのts"` を追加する。

---
