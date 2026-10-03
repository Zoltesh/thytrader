"""HTTP helpers for confirmation-gated paper and live runtime control.

Includes write-only Coinbase credential show/set/clear. Those helpers never
log request bodies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from thytrader.agent_http import request_json, request_mutation_json

if TYPE_CHECKING:
    from thytrader.config import Settings

_DEPLOYMENTS_PREFIX = "/api/v1/deployments"
_STRATEGIES_PREFIX = "/api/v1/strategies"
_RISK_POLICY_PREFIX = "/api/v1/risk-policy"
_SETTINGS_PREFIX = "/api/v1/settings"
_CREDENTIALS_PREFIX = "/api/v1/credentials/coinbase"
_PORTFOLIOS_PREFIX = "/api/v1/portfolios"


class RuntimeControlError(RuntimeError):
    """Report a redacted runtime-control failure."""


def list_deployments(base_url: str) -> object:
    """Return the newest-first deployment list from the API."""
    return request_json(method="GET", url=f"{base_url}{_DEPLOYMENTS_PREFIX}")


def show_deployment(base_url: str, deployment_id: str) -> object:
    """Return one deployment snapshot including orders and fills."""
    return request_json(method="GET", url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}")


def list_decisions(
    base_url: str,
    *,
    deployment_id: str | None,
    strategy_id: str | None,
    outcomes: tuple[str, ...] = (),
    limit: int = 50,
    cursor: str | None = None,
) -> object:
    """Return one newest-first page of per-bar decisions for a bot or a strategy (read-only).

    With ``strategy_id`` the page aggregates that strategy's bots; ``deployment_id``
    then narrows it to one bot. Without ``strategy_id`` it reads one bot's page.
    """
    query: dict[str, str | tuple[str, ...]] = {"limit": str(limit)}
    if outcomes:
        query["outcome"] = outcomes
    if cursor:
        query["cursor"] = cursor
    if strategy_id is not None:
        if deployment_id is not None:
            query["deployment_id"] = deployment_id
        path = f"{_STRATEGIES_PREFIX}/{quote(strategy_id, safe='')}/decisions"
    elif deployment_id is not None:
        path = f"{_DEPLOYMENTS_PREFIX}/{quote(deployment_id, safe='')}/decisions"
    else:
        raise RuntimeControlError("Pass a deployment id or --strategy-id.")
    encoded = urlencode(query, doseq=True)
    return request_json(method="GET", url=f"{base_url}{path}?{encoded}")


def start_deployment(
    base_url: str,
    *,
    strategy_id: str,
    mode: str,
    paper_starting_cash: str | None,
    maker_fee_rate: str | None = None,
    taker_fee_rate: str | None = None,
    settings: Settings | None = None,
    i_understand_live: bool = False,
) -> object:
    """Start one paper or live deployment from a strategy's current rules.

    The server snapshots the strategy and returns the snapshot
    ``strategy_fingerprint`` the bot runs. ``i_understand_live`` is forwarded
    only for live; the API rejects live without it.
    """
    payload: dict[str, str | bool] = {
        "strategy_id": strategy_id,
        "mode": mode,
    }
    if mode == "live" and i_understand_live:
        payload["i_understand_live"] = True
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
    i_understand_live: bool = False,
) -> object:
    """Place one long or short discretionary order through the HTTP contract.

    ``i_understand_live`` is forwarded only for live; the API rejects live without it.
    """
    payload: dict[str, str | bool] = {
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
    if mode == "live" and i_understand_live:
        payload["i_understand_live"] = True
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
    i_understand_live: bool = False,
) -> object:
    """Pause, resume, or stop one deployment.

    Resume sends ``{"i_understand_live": true}`` when acknowledged; the API rejects
    resuming a live deployment without it.
    """
    if action not in {"pause", "resume", "stop"}:
        raise RuntimeControlError(f"unsupported runtime action: {action}")
    suffix = "?flatten=true" if action == "stop" and flatten else ""
    payload: dict[str, object] | None = None
    if action == "resume" and i_understand_live:
        payload = {"i_understand_live": True}
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/{action}{suffix}",
        payload=payload,
        settings=settings,
    )


def reset_breaker_latches(
    base_url: str,
    deployment_id: str,
    *,
    settings: Settings | None = None,
) -> object:
    """Clear latched daily-loss and drawdown breakers on one deployment."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/reset-breaker-latches",
        settings=settings,
    )


