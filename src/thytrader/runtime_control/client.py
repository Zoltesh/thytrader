"""HTTP helpers for confirmation-gated paper and live runtime control.

Includes write-only Coinbase credential show/set/clear. Those helpers never
log request bodies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import quote, urlencode

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
)

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


def list_deployments(
    base_url: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    page_size: int = 200,
    cursor: str | None = None,
) -> object:
    """Return one stable page, or the complete snapshot when ``limit`` is omitted.

    The complete walk follows membership-fenced keyset cursors. A failed later page
    raises instead of returning a prefix as if it were the fleet.
    """
    if limit is not None:
        return _deployment_page(base_url, limit=limit, offset=offset, as_of=None, cursor=cursor)
    return _complete_deployment_inventory(base_url, page_size=page_size)


def show_deployment(base_url: str, deployment_id: str, *, detail: str = "summary") -> object:
    """Return one deployment. Summary omits historical orders and fills.

    The summary payload labels that omission in ``ledger_omission``. ``detail=full``
    includes the historical collections. Paged ``/orders`` and ``/fills`` remain
    the bounded history reads.
    """
    if detail not in {"summary", "full"}:
        raise RuntimeControlError("detail must be summary or full.")
    query = urlencode({"detail": detail})
    return request_json(
        method="GET",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{quote(deployment_id, safe='')}?{query}",
    )


def show_deployment_futures(base_url: str, deployment_id: str) -> object:
    """Return the futures view of one paper futures bot (ADR 0129, P1-6)."""
    return request_json(
        method="GET",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}/{quote(deployment_id, safe='')}/futures",
    )


def list_deployment_orders(
    base_url: str,
    deployment_id: str,
    *,
    limit: int = 100,
    cursor: str | None = None,
) -> object:
    """Return one read-only order page. This does not mutate the book."""
    return _ledger_page(base_url, deployment_id, "orders", limit=limit, cursor=cursor)


def list_deployment_fills(
    base_url: str,
    deployment_id: str,
    *,
    limit: int = 100,
    cursor: str | None = None,
) -> object:
    """Return one read-only fill page. This does not mutate the book."""
    return _ledger_page(base_url, deployment_id, "fills", limit=limit, cursor=cursor)


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
    adopt_holdings: str | None = None,
    paper_fee_per_contract: str | None = None,
) -> object:
    """Start one paper or live deployment from a strategy's current rules.

    The server snapshots the strategy and returns the snapshot
    ``strategy_fingerprint`` the bot runs. ``i_understand_live`` is forwarded
    only for live; the API rejects live without it. ``adopt_holdings`` (a quantity or
    ``all``) starts the live bot already holding coins the account holds (ADR 0124).
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
    if adopt_holdings is not None:
        payload["adopt_holdings"] = adopt_holdings
    if paper_fee_per_contract is not None:
        payload["paper_fee_per_contract"] = paper_fee_per_contract
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


_INVENTORY_MAX_PAGES = 500
_FLEET_PREFIX = "/api/v1/fleet-control"


def _deployment_page(
    base_url: str, *, limit: int, offset: int, as_of: str | None, cursor: str | None = None
) -> object:
    """Read one stable inventory page."""
    query: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
    if as_of is not None:
        query["as_of"] = as_of
    if cursor is not None:
        query["cursor"] = cursor
    return request_json(
        method="GET",
        url=f"{base_url}{_DEPLOYMENTS_PREFIX}?{urlencode(query)}",
    )


def _complete_deployment_inventory(base_url: str, *, page_size: int) -> object:
    """Walk a fenced keyset inventory; certify only consistent, complete membership."""
    if not 1 <= page_size <= 200:
        raise RuntimeControlError("Inventory page size must be between 1 and 200.")
    first = _require_inventory_page(
        _deployment_page(base_url, limit=page_size, offset=0, as_of=None)
    )
    rows = _rows_of(first)
    as_of = str(first["as_of"])
    pages = 1
    has_more = bool(first["has_more"])
    cursor = first["next_cursor"]
    seen_cursors: set[str] = set()
    seen_ids = _inventory_ids(rows)
    while has_more:
        if pages >= _INVENTORY_MAX_PAGES:
            raise RuntimeControlError(
                f"Deployment inventory incomplete after {len(rows)} rows (has_more=true). "
                "Not a complete fleet."
            )
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise RuntimeControlError(
                "Deployment inventory cursor is missing or repeated; incomplete."
            )
        seen_cursors.add(cursor)
        page = _require_inventory_page(
            _deployment_page(base_url, limit=page_size, offset=0, as_of=as_of, cursor=cursor)
        )
        if any(page[key] != first[key] for key in ("fingerprint", "total", "as_of", "order")):
            raise RuntimeControlError(
                "Deployment inventory membership changed; incomplete. Restart read."
            )
        batch = _rows_of(page)
        if not batch:
            raise RuntimeControlError(
                "Deployment inventory returned an empty page while claiming more rows."
            )
        identifiers = _inventory_ids(batch)
        if seen_ids & identifiers:
            raise RuntimeControlError("Deployment inventory duplicated rows; incomplete.")
        seen_ids.update(identifiers)
        rows.extend(batch)
        pages += 1
        has_more = bool(page["has_more"])
        cursor = page["next_cursor"]
    if len(rows) != first["total"]:
        raise RuntimeControlError("Deployment inventory omitted rows; incomplete.")
    return {
        "deployments": rows,
        "limit": page_size,
        "offset": 0,
        "returned": len(rows),
        "has_more": False,
        "complete": True,
        "order": first.get("order"),
        "as_of": as_of,
        "total": first.get("total"),
        "inventory": "complete_snapshot",
        "fingerprint": first["fingerprint"],
        "next_cursor": None,
    }


