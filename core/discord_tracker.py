from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord

from core.models import StatusEvent
from core import storage, sleep_detector

log = logging.getLogger(__name__)


class DiscordTracker:
    def __init__(self, config: dict):
        self.config = config
        self._discord_cfg = config.get("discord", {})
        self._tracking_config = config.get("tracking", {})

        # discord_member_id -> internal user_id
        self.tracked_users: dict[int, int] = {}
        # internal user_id -> label (for logging)
        self._labels: dict[int, str] = {}

        # Collect discord users from config
        self._discord_users_cfg: list[dict] = []
        for user_cfg in config.get("tracked_users", []):
            if user_cfg.get("discord_id"):
                self._discord_users_cfg.append(user_cfg)

        # Set up intents
        intents = discord.Intents.default()
        intents.presences = True
        intents.members = True

        self.client = discord.Client(intents=intents)
        self._setup_handlers()

    def _setup_handlers(self):
        @self.client.event
        async def on_ready():
            log.info("Discord bot connected as %s", self.client.user)
            log.info("Discord bot is in %d guild(s): %s",
                     len(self.client.guilds),
                     ", ".join(g.name for g in self.client.guilds))

            for user_cfg in self._discord_users_cfg:
                dc_id = user_cfg["discord_id"]
                label = user_cfg.get("label", str(dc_id))
                # Try to fetch username from Discord
                username = None
                try:
                    user = await self.client.fetch_user(dc_id)
                    username = user.name
                except Exception:
                    pass

                internal_uid = storage.ensure_user(
                    label=label,
                    discord_id=dc_id,
                    telegram_id=user_cfg.get("telegram_id"),
                    username=username,
                )
                self.tracked_users[dc_id] = internal_uid
                self._labels[internal_uid] = label
                log.info("Tracking Discord user: %s (dc_id=%d, uid=%d)", label, dc_id, internal_uid)

                # Capture initial status from guild member cache
                member = None
                for guild in self.client.guilds:
                    member = guild.get_member(dc_id)
                    if member:
                        break

                if member:
                    is_online = member.status == discord.Status.online
                    status_str = "online" if is_online else "offline"
                    raw_type = f"Discord{str(member.status).capitalize()}"
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                    log.info("Discord user %d [%s]: initial status = %s (raw: %s)",
                             dc_id, label, status_str, raw_type)

                    event_obj = StatusEvent(
                        timestamp_utc=ts,
                        status=status_str,
                        raw_status_type=raw_type,
                        source="discord",
                    )
                    if storage.append_event(internal_uid, event_obj):
                        sleep_detector.analyze(internal_uid, self._tracking_config)
                else:
                    log.warning("Discord user %d [%s]: NOT found in any guild member cache! "
                                "Presence updates won't work for this user.", dc_id, label)

            if not self.tracked_users:
                log.warning("No Discord users to track!")

        @self.client.event
        async def on_presence_update(before: discord.Member, after: discord.Member):
            dc_id = after.id
            if dc_id not in self.tracked_users:
                return

            # Binary model: only discord.Status.online counts as "online"
            old_online = before.status == discord.Status.online
            new_online = after.status == discord.Status.online

            if old_online == new_online:
                return

            internal_uid = self.tracked_users[dc_id]
            now_utc = datetime.now(timezone.utc)
            ts = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

            if new_online:
                status_str = "online"
                raw_type = "DiscordOnline"
            else:
                status_str = "offline"
                raw_type = f"Discord{str(after.status).capitalize()}"

            event_obj = StatusEvent(
                timestamp_utc=ts,
                status=status_str,
                raw_status_type=raw_type,
                source="discord",
            )

            log.info(
                "Discord user %d [%s]: %s at %s (raw: %s)",
                dc_id, self._labels.get(internal_uid, "?"), status_str, ts, raw_type,
            )

            if storage.append_event(internal_uid, event_obj):
                sleep_detector.analyze(internal_uid, self._tracking_config)

    async def run(self):
        """Start the Discord bot. Compatible with asyncio.gather()."""
        token = self._discord_cfg.get("bot_token")
        if not token:
            log.warning("No Discord bot token configured, skipping Discord tracker")
            return
        if not self._discord_users_cfg:
            log.warning("No Discord users to track, skipping Discord tracker")
            return
        log.info("Starting Discord tracker...")
        await self.client.start(token)

    async def disconnect(self):
        await self.client.close()
