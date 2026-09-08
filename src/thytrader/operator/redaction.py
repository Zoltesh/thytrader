"""Secret redaction for operator reports and CLI output."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.config import Settings

REDACTION = "[REDACTED]"
_PEM_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----",
    flags=re.DOTALL,
)
_POSTGRES_PASSWORD_PATTERN = re.compile(
    r"(postgres(?:ql)?://[^:]+:)([^@]+)(@)",
    flags=re.IGNORECASE,
)


def configured_secrets(settings: Settings) -> tuple[str, ...]:
    """Return configured secret strings longest-first for replacement."""
    values: list[str] = []
    for secret in (
        settings.coinbase_api_private_key,
        settings.coinbase_api_key_name,
        settings.database_url,
    ):
        if secret is None:
            continue
        raw = secret.get_secret_value()
        if raw:
            values.append(raw)
    return tuple(sorted(set(values), key=len, reverse=True))


def redact_text(text: str, secrets: tuple[str, ...]) -> str:
    """Remove configured secrets, PEM blocks, and URL passwords from text."""
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTION)
    redacted = _PEM_PATTERN.sub(REDACTION, redacted)
    return _POSTGRES_PASSWORD_PATTERN.sub(rf"\1{REDACTION}\3", redacted)


def redact_json_text(document: str, secrets: tuple[str, ...]) -> str:
    """Redact secrets inside canonical JSON without changing object shape."""
    return redact_text(document, secrets)


def dumps_redacted(payload: object, secrets: tuple[str, ...]) -> str:
    """Serialize one report to JSON and redact residual secrets."""
    document = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return redact_json_text(document, secrets)
