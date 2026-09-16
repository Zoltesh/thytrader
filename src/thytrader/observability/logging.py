"""Structured logging with configured-secret redaction."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from io import TextIOBase

    from thytrader.config import Settings

_REDACTION = "[REDACTED]"
_extra_secret_values: tuple[str, ...] = ()


def extra_redacted_secrets() -> tuple[str, ...]:
    """Return process-held extras such as a pasted LLM API key."""
    return _extra_secret_values


def set_extra_redacted_secrets(values: tuple[str, ...]) -> None:
    """Replace process-held extras. Coinbase Settings secrets stay separate."""
    global _extra_secret_values
    _extra_secret_values = tuple(
        sorted({item for item in values if item}, key=len, reverse=True)
    )


def _redaction_values(configured: tuple[str, ...]) -> tuple[str, ...]:
    """Merge startup Settings secrets with process-held extras, longest first."""
    merged = {item for item in (*configured, *extra_redacted_secrets()) if item}
    return tuple(sorted(merged, key=len, reverse=True))


class JsonLogFormatter(logging.Formatter):
    """Render log records as one-line JSON with known secrets removed."""

    def __init__(self, secret_values: tuple[str, ...]) -> None:
        """Initialize the formatter with Settings secrets; extras are live."""
        super().__init__()
        self._secret_values = tuple(sorted(secret_values, key=len, reverse=True))

    def format(self, record: logging.LogRecord) -> str:
        """Return one redacted JSON object for a log record."""
        message = record.getMessage()
        for value in _redaction_values(self._secret_values):
            message = message.replace(value, _REDACTION)
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_log_handler(
    settings: Settings,
    stream: TextIOBase | None = None,
) -> logging.Handler:
    """Build a structured stream handler that redacts configured secrets."""
    secrets = tuple(
        secret.get_secret_value()
        for secret in (
            settings.coinbase_api_key_name,
            settings.coinbase_api_private_key,
        )
        if secret is not None and secret.get_secret_value()
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter(secrets))
    return handler


def configure_logging(settings: Settings) -> None:
    """Configure the root logger for one ThyTrader process."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(settings.log_level)
    root_logger.addHandler(build_log_handler(settings=settings))
