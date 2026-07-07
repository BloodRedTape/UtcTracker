"""Daily SQLite backup → zip → Telegram.

A dedicated Telegram *bot* (separate from the tracker account) ships a
compressed snapshot of the database to a fixed chat every day at 12:00 local
time.

The snapshot is taken with sqlite3's online-backup API (``conn.backup()``),
NOT a raw file copy — the live database runs in WAL mode, so copying the
``.db`` file alone could miss committed data still sitting in the ``-wal``
file. The backup API produces a single, self-consistent ``.db``.

Configuration (``backup`` section of ``config.json``)::

    "backup": {
        "bot_token": "123456:ABC...",     # dedicated backup bot
        "chat_id": -1001234567890,         # where to send the archive
        "hour": 12,                        # optional, local hour (default 12)
        "minute": 0                        # optional, local minute (default 0)
    }

If ``bot_token`` or ``chat_id`` is missing, the scheduler is not started and
this module is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

from core import storage, monitoring

log = logging.getLogger(__name__)

# Name of the archive as it appears in Telegram, and of the .db inside it.
ARCHIVE_NAME = "utc_backup.zip"
DB_ENTRY_NAME = "nickutc.db"
CAPTION = "#utc"

_TELEGRAM_API = "https://api.telegram.org"


def _snapshot_db(dest_path: str) -> None:
    """Write a consistent copy of the live DB to ``dest_path`` via backup API.

    Uses a fresh read connection so it does not interfere with the app's
    thread-local write connection. WAL data is included automatically.
    """
    src_path = storage.get_db_path()
    if not src_path:
        raise RuntimeError("storage is not initialized (no db path)")

    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def create_backup_zip(out_dir: str) -> Path:
    """Snapshot the DB and pack it into ``utc_backup.zip`` inside ``out_dir``.

    Returns the path to the created archive.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Snapshot into a temp .db first, then compress into the archive.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db = Path(tmp.name)
    try:
        _snapshot_db(str(tmp_db))
        archive_path = out / ARCHIVE_NAME
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            zf.write(tmp_db, arcname=DB_ENTRY_NAME)
    finally:
        tmp_db.unlink(missing_ok=True)

    return archive_path


async def send_backup(bot_token: str, chat_id, archive_path: Path) -> None:
    """Send the archive to ``chat_id`` via the backup bot's sendDocument."""
    url = f"{_TELEGRAM_API}/bot{bot_token}/sendDocument"

    data = aiohttp.FormData()
    data.add_field("chat_id", str(chat_id))
    data.add_field("caption", CAPTION)
    data.add_field(
        "document",
        archive_path.read_bytes(),
        filename=archive_path.name,
        content_type="application/zip",
    )

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=data) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(
                    f"Telegram sendDocument failed ({resp.status}): {body}"
                )


async def run_backup(config: dict) -> None:
    """Create the archive and send it once. Raises on failure."""
    backup_cfg = config.get("backup", {})
    bot_token = backup_cfg.get("bot_token")
    chat_id = backup_cfg.get("chat_id")
    if not bot_token or chat_id is None:
        raise RuntimeError("backup.bot_token / backup.chat_id not configured")

    with tempfile.TemporaryDirectory(prefix="nickutc-backup-") as tmp_dir:
        archive_path = await asyncio.to_thread(create_backup_zip, tmp_dir)
        size_kb = archive_path.stat().st_size / 1024
        await send_backup(bot_token, chat_id, archive_path)
        log.info("Backup sent to Telegram (%s, %.1f KB)", ARCHIVE_NAME, size_kb)


def _seconds_until(hour: int, minute: int, now: datetime | None = None) -> float:
    """Seconds from ``now`` until the next local ``hour:minute``."""
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def backup_scheduler_loop(config: dict) -> None:
    """Run a daily backup at the configured local time (default 12:00).

    No-op (returns immediately) if the backup bot/chat aren't configured, so
    it's safe to always schedule this task.
    """
    backup_cfg = config.get("backup", {})
    bot_token = backup_cfg.get("bot_token")
    chat_id = backup_cfg.get("chat_id")
    if not bot_token or chat_id is None:
        log.info("Backup disabled (backup.bot_token / backup.chat_id not set)")
        return

    hour = int(backup_cfg.get("hour", 12))
    minute = int(backup_cfg.get("minute", 0))
    log.info("Daily backup scheduled for %02d:%02d local time", hour, minute)

    while True:
        delay = _seconds_until(hour, minute)
        log.info("Next backup in %.1f h", delay / 3600)
        await asyncio.sleep(delay)
        try:
            await run_backup(config)
        except Exception as e:
            # Never let a backup failure kill the loop — log, report, retry
            # tomorrow.
            log.error("Backup failed: %s", e)
            monitoring.capture_exception(e)
