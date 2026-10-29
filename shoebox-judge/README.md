# 靴箱 くつ判定ツール

靴箱（4行×8列）の各マスにくつが入っているかを、カメラ画像から判定します。

```
     1   2   3   4   5   6   7   8
 A  A1  A2  A3  A4  A5  A6  A7  A8
 B  B1  ...
 C  C1  ...
 D  D1  ...                     D8
```

## インストール
```
pip install -r requirements.txt
```

## 使い方（初回設定）
1. カメラを固定し、靴箱全体が写るように設置する（設置後は動かさない）
2. **マス設定**：マスごとに、判定に使う範囲の四隅をクリックする（A1 → A2 → … → D8）
3. **空の状態を登録**：靴箱を空にした状態で登録（判定の基準になります）

カメラの位置を変えたら、2・3をやり直してください。
マス設定だけを直したとき（カメラは動かしていない）は、3 のやり直しは不要です。

### マス設定のコツ
- 四隅は **マスの内側（仕切り板の手前）** をクリックします。仕切り板や棚板の縁を含めないほうが精度が上がります
- クリックの順番は 左上→右上→右下→左下 が基本ですが、順番を間違えても自動で並べ直します
- 右クリックで1点戻せます（前のマスにも戻れます）。1マス設定するごとに保存されるので、途中でやめても続きから設定できます
- GUI ではカーソル付近が右上に拡大表示されます

## GUI
```
python main.py gui                 # カメラ0を使う
python main.py gui --image 写真.jpg
```
- 「マス設定」→ プレビュー上で各マスの四隅をクリック（右クリックで1点戻す、Esc でやめる）
- 1マスだけ直したいときは、右のマス一覧でマスを選んで「選択マスを設定し直す」
- 「空の状態を登録」→ 基準画像を保存
- 下の入力欄に `A1` と入力して Enter → 「A1：くつあり／くつなし」を表示
- 右のマス一覧は くつあり＝緑。マスをクリックしてもその結果を表示
- 誤判定が多いときは「閾値」スライダーで調整（上げると「あり」になりにくい）

## コマンド
```
python main.py calibrate --camera 0          # 全マスの四隅をクリック (Esc で中止)
python main.py calibrate A3 B5 --camera 0    # 指定したマスだけ設定し直す
python main.py set-empty --camera 0          # 空の状態を登録
python main.py check A1 --camera 0           # → A1: くつあり (score=0.31)
python main.py status --camera 0             # 全マスを表で表示
python main.py --camera 0                    # 対話モード: A1 と入力 / all / q で終了
```
`--camera 0` の代わりに `--image ファイル名` で画像ファイル、`--pi` で Raspberry Pi のカメラモジュールも使えます。
省略すると、Pi（`rpicam-still` がある環境）では Pi のカメラ、それ以外ではカメラ0を使います。
マス名は小文字（a1）や全角（Ａ１）でもOKです。

