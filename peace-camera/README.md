# peace-camera（ピースカメラ）

Raspberry Pi につないだディスプレイに、ふだんはまばたきする目（`pi_eyes.py` と同じ）を出す。
ピースが写るとカメラ映像に切り替わり、ピースを 2 秒続けると撮影して Slack に投稿する。
同じ映像で、靴箱判定（`shoebox-judge`）も 1 分ごとに行う。pi-console の「見守り」で起動する。

```
rpicam-vid（1280×748、15fps、開きっぱなし、プレビューなし）
  └─ stdout の最新の1枚（メモリ）
       ├─ 画面 … ピースが写っている間だけ、Python が縮めて /dev/fb0 の真ん中に描く（854×500）。それ以外は目
       ├─ ピース認識（MediaPipe）→ 撮影 → POST /photo（lab-attendance）→ Slack
       └─ 靴箱判定（1分ごと、手が写っていないとき）→ POST /events（lab-attendance）
```

- カメラを開き直さないので、撮影中も靴箱判定中も映像は止まらない。写真は保存しない
- 映像に切り替わるのは、ピースが写っている間と、撮影して `POSTED` / `ERROR` を出し終えるまで。ピースが写らなくなって 2 秒で目に戻る
- rpicam-vid の GPU プレビューは画面のいちばん上に重なって目を隠すので使わない（映像の描画は 1 枚 40ms ほど）
- 映像は靴箱判定と同じ 4:3 全体のモード（1296×972）から、上（天井側）を切り取って横長にしている
- 画面の余白には、上に状態（`HANDS n` / `SENDING` / `POSTED` / `ERROR`）、下に日時、左にカウントダウンを描く
- 動いている間は `/tmp/pi-camera.lock` を握るので、`shoebox-judge` の `watch`（`shoebox.service`）は撮影を休む
- `shoebox-judge` の `snapshot` などが `/tmp/pi-camera.request` を置いたら、映像を止めてカメラを譲り、撮り終わったら再開する（カウントダウン中は撮り終えてから譲る）

## Pi での置き場所

| Pi のパス | 中身 |
| --- | --- |
| `~/hand-landmarker/pi_peace.py` | このフォルダの `pi_peace.py` |
| `~/hand-landmarker/pi_eyes.py` | このフォルダの `pi_eyes.py`（pi-console の「目」。`pi_peace.py` も目の絵に使う） |
| `~/hand-landmarker/models/hand_landmarker.task` | MediaPipe のモデル（リポジトリには入れていない） |
| `~/hand-landmarker/.venv/` | Python 3.12 + mediapipe 0.10.18 + opencv-contrib-python + Pillow |
| `~/shoebox/` | `shoebox-judge`（`main.py`・`shoebox.py`・`config.json`・`empty.png`）。靴箱判定に使う |

ログは `/tmp/pi-panel-peace.log`（`shoebox:` で始まる行が靴箱判定）。

## 更新するとき

置き換える前に、Pi 上で今のファイルを `.bak-<わかる名前>` で残しておく。

```sh
ssh raspi@ddse 'cp ~/hand-landmarker/pi_peace.py ~/hand-landmarker/pi_peace.py.bak-$(date +%Y%m%d)'
scp peace-camera/pi_peace.py peace-camera/pi_eyes.py raspi@ddse:hand-landmarker/
```

`shoebox-judge/main.py` を変えたときは `~/shoebox/` にも送り、`systemctl --user restart shoebox` する。
`pi_peace.py` と `main.py` はカメラの使い方をそろえているので、片方だけ古いままにしない。

送ったあとは pi-console で「デフォルト」→「見守り」と押して起動し直す（動いているプログラムは古いコードのまま）。

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
