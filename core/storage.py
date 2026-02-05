from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from core.models import StatusEvent, SleepPeriod, DayTimezone

_local = threading.local()
_db_path: str | None = None


def init(db_path: str) -> None:
    """Set the database path and create tables."""
    global _db_path
    _db_path = db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _migrate(_get_conn())


def _get_conn() -> sqlite3.Connection:
    """Thread-local connection (sqlite3 objects can't cross threads)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_db_path)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            label         TEXT NOT NULL,
            current_status    TEXT,
            current_tz_offset REAL
        );

        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            timestamp_utc   TEXT NOT NULL,
            status          TEXT NOT NULL,
            raw_status_type TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_user_ts
            ON events(user_id, timestamp_utc);

        CREATE TABLE IF NOT EXISTS sleep_periods (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(user_id),
            offline_at_utc      TEXT NOT NULL,
            online_at_utc       TEXT NOT NULL,
            gap_hours           REAL NOT NULL,
            estimated_tz_offset REAL NOT NULL,
            date                TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sleep_user_date
            ON sleep_periods(user_id, date);

        CREATE TABLE IF NOT EXISTS daily_timezones (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(user_id),
            date          TEXT NOT NULL,
            offset_hours  REAL NOT NULL,
            wakeup_utc    TEXT NOT NULL,
            UNIQUE(user_id, date)
        );

        CREATE INDEX IF NOT EXISTS idx_tz_user_date
            ON daily_timezones(user_id, date);
    """)


# ── Users ──────────────────────────────────────────────

def ensure_user(user_id: int, username: Optional[str], label: str) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users(user_id, username, label) VALUES (?, ?, ?)",
        (user_id, username, label),
    )
    conn.commit()


def get_user(user_id: int) -> Optional[dict]:
    row = _get_conn().execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def get_all_users() -> list[dict]:
    rows = _get_conn().execute("SELECT * FROM users").fetchall()
    return [dict(r) for r in rows]


def update_user_status(user_id: int, status: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET current_status = ? WHERE user_id = ?",
        (status, user_id),
    )
    conn.commit()


def update_user_tz(user_id: int, offset: float) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE users SET current_tz_offset = ? WHERE user_id = ?",
        (offset, user_id),
    )
    conn.commit()


# ── Events ─────────────────────────────────────────────

def append_event(user_id: int, event: StatusEvent) -> bool:
    """Insert event. Returns False if duplicate (same ts+status)."""
    conn = _get_conn()
    # Deduplicate: skip if last event is identical
    last = conn.execute(
        "SELECT timestamp_utc, status FROM events WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if last and last["timestamp_utc"] == event.timestamp_utc and last["status"] == event.status:
        return False

    conn.execute(
        "INSERT INTO events(user_id, timestamp_utc, status, raw_status_type) VALUES (?, ?, ?, ?)",
        (user_id, event.timestamp_utc, event.status, event.raw_status_type),
    )
    conn.execute(
        "UPDATE users SET current_status = ? WHERE user_id = ?",
        (event.status, user_id),
    )
    conn.commit()
    return True


def get_events(
    user_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[StatusEvent], int]:
    """Return (events, total_count) with optional date filtering and pagination."""
    conn = _get_conn()
    where_clauses = ["user_id = ?"]
    params: list = [user_id]
    if from_date:
        where_clauses.append("timestamp_utc >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("timestamp_utc <= ?")
        params.append(to_date)

    # Build WHERE clause safely
    where_sql = " AND ".join(where_clauses)

    # Use parameterized queries throughout
    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE " + where_sql, params
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT timestamp_utc, status, raw_status_type FROM events "
        "WHERE " + where_sql + " ORDER BY timestamp_utc "
        "LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    events = [StatusEvent(r["timestamp_utc"], r["status"], r["raw_status_type"]) for r in rows]
    return events, total


def get_all_events_for_user(user_id: int) -> list[StatusEvent]:
    """Get all events for analysis (no pagination)."""
    rows = _get_conn().execute(
        "SELECT timestamp_utc, status, raw_status_type FROM events "
        "WHERE user_id = ? ORDER BY timestamp_utc",
        (user_id,),
    ).fetchall()
    return [StatusEvent(r["timestamp_utc"], r["status"], r["raw_status_type"]) for r in rows]


def get_last_event(user_id: int) -> Optional[StatusEvent]:
    row = _get_conn().execute(
        "SELECT timestamp_utc, status, raw_status_type FROM events "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return StatusEvent(row["timestamp_utc"], row["status"], row["raw_status_type"])
    return None


def get_events_count(user_id: int) -> int:
    return _get_conn().execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,)
    ).fetchone()[0]


# ── Sleep Periods ──────────────────────────────────────

def replace_sleep_periods(user_id: int, periods: list[SleepPeriod]) -> None:
    """Replace all sleep periods for a user (full recompute)."""
    conn = _get_conn()
    conn.execute("DELETE FROM sleep_periods WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO sleep_periods(user_id, offline_at_utc, online_at_utc, gap_hours, estimated_tz_offset, date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(user_id, sp.offline_at_utc, sp.online_at_utc, sp.gap_hours, sp.estimated_tz_offset, sp.date)
         for sp in periods],
    )
    conn.commit()


def get_sleep_periods(
    user_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list[SleepPeriod]:
    conn = _get_conn()
    where_clauses = ["user_id = ?"]
    params: list = [user_id]
    if from_date:
        where_clauses.append("date >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("date <= ?")
        params.append(to_date)

    where_sql = " AND ".join(where_clauses)
    rows = conn.execute(
        "SELECT offline_at_utc, online_at_utc, gap_hours, estimated_tz_offset, date "
        "FROM sleep_periods WHERE " + where_sql + " ORDER BY date",
        params,
    ).fetchall()
    return [SleepPeriod(r["offline_at_utc"], r["online_at_utc"], r["gap_hours"],
                        r["estimated_tz_offset"], r["date"]) for r in rows]


# ── Daily Timezones ────────────────────────────────────

def replace_daily_timezones(user_id: int, days: list[DayTimezone]) -> None:
    """Replace all daily timezone records for a user (full recompute)."""
    conn = _get_conn()
    conn.execute("DELETE FROM daily_timezones WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO daily_timezones(user_id, date, offset_hours, wakeup_utc) VALUES (?, ?, ?, ?)",
        [(user_id, d.date, d.offset_hours, d.wakeup_utc) for d in days],
    )
    conn.commit()


def get_daily_timezones(
    user_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list[DayTimezone]:
    conn = _get_conn()
    where_clauses = ["user_id = ?"]
    params: list = [user_id]
    if from_date:
        where_clauses.append("date >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("date <= ?")
        params.append(to_date)

    where_sql = " AND ".join(where_clauses)
    rows = conn.execute(
        "SELECT date, offset_hours, wakeup_utc FROM daily_timezones "
        "WHERE " + where_sql + " ORDER BY date",
        params,
    ).fetchall()
    return [DayTimezone(r["date"], r["offset_hours"], r["wakeup_utc"]) for r in rows]
