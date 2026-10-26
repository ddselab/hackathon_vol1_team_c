"""SQLite によるデータ保存（メンバー・在室状態・履歴・ユーザートークン）"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TIMEZONE)
_lock = threading.Lock()  # カメラAPIとSlackコマンドが別スレッドで動くため


def now() -> datetime:
    return datetime.now(TZ)


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS members (
                slack_user_id TEXT PRIMARY KEY,
                slot          TEXT UNIQUE NOT NULL,   -- 靴箱の番号（カメラ班と共通のID）
                name          TEXT NOT NULL,
                present       INTEGER,                -- 1=出席中 0=退席中 NULL=未確認
                since         TEXT                    -- 状態が変わった時刻 ISO8601
            );
            CREATE TABLE IF NOT EXISTS events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                slack_user_id TEXT NOT NULL,
                name          TEXT NOT NULL,
                slot          TEXT NOT NULL,
                present       INTEGER NOT NULL,
                at            TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_tokens (
                slack_user_id TEXT PRIMARY KEY,
                access_token  TEXT NOT NULL,          -- xoxp-... ステータス変更用
                installed_at  TEXT NOT NULL
            );
            """
        )


# ---------- メンバー ----------

def register_member(user_id: str, slot: str, name: str) -> None:
    with _lock, _connect() as c:
        row = c.execute("SELECT slack_user_id FROM members WHERE slot = ?", (slot,)).fetchone()
        if row and row["slack_user_id"] != user_id:
            raise ValueError(f"靴箱 {slot} はすでに別のメンバーが登録しています")
        c.execute(
            """
            INSERT INTO members (slack_user_id, slot, name) VALUES (?, ?, ?)
            ON CONFLICT(slack_user_id) DO UPDATE SET slot = excluded.slot, name = excluded.name
            """,
            (user_id, slot, name),
        )


def unregister_member(user_id: str) -> bool:
    with _lock, _connect() as c:
        cur = c.execute("DELETE FROM members WHERE slack_user_id = ?", (user_id,))
        return cur.rowcount > 0


def get_member(user_id: str) -> dict | None:
    with _connect() as c:
        row = c.execute("SELECT * FROM members WHERE slack_user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_members() -> list[dict]:
    with _connect() as c:
        rows = c.execute("SELECT * FROM members ORDER BY slot").fetchall()
        return [dict(r) for r in rows]


# ---------- 在室状態 ----------

def set_presence_if_changed(slot: str, present: bool) -> tuple[str, dict | None]:
    """
    状態が変わったときだけ DB を更新する。
    戻り値: (結果, メンバー情報)
      結果 = "changed"      状態が変わった
             "initial"      初めて状態が分かった（未確認→出席/退席）
             "no_change"    変化なし
             "unknown_slot" 未登録の靴箱
    """
    ts = now().isoformat(timespec="seconds")
    with _lock, _connect() as c:
        row = c.execute("SELECT * FROM members WHERE slot = ?", (slot,)).fetchone()
        if row is None:
            return "unknown_slot", None
        if row["present"] is not None and bool(row["present"]) == present:
            return "no_change", dict(row)

        result = "initial" if row["present"] is None else "changed"
        c.execute("UPDATE members SET present = ?, since = ? WHERE slot = ?", (int(present), ts, slot))
        c.execute(
            "INSERT INTO events (slack_user_id, name, slot, present, at) VALUES (?, ?, ?, ?, ?)",
            (row["slack_user_id"], row["name"], slot, int(present), ts),
        )
        member = dict(row)
        member.update(present=int(present), since=ts)
        return result, member


def events_on(date_str: str) -> list[dict]:
    """指定日(YYYY-MM-DD)の出入り履歴"""
    with _connect() as c:
        rows = c.execute(
            "SELECT * FROM events WHERE substr(at, 1, 10) = ? ORDER BY at", (date_str,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- ユーザートークン ----------

def save_user_token(user_id: str, token: str) -> None:
    with _lock, _connect() as c:
        c.execute(
            """
            INSERT INTO user_tokens (slack_user_id, access_token, installed_at) VALUES (?, ?, ?)
            ON CONFLICT(slack_user_id) DO UPDATE SET
                access_token = excluded.access_token, installed_at = excluded.installed_at
            """,
            (user_id, token, now().isoformat(timespec="seconds")),
        )


def get_user_token(user_id: str) -> str | None:
    with _connect() as c:
        row = c.execute(
            "SELECT access_token FROM user_tokens WHERE slack_user_id = ?", (user_id,)
        ).fetchone()
        return row["access_token"] if row else None


def delete_user_token(user_id: str) -> None:
    with _lock, _connect() as c:
        c.execute("DELETE FROM user_tokens WHERE slack_user_id = ?", (user_id,))
