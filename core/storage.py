from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from core.models import StatusEvent, SleepPeriod, DayTimezone

_local = threading.local()
_db_path: str | None = None

log = logging.getLogger(__name__)


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


# ── Schema & Migration ────────────────────────────────

_SCHEMA_NEW = """
    CREATE TABLE IF NOT EXISTS users (
        user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id     INTEGER UNIQUE,
        discord_id      INTEGER UNIQUE,
        username        TEXT,
        label           TEXT NOT NULL,
        current_status      TEXT,
        telegram_status     TEXT,
        discord_status      TEXT,
        current_tz_offset   REAL
    );

    CREATE TABLE IF NOT EXISTS events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES users(user_id),
        timestamp_utc   TEXT NOT NULL,
        status          TEXT NOT NULL,
        raw_status_type TEXT NOT NULL,
        source          TEXT NOT NULL DEFAULT 'telegram'
    );

    CREATE INDEX IF NOT EXISTS idx_events_user_ts
        ON events(user_id, timestamp_utc);

    CREATE INDEX IF NOT EXISTS idx_events_user_source_ts
        ON events(user_id, source, timestamp_utc);

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
"""


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}

    if not columns:
        # Fresh install — create everything from scratch
        conn.executescript(_SCHEMA_NEW)
        return

    if "telegram_id" in columns:
        # Already migrated — just ensure indexes/tables exist
        conn.executescript(_SCHEMA_NEW)
        return

    # ── Legacy migration: old schema has user_id as direct Telegram ID ──
    log.info("Migrating database to new schema (telegram_id/discord_id)...")

    conn.execute("PRAGMA foreign_keys=OFF")

    # 1. Recreate users table with new schema
    conn.executescript("""
        CREATE TABLE users_new (
            user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     INTEGER UNIQUE,
            discord_id      INTEGER UNIQUE,
            username        TEXT,
            label           TEXT NOT NULL,
            current_status      TEXT,
            telegram_status     TEXT,
            discord_status      TEXT,
            current_tz_offset   REAL
        );

        INSERT INTO users_new (telegram_id, username, label, current_status, current_tz_offset)
            SELECT user_id, username, label, current_status, current_tz_offset FROM users;
    """)

    # 2. Remap user_id in events, sleep_periods, daily_timezones
    #    Old user_id was the telegram_id; new user_id is autoincrement
    for table in ("events", "sleep_periods", "daily_timezones"):
        conn.execute(f"""
            UPDATE {table}
            SET user_id = (
                SELECT users_new.user_id FROM users_new
                WHERE users_new.telegram_id = {table}.user_id
            )
        """)

    # 3. Add source column to events if not present
    event_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "source" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN source TEXT NOT NULL DEFAULT 'telegram'")

    # 4. Swap tables
    conn.executescript("""
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
    """)

    # 5. Set telegram_status from current_status for existing users
    conn.execute("UPDATE users SET telegram_status = current_status")

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()

    # Ensure indexes exist
    conn.executescript(_SCHEMA_NEW)
    log.info("Migration complete.")


# ── Users ──────────────────────────────────────────────

def ensure_user(
    label: str,
    telegram_id: Optional[int] = None,
    discord_id: Optional[int] = None,
    username: Optional[str] = None,
) -> int:
    """Create or update a user. Returns the internal user_id."""
    conn = _get_conn()

    # Try to find existing user by telegram_id or discord_id
    existing = None
    if telegram_id is not None:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    if existing is None and discord_id is not None:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE discord_id = ?", (discord_id,)
        ).fetchone()

    if existing:
        uid = existing[0]
        # Update fields that may have been added
        if telegram_id is not None:
            conn.execute("UPDATE users SET telegram_id = ? WHERE user_id = ?", (telegram_id, uid))
        if discord_id is not None:
            conn.execute("UPDATE users SET discord_id = ? WHERE user_id = ?", (discord_id, uid))
        if username is not None:
            conn.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, uid))
        conn.commit()
        return uid

    # Insert new user
    cur = conn.execute(
        "INSERT INTO users(telegram_id, discord_id, username, label) VALUES (?, ?, ?, ?)",
        (telegram_id, discord_id, username, label),
    )
    conn.commit()
    return cur.lastrowid


