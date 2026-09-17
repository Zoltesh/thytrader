"""HTTP helpers for confirmation-gated paper and live runtime control.

Includes write-only Coinbase credential show/set/clear. Those helpers never
log request bodies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.agent_http import request_json, request_mutation_json

if TYPE_CHECKING:
    from thytrader.config import Settings

_DEPLOYMENTS_PREFIX = "/api/v1/deployments"
_RISK_POLICY_PREFIX = "/api/v1/risk-policy"
_SETTINGS_PREFIX = "/api/v1/settings"
_CREDENTIALS_PREFIX = "/api/v1/credentials/coinbase"


class RuntimeControlError(RuntimeError):
    """Report a redacted runtime-control failure."""


def list_deployments(base_url: str) -> object:
    """Return the newest-first deployment list from the API."""
    return request_json(method="GET", url=f"{base_url}{_DEPLOYMENTS_PREFIX}")


def show_deployment(base_url: str, deployment_id: str) -> object:
    """Return one deployment snapshot including orders and fills."""
    return request_json(method="GET", url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}")


def start_deployment(
    base_url: str,
    *,
    strategy_fingerprint: str,
    mode: str,
    paper_starting_cash: str | None,
    maker_fee_rate: str | None = None,
    taker_fee_rate: str | None = None,
    settings: Settings | None = None,
) -> object:
    """Create one paper or live deployment through the existing HTTP contract."""
    payload: dict[str, str] = {
        "strategy_fingerprint": strategy_fingerprint,
        "mode": mode,
    }
    if paper_starting_cash is not None:
        payload["paper_starting_cash"] = paper_starting_cash
    if maker_fee_rate is not None:
        payload["maker_fee_rate"] = maker_fee_rate
    if taker_fee_rate is not None:
        payload["taker_fee_rate"] = taker_fee_rate
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}",
        payload=payload,
        settings=settings,
    )


def place_discretionary_order(
    base_url: str,
    *,
    settings: Settings | None = None,
    mode: str,
    product_id: str,
    stop_price: str,
    take_profit_price: str,
    idempotency_key: str,
    origin: str,
    entry_kind: str,
    timeframe: str,
    side: str,
    quantity: str | None,
    quote_notional: str | None,
    limit_price: str | None,
    paper_starting_cash: str | None,
    maker_fee_rate: str | None = None,
    taker_fee_rate: str | None = None,
    note: str | None = None,
) -> object:
    """Place one long or short discretionary order through the HTTP contract."""
    payload: dict[str, str] = {
        "mode": mode,
        "product_id": product_id,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "idempotency_key": idempotency_key,
        "origin": origin,
        "entry_kind": entry_kind,
        "timeframe": timeframe,
        "side": side,
    }
    if quantity is not None:
        payload["quantity"] = quantity
    if quote_notional is not None:
        payload["quote_notional"] = quote_notional
    if limit_price is not None:
        payload["limit_price"] = limit_price
    if paper_starting_cash is not None:
        payload["paper_starting_cash"] = paper_starting_cash
    if maker_fee_rate is not None:
        payload["maker_fee_rate"] = maker_fee_rate
    if taker_fee_rate is not None:
        payload["taker_fee_rate"] = taker_fee_rate
    if note is not None:
        payload["note"] = note
    return request_mutation_json(
        method="POST",
        url=f"{base_url}/api/v1/discretionary-orders",
        payload=payload,
        settings=settings,
    )


def set_deployment_status(
    base_url: str,
    deployment_id: str,
    action: str,
    *,
    settings: Settings | None = None,
    flatten: bool = False,
) -> object:
    """Pause, resume, or stop one deployment."""
    if action not in {"pause", "resume", "stop"}:
        raise RuntimeControlError(f"unsupported runtime action: {action}")
    suffix = "?flatten=true" if action == "stop" and flatten else ""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/{action}{suffix}",
        settings=settings,
    )


def reset_breaker_latches(
    base_url: str,
    deployment_id: str,
    *,
    settings: Settings | None = None,
) -> object:
    """Clear latched daily-loss and drawdown breakers on one deployment."""
    headers = mutation_headers(settings) if settings else None
    return request_json(
        method="POST",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/reset-breaker-latches",
        extra_headers=headers,
    )


def show_risk_policy(base_url: str) -> object:
    """Return the effective compiled or published risk policy."""
    return request_json(method="GET", url=f"{base_url}{_RISK_POLICY_PREFIX}")


def set_risk_policy(
    base_url: str,
    payload: dict[str, object],
    *,
    settings: Settings | None = None,
) -> object:
    """Publish one new immutable risk-policy version."""
    return request_mutation_json(
        method="PUT",
        url=f"{base_url}{_RISK_POLICY_PREFIX}",
        payload=payload,
        settings=settings,
    )


def show_yaml_settings(base_url: str) -> object:
    """Return YAML non-secret settings and redacted process identity."""
    return request_json(method="GET", url=f"{base_url}{_SETTINGS_PREFIX}")


def set_yaml_settings(
    base_url: str,
    payload: dict[str, object],
    *,
    settings: Settings | None = None,
) -> object:
    """Persist YAML non-secrets. YOLO and intervals apply without restart."""
    return request_mutation_json(
        method="PUT",
        url=f"{base_url}{_SETTINGS_PREFIX}",
        payload=payload,
        settings=settings,
    )


def show_coinbase_credentials(base_url: str) -> object:
    """Return write-only Coinbase credential presence flags."""
    return request_json(method="GET", url=f"{base_url}{_CREDENTIALS_PREFIX}")


def set_coinbase_credentials(
    base_url: str,
    *,
    api_key_name: str,
    private_key: str,
    settings: Settings | None = None,
) -> object:
    """Set or rotate Coinbase secrets through the write-only HTTP contract."""
    return request_mutation_json(
        method="PUT",
        url=f"{base_url}{_CREDENTIALS_PREFIX}",
        payload={"api_key_name": api_key_name, "private_key": private_key},
        settings=settings,
    )


def clear_coinbase_credentials(base_url: str, *, settings: Settings | None = None) -> object:
    """Clear Coinbase secrets through the write-only HTTP contract."""
    return request_mutation_json(
        method="DELETE",
        url=f"{base_url}{_CREDENTIALS_PREFIX}",
        settings=settings,
    )
