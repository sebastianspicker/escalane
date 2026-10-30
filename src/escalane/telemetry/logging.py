"""Process logging setup that keeps structured ``extra`` fields in the output."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_LOGGER_NAME = "escalane"
_RESERVED_RECORD_FIELDS = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime"}


class JsonLineFormatter(logging.Formatter):
    """Render one JSON object per record: timestamp, level, event and extras."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_FIELDS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class _CurrentStderrHandler(logging.StreamHandler):
    """Write to the current ``sys.stderr`` so replaced streams are honoured."""

    def __init__(self) -> None:
        super().__init__()

    @property  # type: ignore[override]
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        """Ignore the stream StreamHandler assigns; stderr is looked up per record."""


def configure_logging(level: str) -> None:
    """Apply ``LOG_LEVEL`` to the application logger and attach one JSON handler.

    Repeated calls only update the level, so API and worker startup can both
    call this without duplicating output.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level.upper())
    if not any(isinstance(handler, _CurrentStderrHandler) for handler in logger.handlers):
        handler = _CurrentStderrHandler()
        handler.setFormatter(JsonLineFormatter())
        logger.addHandler(handler)
