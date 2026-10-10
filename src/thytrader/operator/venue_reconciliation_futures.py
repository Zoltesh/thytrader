"""Venue reconciliation futures section: external CFM positions and orders (ADR 0127, P0-6).

ThyTrader manages no futures, so every CFM position and futures order is external,
unmanaged exposure. Positions come from the newest read-only mirror snapshot; futures
orders from a fresh read-only order-history listing. Unknown is never shown as none:

- ``FUTURES_EXTERNAL_POSITIONS`` (info): open positions, with USD notional when every
  contract size is known.
- ``FUTURES_EXTERNAL_ORDERS`` (info): nonterminal futures orders at the venue.
- ``FUTURES_POSITIONS_UNKNOWN`` (unknown): the snapshot is stale or the position read failed.
- ``FUTURES_ORDERS_LISTING_INCOMPLETE`` (unknown): the futures order listing failed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from thytrader.exchanges.futures_models import FuturesAccountStoreUnavailableError
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailureKind
from thytrader.operator.venue_reconciliation_models import (
    ForeignOpenOrderRow,
    FuturesExternalPositionRow,
    FuturesReconciliationSection,
    VenueFinding,
    VenueListingEvidence,
    VenueSeverity,
)

if TYPE_CHECKING:
    from thytrader.exchanges.futures_models import (
        FuturesAccountObservation,
        FuturesAccountSnapshotStore,
        FuturesPosition,
    )
    from thytrader.market_data.instruments import FuturesProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.portfolio.service import PortfolioService

_STALE_AFTER = timedelta(seconds=180)
PositionsSource = Literal["mirror_snapshot", "stale", "unavailable", "not_observed"]


async def futures_reconciliation(
    *,
    portfolio: PortfolioService,
    store: FuturesAccountSnapshotStore | None,
    market_data: MarketDataService | None,
    findings: list[VenueFinding],
    now: datetime | None = None,
) -> FuturesReconciliationSection:
    """Build the futures section; every unknown input is disclosed as unknown."""
    observed = now or datetime.now(UTC)
    orders, orders_listing = await _futures_orders(portfolio, findings)
    latest, source = await _latest(store, observed, findings)
    contracts = await _contracts(market_data)
    positions = None
    total: str | None = None
    if latest is not None and latest.positions is not None:
        rows = tuple(
            _row(position, contracts.get(position.product_id)) for position in latest.positions
        )
        positions = rows
        total = _total(rows)
        if rows:
            amount = (
                f"{total} USD" if total is not None else "unknown USD (a contract size is unknown)"
            )
            findings.append(
                VenueFinding(
                    reason_code="FUTURES_EXTERNAL_POSITIONS",
                    severity=VenueSeverity.INFO,
                    detail=(
                        f"{len(rows)} external CFM position(s), notional {amount}: unmanaged "
                        "exposure on collateral shared with the USDC spot balance."
                    ),
                )
            )
    if orders:
        findings.append(
            VenueFinding(
                reason_code="FUTURES_EXTERNAL_ORDERS",
                severity=VenueSeverity.INFO,
                detail=f"{len(orders)} nonterminal futures order(s) at the venue; none is managed.",
            )
        )
    return FuturesReconciliationSection(
        positions_source=source,
        positions_observed_at=None if latest is None else latest.observed_at,
        positions=positions,
        unmanaged_notional_usd=total,
        orders=orders,
        orders_listing=orders_listing,
    )


async def _latest(
    store: FuturesAccountSnapshotStore | None, now: datetime, findings: list[VenueFinding]
) -> tuple[FuturesAccountObservation | None, PositionsSource]:
    """Return the newest snapshot and how far its positions can be trusted."""
    if store is None:
        return None, "not_observed"
    try:
        latest = await store.latest()
    except FuturesAccountStoreUnavailableError:
        findings.append(_positions_unknown("Futures account storage could not be read."))
        return None, "unavailable"
    if latest is None:
        return None, "not_observed"
    if latest.positions is None:
        findings.append(_positions_unknown("The newest CFM position read failed."))
        return latest, "unavailable"
    if now - latest.observed_at > _STALE_AFTER:
        findings.append(_positions_unknown("The newest CFM snapshot is older than 3 minutes."))
        return latest, "stale"
    return latest, "mirror_snapshot"


def _positions_unknown(cause: str) -> VenueFinding:
    """One unknown finding for unproved futures positions."""
    return VenueFinding(
        reason_code="FUTURES_POSITIONS_UNKNOWN",
        severity=VenueSeverity.UNKNOWN,
        detail=f"{cause} External futures exposure is unknown, not zero.",
    )


async def _futures_orders(
    portfolio: PortfolioService, findings: list[VenueFinding]
) -> tuple[tuple[ForeignOpenOrderRow, ...] | None, VenueListingEvidence]:
    """Read every nonterminal futures order; ``None`` means unknown."""
    try:
        orders = await portfolio.list_futures_open_orders()
    except Exception as error:  # noqa: BLE001 - provider failures are redacted at this boundary.
        failure = error.failure if isinstance(error, ExchangeReadError) else None
        if failure is not None and failure.kind is ExchangeReadFailureKind.UNSUPPORTED:
            # The account adapter has no futures capability: not observed, not a failed read.
            return None, VenueListingEvidence(status="unavailable", failure=failure)
        findings.append(
            VenueFinding(
                reason_code="FUTURES_ORDERS_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail="The futures order listing is incomplete; external futures orders are "
                "unknown, never guessed.",
            )
        )
        return None, VenueListingEvidence(status="unavailable", failure=failure)
    rows = tuple(
        ForeignOpenOrderRow(
            venue_order_id=order.venue_order_id,
            client_order_id=order.client_order_id,
            product_id=order.product_id,
            side=order.side,
            status=order.status,
        )
        for order in orders
    )
    evidence = VenueListingEvidence(
        status="complete",
        scope="futures_order_history_nonterminal",
        demo=portfolio.demo,
        observed_at=datetime.now(UTC),
        rows=len(rows),
    )
    return rows, evidence


async def _contracts(market_data: MarketDataService | None) -> dict[str, FuturesProduct]:
    """Map futures ids to listed contracts; an unreadable listing yields no sizes."""
    cache = None if market_data is None else market_data.futures_catalog
    if cache is None:
        return {}
    try:
        snapshot = await cache.snapshot()
    except Exception:  # noqa: BLE001 - notional becomes unknown, never guessed.
        return {}
    return {product.product_id: product for product in snapshot.products}


def _row(position: FuturesPosition, contract: FuturesProduct | None) -> FuturesExternalPositionRow:
    """One external position, with USD notional when size and price are known."""
    notional: Decimal | None = None
    if contract is not None and position.current_price is not None:
        notional = position.number_of_contracts * contract.contract_size * position.current_price
    return FuturesExternalPositionRow(
        product_id=position.product_id,
        side=position.side.value,
        number_of_contracts=format(position.number_of_contracts, "f"),
        contract_size=None if contract is None else format(contract.contract_size, "f"),
        underlying=None if contract is None else contract.underlying,
        current_price=(
            None if position.current_price is None else format(position.current_price, "f")
        ),
        notional_usd=None if notional is None else format(notional, "f"),
    )


def _total(rows: tuple[FuturesExternalPositionRow, ...]) -> str | None:
    """Sum USD notionals only when every row's notional is known."""
    if any(row.notional_usd is None for row in rows):
        return None
    return format(sum((Decimal(row.notional_usd or "0") for row in rows), Decimal(0)), "f")
