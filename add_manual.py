"""Add a manual online interval ("from … to …") for a tracked user.

Writes two `manual`-source events — `online` at the start, `offline` at the
end — into the same SQLite DB the app uses, then re-runs sleep detection so the
new interval is reflected everywhere immediately.

Time input MUST carry an explicit UTC offset (e.g. ``+03:00``). Bare/`Z`/UTC
timestamps are rejected on purpose: you state the wall-clock time *and* the zone
it was in, and the tool converts to the UTC that the DB stores.

Usage:
    python add_manual.py <data_dir> --user <ident> \\
        --from "2026-07-07 23:00 +03:00" --to "2026-07-08 01:30 +03:00"

`<ident>` resolves, in order: internal user_id → telegram_id → discord_id →
label (case-insensitive). Use --dry-run to preview without writing.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import storage, sleep_detector
from core.models import StatusEvent
from main import load_config


# Accepts an explicit offset only: "+HH:MM", "+HHMM", "+HH", or "-…". A "Z"
# suffix or a bare timestamp with no offset is rejected below.
_OFFSET_RE = re.compile(r"[+-]\d{2}(:?\d{2})?$")


def parse_offset_ts(raw: str) -> str:
    """Parse a timestamp that MUST carry an explicit UTC offset → UTC ISO 'Z'.

    Accepts a space or 'T' between date and time, and the offset either glued to
    the time or separated by a space, e.g.:
        "2026-07-07 23:00 +03:00"   "2026-07-07T23:00:00+03:00"   "2026-07-07 23:00+0300"
    Rejects 'Z'/UTC and offset-less input.
    """
    s = raw.strip()

    if s.endswith("Z") or s.endswith("z"):
        raise ValueError(
            f"'{raw}': 'Z'/UTC time is not allowed — pass an explicit offset like +03:00"
        )

    # Allow "<datetime> <offset>" with a space before the offset by gluing it on.
    m = re.search(r"\s([+-]\d{2}:?\d{0,2})$", s)
    if m:
        s = s[: m.start()] + m.group(1)

    if not _OFFSET_RE.search(s):
        raise ValueError(
            f"'{raw}': missing timezone offset — end the value with e.g. +03:00 or -05:00"
        )

    # Normalize the separator to 'T' for fromisoformat.
    s = s.replace(" ", "T", 1) if " " not in s.rsplit("T", 1)[0] else s
    # Ensure seconds are present (fromisoformat is fine without, but be explicit).
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"'{raw}': cannot parse — {e}") from e

    if dt.tzinfo is None:
        # Should not happen given the regex, but guard anyway.
        raise ValueError(f"'{raw}': missing timezone offset")

    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_user(ident: str) -> int | None:
    """Resolve a user identifier to an internal user_id.

    Tries, in order: internal user_id, telegram_id, discord_id, then label.
    """
    users = storage.get_all_users()

    if ident.isdigit():
        n = int(ident)
        for u in users:
            if u["user_id"] == n:
                return n
        for u in users:
            if u.get("telegram_id") == n or u.get("discord_id") == n:
                return u["user_id"]

    lowered = ident.lower()
    for u in users:
        if (u.get("label") or "").lower() == lowered:
            return u["user_id"]

    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Add a manual online interval for a tracked user.",
    )
    p.add_argument("data_dir", help="Directory holding config.json and nickutc.db")
    p.add_argument("--user", required=True,
                   help="user_id, telegram_id, discord_id, or label")
    p.add_argument("--from", dest="from_ts", required=True, metavar="TS",
                   help='Interval start WITH offset, e.g. "2026-07-07 23:00 +03:00"')
    p.add_argument("--to", dest="to_ts", required=True, metavar="TS",
                   help='Interval end WITH offset, e.g. "2026-07-08 01:30 +03:00"')
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be written without touching the DB")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = str(Path(args.data_dir).resolve())

    try:
        start_utc = parse_offset_ts(args.from_ts)
        end_utc = parse_offset_ts(args.to_ts)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if end_utc <= start_utc:
        print(
            f"error: --to ({end_utc}) must be strictly after --from ({start_utc})",
            file=sys.stderr,
        )
        return 2

    config = load_config(data_dir)
    db_path = str(Path(data_dir) / "nickutc.db")
    storage.init(db_path)

    uid = resolve_user(args.user)
    if uid is None:
        print(f"error: no user matched '{args.user}'", file=sys.stderr)
        print("known users:", file=sys.stderr)
        for u in storage.get_all_users():
            print(
                f"  user_id={u['user_id']} label={u['label']!r} "
                f"telegram_id={u.get('telegram_id')} discord_id={u.get('discord_id')}",
                file=sys.stderr,
            )
        return 1

    user = storage.get_user(uid)
    print(f"user: user_id={uid} label={user['label']!r}")
    print(f"manual online (UTC): {start_utc}  →  {end_utc}")

    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    storage.append_event(
        uid, StatusEvent(start_utc, "online", "ManualOnline", "manual")
    )
    storage.append_event(
        uid, StatusEvent(end_utc, "offline", "ManualOffline", "manual")
    )

    # Full recompute: the interval may land anywhere in history, and manual
    # events can predate the incremental window.
    sleep_detector.analyze(uid, config.get("tracking", {}), full=True)

    print("done: 2 manual events written, sleep analysis recomputed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
