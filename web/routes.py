from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Callable

from fastapi import APIRouter, Query, HTTPException, status

from core import storage
from web.security import validate_user_id

# ── Runtime cache ──────────────────────────────────────
#
# Domain fact: a user's history older than ~4 days is effectively immutable.
# Sleep detection only ever rewrites recent periods, and old events don't
# change. So once a request whose date range is entirely "stale" has been
# computed, its result is stable until either the process restarts or the
# calendar day rolls over (which can shift the stale/fresh boundary).
#
# We cache the *fully-built response object*. Every cache key's first element
# is the UTC "day bucket" it was computed for. Bucketing by day gives free
# expiry: on a new day, new keys are used and stale-day entries are swept out
# on the next miss (keeping memory bounded without a TTL mechanism). A query
# that touches the live (last-4-days) window is never cached.

_CACHE_FRESH_DAYS = 4
_cache: dict[tuple, Any] = {}


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fresh_boundary() -> str:
    """Oldest date (YYYY-MM-DD) still in the 'fresh' / mutable window.

    Dates >= this are recomputed live; dates < this are immutable.
    """
    today = datetime.now(timezone.utc)
    return (today - timedelta(days=_CACHE_FRESH_DAYS)).strftime("%Y-%m-%d")


def _day_before(date_str: str) -> str:
    return (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def _cached_date_range(
    name: str,
    user_id: int,
    fetch: Callable[[Optional[str], Optional[str]], list],
) -> list:
    """Serve a full-history, date-keyed list endpoint with a cached older half.

    These endpoints always return the user's whole history. The response is
    split at the fresh boundary:
      • dates older than the boundary are immutable → built once, cached for
        the day;
      • the last ~4 days (date >= boundary) are fetched live every time.

    `fetch(from, to)` filters records inclusively by their own `date` field, so
    the stale half ends the day *before* the boundary to avoid overlap.
    """
    boundary = _fresh_boundary()         # first fresh day
    stale_end = _day_before(boundary)    # last stale day (inclusive)

    key = (name, user_id)
    stale = _cached(_today_utc(), key, lambda: fetch(None, stale_end))
    fresh = fetch(boundary, None)
    return stale + fresh


def _cached(day: str, key: tuple, build: Callable[[], Any]) -> Any:
    """Return cached value for `key`, computing it on a miss.

    `day` is the UTC day bucket this entry belongs to and is prepended to the
    stored key. On a miss we first drop any entries from other days so the
    cache cannot grow without bound across long uptimes.
    """
    full_key = (day,) + key
    if full_key in _cache:
        return _cache[full_key]
    # Sweep entries from other day buckets.
    stale = [k for k in _cache if k[0] != day]
    for k in stale:
        del _cache[k]
    value = build()
    _cache[full_key] = value
    return value


# If two consecutive `online` events are far apart, an `offline` event
# between them was likely missed (bot restart, network hiccup, Telegram
# UserUpdate dropped). Closing the first period at the first online's own
# timestamp prevents fake multi-hour online stretches.
_MAX_ONLINE_GAP_SECONDS = 600


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _build_online_periods(events_by_source: dict[str, list]) -> list[dict]:
    """Turn per-source event lists into online intervals.

    `events_by_source` maps a source name to its events sorted by timestamp.
    An unterminated trailing `online` is closed at "now".
    """
    online_periods: list[dict] = []
    for source, evts in events_by_source.items():
        i = 0
        while i < len(evts):
            if evts[i].status == "online":
                start = evts[i].timestamp_utc
                # Walk through consecutive `online` events, but split if the
                # gap between them exceeds _MAX_ONLINE_GAP_SECONDS.
                split_end: Optional[str] = None
                while i + 1 < len(evts) and evts[i + 1].status == "online":
                    gap = (_parse_ts(evts[i + 1].timestamp_utc)
                           - _parse_ts(evts[i].timestamp_utc)).total_seconds()
                    if gap > _MAX_ONLINE_GAP_SECONDS:
                        split_end = evts[i].timestamp_utc
                        break
                    i += 1
                if split_end is not None:
                    online_periods.append({"start": start, "end": split_end, "source": source})
                    i += 1
                    continue
                if i + 1 < len(evts) and evts[i + 1].status == "offline":
                    end = evts[i + 1].timestamp_utc
                    i += 2
                else:
                    end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    i += 1
                online_periods.append({"start": start, "end": end, "source": source})
            else:
                i += 1
    online_periods.sort(key=lambda p: p["start"])
    return online_periods


def _format_tz(offset: Optional[float]) -> str:
    """Format offset like UTC+3, UTC-5, UTC+5:30."""
    if offset is None:
        return "N/A"
    sign = "+" if offset >= 0 else "-"
    abs_offset = abs(offset)
    hours = int(abs_offset)
    minutes = int((abs_offset - hours) * 60)
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}"


