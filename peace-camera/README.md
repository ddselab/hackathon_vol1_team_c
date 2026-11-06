# peace-camera（ピースカメラ）

Raspberry Pi につないだディスプレイにカメラ映像を映し、両手ピースを 3 秒続けると撮影して Slack に投稿する。
同じ映像で、靴箱判定（`shoebox-judge`）も 1 分ごとに行う。pi-console の「見守り」で起動する。

同じフォルダの `pi_eyes.py` は、カメラを使わずにディスプレイにドットの目を出してまばたきさせる（pi-console の「目」）。
目の開き具合ごとの画面を起動時に作っておき、まばたきの瞬間だけ書き込むので、CPU もメモリもほとんど使わない。

```
rpicam-vid（1280×748、15fps、開きっぱなし）
  ├─ プレビュー … GPU が画面の真ん中に映す（855×500、見た目は 16:9）
  └─ stdout の最新の1枚（メモリ）
       ├─ ピース認識（MediaPipe）→ 撮影 → POST /photo（lab-attendance）→ Slack
       └─ 靴箱判定（1分ごと、手が写っていないとき）→ POST /events（lab-attendance）
```

- カメラを開き直さないので、撮影中も靴箱判定中も映像は止まらない。写真は保存しない
- 映像は靴箱判定と同じ 4:3 全体のモード（1296×972）から、上（天井側）を切り取って横長にしている
- 画面の余白には、上に状態（`HANDS n` / `SENDING` / `POSTED` / `ERROR`）、下に日時、左にカウントダウンを描く
- 動いている間は `/tmp/pi-camera.lock` を握るので、`shoebox-judge` の `watch`（`shoebox.service`）は撮影を休む

## Pi での置き場所

| Pi のパス | 中身 |
| --- | --- |
| `~/hand-landmarker/pi_peace.py` | このフォルダの `pi_peace.py` |
| `~/hand-landmarker/pi_eyes.py` | このフォルダの `pi_eyes.py` |
| `~/hand-landmarker/models/hand_landmarker.task` | MediaPipe のモデル（リポジトリには入れていない） |
| `~/hand-landmarker/.venv/` | Python 3.12 + mediapipe 0.10.18 + opencv-contrib-python + Pillow |
| `~/shoebox/` | `shoebox-judge`（`main.py`・`shoebox.py`・`config.json`・`empty.png`）。靴箱判定に使う |

ログは `/tmp/pi-panel-peace.log`（`shoebox:` で始まる行が靴箱判定）。

## 更新するとき

置き換える前に、Pi 上で今のファイルを `.bak-<わかる名前>` で残しておく。

```sh
ssh raspi@ddse 'cp ~/hand-landmarker/pi_peace.py ~/hand-landmarker/pi_peace.py.bak-$(date +%Y%m%d)'
scp peace-camera/pi_peace.py raspi@ddse:hand-landmarker/
```

`shoebox-judge/main.py` を変えたときは `~/shoebox/` にも送り、`systemctl --user restart shoebox` する。
`pi_peace.py` と `main.py` はカメラの使い方をそろえているので、片方だけ古いままにしない。

送ったあとは pi-console で「デフォルト」→「見守り」と押して起動し直す（動いているプログラムは古いコードのまま）。
`pi_eyes.py` も同じように `.bak-` を残して送り、「デフォルト」→「目」で起動し直す。

## バックアップに戻す

### ピースカメラ

```sh
ssh raspi@ddse 'ls ~/hand-landmarker/pi_peace.py.bak-*'          # 残っているバックアップを確認
ssh raspi@ddse 'cp ~/hand-landmarker/pi_peace.py.bak-<名前> ~/hand-landmarker/pi_peace.py'
```

そのあと pi-console で「デフォルト」→「見守り」。

### 靴箱判定

```sh
ssh raspi@ddse 'cp ~/shoebox/main.py.bak-<名前> ~/shoebox/main.py && systemctl --user restart shoebox'
```

古い `main.py` は、ピースカメラが動いていても自分で撮ろうとする。ピースカメラも同じ時期のバックアップに戻すこと。

### 靴箱のマス設定・基準画像

`config.json` と `empty.png` は `~/shoebox/backup-YYYYMMDD/` に残してある。ピースカメラはこれらを読むだけで書き換えない。

```sh
ssh raspi@ddse 'cp ~/shoebox/backup-<日付>/config.json ~/shoebox/backup-<日付>/empty.png ~/shoebox/'
```

どちらのプログラムも判定のたびに読み直すので、再起動はいらない。
