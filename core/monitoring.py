"""GlitchTip / Sentry integration.

GlitchTip is a self-hosted, Sentry-compatible error tracker, so we use the
official ``sentry-sdk`` and point it at the GlitchTip DSN.

Two things flow to GlitchTip:

- **Issues** (exceptions) — uncaught exceptions and ``logging.error(...)``
  calls are captured automatically via Sentry's ``logging`` integration.
- **Logs** (the Logs tab) — ``INFO``/``WARNING`` records are mirrored into
  GlitchTip's structured logs via ``enable_logs=True``.

Configuration (env vars take precedence, falling back to the ``monitoring``
section of ``config.json``):

    GLITCHTIP_DSN=https://<key>@glitchtip.example.com/<project-id>
    ENVIRONMENT=production   # optional, defaults to "production"
    SERVICE_NAME=nickutc     # optional, tagged as service.name on all events

If no DSN is configured, this module is a no-op — the app runs normally
without any monitoring, and nothing is sent anywhere.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_initialized = False


def init_sentry(config: dict | None = None) -> bool:
    """Initialize the Sentry SDK for GlitchTip. Safe to call once.

    Returns True if monitoring was enabled, False if it was skipped
    (no DSN configured, or the SDK is not installed).
    """
    global _initialized
    if _initialized:
        return True

    monitoring_cfg = (config or {}).get("monitoring", {}) if config else {}

    dsn = os.environ.get("GLITCHTIP_DSN") or monitoring_cfg.get("dsn")
    if not dsn:
        log.info("GlitchTip monitoring disabled (no GLITCHTIP_DSN configured)")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        log.warning(
            "GLITCHTIP_DSN is set but sentry-sdk is not installed; "
            "monitoring disabled. Add sentry-sdk to requirements.txt."
        )
        return False

    environment = (
        os.environ.get("ENVIRONMENT")
        or monitoring_cfg.get("environment")
        or "production"
    )

    service_name = (
        os.environ.get("SERVICE_NAME")
        or monitoring_cfg.get("service_name")
        or "nickutc"
    )

    # Route the stdlib logging into Sentry:
    #   - breadcrumbs from INFO and above (context for issues)
    #   - Issues (events) from ERROR and above
    logging_integration = LoggingIntegration(
        level=logging.INFO,        # breadcrumb level
        event_level=logging.ERROR,  # capture as an Issue
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[logging_integration],
        # Mirror stdlib log records into GlitchTip's Logs tab.
        enable_logs=True,
        # We don't need performance tracing for a status tracker.
        traces_sample_rate=0.0,
        # Attach exception stack-locals so Issues are actionable.
        include_local_variables=True,
        send_default_pii=False,
    )

    sentry_sdk.set_tag("service.name", service_name)

    _initialized = True
    log.info(
        "GlitchTip monitoring enabled (service.name=%s, environment=%s)",
        service_name, environment,
    )
    return True


def capture_exception(err: BaseException) -> None:
    """Explicitly send an exception to GlitchTip Issues.

    No-op if monitoring is not initialized (or sentry-sdk is absent).
    """
    if not _initialized:
        return
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(err)
    except ImportError:
        pass


def flush(timeout: float = 2.0) -> None:
    """Flush buffered events before shutdown. No-op if not initialized."""
    if not _initialized:
        return
    try:
        import sentry_sdk
        sentry_sdk.flush(timeout=timeout)
    except ImportError:
        pass
