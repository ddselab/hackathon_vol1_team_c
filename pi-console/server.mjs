// Mac 上で動く Pi 操作パネル。ブラウザ → この server → SSH → Pi の順にコマンドが流れる。
//
// Pi には何もインストールしない。127.0.0.1 だけで待ち受けるので、同じ Mac からしか開けない。
// 依存パッケージなし（Node 標準モジュールだけ）。

import { execFile } from "node:child_process";
import { mkdir, readdir, readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

const PI_HOST = process.env.PI_HOST ?? "pi@raspberrypi.local";
const PORT = Number(process.env.PORT ?? 8765);

// 毎回 SSH の接続確立をすると遅いので、1本の接続を使い回す。
// Windows の OpenSSH は接続の使い回し（ControlMaster）に対応しておらず
// `getsockname failed: Not a socket` で落ちるので、Windows では毎回つなぐ
const SSH_OPTS = [
  "-o",
  "BatchMode=yes",
  "-o",
  "ConnectTimeout=5",
  ...(process.platform === "win32"
    ? []
    : [
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPath=~/.ssh/cm-%r@%h:%p",
        "-o",
        "ControlPersist=120",
      ]),
];

const STATUS_SCRIPT = String.raw`
echo "temp=$(cat /sys/class/thermal/thermal_zone0/temp)"
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
echo "freq=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)"
awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {print $1 "=" $2}' /proc/meminfo | tr -d ':'
echo "disk=$(df -B1 / | awk 'NR==2 {print $2, $3}')"
echo "cpu=$(head -1 /proc/stat)"
echo "throttled=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)"
echo "hostname=$(hostname)"
echo "peace=$([ -f /tmp/pi-panel-peace.pid ] && kill -0 $(cat /tmp/pi-panel-peace.pid) 2>/dev/null && echo 1 || echo 0)"
echo "preview=$([ -f /tmp/pi-panel-preview.pid ] && kill -0 $(cat /tmp/pi-panel-preview.pid) 2>/dev/null && echo 1 || echo 0)"
echo "record=$([ -f ~/peace-dataset/record.pid ] && kill -0 $(cat ~/peace-dataset/record.pid) 2>/dev/null && echo 1 || echo 0)"
echo "eyes=$([ -f /tmp/pi-panel-eyes.pid ] && kill -0 $(cat /tmp/pi-panel-eyes.pid) 2>/dev/null && echo 1 || echo 0)"
echo "recordings=$(ls ~/peace-dataset/recordings/*.h264 2>/dev/null | wc -l) $(du -sb ~/peace-dataset/recordings 2>/dev/null | cut -f1)"
`;

const POWER_COMMANDS = {
  reboot: "sudo -n systemctl reboot",
  poweroff: "sudo -n systemctl poweroff",
};

// Pi につないだディスプレイにカメラ映像を出すアプリ。止めるときのために PID をファイルに残す
const PREVIEW_PID = "/tmp/pi-panel-preview.pid";
const PEACE_PID = "/tmp/pi-panel-peace.pid";
const running = (pidFile) =>
  `[ -f ${pidFile} ] && kill -0 $(cat ${pidFile}) 2>/dev/null`;
// カメラは1つなので、片方を起動するときはもう片方を止める
// 映像の左右の余白からコンソールの文字が見えるので、止めたらログイン画面を出し直す
const STOP_PREVIEW = `${running(PREVIEW_PID)} && { kill $(cat ${PREVIEW_PID}); sleep 1; sudo -n systemctl restart getty@tty1; }; rm -f ${PREVIEW_PID}`;
// setsid で起動しているのでプロセスグループごと止める（中の rpicam-vid も一緒に止まる）。
// 画面はアプリが直接描いているので、ログイン画面を出し直して元に戻す
// Python の終了処理に数秒かかるので、終わるまで待ってからカメラを空ける
const STOP_PEACE = `${running(PEACE_PID)} && { kill -- -$(cat ${PEACE_PID})
for i in $(seq 20); do ${running(PEACE_PID)} || break; sleep 0.5; done
sudo -n systemctl restart getty@tty1; }; rm -f ${PEACE_PID}`;

// 学習データ用の録画。Pi で書き込むのは ~/peace-dataset/ の中だけなので、消せば元に戻る
const DATASET_DIR = "$HOME/peace-dataset";
const RECORD_PID = `${DATASET_DIR}/record.pid`;
// SIGINT で止めると rpicam-vid が最後まで書き出してから終わる
const STOP_RECORD = `${running(RECORD_PID)} && { kill -INT $(cat ${RECORD_PID})
for i in $(seq 20); do ${running(RECORD_PID)} || break; sleep 0.5; done
sudo -n systemctl restart getty@tty1; }; rm -f ${RECORD_PID}`;

// カメラを使わず、画面にドットの目を出してまばたきさせる（raspi/pi_eyes.py）。
// fb0 に描くので、カメラのプレビュー（fb0 より上の層）とは同時に見えない。カメラのモードとは片方ずつ動かす
const EYES_PID = "/tmp/pi-panel-eyes.pid";
const STOP_EYES = `${running(EYES_PID)} && { kill -- -$(cat ${EYES_PID})
for i in $(seq 10); do ${running(EYES_PID)} || break; sleep 0.5; done
sudo -n systemctl restart getty@tty1; }; rm -f ${EYES_PID}`;

const CLEAR_CONSOLE =
  "sudo -n sh -c 'export TERM=linux; setterm --cursor off > /dev/tty1; clear > /dev/tty1'";

const PREVIEW_OPTION_RANGES = {
  ev: [-10, 10],
  brightness: [-1, 1],
  contrast: [0, 32],
  saturation: [0, 32],
  sharpness: [0, 16],
};

function previewOptions(values = {}) {
  if (!values || typeof values !== "object" || Array.isArray(values))
    return null;
  const options = [];
  for (const [name, [min, max]] of Object.entries(PREVIEW_OPTION_RANGES)) {
    if (values[name] == null) continue;
    const value = Number(values[name]);
    if (!Number.isFinite(value) || value < min || value > max) return null;
    options.push(`--${name} ${value}`);
  }
  return options.join(" ");
}

// コンソールの「モード」。待機 = eyes、デフォルト = stop、カメラ = preview、見守り = peace。record は学習データ用
const MODE_COMMANDS = {
  // raspi/pi_peace.py を Pi の ~/hand-landmarker に置いてある前提
  peace: `${running(PEACE_PID)} && { echo already running; exit 0; }
${STOP_PREVIEW}
${STOP_RECORD}
${STOP_EYES}
${CLEAR_CONSOLE}
cd ~/hand-landmarker || exit 1
# "cd && setsid ... &" と書くと、裏で残るサブシェルが SSH の出力を握って返ってこなくなるので分けている
setsid nohup .venv/bin/python -u pi_peace.py >/tmp/pi-panel-peace.log 2>&1 </dev/null & echo $! > ${PEACE_PID}
sleep 3; ${running(PEACE_PID)} && echo "started（読み込みに 30 秒ほどかかります）" || { tail -5 /tmp/pi-panel-peace.log >&2; exit 1; }`,
  // Python を使わず、rpicam-hello のプレビューでカメラ映像をそのまま全画面に出す
  preview: (options) => `${STOP_PREVIEW}
${STOP_PEACE}
${STOP_RECORD}
${STOP_EYES}
${CLEAR_CONSOLE}
setsid nohup rpicam-hello -t 0 -f --vflip ${options} >/tmp/pi-panel-preview.log 2>&1 </dev/null & echo $! > ${PREVIEW_PID}
sleep 2; ${running(PREVIEW_PID)} && echo started || { tail -3 /tmp/pi-panel-preview.log >&2; exit 1; }`,
  // pi_peace.py が認識に使うのと同じ映像（640×480 / 15fps / 左右反転、プレビューも同じ位置）を H.264 で保存する
  record: `${running(RECORD_PID)} && { echo already recording; exit 0; }
${STOP_PEACE}
${STOP_PREVIEW}
${STOP_EYES}
${CLEAR_CONSOLE}
mkdir -p ${DATASET_DIR}/recordings
file=${DATASET_DIR}/recordings/rec_$(date +%Y%m%d_%H%M%S).h264
setsid nohup rpicam-vid -t 0 --width 640 --height 480 --framerate 15 --hflip --vflip -p 0,0,800,600 \\
  --codec h264 --bitrate 4000000 --intra 15 -o "$file" >${DATASET_DIR}/record.log 2>&1 </dev/null & echo $! > ${RECORD_PID}
sleep 2; ${running(RECORD_PID)} && echo "recording: $(basename "$file")" || { tail -3 ${DATASET_DIR}/record.log >&2; exit 1; }`,
  // raspi/pi_eyes.py を Pi の ~/hand-landmarker に置いてある前提
  eyes: `${running(EYES_PID)} && { echo already running; exit 0; }
${STOP_PEACE}
${STOP_PREVIEW}
${STOP_RECORD}
${CLEAR_CONSOLE}
cd ~/hand-landmarker || exit 1
setsid nohup .venv/bin/python -u pi_eyes.py >/tmp/pi-panel-eyes.log 2>&1 </dev/null & echo $! > ${EYES_PID}
sleep 2; ${running(EYES_PID)} && echo started || { tail -5 /tmp/pi-panel-eyes.log >&2; exit 1; }`,
  stop: `${STOP_PEACE}; ${STOP_PREVIEW}; ${STOP_RECORD}; ${STOP_EYES}; echo stopped`,
};

// Pi の録画を Mac のリポジトリ内 pi-console/recordings/ に取り込む（コミットはしない）
const LOCAL_RECORDINGS = fileURLToPath(new URL("recordings/", import.meta.url));

function pullRecordings() {
  return new Promise((resolve) => {
    execFile(
      "rsync",
      [
        "-a",
        "-e",
        `ssh ${SSH_OPTS.join(" ")}`,
        `${PI_HOST}:peace-dataset/recordings/`,
        LOCAL_RECORDINGS,
      ],
      { timeout: 600_000 },
      (err, _stdout, stderr) => resolve({ ok: !err, stderr }),
    );
  });
}

let prevCpu = null; // CPU 使用率は /proc/stat の差分で出すので前回値を持っておく

function ssh(command, timeoutMs = 10_000) {
  return new Promise((resolve) => {
    execFile(
      "ssh",
      [...SSH_OPTS, PI_HOST, command],
      { timeout: timeoutMs },
      (err, stdout, stderr) => {
        const code = err ? (err.killed ? null : (err.code ?? 1)) : 0;
        resolve({
          code,
          stdout,
          stderr: err?.killed ? `timeout (${timeoutMs / 1000}s)` : stderr,
        });
      },
    );
  });
}

// 画像などバイナリを受け取る版。stdout をそのまま Buffer で返す
function sshBuffer(command, timeoutMs = 20_000) {
  return new Promise((resolve) => {
    execFile(
      "ssh",
      [...SSH_OPTS, PI_HOST, command],
      { timeout: timeoutMs, encoding: "buffer", maxBuffer: 20 * 1024 * 1024 },
      (err, stdout, stderr) =>
        resolve({ ok: !err, stdout, stderr: stderr.toString() }),
    );
  });
}

function cpuPercent(statLine) {
  const values = statLine.split(/\s+/).slice(1).map(Number);
  const idle = values[3] + values[4];
  const total = values.reduce((a, b) => a + b, 0);
  let percent = null;
  if (prevCpu) {
    const dIdle = idle - prevCpu.idle,
      dTotal = total - prevCpu.total;
    if (dTotal > 0) percent = Math.round(1000 * (1 - dIdle / dTotal)) / 10;
  }
  prevCpu = { idle, total };
  return percent;
}

async function getStatus() {
  const { code, stdout, stderr } = await ssh(STATUS_SCRIPT);
  if (code !== 0)
    return { online: false, error: stderr.trim() || `ssh exit ${code}` };

  const raw = Object.fromEntries(
    stdout
      .split("\n")
      .filter((l) => l.includes("="))
      .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]),
  );
  const [diskTotal, diskUsed] = raw.disk.split(" ").map(Number);
  return {
    online: true,
    hostname: raw.hostname,
    temp_c: Number(raw.temp) / 1000,
    load: raw.load.split(" "),
    uptime_s: Number(raw.uptime),
    freq_mhz: raw.freq ? Math.floor(Number(raw.freq) / 1000) : null,
    cpu_percent: cpuPercent(raw.cpu),
    mem_total_kb: Number(raw.MemTotal),
    mem_available_kb: Number(raw.MemAvailable),
    swap_total_kb: Number(raw.SwapTotal),
    swap_used_kb: Number(raw.SwapTotal) - Number(raw.SwapFree),
    disk_total: diskTotal,
    disk_used: diskUsed,
    throttled: raw.throttled || null,
    peace: raw.peace === "1",
    preview: raw.preview === "1",
    record: raw.record === "1",
    eyes: raw.eyes === "1",
    recordings: {
      count: Number(raw.recordings.split(" ")[0]),
      bytes: Number(raw.recordings.split(" ")[1] || 0),
    },
  };
}

