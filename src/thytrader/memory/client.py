"""HTTP helpers for experiential memory journals, hooks, monitor, and notify."""

from __future__ import annotations

from urllib.parse import urlencode

from thytrader.agent_http import request_json, request_mutation_json

MEMORY_API_PREFIX = "/api/v1/memory"


def memory_status(base_url: str) -> object:
    """Return counts and redacted notifier configuration."""
    return request_json(method="GET", url=f"{base_url}{MEMORY_API_PREFIX}")


def monitor(base_url: str) -> object:
    """Return the composite monitor snapshot."""
    return request_json(method="GET", url=f"{base_url}{MEMORY_API_PREFIX}/monitor")


def list_journals(
    base_url: str,
    *,
    origin: str | None = None,
    kind: str | None = None,
) -> object:
    """Return newest-first journals."""
    return request_json(
        method="GET",
        url=_filtered(f"{base_url}{MEMORY_API_PREFIX}/journals", origin=origin, kind=kind),
    )


def add_journal(base_url: str, payload: dict[str, object]) -> object:
    """Append one journal row."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{MEMORY_API_PREFIX}/journals",
        payload=payload,
    )


def list_sentiment(base_url: str, *, origin: str | None = None) -> object:
    """Return newest-first sentiment snapshots."""
    return request_json(
        method="GET",
        url=_filtered(f"{base_url}{MEMORY_API_PREFIX}/sentiment", origin=origin),
    )


def add_sentiment(base_url: str, payload: dict[str, object]) -> object:
    """Append one sentiment snapshot."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{MEMORY_API_PREFIX}/sentiment",
        payload=payload,
    )


def list_patterns(
    base_url: str,
    *,
    origin: str | None = None,
    pattern_key: str | None = None,
) -> object:
    """Return newest-first pattern observations."""
    return request_json(
        method="GET",
        url=_filtered(
            f"{base_url}{MEMORY_API_PREFIX}/patterns",
            origin=origin,
            pattern_key=pattern_key,
        ),
    )


def add_pattern(base_url: str, payload: dict[str, object]) -> object:
    """Append one pattern observation."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{MEMORY_API_PREFIX}/patterns",
        payload=payload,
    )


def list_notifications(base_url: str, *, origin: str | None = None) -> object:
    """Return newest-first notification attempts."""
    return request_json(
        method="GET",
        url=_filtered(f"{base_url}{MEMORY_API_PREFIX}/notifications", origin=origin),
    )


def notify(base_url: str, payload: dict[str, object]) -> object:
    """Request one user notification through the configured provider."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{MEMORY_API_PREFIX}/notifications",
        payload=payload,
    )


def list_models(base_url: str) -> object:
    """Return newest-first trained experiential models."""
    return request_json(method="GET", url=f"{base_url}{MEMORY_API_PREFIX}/models")


def show_model(base_url: str, model_id: str) -> object:
    """Return one trained model by id."""
    return request_json(method="GET", url=f"{base_url}{MEMORY_API_PREFIX}/models/{model_id}")


def train_model(base_url: str, payload: dict[str, object]) -> object:
    """Train one fail-closed model from attributed local journal evidence."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{MEMORY_API_PREFIX}/models",
        payload=payload,
    )


def list_trade_reasons(
    base_url: str,
    *,
    origin: str | None = None,
    deployment_id: str | None = None,
    intent_id: str | None = None,
) -> object:
    """Return newest-first why-trade records."""
    return request_json(
        method="GET",
        url=_filtered(
            f"{base_url}{MEMORY_API_PREFIX}/trade-reasons",
            origin=origin,
            deployment_id=deployment_id,
            intent_id=intent_id,
        ),
    )


def show_trade_reason(base_url: str, intent_id: str) -> object:
    """Return one composed why-trade record."""
    return request_json(
        method="GET",
        url=f"{base_url}{MEMORY_API_PREFIX}/trade-reasons/{intent_id}",
    )


def add_trade_reason_note(base_url: str, intent_id: str, payload: dict[str, object]) -> object:
    """Append one attributed note to a why-trade record."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{MEMORY_API_PREFIX}/trade-reasons/{intent_id}/notes",
        payload=payload,
    )


def _filtered(url: str, **params: str | None) -> str:
    """Append non-empty query parameters."""
    encoded = urlencode({key: value for key, value in params.items() if value})
    if not encoded:
        return url
    return f"{url}?{encoded}"
