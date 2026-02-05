from __future__ import annotations

from datetime import datetime
from collections import defaultdict

from core.models import StatusEvent, SleepPeriod, DayTimezone
from core import storage


def _parse_ts(ts: str) -> datetime:
    """Parse ISO 8601 timestamp string to datetime."""
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts).replace(tzinfo=None)


def _filter_noise(events: list[StatusEvent], min_online_seconds: int) -> list[StatusEvent]:
    """Remove brief online blips shorter than min_online_seconds."""
    if not events or min_online_seconds <= 0:
        return list(events)

    filtered: list[StatusEvent] = []
    i = 0
    while i < len(events):
        if (
            events[i].status == "online"
            and i + 1 < len(events)
            and events[i + 1].status == "offline"
        ):
            gap = _parse_ts(events[i + 1].timestamp_utc) - _parse_ts(events[i].timestamp_utc)
            if gap.total_seconds() < min_online_seconds:
                i += 2  # skip the brief online + offline pair
                continue
        filtered.append(events[i])
        i += 1
    return filtered


def _calculate_tz_offset(wakeup_utc: datetime, assumed_local_hour: int) -> float:
    """
    Calculate timezone offset from a wake-up UTC time.

    If user came online at 06:00 UTC and we assume it's 9:00 local:
      offset = 9 - 6 = +3.0 -> UTC+3
    """
    utc_decimal_hour = wakeup_utc.hour + wakeup_utc.minute / 60.0
    offset = assumed_local_hour - utc_decimal_hour

    # Normalize to valid timezone range [-12, +14]
    if offset < -12:
        offset += 24
    elif offset > 14:
        offset -= 24

    # Round to nearest 0.5 (timezones come in whole and half-hour offsets)
    offset = round(offset * 2) / 2

    return offset


def _detect_sleep_periods(
    events: list[StatusEvent],
    threshold_hours: float,
    min_online_seconds: int,
    assumed_wakeup_hour: int,
) -> list[SleepPeriod]:
    """
    Walk through events, find offline->online gaps >= threshold_hours.
    Each such gap is a sleep period with a timezone estimate.
    """
    cleaned = _filter_noise(events, min_online_seconds)

    sleep_periods: list[SleepPeriod] = []
    last_offline_time: datetime | None = None
    last_offline_ts: str | None = None

    for event in cleaned:
        if event.status == "offline":
            last_offline_time = _parse_ts(event.timestamp_utc)
            last_offline_ts = event.timestamp_utc
        elif event.status == "online" and last_offline_time is not None:
            online_time = _parse_ts(event.timestamp_utc)
            gap_hours = (online_time - last_offline_time).total_seconds() / 3600

            if gap_hours >= threshold_hours:
                tz_offset = _calculate_tz_offset(online_time, assumed_wakeup_hour)
                wake_date = online_time.strftime("%Y-%m-%d")

                sleep_periods.append(SleepPeriod(
                    offline_at_utc=last_offline_ts,
                    online_at_utc=event.timestamp_utc,
                    gap_hours=round(gap_hours, 2),
                    estimated_tz_offset=tz_offset,
                    date=wake_date,
                ))

            last_offline_time = None
            last_offline_ts = None

    return sleep_periods


def _compute_daily_timezones(sleep_periods: list[SleepPeriod]) -> list[DayTimezone]:
    """
    For each day, pick the sleep period with the longest gap (the main sleep)
    and use its timezone estimate as the day's timezone.
    """
    by_date: dict[str, list[SleepPeriod]] = defaultdict(list)
    for sp in sleep_periods:
        by_date[sp.date].append(sp)

    daily: list[DayTimezone] = []
    for date_str in sorted(by_date.keys()):
        periods = by_date[date_str]
        main_sleep = max(periods, key=lambda sp: sp.gap_hours)
        daily.append(DayTimezone(
            date=date_str,
            offset_hours=main_sleep.estimated_tz_offset,
            wakeup_utc=main_sleep.online_at_utc,
        ))

    return daily


def analyze(user_id: int, config_tracking: dict) -> None:
    """
    Run full analysis on a user:
    1. Load all events from DB
    2. Detect sleep periods
    3. Compute daily timezone estimates
    4. Save results back to DB
    5. Update user's current_tz_offset
    """
    threshold = config_tracking.get("sleep_threshold_hours", 4.0)
    min_online = config_tracking.get("min_online_duration_seconds", 30)
    assumed_hour = config_tracking.get("assumed_wakeup_hour", 9)

    events = storage.get_all_events_for_user(user_id)
    if not events:
        return

    sleep_periods = _detect_sleep_periods(events, threshold, min_online, assumed_hour)
    daily_timezones = _compute_daily_timezones(sleep_periods)

    storage.replace_sleep_periods(user_id, sleep_periods)
    storage.replace_daily_timezones(user_id, daily_timezones)

    if daily_timezones:
        storage.update_user_tz(user_id, daily_timezones[-1].offset_hours)
