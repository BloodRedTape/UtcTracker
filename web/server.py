from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.routes import create_router


def create_app() -> FastAPI:
    app = FastAPI(title="NickUtc - Timezone Tracker")

    router = create_router()
    app.include_router(router, prefix="/api")

    # Serve static files (dashboard HTML/CSS/JS)
    # html=True makes "/" serve index.html automatically
    app.mount("/", StaticFiles(directory="static", html=True), name="static")

    return app
