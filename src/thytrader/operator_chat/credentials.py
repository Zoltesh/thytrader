"""Process-lifetime LLM credential store, separate from Coinbase settings."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlparse

from pydantic import SecretStr

from thytrader.observability.logging import set_extra_redacted_secrets
from thytrader.operator_chat.models import (
    DEFAULT_OPENAI_BASE_URL,
    LlmProvider,
    OperatorChatCredentialWrite,
    OperatorChatStatus,
)

_COINBASE_HOST_MARKERS = (
    "coinbase.com",
    "exchange.coinbase.com",
)


class OperatorChatCredentialError(ValueError):
    """Reject unsafe or Coinbase-shaped LLM credential input."""


@dataclass(slots=True)
class StoredLlmCredentials:
    """Server-side LLM settings. The secret never enters a status payload."""

    provider: LlmProvider
    model: str
    base_url: str
    api_key: SecretStr


class OperatorChatCredentialStore:
    """Hold one pasted LLM key in this API process only."""

    def __init__(self) -> None:
        """Start empty so Coinbase env credentials stay on their own surface."""
        self._lock = Lock()
        self._current: StoredLlmCredentials | None = None

    def status(self) -> OperatorChatStatus:
        """Return configured flags without the secret."""
        with self._lock:
            current = self._current
        if current is None:
            return OperatorChatStatus(llm_configured=False)
        return OperatorChatStatus(
            llm_configured=True,
            provider=current.provider.value,
            model=current.model,
            base_url=current.base_url,
        )

    def get(self) -> StoredLlmCredentials | None:
        """Return the stored credentials for outbound LLM calls."""
        with self._lock:
            return self._current

    def replace(self, write: OperatorChatCredentialWrite) -> OperatorChatStatus:
        """Validate and store one LLM key; never accept Coinbase hosts or fields."""
        stored = _validated_credentials(write)
        with self._lock:
            self._current = stored
        set_extra_redacted_secrets((stored.api_key.get_secret_value(),))
        return self.status()

    def clear(self) -> OperatorChatStatus:
        """Drop the process-held LLM key."""
        with self._lock:
            self._current = None
        set_extra_redacted_secrets(())
        return self.status()


def _validated_credentials(write: OperatorChatCredentialWrite) -> StoredLlmCredentials:
    """Normalize provider URL and refuse Coinbase endpoints."""
    secret = write.api_key.get_secret_value().strip()
    if len(secret) < 8:
        raise OperatorChatCredentialError("LLM API key is too short.")
    lowered = secret.lower()
    if "coinbase" in lowered or "begin ec private key" in lowered:
        raise OperatorChatCredentialError(
            "This form accepts an LLM provider key only. Coinbase credentials stay "
            "on the separate Coinbase secrets surface."
        )
    base_url = _resolved_base_url(write.provider, write.base_url)
    return StoredLlmCredentials(
        provider=write.provider,
        model=write.model,
        base_url=base_url,
        api_key=SecretStr(secret),
    )


def _resolved_base_url(provider: LlmProvider, explicit: str | None) -> str:
    """Require a base URL only for compatible providers; block Coinbase hosts."""
    if provider is LlmProvider.OPENAI:
        resolved = explicit or DEFAULT_OPENAI_BASE_URL
    elif explicit is None:
        raise OperatorChatCredentialError(
            "openai_compatible requires base_url (OpenAI-compatible LLM HTTP, not Coinbase)."
        )
    else:
        resolved = explicit.rstrip("/")
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OperatorChatCredentialError("LLM base_url must be an http(s) URL.")
    host = parsed.hostname.lower()
    if any(marker in host for marker in _COINBASE_HOST_MARKERS):
        raise OperatorChatCredentialError(
            "LLM base_url cannot be a Coinbase host. Coinbase keys stay off this surface."
        )
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
