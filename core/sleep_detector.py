from __future__ import annotations
from datetime import datetime, timedelta
from core.models import StatusEvent, SleepPeriod

# --- Конфигурация для понимания ---
# MIN_ONLINE_NOISE_SECONDS = 10  -> Если онлайн < 10 сек, мы считаем, что это глюк сети (удаляем)
# MAX_INTERRUPTION_MINUTES = 45  -> Если бодрствовал < 45 мин, склеиваем в один сон

def _parse_ts(ts: str) -> datetime:
    """Parse ISO 8601 timestamp string to datetime."""
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts).replace(tzinfo=None)

def _filter_network_noise(events: list[StatusEvent], min_online_seconds: int) -> list[StatusEvent]:
    """
    Удаляет очень короткие периоды онлайна (технический шум).
    Если онлайн длился 5 секунд - считаем, что юзер и не выходил из оффлайна.
    """
    if not events:
        return []

    # Сначала сортируем, чтобы логика не сломалась из-за беспорядка в БД
    sorted_events = sorted(events, key=lambda e: e.timestamp_utc)
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
    
    # 1. Чистим технический шум
    cleaned = _filter_network_noise(events, min_online_seconds)
    
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
    merged_periods = []
    if raw_periods:
        current_period = raw_periods[0]
        
        for next_period in raw_periods[1:]:
            # Разрыв между концом текущего и началом следующего
            awake_gap_minutes = (next_period["start"] - current_period["end"]).total_seconds() / 60
            
            if awake_gap_minutes <= max_interruption_minutes:
                # СКЛЕИВАЕМ: Продлеваем текущий период
                current_period["end"] = next_period["end"]
                current_period["end_ts"] = next_period["end_ts"]
                # Пересчитываем длительность (включая время бодрствования как часть периода сна,
                # либо можно вычитать gap, если хочешь "чистое время сна")
                current_period["duration"] = (current_period["end"] - current_period["start"]).total_seconds() / 3600
            else:
                # Разрыв большой, сохраняем старый, начинаем новый
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