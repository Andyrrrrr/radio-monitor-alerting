"""structlog configuration for the whole package.

Structured key-value logging, not formatted strings, because this system
gets debugged from logs alone, after the fact, on a machine you aren't
sitting at (docs/conventions.md §4). Stages bind transmission_id /
incident_id via structlog.contextvars so every line downstream carries them.
"""

import logging

import structlog


def configure_logging(level: str = "info") -> None:
    """Call once at process startup, before any get_logger()."""
    level_num = logging.getLevelNamesMapping()[level.upper()]
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_num),
        cache_logger_on_first_use=True,
    )
