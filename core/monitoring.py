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
        import sentry_sdk.logger as sentry_logger
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

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        server_name=service_name,
        # Enable the Logs feature. We disable the built-in LoggingIntegration
        # (which both auto-mirrors logs AND turns log records into Issues) and
        # replace it with our own handler below, so that every log line carries
        # service.name / environment as attributes in the Logs tab — the same
        # thing the Dirk service does. Issues are still raised from ERROR-level
        # logs by our handler, and explicitly via capture_exception().
        enable_logs=True,
        disabled_integrations=[LoggingIntegration()],
        # We don't need performance tracing for a status tracker.
        traces_sample_rate=0.0,
        # Attach exception stack-locals so Issues are actionable.
        include_local_variables=True,
        send_default_pii=False,
    )

    # Also tag Issues (events) with the service, so they group/filter the same
    # way logs do.
    sentry_sdk.set_tag("service.name", service_name)

    # Attributes attached to every log record shown in GlitchTip's Logs tab.
    log_attrs = {"service.name": service_name, "environment": environment}

    _level_map = {
        logging.DEBUG: sentry_logger.debug,
        logging.INFO: sentry_logger.info,
        logging.WARNING: sentry_logger.warning,
        logging.ERROR: sentry_logger.error,
        logging.CRITICAL: sentry_logger.fatal,
    }

    class _SentryLogsHandler(logging.Handler):
        """Mirror stdlib log records into GlitchTip Logs with service attrs.

        ERROR and above are additionally sent to Issues via capture_exception
        (using the record's exc_info when present), since we disabled the
        built-in LoggingIntegration that would normally do that.
        """

        def emit(self, record: logging.LogRecord) -> None:
            # Never let logging-side failures crash the app.
            try:
                fn = _level_map.get(record.levelno)
                if fn is None:
                    # Map any non-standard level to the nearest bucket.
                    fn = sentry_logger.error if record.levelno > logging.WARNING else sentry_logger.info
                fn(self.format(record), attributes=log_attrs)

                if record.levelno >= logging.ERROR:
                    if record.exc_info and record.exc_info[1] is not None:
                        sentry_sdk.capture_exception(record.exc_info[1])
                    else:
                        sentry_sdk.capture_message(record.getMessage(), level="error")
            except Exception:
                self.handleError(record)

    handler = _SentryLogsHandler(level=logging.INFO)
    # Attach to the root logger (covers our loggers) plus uvicorn's, matching
    # the Dirk setup so web-server logs also reach GlitchTip.
    for _name in ("", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(_name).addHandler(handler)

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
