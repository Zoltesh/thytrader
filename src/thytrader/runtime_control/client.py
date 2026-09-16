"""HTTP helpers for confirmation-gated paper and live runtime control."""

from __future__ import annotations

from thytrader.agent_http import request_json

_DEPLOYMENTS_PREFIX = "/api/v1/deployments"
_RISK_POLICY_PREFIX = "/api/v1/risk-policy"
_SETTINGS_PREFIX = "/api/v1/settings"


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
    return request_json(method="POST", url=f"{base_url}{_DEPLOYMENTS_PREFIX}", payload=payload)


def place_discretionary_order(
    base_url: str,
    *,
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
    return request_json(
        method="POST",
        url=f"{base_url}/api/v1/discretionary-orders",
        payload=payload,
    )


def set_deployment_status(base_url: str, deployment_id: str, action: str) -> object:
    """Pause, resume, or stop one deployment."""
    if action not in {"pause", "resume", "stop"}:
        raise RuntimeControlError(f"unsupported runtime action: {action}")
    return request_json(
        method="POST",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/{action}",
    )


def show_risk_policy(base_url: str) -> object:
    """Return the effective compiled or published risk policy."""
    return request_json(method="GET", url=f"{base_url}{_RISK_POLICY_PREFIX}")


def set_risk_policy(base_url: str, payload: dict[str, object]) -> object:
    """Publish one new immutable risk-policy version."""
    return request_json(method="PUT", url=f"{base_url}{_RISK_POLICY_PREFIX}", payload=payload)


def show_yaml_settings(base_url: str) -> object:
    """Return YAML non-secret settings and redacted process identity."""
    return request_json(method="GET", url=f"{base_url}{_SETTINGS_PREFIX}")


def set_yaml_settings(base_url: str, payload: dict[str, object]) -> object:
    """Persist YAML non-secrets. YOLO and intervals apply without restart."""
    return request_json(method="PUT", url=f"{base_url}{_SETTINGS_PREFIX}", payload=payload)
