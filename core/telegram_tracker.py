from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telethon import TelegramClient, events, types

from core.models import StatusEvent
from core import storage, sleep_detector

log = logging.getLogger(__name__)


class TelegramTracker:
    def __init__(self, config: dict, data_dir: str):
        self.config = config
        self._data_dir = data_dir
        tg = config["telegram"]
        # Session file lives next to the database
        session_path = f"{data_dir}/tracker"
        self.client = TelegramClient(
            session_path,
            tg["api_id"],
            tg["api_hash"],
        )
        self.tracked_users: dict[int, str] = {}  # user_id -> label
        self._tracking_config = config.get("tracking", {})
        self._setup_handlers()

    def _setup_handlers(self):
        @self.client.on(events.UserUpdate)
        async def on_user_update(event):
            user_id = event.user_id
            if user_id not in self.tracked_users:
                return

            status = event.status
            if status is None:
                return

            now_utc = datetime.now(timezone.utc)

            if isinstance(status, types.UserStatusOnline):
                status_str = "online"
                raw_type = "UserStatusOnline"
            elif isinstance(status, types.UserStatusOffline):
                status_str = "offline"
                raw_type = "UserStatusOffline"
                if status.was_online:
                    now_utc = status.was_online.replace(tzinfo=timezone.utc)
            elif isinstance(status, types.UserStatusRecently):
                log.info("User %d: status is 'recently' (restricted visibility)", user_id)
                return
            else:
                log.debug("User %d: unhandled status type %s", user_id, type(status).__name__)
                return

            ts = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            event_obj = StatusEvent(
                timestamp_utc=ts,
                status=status_str,
                raw_status_type=raw_type,
            )

            log.info("User %d [%s]: %s at %s", user_id, self.tracked_users[user_id], status_str, ts)

            if storage.append_event(user_id, event_obj):
                sleep_detector.analyze(user_id, self._tracking_config)

    async def connect(self):
        """Start the Telethon client and resolve tracked users."""
        await self.client.start()
        log.info("Telegram client connected")

        for user_cfg in self.config.get("tracked_users", []):
            identifier = user_cfg["identifier"]
            label = user_cfg.get("label", str(identifier))
            try:
                entity = await self.client.get_entity(identifier)
                self.tracked_users[entity.id] = label
                username = getattr(entity, "username", None)
                storage.ensure_user(entity.id, username, label)
                log.info("Tracking user: %s (id=%d, username=%s)", label, entity.id, username)
            except Exception as e:
                log.error("Failed to resolve user '%s': %s", identifier, e)

        if not self.tracked_users:
            log.warning("No users to track! Check your config.json tracked_users list.")

    async def run(self):
        """Run the client event loop + polling fallback."""
        await asyncio.gather(
            self.client.run_until_disconnected(),
            self._poll_loop(),
        )

    async def _poll_loop(self):
        """Periodically poll user status as a fallback for missed events."""
        interval = self._tracking_config.get("polling_interval_seconds", 300)
        while True:
            await asyncio.sleep(interval)
            for user_id in list(self.tracked_users):
                try:
                    user = await self.client.get_entity(user_id)
                    if user.status is not None:
                        self._process_polled_status(user_id, user.status)
                except Exception as e:
                    log.debug("Poll error for user %d: %s", user_id, e)

    def _process_polled_status(self, user_id: int, status):
        """Process a polled status the same way as an event."""
        now_utc = datetime.now(timezone.utc)

        if isinstance(status, types.UserStatusOnline):
            status_str = "online"
            raw_type = "UserStatusOnline"
        elif isinstance(status, types.UserStatusOffline):
            status_str = "offline"
            raw_type = "UserStatusOffline"
            if status.was_online:
                now_utc = status.was_online.replace(tzinfo=timezone.utc)
        else:
            return

        ts = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        event_obj = StatusEvent(
            timestamp_utc=ts,
            status=status_str,
            raw_status_type=raw_type,
        )

        if storage.append_event(user_id, event_obj):
            sleep_detector.analyze(user_id, self._tracking_config)

    async def disconnect(self):
        await self.client.disconnect()
