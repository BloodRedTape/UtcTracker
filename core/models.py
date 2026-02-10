from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StatusEvent:
    """A single online/offline transition."""
    timestamp_utc: str  # ISO 8601, e.g. "2025-06-15T06:00:00Z"
    status: str  # "online" or "offline"
    raw_status_type: str  # "UserStatusOnline", "UserStatusOffline", etc.
    source: str = "telegram"  # "telegram" or "discord"


@dataclass
class SleepPeriod:
    """A detected sleep gap with timezone estimate."""
    offline_at_utc: str
    online_at_utc: str
    gap_hours: float
    estimated_tz_offset: float  # e.g. +3.0 for UTC+3
    date: str  # YYYY-MM-DD, the day user woke up


@dataclass
class DayTimezone:
    """Timezone estimate for a specific day."""
    date: str  # YYYY-MM-DD
    offset_hours: float  # e.g. +3.0
    wakeup_utc: str  # ISO 8601 timestamp of the wakeup event
