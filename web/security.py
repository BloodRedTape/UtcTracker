from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiter.
    Tracks requests per IP address with a sliding window.
    """
    def __init__(self, app, requests_per_minute: int = 300, requests_per_second: int = 30):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests_per_second = requests_per_second
        # Store: {ip: [(timestamp, count)]}
        self.request_history: Dict[str, list] = defaultdict(list)
        self.last_cleanup = time.time()

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for static files
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        current_time = time.time()

        # Cleanup old entries every 5 minutes
        if current_time - self.last_cleanup > 300:
            self._cleanup_old_entries(current_time)
            self.last_cleanup = current_time

        # Check rate limits
        if self._is_rate_limited(client_ip, current_time):
            log.warning(
                "Rate limit exceeded for IP %s on %s %s",
                client_ip,
                request.method,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down.",
            )

        # Record this request
        self.request_history[client_ip].append(current_time)

        response = await call_next(request)
        return response

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP, handling proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        return request.client.host if request.client else "unknown"

    def _is_rate_limited(self, client_ip: str, current_time: float) -> bool:
        """Check if client has exceeded rate limits."""
        history = self.request_history[client_ip]

        # Remove requests older than 1 minute
        cutoff_minute = current_time - 60
        cutoff_second = current_time - 1

        recent_requests = [ts for ts in history if ts > cutoff_minute]
        self.request_history[client_ip] = recent_requests

        # Check per-minute limit
        if len(recent_requests) >= self.requests_per_minute:
            return True

        # Check per-second limit
        last_second_requests = [ts for ts in recent_requests if ts > cutoff_second]
        if len(last_second_requests) >= self.requests_per_second:
            return True

        return False

    def _cleanup_old_entries(self, current_time: float):
        """Remove entries older than 1 minute to prevent memory bloat."""
        cutoff = current_time - 60
        for ip in list(self.request_history.keys()):
            self.request_history[ip] = [
                ts for ts in self.request_history[ip] if ts > cutoff
            ]
            if not self.request_history[ip]:
                del self.request_history[ip]


def validate_user_id(user_id: int) -> None:
    """Validate user_id parameter."""
    if user_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id: must be positive integer",
        )


def validate_date_format(date_str: str, param_name: str) -> None:
    """Validate date format (YYYY-MM-DD or ISO8601)."""
    if not date_str:
        return

    # Allow ISO8601 format: YYYY-MM-DDTHH:MM:SSZ
    # Or simple date format: YYYY-MM-DD
    import re
    iso_pattern = r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$'
    if not re.match(iso_pattern, date_str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {param_name}: must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ format",
        )


def validate_pagination(page: int, per_page: int) -> None:
    """Validate pagination parameters."""
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid page: must be >= 1",
        )
    if per_page < 1 or per_page > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid per_page: must be between 1 and 1000",
        )
