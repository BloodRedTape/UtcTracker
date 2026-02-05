import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import uvicorn

from core.telegram_tracker import TelegramTracker
from core import storage
from web.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nickutc")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NickUtc - Telegram Timezone Tracker")
    parser.add_argument(
        "data_dir",
        help="Directory for config.json, database and Telethon session files",
    )
    return parser.parse_args()


def load_config(data_dir: str) -> dict:
    config_path = Path(data_dir) / "config.json"
    if not config_path.exists():
        log.error("Config file not found: %s", config_path)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def main():
    args = parse_args()
    data_dir = str(Path(args.data_dir).resolve())
    config = load_config(data_dir)

    # Initialize SQLite database
    db_path = str(Path(data_dir) / "nickutc.db")
    storage.init(db_path)

    # Create Telegram tracker (session file lives in data_dir)
    tracker = TelegramTracker(config, data_dir)

    # Create FastAPI app
    app = create_app()

    # Configure uvicorn
    web_cfg = config.get("web", {})
    host = web_cfg.get("host", "0.0.0.0")
    port = web_cfg.get("port", 8000)

    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(uvicorn_config)

    # Connect Telegram client (will prompt for auth on first run)
    await tracker.connect()

    log.info("Dashboard available at http://%s:%d", host, port)
    log.info("Data directory: %s", data_dir)

    # Run both concurrently
    await asyncio.gather(
        tracker.run(),
        server.serve(),
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
