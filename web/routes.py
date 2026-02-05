from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query

from core import storage


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
        user = storage.get_user(user_id)
        if user is None:
            return {"error": "User not found"}
        return _user_summary(user)

    @router.get("/users/{user_id}/events")
    async def get_events(
        user_id: int,
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to"),
        page: int = Query(1, ge=1),
        per_page: int = Query(200, ge=1, le=1000),
    ):
        user = storage.get_user(user_id)
        if user is None:
            return {"error": "User not found"}

        offset = (page - 1) * per_page
        events, total = storage.get_events(user_id, from_date, to_date, per_page, offset)

        return {
            "events": [asdict(e) for e in events],
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    @router.get("/users/{user_id}/sleep-periods")
    async def get_sleep_periods(
        user_id: int,
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to"),
    ):
        periods = storage.get_sleep_periods(user_id, from_date, to_date)
        return [asdict(sp) for sp in periods]

    @router.get("/users/{user_id}/timezone-history")
    async def get_timezone_history(
        user_id: int,
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to"),
    ):
        history = storage.get_daily_timezones(user_id, from_date, to_date)
        return [asdict(dt) for dt in history]

    @router.get("/users/{user_id}/stats")
    async def get_stats(
        user_id: int,
        days: int = Query(30, ge=1, le=365),
    ):
        user = storage.get_user(user_id)
        if user is None:
            return {"error": "User not found"}

        daily_tzs = storage.get_daily_timezones(user_id)
        sleep_periods = storage.get_sleep_periods(user_id)

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

        # Online periods for the activity timeline
        all_events = storage.get_all_events_for_user(user_id)
        online_periods = []
        i = 0
        while i < len(all_events):
            if all_events[i].status == "online":
                start = all_events[i].timestamp_utc
                if i + 1 < len(all_events) and all_events[i + 1].status == "offline":
                    end = all_events[i + 1].timestamp_utc
                    i += 2
                else:
                    end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    i += 1
                online_periods.append({"start": start, "end": end})
            else:
                i += 1

        offsets_seen = sorted(set(dt.offset_hours for dt in daily_tzs))

        return {
            "user_id": user_id,
            "period_days": days,
            "total_events": storage.get_events_count(user_id),
            "total_sleep_periods": len(sleep_periods),
            "timezone_offsets_seen": offsets_seen,
            "wakeup_times": wakeup_times[-days:],
            "online_periods": online_periods[-500:],
        }

    return router
