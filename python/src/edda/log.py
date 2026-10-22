"""structlog setup: one JSON object per line, ready for a log aggregator."""

import structlog


def configure_logging() -> None:
    """Renders every structlog event as JSON with an ISO timestamp and level."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        cache_logger_on_first_use=True,
    )