class _InventoryMetadata(BaseModel):
    """Validate untrusted completeness metadata before walking an HTTP inventory."""

    model_config = ConfigDict(extra="ignore")
    returned: StrictInt = Field(ge=0)
    total: StrictInt = Field(ge=0)
    has_more: StrictBool
    as_of: AwareDatetime
    order: Literal["created_at_desc_id_desc"]
    fingerprint: str = Field(min_length=1)
    next_cursor: str | None


def _require_inventory_page(payload: object) -> dict[str, object]:
    """Validate one inventory page before using it as a complete-fleet input."""
    page = _strict_mapping(payload, label="Deployment inventory response")
    rows = _rows_of(page)
    try:
        metadata = _InventoryMetadata.model_validate(page)
    except ValidationError as error:
        raise RuntimeControlError(
            "Deployment inventory metadata is missing/invalid; incomplete."
        ) from error
    if (metadata.has_more and not metadata.next_cursor) or (
        not metadata.has_more and metadata.next_cursor is not None
    ):
        raise RuntimeControlError("Deployment inventory continuation contradicts has_more.")
    if metadata.returned != len(rows):
        raise RuntimeControlError(
            "Deployment inventory is inconsistent: returned does not match the row count."
        )
    return page


def _inventory_ids(rows: list[object]) -> set[str]:
    """Refuse duplicate/missing identities rather than certify a partial fleet."""
    identifiers: set[str] = set()
    for row in rows:
        raw = _strict_mapping(row, label="Deployment inventory row")
        identifier = raw.get("id")
        if not isinstance(identifier, str) or identifier in identifiers:
            raise RuntimeControlError(
                "Deployment inventory identity missing or duplicated; incomplete."
            )
        identifiers.add(identifier)
    return identifiers


def _rows_of(page: dict[str, object]) -> list[object]:
    """Return the validated deployments list of one inventory page."""
    rows = page.get("deployments")
    if not isinstance(rows, list):
        raise RuntimeControlError("Deployment inventory response omitted deployments.")
    # Narrowed by the isinstance above; ty models the element type as unknown.
    return cast("list[object]", rows)


def _strict_mapping(payload: object, *, label: str) -> dict[str, object]:
    """Validate one JSON object into a string-keyed mapping."""
    if not isinstance(payload, dict):
        raise RuntimeControlError(f"{label} was not an object.")
    return {str(key): item for key, item in payload.items()}


def _ledger_page(
    base_url: str,
    deployment_id: str,
    collection: str,
    *,
    limit: int,
    cursor: str | None,
) -> object:
    """Read one orders or fills page."""
    if not 1 <= limit <= 500:
        raise RuntimeControlError("--limit must be between 1 and 500.")
    query: dict[str, str] = {"limit": str(limit)}
    if cursor is not None:
        query["cursor"] = cursor
    path = f"{_DEPLOYMENTS_PREFIX}/{quote(deployment_id, safe='')}/{collection}"
    return request_json(method="GET", url=f"{base_url}{path}?{urlencode(query)}")


def fleet_preview(base_url: str, *, action: str, mode: str) -> object:
    """Read affected ids and residual positions. This does not mutate."""
    query = urlencode({"action": action, "mode": mode})
    return request_json(method="GET", url=f"{base_url}{_FLEET_PREFIX}/preview?{query}")


def fleet_inhibition(base_url: str) -> object:
    """Read the durable entry latch."""
    return request_json(method="GET", url=f"{base_url}{_FLEET_PREFIX}")


def fleet_execute(
    base_url: str,
    action: str,
    payload: dict[str, object],
    *,
    settings: Settings | None = None,
) -> object:
    """Post one confirmed fleet action through the mutation boundary."""
    if action not in {"disarm", "stop", "flatten", "rearm"}:
        raise RuntimeControlError(f"unsupported fleet action: {action}")
    return request_mutation_json(
        method="POST",
        url=f"{base_url}{_FLEET_PREFIX}/{action}",
        payload=payload,
        settings=settings,
    )
