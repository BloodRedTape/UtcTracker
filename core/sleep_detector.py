from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from core.models import StatusEvent, SleepPeriod, DayTimezone
from core import storage

# --- Конфигурация для понимания ---
# MIN_ONLINE_NOISE_SECONDS = 10  -> Если онлайн < 10 сек, мы считаем, что это глюк сети (удаляем)
# MAX_INTERRUPTION_MINUTES = 45  -> Если бодрствовал < 45 мин, склеиваем в один сон

def _parse_ts(ts: str) -> datetime:
    """Parse ISO 8601 timestamp string to datetime."""
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts).replace(tzinfo=None)

def _dedup(events: list[StatusEvent]) -> list[StatusEvent]:
    """Collapse consecutive events with the same status, keeping the first."""
    if not events:
        return []
    result = [events[0]]
    for e in events[1:]:
        if e.status != result[-1].status:
            result.append(e)
    return result


def _merge_sources(events: list[StatusEvent]) -> list[StatusEvent]:
    """Merge multi-source events into a single combined status stream.

    User is 'online' if ANY source is online, 'offline' only when ALL are offline.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e.timestamp_utc)

    source_state: dict[str, str] = {}
    combined_status = "offline"
    result: list[StatusEvent] = []

    for e in sorted_events:
        source_state[e.source] = e.status
        new_combined = "online" if any(s == "online" for s in source_state.values()) else "offline"

        if new_combined != combined_status:
            combined_status = new_combined
            result.append(StatusEvent(
                timestamp_utc=e.timestamp_utc,
                status=combined_status,
                raw_status_type=e.raw_status_type,
                source="combined",
            ))

    return result


def _filter_network_noise(events: list[StatusEvent], min_online_seconds: int) -> list[StatusEvent]:
    """
    Удаляет очень короткие периоды онлайна (технический шум).
    Если онлайн длился 5 секунд - считаем, что юзер и не выходил из оффлайна.
    """
    if not events:
        return []

    # Merge multi-source events into combined status stream, then sort
    sorted_events = _merge_sources(events)
    filtered = []
    
    i = 0
    while i < len(sorted_events):
        current = sorted_events[i]
        
        # Логика: Если видим Online -> Offline, проверяем длительность
        if current.status == "online" and i + 1 < len(sorted_events):
            next_event = sorted_events[i+1]
            if next_event.status == "offline":
                delta = _parse_ts(next_event.timestamp_utc) - _parse_ts(current.timestamp_utc)
                
                # Если это просто "блип" сети (меньше порога), пропускаем оба события.
                # Таким образом, окружающие оффлайны схлопнутся в один длинный оффлайн.
                if delta.total_seconds() < min_online_seconds:
                    i += 2
                    continue

        filtered.append(current)
        i += 1
        
    return filtered

def _calculate_tz_offset(wakeup_utc: datetime, assumed_local_hour: int) -> float:
    # (Твоя функция без изменений)
    utc_decimal_hour = wakeup_utc.hour + wakeup_utc.minute / 60.0
    offset = assumed_local_hour - utc_decimal_hour
    if offset < -12: offset += 24
    elif offset > 14: offset -= 24
    return round(offset * 2) / 2

def _detect_sleep_periods(
    events: list[StatusEvent],
    threshold_hours: float,      # Например, 4.0 часа (минимум для зачета сна)
    min_online_seconds: int,     # Например, 10 сек (фильтр шума)
    assumed_wakeup_hour: int,    # Твой параметр (например, 9)
    max_interruption_minutes: int = 45 # Новое: "туалетный перерыв"
) -> list[SleepPeriod]:
    
    # 1. Чистим технический шум и повторно дедуплицируем
    cleaned = _dedup(_filter_network_noise(events, min_online_seconds))
    
    # 2. Собираем "сырые" отрезки оффлайна
    raw_periods = []
    last_offline_time = None
    last_offline_ts = None

    for event in cleaned:
        ts = _parse_ts(event.timestamp_utc)
        if event.status == "offline":
            last_offline_time = ts
            last_offline_ts = event.timestamp_utc
        elif event.status == "online" and last_offline_time is not None:
            # Нашли отрезок оффлайна
            duration = (ts - last_offline_time).total_seconds() / 3600
            raw_periods.append({
                "start": last_offline_time,
                "end": ts,
                "start_ts": last_offline_ts,
                "end_ts": event.timestamp_utc,
                "duration": duration
            })
            last_offline_time = None # Сброс

    if not raw_periods:
        return []

    # 3. Склеиваем перерывы (Merge Logic)
    # Только для периодов, которые уже похожи на сон (>= 1 час).
    # Короткие offline'ы (человек отложил телефон на 10 мин) не склеиваем —
    # иначе весь день превращается в один гигантский "сон".
    min_merge_hours = threshold_hours / 2  # период должен быть >= половины порога сна
    candidates = [p for p in raw_periods if p["duration"] >= min_merge_hours]

    merged_periods = []
    if candidates:
        current_period = candidates[0]

        for next_period in candidates[1:]:
            # Разрыв между концом текущего и началом следующего
            awake_gap_minutes = (next_period["start"] - current_period["end"]).total_seconds() / 60

            if awake_gap_minutes <= max_interruption_minutes:
                # СКЛЕИВАЕМ: Продлеваем текущий период
                current_period["end"] = next_period["end"]
                current_period["end_ts"] = next_period["end_ts"]
                current_period["duration"] = (current_period["end"] - current_period["start"]).total_seconds() / 3600
            else:
                merged_periods.append(current_period)
                current_period = next_period

        merged_periods.append(current_period)

    # 4. Финальная фильтрация по длительности и расчет TZ
    final_results = []
    for p in merged_periods:
        if p["duration"] >= threshold_hours:
            wakeup_time = p["end"]
            tz_offset = _calculate_tz_offset(wakeup_time, assumed_wakeup_hour)
            wake_date = wakeup_time.strftime("%Y-%m-%d")

            final_results.append(SleepPeriod(
                offline_at_utc=p["start_ts"],
                online_at_utc=p["end_ts"],
                gap_hours=round(p["duration"], 2),
                estimated_tz_offset=tz_offset,
                date=wake_date,
            ))

    return final_results


def _compute_daily_timezones(sleep_periods: list[SleepPeriod]) -> list[DayTimezone]:
    """Pick the longest sleep period per day as that day's timezone estimate."""
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