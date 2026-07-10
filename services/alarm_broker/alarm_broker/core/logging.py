"""JSON and human-readable log formatters plus configure_logging() for the alarm broker."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_STANDARD_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "relativeCreated",
        "thread",
        "threadName",
        "process",
        "processName",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "msecs",
        "message",
        "taskName",
    }
)


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter.

    Outputs log records as JSON objects suitable for log aggregation systems.
    """

    def __init__(self, *, include_timestamp: bool = True, include_level: bool = True) -> None:
        """Initialize the formatter.

        Args:
            include_timestamp: Whether to include timestamp in output
            include_level: Whether to include log level in output
        """
        super().__init__()
        self.include_timestamp = include_timestamp
        self.include_level = include_level

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON.

        Args:
            record: The log record to format

        Returns:
            JSON formatted log string
        """
        log_data: dict[str, Any] = {
            "message": record.getMessage(),
        }

        if self.include_timestamp:
            log_data["timestamp"] = datetime.now(UTC).isoformat()

        if self.include_level:
            log_data["level"] = record.levelname

        log_data["logger"] = record.name

        extra_data = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        log_data.update(extra_data)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        log_data["location"] = f"{record.filename}:{record.lineno}"

        return json.dumps(log_data)


class HumanReadableFormatter(logging.Formatter):
    """Human-readable log formatter for development.

    Uses colors and indentation for easier reading in terminals.
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record for human reading.

        Args:
            record: The log record to format

        Returns:
            Human readable log string
        """
        timestamp = datetime.now(UTC).strftime(self.TIMESTAMP_FORMAT)
        color = self.COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:8}{self.RESET}"

        parts = [f"[{timestamp}] {level} {record.name}: {record.getMessage()}"]

        extra_parts = []
        extra_data = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        for key, value in extra_data.items():
            extra_parts.append(f"{key}={value}")

        if extra_parts:
            parts.append("  " + " ".join(extra_parts))

        if record.exc_info:
            parts.append(self.formatException(record.exc_info))

        return "\n".join(parts)


def configure_logging(
    level: str = "INFO",
    json_format: bool = False,
    loggers: list[str] | None = None,
) -> None:
    """Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Use JSON structured logging
        loggers: Additional loggers to configure
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    formatter: logging.Formatter = (
        StructuredFormatter() if json_format else HumanReadableFormatter()
    )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    loggers_to_configure = ["alarm_broker", "uvicorn", "sqlalchemy"]
    if loggers:
        loggers_to_configure.extend(loggers)

    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        # Don't propagate to root to avoid duplicate logs
        logger.propagate = False
        logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