def _user_summary(user: dict) -> dict:
    last_event = storage.get_last_event(user["user_id"])
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "label": user["label"],
        "current_status": user["current_status"],
        "telegram_status": user.get("telegram_status"),
        "discord_status": user.get("discord_status"),
        "telegram_id": user.get("telegram_id"),
        "discord_id": user.get("discord_id"),
        "current_tz_offset": user["current_tz_offset"],
        "timezone_display": _format_tz(user["current_tz_offset"]),
        "last_event_utc": last_event.timestamp_utc if last_event else None,
        "events_count": storage.get_events_count(user["user_id"]),
    }


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/users")
    async def list_users():
        users = storage.get_all_users()
        return [_user_summary(u) for u in users]

    @router.get("/users/{user_id}")
    async def get_user(user_id: int):
        validate_user_id(user_id)
        user = storage.get_user(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        return _user_summary(user)

    @router.get("/users/{user_id}/sleep-periods")
    async def get_sleep_periods(user_id: int):
        validate_user_id(user_id)
        return _cached_date_range(
            "sleep-periods", user_id,
            lambda f, t: [asdict(sp) for sp in storage.get_sleep_periods(user_id, f, t)],
        )

    @router.get("/users/{user_id}/timezone-history")
    async def get_timezone_history(user_id: int):
        validate_user_id(user_id)
        return _cached_date_range(
            "timezone-history", user_id,
            lambda f, t: [asdict(dt) for dt in storage.get_daily_timezones(user_id, f, t)],
        )

    @router.get("/users/{user_id}/online-periods")
    async def get_online_periods(
        user_id: int,
        hours: int = Query(48, ge=1, le=24 * 30),
    ):
        """Online intervals for the activity timeline, limited to a window.

        Only the requested window is read and built, so cost scales with the
        window — not the user's whole history. A period that was already open
        when the window started is anchored by each source's last prior event
        (the frontend clamps it to its own left edge).
        """
        validate_user_id(user_id)
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = storage.get_events_since(user_id, since)

        events_by_source: dict[str, list] = {}
        for ev in events:
            events_by_source.setdefault(ev.source, []).append(ev)

        return {
            "user_id": user_id,
            "window_hours": hours,
            "online_periods": _build_online_periods(events_by_source),
        }

    @router.get("/users/{user_id}/stats")
    async def get_stats(user_id: int):
        validate_user_id(user_id)
        user = storage.get_user(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # All cheap reads from pre-aggregated tables — no event scan here.
        daily_tzs = storage.get_daily_timezones(user_id)

        # Wakeup times for the scatter plot
        wakeup_times = []
        for dt in daily_tzs:
            try:
                ts = dt.wakeup_utc.replace("Z", "+00:00")
                wake = datetime.fromisoformat(ts).replace(tzinfo=None)
                wakeup_times.append({
                    "date": dt.date,
                    "hour_utc": round(wake.hour + wake.minute / 60, 2),
                    "offset": dt.offset_hours,
                })
            except (ValueError, AttributeError):
                pass

        offsets_seen = sorted(set(dt.offset_hours for dt in daily_tzs))

        return {
            "user_id": user_id,
            "total_events": storage.get_events_count(user_id),
            "total_sleep_periods": len(storage.get_sleep_periods(user_id)),
            "timezone_offsets_seen": offsets_seen,
            "wakeup_times": wakeup_times,
        }

    return router