## Raspberry Pi で動かす（1分に1回判定）
Raspberry Pi のカメラモジュール（CSI 接続）は OpenCV では開けないので、`rpicam-still` で撮影します
（[pi-zero-playground](https://github.com/k4nkan/pi-zero-playground) と同じ方式）。
カメラを使えるのは同時に1プログラムだけなので、撮影のたびに起動して終わったら閉じます。

### 準備（Raspberry Pi OS Lite 64bit、Pi Zero 2 W で想定）
```
sudo apt install --no-install-recommends rpicam-apps python3-opencv python3-numpy
rpicam-hello --list-cameras        # カメラが見えるか確認
```
コードは `main.py` と `shoebox.py` だけあれば動きます（GUI は不要）。

### 初回設定（PC から SSH で操作）
Pi Lite には画面（GUI）がないので、Pi 側のコマンドは PC から SSH で打ち、
マス設定（`calibrate`、画像をクリックする操作）だけは PC で行います。
**マスの座標は画素単位なので、必ず Pi のカメラで撮った画像を使ってください。**

以下は PC の PowerShell で打ちます（Windows 11 には `ssh` / `scp` が最初から入っています）。
`pi@raspberrypi.local` と `~/shoebox` は自分の環境に合わせてください。
```
# 0. コードを Pi に送る（初回とコードを変えたとき）
ssh pi@raspberrypi.local "mkdir -p ~/shoebox"
scp main.py shoebox.py pi@raspberrypi.local:shoebox/

# 1. 必要なものを入れて、カメラが見えるか確認
ssh pi@raspberrypi.local "sudo apt install -y --no-install-recommends rpicam-apps python3-opencv python3-numpy"
ssh pi@raspberrypi.local "rpicam-hello --list-cameras"

# 2. Pi で撮影 → PC に持ってくる
ssh pi@raspberrypi.local "cd ~/shoebox && python3 main.py snapshot rack.jpg"
scp pi@raspberrypi.local:shoebox/rack.jpg .

# 3. PC でマス設定 → config.json を Pi に送る
python main.py calibrate --image rack.jpg
scp config.json pi@raspberrypi.local:shoebox/

# 4. 靴箱を空にして、Pi で基準を登録・確認
ssh pi@raspberrypi.local "cd ~/shoebox && python3 main.py set-empty"
ssh pi@raspberrypi.local "cd ~/shoebox && python3 main.py status"
```
`status` で全マスが「・」（くつなし）になれば準備完了です。

注意:
- PC にある `empty.png` は Pi に送らないでください（解像度が違うので使えません）。基準画像は 4 で Pi 側に作られます
- 3 で PC の `config.json` が Pi 用のマス設定に上書きされます。PC 用の設定を残したいときは、先に別名でコピーしておいてください
- カメラは同時に1つのプログラムしか使えません。`watch` を systemd で動かしている間に `snapshot` や `set-empty` をするときは、
  先に `ssh pi@raspberrypi.local "sudo systemctl stop shoebox"` で止めてください

### 1分に1回判定する
```
python3 main.py watch                        # 60秒ごと (--interval 30 などで変更可)
```
SSH で `watch` を直接動かすと、SSH を切ったときに止まります。動かし続けるなら下の systemd に登録してください。
最初に全マスの表を出し、その後は毎回「くつあり ○/32 マス」と、変化があったマスと表を出します。
暗い・カメラが使えないときはその回だけ「判定できません」と出して、次の回にまた試します。
毎回 `config.json` と `empty.png` を読み直すので、動かしたまま「空の状態を登録」をし直しても反映されます
（ただし撮影が重なると片方が失敗します）。

電源を入れたら自動で動かすには systemd に登録します（パスとユーザー名は自分の環境に合わせる）。
```
# /etc/systemd/system/shoebox.service
[Unit]
Description=Shoebox watcher
After=multi-user.target

[Service]
User=pi
WorkingDirectory=/home/pi/shoebox
ExecStart=/usr/bin/python3 -u main.py watch
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```
sudo systemctl enable --now shoebox
journalctl -u shoebox -f                     # 判定結果を見る
```

撮影サイズは `shoebox.py` の `PI_CAMERA_SIZE`（既定 1296x972）で変えられます。
変えたらマス設定と空の状態の登録をやり直してください。

## サンプルで試す
```
python make_sample.py                        # 合成画像を作成 (A1, B3, D8 にくつ)
copy sample_config.json config.json          # 各マスの四隅を設定
python main.py set-empty --image sample_empty.png
python main.py status --image sample_shoes.png    # 普通の状態
python main.py status --image sample_shadow.png   # 片側に影 → それでも正しく判定
python main.py status --image sample_dark.png     # 電気が消えている → 「暗すぎて判定できません」
```

## 判定の仕組み
1. 各マスを、指定した四隅で正面向きに補正して切り出します（斜めから撮っていてもOK）
2. **明るさチェック**：空の状態を登録したときより大幅に暗い（初期値は 30% 未満）、
   または白飛びしているときは、間違った結果を出さないように「判定できません」を返します
3. **模様の比較**：マスごとに明るさの平均を引いてから、空の状態の画像と模様・形がどれだけ違うかを比べます
   （score：0＝同じ〜1＝別物）。マス単位で明るさを打ち消すので、部屋が少し暗い、片側に影がある、
   スポット照明、電球色の照明といった違いには反応せず、くつが置かれたときだけ score が上がります。
   score が閾値（初期値 0.15）以上なら「くつあり」と判定します

照明の条件が大きく変わる場所（昼は日差しが強く、夜は照明だけなど）でうまくいかないときは、
その時間帯に合わせて「空の状態を登録」をやり直してください。

### 調整できる値（config.json）
| 項目 | 初期値 | 意味 |
|---|---|---|
| `threshold` | 0.15 | score がこれ以上なら「くつあり」 |
| `min_ratio` | 0.30 | 空の状態を登録したときと比べ、この割合より暗いと判定しない |
| `min_brightness` | 15 | 明るさ（0〜255）がこれ未満なら判定しない |
| `min_contrast` | 1.5 | 画像がほぼ真っ黒（レンズをふさいだ等）なら判定しない |

## ファイル
- `shoebox.py` … 判定処理の本体
- `main.py` … コマンドライン（`watch` で定期判定）
- `gui.py` … GUI
- `config.json` / `empty.png` … マス設定と空の基準画像（自動生成）

## 旧バージョンからの変更
- 靴箱全体の四隅から自動で 4×8 に等分する方式をやめ、マスごとに四隅を指定する方式にしました。
  旧バージョンの `config.json` の四隅（corners）は使われないので、マス設定をやり直してください
- `empty.png` の保存形式が変わったため、「空の状態を登録」もやり直してください