def get_user(user_id: int) -> Optional[dict]:
    row = _get_conn().execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_telegram_id(telegram_id: int) -> Optional[int]:
    """Return internal user_id for a telegram_id, or None."""
    row = _get_conn().execute(
        "SELECT user_id FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    return row[0] if row else None


def get_user_by_discord_id(discord_id: int) -> Optional[int]:
    """Return internal user_id for a discord_id, or None."""
    row = _get_conn().execute(
        "SELECT user_id FROM users WHERE discord_id = ?", (discord_id,)
    ).fetchone()
    return row[0] if row else None


def get_all_users() -> list[dict]:
    rows = _get_conn().execute("SELECT * FROM users").fetchall()
    return [dict(r) for r in rows]


def update_user_status(user_id: int, status: str, source: str = "telegram") -> None:
    """Update per-source status and recompute combined current_status."""
    conn = _get_conn()
    col = "telegram_status" if source == "telegram" else "discord_status"
    conn.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (status, user_id))
    # Recompute: online if any source is online
    conn.execute("""
        UPDATE users SET current_status =
            CASE WHEN telegram_status = 'online' OR discord_status = 'online'
                 THEN 'online' ELSE 'offline' END
        WHERE user_id = ?
    """, (user_id,))
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
    conn = _get_conn()
    cur = conn.cursor()
    source = event.source

    # Per-source deduplication: get last 2 events for this user+source
    cur.execute("""
        SELECT id, status
        FROM events
        WHERE user_id = ? AND source = ?
        ORDER BY id DESC
        LIMIT 2
    """, (user_id, source))
    rows = cur.fetchall()

    should_insert = True

    if len(rows) == 2:
        last_id, last_status = rows[0]
        prev_id, prev_status = rows[1]
        if event.status == last_status == prev_status:
            cur.execute("""
                UPDATE events
                SET timestamp_utc = ?, raw_status_type = ?
                WHERE id = ?
            """, (event.timestamp_utc, event.raw_status_type, last_id))
            should_insert = False

    if should_insert:
        cur.execute(
            "INSERT INTO events(user_id, timestamp_utc, status, raw_status_type, source) VALUES (?, ?, ?, ?, ?)",
            (user_id, event.timestamp_utc, event.status, event.raw_status_type, source),
        )

    # Update per-source status
    col = "telegram_status" if source == "telegram" else "discord_status"
    cur.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (event.status, user_id))
    cur.execute("""
        UPDATE users SET current_status =
            CASE WHEN telegram_status = 'online' OR discord_status = 'online'
                 THEN 'online' ELSE 'offline' END
        WHERE user_id = ?
    """, (user_id,))

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

    where_sql = " AND ".join(where_clauses)

    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE " + where_sql, params
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT timestamp_utc, status, raw_status_type, source FROM events "
        "WHERE " + where_sql + " ORDER BY timestamp_utc "
        "LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    events = [StatusEvent(r["timestamp_utc"], r["status"], r["raw_status_type"], r["source"]) for r in rows]
    return events, total


def get_all_events_for_user(user_id: int) -> list[StatusEvent]:
    """Get all events for analysis (both sources, sorted by timestamp)."""
    rows = _get_conn().execute(
        "SELECT timestamp_utc, status, raw_status_type, source FROM events "
        "WHERE user_id = ? ORDER BY timestamp_utc",
        (user_id,),
    ).fetchall()
    return [StatusEvent(r["timestamp_utc"], r["status"], r["raw_status_type"], r["source"]) for r in rows]


def get_last_event(user_id: int) -> Optional[StatusEvent]:
    row = _get_conn().execute(
        "SELECT timestamp_utc, status, raw_status_type, source FROM events "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return StatusEvent(row["timestamp_utc"], row["status"], row["raw_status_type"], row["source"])
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