def show_portfolio(base_url: str, portfolio_id: str) -> object:
    """Return one portfolio (mode, revision, sleeves) before a portfolio action."""
    return request_json(
        method="GET", url=f"{base_url}{_PORTFOLIOS_PREFIX}/{quote(portfolio_id, safe='')}"
    )


def show_portfolio_deployment(base_url: str, portfolio_id: str) -> object:
    """Return one portfolio's deployment: state, sleeve bots, breakers, exposure."""
    return request_json(
        method="GET",
        url=f"{base_url}{_PORTFOLIOS_PREFIX}/{quote(portfolio_id, safe='')}/deployment",
    )


def start_portfolio(
    base_url: str,
    portfolio_id: str,
    *,
    revision: int,
    sleeve_id: str | None = None,
    maker_fee_rate: str | None = None,
    taker_fee_rate: str | None = None,
    i_understand_live: bool = False,
    settings: Settings | None = None,
) -> object:
    """Start (or attach) one bot per sleeve, or one sleeve's bot (ADR 0091)."""
    payload: dict[str, object] = {"revision": revision}
    if maker_fee_rate is not None:
        payload["maker_fee_rate"] = maker_fee_rate
    if taker_fee_rate is not None:
        payload["taker_fee_rate"] = taker_fee_rate
    if i_understand_live:
        payload["i_understand_live"] = True
    return request_mutation_json(
        method="POST",
        url=_portfolio_action_url(base_url, portfolio_id, "start", sleeve_id=sleeve_id),
        payload=payload,
        settings=settings,
    )


def portfolio_action(
    base_url: str,
    portfolio_id: str,
    action: str,
    *,
    sleeve_id: str | None = None,
    flatten: bool = False,
    i_understand_live: bool = False,
    settings: Settings | None = None,
) -> object:
    """Pause, resume, or stop a portfolio's sleeves (or one sleeve)."""
    if action not in {"pause", "resume", "stop"}:
        raise RuntimeControlError(f"unsupported portfolio action: {action}")
    url = _portfolio_action_url(base_url, portfolio_id, action, sleeve_id=sleeve_id)
    if action == "stop" and flatten:
        url = f"{url}?flatten=true"
    payload: dict[str, object] | None = None
    if action == "resume" and i_understand_live:
        payload = {"i_understand_live": True}
    return request_mutation_json(method="POST", url=url, payload=payload, settings=settings)


def reset_portfolio_breaker(
    base_url: str, portfolio_id: str, *, settings: Settings | None = None
) -> object:
    """Clear a latched portfolio breaker (sleeves stay paused until resumed)."""
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_PORTFOLIOS_PREFIX}/{quote(portfolio_id, safe='')}/breaker/reset",
        settings=settings,
    )


def _portfolio_action_url(
    base_url: str, portfolio_id: str, action: str, *, sleeve_id: str | None
) -> str:
    """The portfolio-wide or one-sleeve route for an action."""
    root = f"{base_url}{_PORTFOLIOS_PREFIX}/{quote(portfolio_id, safe='')}"
    if sleeve_id is None:
        return f"{root}/{action}"
    return f"{root}/sleeves/{quote(sleeve_id, safe='')}/{action}"


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


def show_twin(base_url: str, deployment_id: str) -> object:
    """Read the deliberately saved pair without changing execution."""
    return request_json(method="GET", url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/twin")


def link_twin(
    base_url: str, deployment_id: str, counterpart_id: str, *, settings: Settings
) -> object:
    """Save comparison metadata through the authenticated mutation boundary."""
    return request_mutation_json(
        method="PUT",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/twin",
        payload={"counterpart_deployment_id": counterpart_id},
        settings=settings,
    )


def unlink_twin(
    base_url: str, deployment_id: str, counterpart_id: str, *, settings: Settings
) -> object:
    """Remove only the expected partner; a changed partner returns a conflict."""
    query = urlencode({"counterpart_deployment_id": counterpart_id})
    return request_mutation_json(
        method="DELETE",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{deployment_id}/twin?{query}",
        payload=None,
        settings=settings,
    )
