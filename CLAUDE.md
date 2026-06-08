# NickUtc

Multi-source online-status tracker. Monitors when users go online/offline via Telegram and/or Discord, detects sleep patterns, and visualizes activity.

## How it works

1. **Tracking**: `TelegramTracker` (Telethon) and `DiscordTracker` (discord.py) run concurrently. Each listens for status changes and saves `StatusEvent`s in SQLite. Events are tagged with `source` ("telegram" or "discord").
2. **User model**: One user can have both `telegram_id` and `discord_id` linked. Events from both sources merge into a single timeline per user. User is "online" if online in *any* source.
3. **Sleep detection**: On every new event, `sleep_detector.analyze()` re-scans all user events (both sources combined): dedup → noise filter (remove <10s online blips) → find offline gaps ≥ 4h → merge nearby long gaps (≤45min apart) → save `SleepPeriod`s.
4. **Timezone estimation**: From each sleep period's wakeup time, estimate UTC offset assuming wakeup ≈ 9:00 local. Per-day timezone picked from the longest sleep that day.
5. **Web**: FastAPI serves a REST API + static frontend. Frontend renders activity timelines and sleep charts in the browser's local timezone.

## Structure

- `core/` — backend logic
  - `telegram_tracker.py` — Telethon-based tracker (event handler + polling fallback)
  - `discord_tracker.py` — discord.py-based tracker (presence updates)
  - `storage.py` — SQLite storage layer (events, users, sleep periods)
  - `sleep_detector.py` — sleep pattern analysis
  - `models.py` — dataclasses (`StatusEvent`, etc.)
- `web/` — FastAPI web app
  - `server.py` — app entry point
  - `routes.py` — API endpoints (`/api/users/{id}/stats`, online-periods, sleep-periods, timezone-history) + runtime cache
- `static/js/app.js` — frontend (charts, timeline rendering)
- `config.json` — tracking config (polling interval, sleep threshold, etc.)

## Key concepts

- Events are `online`/`offline` transitions stored in SQLite with UTC timestamps and `source` tag
- One user = one person, optionally linked to both Telegram and Discord IDs
- `user_id` in DB is an internal autoincrement, not a platform-specific ID
- Deduplication: consecutive events with the same status **per source** are collapsed
- Sleep detection operates on the **merged** event stream from all sources
- `current_status` = "online" if `telegram_status` or `discord_status` is "online"
- Discord statuses: only `online` → "online"; `idle`, `dnd`, `offline` → "offline"
- Polling interval and sleep thresholds are configurable via `config.json`

## Config format

```json
{
    "telegram": { "api_id": ..., "api_hash": "...", "use_qr_login": true },
    "discord": { "bot_token": "..." },
    "tracked_users": [
        {"label": "Name", "telegram_id": 123456, "discord_id": 789012345678901234},
        {"label": "TgOnly", "telegram_id": 654321},
        {"label": "DcOnly", "discord_id": 987654321098765432}
    ],
    "tracking": { "sleep_threshold_hours": 4.0, "assumed_wakeup_hour": 9, ... },
    "web": { "host": "0.0.0.0", "port": 8111 }
}
```

Both `telegram` and `discord` sections are optional. Each tracked user needs at least one of `telegram_id` / `discord_id`. Legacy `"identifier"` field is supported for backward compatibility.

## DB schema gotchas

- **`user_id` is autoincrement**, NOT the Telegram/Discord ID. Platform IDs live in `telegram_id`/`discord_id` columns on `users` table. All foreign keys (`events`, `sleep_periods`, `daily_timezones`) reference the internal `user_id`.
- **Migration from legacy schema**: `_migrate()` in `storage.py` detects old schema (where `user_id` was the raw Telegram ID) and remaps all tables. It runs automatically on startup.
- **`events.source`** column distinguishes "telegram" vs "discord" events. Deduplication in `append_event()` is **per source** — last 2 events with the same `(user_id, source)`.
- **Per-source status**: `users` table has `telegram_status`, `discord_status`, and computed `current_status` (online if ANY source is online). Updated atomically in `append_event()`.

## Multi-source architecture

- **User linking**: both trackers pass all known IDs from config to `ensure_user()`. This is critical — if Telegram tracker only passes `telegram_id`, and Discord tracker only passes `discord_id`, they create **two separate users** instead of one. Both must pass `discord_id=user_cfg.get("discord_id")` etc.
- **Sleep detection**: `get_all_events_for_user()` returns events from ALL sources sorted by timestamp. Sleep detector treats this as a single stream — `_dedup()` naturally collapses overlapping online/offline from different sources.
- **Activity timeline**: `online_periods` come from a **dedicated** endpoint `GET /api/users/{id}/online-periods?hours=48` (NOT `/stats`). They are built **per source** (split events by source, compute periods separately). Frontend renders them as separate colored rows (TG=blue, DC=purple).
- **Discord bot requirements**: Must have **Presence Intent** and **Server Members Intent** enabled in Discord Developer Portal. Bot must share a guild (server) with tracked users. `on_presence_update` only fires for guild members in the bot's member cache.
- **Discord initial status**: On `on_ready`, bot captures current status from guild member cache via `guild.get_member()`. Without this, the first event only arrives on next status *change*.

## API & caching

- **Frontend always requests full history** — the dashboard (`static/js/app.js`) calls `sleep-periods`, `timezone-history`, `stats` with **no** `from`/`to`/`days`/`page` params. Those range params were removed from `sleep-periods`/`timezone-history` route signatures. The old `GET /users/{id}/events` endpoint was **removed** (frontend never used it). `storage.*` functions still accept optional `from`/`to` because the cache layer calls them with computed boundaries.
- **`online-periods` is its own endpoint**: `GET /users/{id}/online-periods?hours=48`. It reads only the requested window via `storage.get_events_since(user_id, since)` (which also returns one anchor event per source from *before* the window, so an already-open period renders correctly) and builds periods with `_build_online_periods`. Cost scales with the window, not the whole history. Frontend clamps the display to 48h client-side.
- **`/stats` does NOT scan events** — it only reads pre-aggregated `daily_timezones`/`sleep_periods` tables (wakeup scatter data, offsets seen, counts). Never reintroduce a full event scan here; that was the original slowness.
- **Runtime cache** (`web/routes.py`): domain fact is that history older than ~4 days is immutable (sleep detection only rewrites recent days; new events always have current timestamps). `_cached_date_range` splits a full-history response into an immutable older half (dates < boundary = today−4d, cached) + a live last-~4-days tail, concatenated. Cache keys are **day-bucketed** (`_today_utc()`); stale-day entries are swept out on the next miss, so memory stays bounded without a TTL. Cache is in-process and resets on restart (safe — only immutable data is cached).

## Timezone handling

- **Backend**: all timestamps stored and transmitted in UTC (ISO format with `Z` suffix)
- **Frontend**: displays all times in user's local browser timezone
  - `timeAgo()` — calculates relative time correctly using UTC timestamps
  - `formatDateTime()` — converts UTC to local timezone for display
  - System clock in header shows local time with timezone indicator
- **Computed user timezone**: estimated from sleep patterns, displayed as `UTC+X`
  - User's local time is calculated from UTC + their estimated offset
  - Wake-up Pattern chart remains in UTC hours (for timezone analysis)
