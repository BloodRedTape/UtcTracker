from __future__ import annotations

import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from web.routes import create_router
from web.security import RateLimitMiddleware

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="NickUtc - Timezone Tracker")

    # Add rate limiting middleware
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=60,
        requests_per_second=10,
    )

    # Security logging middleware
    @app.middleware("http")
    async def log_suspicious_requests(request: Request, call_next):
        """Log suspicious access patterns for security monitoring."""
        path = request.url.path
        method = request.method
        client_ip = request.client.host if request.client else "unknown"

        # Log API access attempts
        if path.startswith("/api/"):
            log.info(
                "API request: %s %s from %s (User-Agent: %s)",
                method,
                path,
                client_ip,
                request.headers.get("User-Agent", "unknown"),
            )

        # Detect suspicious patterns
        suspicious_patterns = [
            "../",  # Path traversal attempts
            "..\\",  # Windows path traversal
            "%2e%2e",  # URL-encoded path traversal
            "etc/passwd",  # Linux file access
            "cmd.exe",  # Windows command execution
            "<script",  # XSS attempts
            "SELECT ",  # SQL injection attempts
            "UNION ",
            "DROP ",
        ]

        path_and_query = str(request.url)
        for pattern in suspicious_patterns:
            if pattern.lower() in path_and_query.lower():
                log.warning(
                    "SUSPICIOUS REQUEST DETECTED: %s %s from %s - Pattern: %s",
                    method,
                    path_and_query,
                    client_ip,
                    pattern,
                )
                break

        response = await call_next(request)

        # Log failed authorization attempts (4xx errors on API)
        if path.startswith("/api/") and 400 <= response.status_code < 500:
            log.warning(
                "API error response: %s %s from %s - Status: %d",
                method,
                path,
                client_ip,
                response.status_code,
            )

        return response

    # Global exception handler for better error logging
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        log.error(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )

    router = create_router()
    app.include_router(router, prefix="/api")

    # Serve static files (dashboard HTML/CSS/JS)
    # Use absolute path to prevent path traversal attacks
    static_dir = Path(__file__).parent.parent / "static"
    app.mount("/", StaticFiles(directory=str(static_dir.resolve()), html=True), name="static")

    return app
