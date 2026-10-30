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
scp main.py shoebox.py shoebox.service pi@raspberrypi.local:shoebox/

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
  先に `ssh pi@raspberrypi.local "systemctl --user stop shoebox"` で止めてください
  （終わったら `systemctl --user start shoebox` で再開）

### 1分に1回判定する
```
python3 main.py watch                        # 60秒ごと (--interval 30 などで変更可)
```
SSH で `watch` を直接動かすと、SSH を切ったときに止まります。動かし続けるなら下の systemd に登録してください。
最初に全マスの表を出し、その後は毎回「くつあり ○/32 マス」と、変化があったマスと表を出します。
暗い・カメラが使えないときはその回だけ「判定できません」と出して、次の回にまた試します。
毎回 `config.json` と `empty.png` を読み直すので、動かしたまま「空の状態を登録」をし直しても反映されます
（ただし撮影が重なると片方が失敗します）。

電源を入れたら自動で動かすには、`shoebox.service` を systemd に登録します。
出席 Bot（`lab-attendance`）と同じく **ユーザーサービス** として登録するので、`sudo` はいりません。
Pi の電源を入れたら自動で起動し、落ちても 10 秒後に自動で再起動します。
出席 Bot に送らないなら、`shoebox.service` の `ExecStart` の `--post` を消してから登録してください。
```
mkdir -p ~/.config/systemd/user
cp ~/shoebox/shoebox.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now shoebox          # 今すぐ起動し、次回以降も自動で起動する
sudo loginctl enable-linger "$USER"            # SSH でログインしていなくても動かし続ける（最初の 1 回だけ）
```
> `enable-linger` を忘れると、SSH を切った瞬間や再起動後に止まります。出席 Bot の登録で一度やっていれば不要です。

よく使うコマンド:

| やりたいこと | コマンド |
| --- | --- |
| 動いているか確認 | `systemctl --user status shoebox` |
| 判定結果をリアルタイムで見る（Ctrl+C で抜ける） | `journalctl --user -u shoebox -f` |
| 再起動（コードを変えたあと） | `systemctl --user restart shoebox` |
| 止める | `systemctl --user stop shoebox` |
| 自動起動をやめる | `systemctl --user disable --now shoebox` |
| 出席 Bot と一緒に確認 | `systemctl --user status lab-attendance shoebox` |

注意:
- `shoebox.service` は `~/shoebox` に置いてある前提です。別の場所に置いたら `WorkingDirectory` を直してください
- カメラは同時に1つのプログラムしか使えません。ピースカメラ（`pi_peace.py`）とは `/tmp/pi-camera.lock` で譲り合うので、
  同じ Pi で一緒に動かせます（撮影のたびにピースカメラの映像が 3 秒ほど止まり、画面に `PAUSE` と出ます）。
  ピースのカウントダウン中は撮り終わるまで最大 15 秒待ち、それでも譲ってもらえなければその回は飛ばします。
  pi-console の「カメラ」「録画」は譲り合わないので、動かしている間は撮影に失敗します
- 以前の手順で `/etc/systemd/system/shoebox.service` に登録していたら、二重に動かないように先に消してください
  ```
  sudo systemctl disable --now shoebox
  sudo rm /etc/systemd/system/shoebox.service
  sudo systemctl daemon-reload
  ```

### コードを更新するとき
動いているプログラムは起動したときのコードのまま動き続けるので、`.py` ファイルを置き換えただけでは反映されません。
送ったあとに再起動してください（以下は PC で打ちます）。
```
scp main.py shoebox.py pi@raspberrypi.local:shoebox/
ssh pi@raspberrypi.local "systemctl --user restart shoebox"
ssh pi@raspberrypi.local "journalctl --user -u shoebox -f"    # エラーが出ていないか確認（Ctrl+C で抜ける）
```
コードにエラーがあると、10 秒おきに再起動を繰り返します。ログにエラーが出ていたら直して送り直してください。

変えたものによって、必要な操作が違います。

| 変えたもの | 必要な操作 |
| --- | --- |
| `main.py` / `shoebox.py` | `systemctl --user restart shoebox` |
| `config.json` / `empty.png` | なし（`watch` が毎回読み直す） |
| `shoebox.service` | `~/.config/systemd/user/` にコピーし直してから `systemctl --user daemon-reload` → `systemctl --user restart shoebox`（`~/shoebox/` に送るだけでは反映されない） |

```
scp shoebox.service pi@raspberrypi.local:shoebox/
ssh pi@raspberrypi.local "cp ~/shoebox/shoebox.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user restart shoebox"
```

> [!CAUTION]
> コードを送るときに `config.json` や `empty.png` を一緒に送らないでください。Pi 上のマス設定や基準画像が上書きされます
> （PC の `empty.png` は解像度が違うので、Pi では使えません）。

### 出席 Bot に判定結果を送る
`--post` を付けると、判定のたびに全マスの結果を出席 Bot（`lab-attendance/bot.py`）の `POST /events` にまとめて送ります。
```
python3 main.py watch --post                                  # http://127.0.0.1:8080/events に送る
python3 main.py watch --post http://127.0.0.1:9000/events     # 送り先を変えるとき
```
- マス名は次の番号に変換して送ります（左上から右へ、上の段から順）。Bot の `slots.json` の番号と同じです
  ```
       1   2   3   4   5   6   7   8
   A   1   2   3   4   5   6   7   8
   B   9  10  11  12  13  14  15  16
   C  17  18  19  20  21  22  23  24
   D  25  26  27  28  29  30  31  32
  ```
- 送る内容は `{"time": "撮影した時刻", "slots": {"1": "in", "2": "out", …, "32": "out"}}`（くつあり＝`in`、くつなし＝`out`）。
  毎回全マスを送りますが、Bot は前回と変わったマスだけを記録します。Bot が記録したマスがあれば「出席 Bot に記録: 1, 11 番」と表示します
- 暗くて判定できなかった回は送りません（間違った「くつなし」で退室扱いにならないように）
- Bot が止まっている・つながらないときは「出席 Bot に送れませんでした」と出して、判定はそのまま続けます。
  次の回にまた全マスを送るので、Bot が戻れば状態は追いつきます
- Bot と同じ Pi で動かす前提です（Bot は `127.0.0.1` でしか受け付けません）。
  Bot 側の HTTP サーバーを有効にする方法は `lab-attendance/README.md` の「カメラとの連携」を見てください

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
- `main.py` … コマンドライン（`watch` で定期判定、`--post` で出席 Bot に送信）
- `gui.py` … GUI
- `config.json` / `empty.png` … マス設定と空の基準画像（自動生成）
- `shoebox.service` … Raspberry Pi で `watch` を常時実行するための systemd の設定

## 旧バージョンからの変更
- 靴箱全体の四隅から自動で 4×8 に等分する方式をやめ、マスごとに四隅を指定する方式にしました。
  旧バージョンの `config.json` の四隅（corners）は使われないので、マス設定をやり直してください
- `empty.png` の保存形式が変わったため、「空の状態を登録」もやり直してください