function sendJson(res, data, status = 200) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

async function readJson(req) {
  let body = "";
  for await (const chunk of req) body += chunk;
  return body ? JSON.parse(body) : {};
}

const server = createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(await readFile(new URL("index.html", import.meta.url)));
  }
  if (req.method === "GET" && req.url === "/api/status") {
    return sendJson(res, await getStatus());
  }
  if (req.method === "GET" && req.url.startsWith("/api/photos/latest")) {
    const result = await sshBuffer(
      "latest=$(ls -1t ~/hand-landmarker/photos/peace_*.jpg 2>/dev/null | head -1); " +
        '[ -n "$latest" ] || { echo \'写真がまだありません\' >&2; exit 1; }; cat "$latest"',
    );
    if (!result.ok) {
      return sendJson(
        res,
        { error: result.stderr.trim() || "最新の写真を取得できませんでした" },
        404,
      );
    }
    res.writeHead(200, {
      "Content-Type": "image/jpeg",
      "Cache-Control": "no-store",
    });
    return res.end(result.stdout);
  }
  if (req.method !== "POST") return sendJson(res, { error: "not found" }, 404);

  // 別サイトのページからこの API を叩かれないように、同じオリジン以外は拒否する
  const origin = req.headers.origin;
  if (
    origin &&
    origin !== `http://127.0.0.1:${PORT}` &&
    origin !== `http://localhost:${PORT}`
  ) {
    return sendJson(res, { error: "forbidden" }, 403);
  }

  const body = await readJson(req).catch(() => ({}));

  if (req.url === "/api/recordings/pull") {
    // 録画中のファイルは書きかけなので、止めてから取り込む
    const recording = await ssh(`${running(RECORD_PID)}`);
    if (recording.code === 0)
      return sendJson(
        res,
        { error: "録画中です。停止してから取り込んでください" },
        409,
      );
    await mkdir(LOCAL_RECORDINGS, { recursive: true });
    const result = await pullRecordings();
    if (!result.ok)
      return sendJson(
        res,
        { error: result.stderr.trim() || "取り込みに失敗しました" },
        500,
      );
    const files = await Promise.all(
      (await readdir(LOCAL_RECORDINGS))
        .filter((name) => name.endsWith(".h264"))
        .map(async (name) => ({
          name,
          bytes: (await stat(LOCAL_RECORDINGS + name)).size,
        })),
    );
    return sendJson(res, { dir: LOCAL_RECORDINGS, files });
  }

  let command;
  if (req.url === "/api/power") command = POWER_COMMANDS[body.action];
  else if (req.url === "/api/mode") {
    command = MODE_COMMANDS[body.action];
    if (body.action === "preview") {
      const options = previewOptions(body.options);
      if (options == null)
        return sendJson(res, { error: "カメラ設定の値が範囲外です" }, 400);
      command = command?.(options);
    }
  } else return sendJson(res, { error: "not found" }, 404);
  if (!command) return sendJson(res, { error: "bad request" }, 400);

  const started = Date.now();
  const result = await ssh(command, 30_000);
  sendJson(res, {
    ...result,
    elapsed: Math.round((Date.now() - started) / 10) / 100,
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Pi console: http://127.0.0.1:${PORT}  (target: ${PI_HOST})`);
  // 待ち受け開始後に開くので、ブラウザが先に開いて接続エラーになることがない
  if (process.argv.includes("--open"))
    execFile("open", [`http://127.0.0.1:${PORT}`]);
});
