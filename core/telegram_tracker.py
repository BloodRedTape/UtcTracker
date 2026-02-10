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
        # telegram_entity_id -> internal user_id
        self.tracked_users: dict[int, int] = {}
        # internal user_id -> label (for logging)
        self._labels: dict[int, str] = {}
        self._tracking_config = config.get("tracking", {})
        self._setup_handlers()

    def _setup_handlers(self):
        @self.client.on(events.UserUpdate)
        async def on_user_update(event):
            tg_entity_id = event.user_id
            if tg_entity_id not in self.tracked_users:
                return

            status = event.status
            if status is None:
                return

            internal_uid = self.tracked_users[tg_entity_id]
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
                log.info("User %d: status is 'recently' (restricted visibility)", tg_entity_id)
                return
            else:
                log.debug("User %d: unhandled status type %s", tg_entity_id, type(status).__name__)
                return

            ts = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            event_obj = StatusEvent(
                timestamp_utc=ts,
                status=status_str,
                raw_status_type=raw_type,
                source="telegram",
            )

            log.info("User %d [%s]: %s at %s", tg_entity_id, self._labels.get(internal_uid, "?"), status_str, ts)

            if storage.append_event(internal_uid, event_obj):
                sleep_detector.analyze(internal_uid, self._tracking_config)

    async def connect(self):
        """Start the Telethon client and resolve tracked users."""
        use_qr_login = self.config.get("telegram", {}).get("use_qr_login", False)

        if use_qr_login:
            # QR code authentication - more reliable for automation
            await self.client.connect()

            if not await self.client.is_user_authorized():
                log.info("Not authorized. Starting QR code login...")
                qr_login = await self.client.qr_login()

                # Display the QR code URL for the user to scan
                log.info("Please scan this QR code with your Telegram mobile app:")
                log.info("QR Code URL: %s", qr_login.url)
                print("\n" + "="*60)
                print("SCAN THIS QR CODE WITH YOUR TELEGRAM MOBILE APP:")
                print(qr_login.url)
                print("="*60 + "\n")

                # Wait for the user to scan the QR code
                try:
                    await qr_login.wait()
                    log.info("Successfully authenticated via QR code!")
                except asyncio.TimeoutError:
                    log.error("QR code expired. Please restart to try again.")
                    raise
        else:
            # Default phone-based authentication
            await self.client.start()

        log.info("Telegram client connected")

        for user_cfg in self.config.get("tracked_users", []):
            telegram_id = user_cfg.get("telegram_id") or user_cfg.get("identifier")
            if not telegram_id:
                continue
            label = user_cfg.get("label", str(telegram_id))
            try:
                entity = await self.client.get_entity(telegram_id)
                username = getattr(entity, "username", None)
                internal_uid = storage.ensure_user(
                    label=label,
                    telegram_id=entity.id,
                    discord_id=user_cfg.get("discord_id"),
                    username=username,
                )
                self.tracked_users[entity.id] = internal_uid
                self._labels[internal_uid] = label
                log.info("Tracking user: %s (tg_id=%d, uid=%d, username=%s)", label, entity.id, internal_uid, username)
            except Exception as e:
                log.error("Failed to resolve user '%s': %s", telegram_id, e)

        if not self.tracked_users:
            log.warning("No Telegram users to track! Check your config.json tracked_users list.")

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
            for tg_entity_id in list(self.tracked_users):
                try:
                    user = await self.client.get_entity(tg_entity_id)
                    if user.status is not None:
                        self._process_polled_status(tg_entity_id, user.status)
                except Exception as e:
                    log.debug("Poll error for user %d: %s", tg_entity_id, e)

    def _process_polled_status(self, tg_entity_id: int, status):
        """Process a polled status the same way as an event."""
        internal_uid = self.tracked_users[tg_entity_id]
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
            source="telegram",
        )

        if storage.append_event(internal_uid, event_obj):
            sleep_detector.analyze(internal_uid, self._tracking_config)

    async def disconnect(self):
        await self.client.disconnect()
