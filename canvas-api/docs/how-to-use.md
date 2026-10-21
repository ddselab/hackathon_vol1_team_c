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

---
