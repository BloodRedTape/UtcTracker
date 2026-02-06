# NickUtc

Telegram user online-status tracker. Monitors when users go online/offline, detects sleep patterns, and visualizes activity.

## Structure

- `core/` — backend logic
  - `telegram_tracker.py` — Telethon-based tracker (event handler + polling fallback)
  - `storage.py` — SQLite storage layer (events, users, sleep periods)
  - `sleep_detector.py` — sleep pattern analysis
  - `models.py` — dataclasses (`StatusEvent`, etc.)
- `web/` — Flask web app
  - `server.py` — app entry point
  - `routes.py` — API endpoints (`/api/users/{id}/stats`, sleep-periods, timezone-history)
- `static/js/app.js` — frontend (charts, timeline rendering)
- `config.json` — tracking config (polling interval, sleep threshold, etc.)

## Key concepts

- Events are `online`/`offline` transitions stored in SQLite with UTC timestamps
- Deduplication: consecutive events with the same status are skipped
- Online periods are computed by pairing online→offline events
- Polling interval and sleep thresholds are configurable via `config.json`

## Timezone handling

- **Backend**: all timestamps stored and transmitted in UTC (ISO format with `Z` suffix)
- **Frontend**: displays all times in user's local browser timezone
  - `timeAgo()` — calculates relative time correctly using UTC timestamps
  - `formatDateTime()` — converts UTC to local timezone for display
  - System clock in header shows local time with timezone indicator
- **Computed user timezone**: estimated from sleep patterns, displayed as `UTC+X`
  - User's local time is calculated from UTC + their estimated offset
  - Wake-up Pattern chart remains in UTC hours (for timezone analysis)
