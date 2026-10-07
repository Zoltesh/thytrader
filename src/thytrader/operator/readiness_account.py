"""Account-scope readiness: venue quotes, account and product caps, book inventory."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailure
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderSide,
    OrderStatus,
    PositionSide,
    is_venue_protection,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.execution.protection import book_inventory_reasons
from thytrader.market_data.products import (
    SPOT_QUOTE_CURRENCIES,
    SpotQuoteCurrency,
    is_spot_product_id,
    quote_currency,
)
from thytrader.operator.readiness_models import (
    ReadinessAccountCaps,
    ReadinessFinding,
    ReadinessInventoryEvidence,
    ReadinessProductCapRow,
    ReadinessQuoteExposure,
    ReadinessSeverity,
    ReadinessVenueQuote,
)
from thytrader.research.indicators import canonical_decimal
from thytrader.risk.exposure import product_exposure, working_entry_notional

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from thytrader.portfolio.models import PortfolioAsset
    from thytrader.portfolio.service import PortfolioService
    from thytrader.risk.models import ActiveRiskPolicy

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class _VenueRead:
    """Venue observation outcome shared by the readiness sections."""

    rows: tuple[ReadinessVenueQuote, ...]
    available: Mapping[str, Decimal]
    failure: ExchangeReadFailure | None
    demo: bool
    complete: bool


def _inventory_evidence(
    deployments: Sequence[Deployment],
    snapshots: Mapping[UUID, DeploymentSnapshot],
    *,
    quote: SpotQuoteCurrency | None = None,
) -> ReadinessInventoryEvidence:
    """Describe expected reads, including unreadable stopped books with unknown residuals."""
    missing = tuple(item.id for item in deployments if item.id not in snapshots)
    read = tuple(snapshots[item.id] for item in deployments if item.id in snapshots)
    unsupported = tuple(
        product for product in _book_products(read) if not is_spot_product_id(product)
    )
    unpriced = _unpriced_entries(read)
    unresolved = tuple(item.deployment.id for item in read if _inventory_unresolved(item, quote))
    return ReadinessInventoryEvidence(
        status="partial" if missing or unsupported or unpriced else "complete",
        unpriced_entry_order_ids=unpriced,
        expected_books=len(deployments),
        read_books=len(read),
        missing_deployment_ids=missing,
        unsupported_products=unsupported,
        accounting_status=(
            "unresolved" if missing or unsupported or unpriced or unresolved else "complete"
        ),
        unresolved_deployment_ids=unresolved,
    )


def _inventory_unresolved(
    snapshot: DeploymentSnapshot, quote: SpotQuoteCurrency | None = None
) -> bool:
    """Product economics and runtime/inventory consistency, not display text, prove completeness."""
    return not snapshot.accounting_complete or any(
        book_inventory_reasons(snapshot, product_id=product)
        for product in _book_products((snapshot,))
        if quote is None or _product_quote(product) == quote
    )


def _unpriced_entries(snapshots: Sequence[DeploymentSnapshot]) -> tuple[UUID, ...]:
    """Unresolved entries without notional evidence make capacity unknown, not free."""
    ids: list[UUID] = []
    for snapshot in snapshots:
        purposes = {intent.id: intent.purpose for intent in snapshot.intents}
        ids.extend(
            order.id
            for order in snapshot.orders
            if order.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
            and order.quantity > order.filled_quantity
            and order.price is None
            and not is_venue_protection(order.kind)
            and purposes.get(order.intent_id, IntentPurpose.ENTRY) is IntentPurpose.ENTRY
        )
    return tuple(ids)


async def _read_venue(
    portfolio: PortfolioService,
    policy: ActiveRiskPolicy | None,
    snapshots: Mapping[UUID, DeploymentSnapshot],
    findings: list[ReadinessFinding],
) -> _VenueRead:
    """Observe venue quote balances for every quote currency the scope references."""
    currencies: set[SpotQuoteCurrency] = set()
    if policy is not None:
        currencies.add(policy.definition.quote_currency)
    for product in _book_products(tuple(snapshots.values())):
        if is_spot_product_id(product):
            currencies.add(quote_currency(product))
    try:
        observed = await portfolio.get_portfolio()
    except ExchangeReadError as error:
        _venue_unknown_finding(snapshots, findings, error.failure)
        return _VenueRead(rows=(), available={}, failure=error.failure, demo=False, complete=False)
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        _venue_unknown_finding(snapshots, findings, None)
        return _VenueRead(rows=(), available={}, failure=None, demo=False, complete=False)
    summed = _sum_quote_balances(observed.assets, currencies)
    rows = [
        ReadinessVenueQuote(
            quote_currency=quote,
            venue_available=canonical_decimal(parts[0]),
            venue_hold=canonical_decimal(parts[1]),
            venue_total=canonical_decimal(parts[2]),
            observed_at=observed.as_of,
            demo=observed.demo,
        )
        for quote, parts in sorted(summed.items())
    ]
    rows.extend(
        ReadinessVenueQuote(
            quote_currency=missing,
            venue_available="0",
            venue_hold="0",
            venue_total="0",
            observed_at=observed.as_of,
            demo=observed.demo,
        )
        for missing in sorted(currencies - set(summed))
    )
    return _VenueRead(
        rows=tuple(rows),
        available={quote: parts[0] for quote, parts in summed.items()},
        failure=None,
        demo=observed.demo,
        complete=True,
    )


def _sum_quote_balances(
    assets: Sequence[PortfolioAsset], currencies: set[SpotQuoteCurrency]
) -> dict[SpotQuoteCurrency, tuple[Decimal, Decimal, Decimal]]:
    """Sum available, hold, and total for each requested quote, including duplicates."""
    summed: dict[SpotQuoteCurrency, tuple[Decimal, Decimal, Decimal]] = {}
    for asset in assets:
        quote = _as_spot_quote(asset.currency)
        if quote is None or quote not in currencies:
            continue
        previous = summed.get(quote, (_ZERO, _ZERO, _ZERO))
        summed[quote] = (
            previous[0] + asset.available,
            previous[1] + asset.hold,
            previous[2] + asset.total,
        )
    return summed


def _as_spot_quote(currency: str) -> SpotQuoteCurrency | None:
    """Narrow one currency text to the spot quote union, or None."""
    for candidate in SPOT_QUOTE_CURRENCIES:
        if candidate == currency:
            return candidate
    return None


def _venue_unknown_finding(
    snapshots: Mapping[UUID, DeploymentSnapshot],
    findings: list[ReadinessFinding],
    failure: ExchangeReadFailure | None,
) -> None:
    """Record that the venue quote is unknown; entries already fail closed there."""
    live = any(item.deployment.mode is DeploymentMode.LIVE for item in snapshots.values())
    reason = (
        "The venue quote balance could not be observed, so account caps and remaining "
        "capacity are unknown (never guessed)."
        if failure is None
        else f"The venue quote balance could not be observed ({failure.summary()}); "
        "account caps and remaining capacity are unknown (never guessed)."
    )
    findings.append(
        ReadinessFinding(
            reason_code="VENUE_BALANCE_UNKNOWN",
            severity=ReadinessSeverity.UNKNOWN if live else ReadinessSeverity.INFO,
            detail=reason,
        )
    )


def _account_section(
    policy: ActiveRiskPolicy,
    live_bearing: Sequence[DeploymentSnapshot],
    venue: _VenueRead,
    findings: list[ReadinessFinding],
    *,
    inventory_evidence: ReadinessInventoryEvidence,
) -> ReadinessAccountCaps:
    """Compute account capacity in the policy quote currency (ADR 0106 scope)."""
    definition = policy.definition
    quote = definition.quote_currency
    venue_available = None if not venue.complete else venue.available.get(quote, _ZERO)
    complete = (
        inventory_evidence.status == "complete"
        and inventory_evidence.accounting_status == "complete"
    )
    inventory = sum(
        (
            position.quantity * position.entry_price
            for snapshot in live_bearing
            for position in snapshot_positions(snapshot)
            if position.side is PositionSide.LONG
            and _product_quote(resolved_product_id(position.product_id, snapshot.deployment))
            == quote
        ),
        _ZERO,
    )
    buy_reserved = sum((_buy_entry_reserved(item, quote) for item in live_bearing), _ZERO)
    capital_base = (
        None
        if venue_available is None or not complete
        else venue_available + inventory + buy_reserved
    )
    exposure = sum((_quote_exposure(item, quote) for item in live_bearing), _ZERO)
    excluded = tuple(
        product for product in _book_products(live_bearing) if _product_quote(product) != quote
    )
    if excluded:
        findings.append(
            ReadinessFinding(
                reason_code="QUOTE_CURRENCY_MISMATCH",
                severity=ReadinessSeverity.INFO,
                detail=f"Products outside {quote} account scope are excluded, not converted: "
                + ", ".join(excluded)
                + ".",
            )
        )
    if not complete:
        findings.append(
            ReadinessFinding(
                reason_code="ACCOUNT_INVENTORY_INCOMPLETE",
                severity=ReadinessSeverity.UNKNOWN,
                detail=(
                    "Managed live inventory is incomplete; account totals and capacity are unknown."
                ),
            )
        )
    fraction_cap = (
        None
        if capital_base is None
        else capital_base * Decimal(definition.max_portfolio_exposure_fraction)
    )
    absolute = (
        None
        if definition.max_portfolio_exposure_quote is None
        else Decimal(definition.max_portfolio_exposure_quote)
    )
    effective = fraction_cap
    if effective is not None and absolute is not None:
        effective = min(effective, absolute)
    remaining = None if effective is None else effective - exposure
    daily_fraction_cap = (
        None
        if capital_base is None
        else capital_base * Decimal(definition.daily_loss_limit_fraction)
    )
    daily_absolute = (
        None
        if definition.max_daily_loss_quote is None
        else Decimal(definition.max_daily_loss_quote)
    )
    effective_daily = daily_fraction_cap
    if effective_daily is not None and daily_absolute is not None:
        effective_daily = min(effective_daily, daily_absolute)
    product_caps = _product_cap_rows(
        live_bearing, capital_base, definition.per_product_max_exposure_fraction, quote, complete
    )
    if effective is not None and exposure > effective:
        findings.append(
            ReadinessFinding(
                reason_code="ACCOUNT_EXPOSURE_CAP_EXCEEDED",
                severity=ReadinessSeverity.VIOLATION,
                detail=(
                    f"Account exposure {canonical_decimal(exposure)} {quote} exceeds the "
                    f"effective cap {canonical_decimal(effective)} {quote}. The entry gate "
                    "already denies new risk-increasing orders; inspect books before resuming."
                ),
            )
        )
    findings.extend(_product_cap_findings(product_caps, quote))
    return ReadinessAccountCaps(
        quote_currency=quote,
        policy_source=policy.source.value,
        policy_fingerprint=policy.policy_fingerprint,
        capital_base=None if capital_base is None else canonical_decimal(capital_base),
        venue_available_quote=(
            None if venue_available is None else canonical_decimal(venue_available)
        ),
        inventory=inventory_evidence,
        excluded_products=excluded,
        managed_long_inventory_cost=canonical_decimal(inventory) if complete else None,
        working_buy_entry_reserved=(
            canonical_decimal(buy_reserved) if inventory_evidence.status == "complete" else None
        ),
        current_exposure=canonical_decimal(exposure) if complete else None,
        exposure_fraction=definition.max_portfolio_exposure_fraction,
        absolute_exposure_cap=definition.max_portfolio_exposure_quote,
        effective_exposure_cap=None if effective is None else canonical_decimal(effective),
        remaining_entry_capacity=None if remaining is None else canonical_decimal(remaining),
        per_product_fraction=definition.per_product_max_exposure_fraction,
        product_caps=product_caps,
        daily_loss_fraction=definition.daily_loss_limit_fraction,
        daily_loss_quote_cap=definition.max_daily_loss_quote,
        effective_daily_loss_cap=(
            None if effective_daily is None else canonical_decimal(effective_daily)
        ),
        drawdown_fraction=definition.max_strategy_drawdown_fraction,
        venue_read_failure=venue.failure,
    )


def _product_cap_findings(
    product_caps: tuple[ReadinessProductCapRow, ...], quote: SpotQuoteCurrency
) -> tuple[ReadinessFinding, ...]:
    """Violation findings for products whose cost-basis exposure exceeds the account cap."""
    return tuple(
        ReadinessFinding(
            reason_code="PRODUCT_EXPOSURE_CAP_EXCEEDED",
            severity=ReadinessSeverity.VIOLATION,
            detail=(
                f"Account exposure on {row.product_id} is {row.exposure} {quote} "
                f"above its cap {row.cap} {quote}."
            ),
        )
        for row in product_caps
        if row.cap is not None
        and row.exposure is not None
        and Decimal(row.exposure) > Decimal(row.cap)
    )


def _product_cap_rows(
    live_bearing: Sequence[DeploymentSnapshot],
    capital_base: Decimal | None,
    per_product_fraction: str,
    quote: SpotQuoteCurrency,
    complete: bool,
) -> tuple[ReadinessProductCapRow, ...]:
    """Cost-basis exposure per product within the live policy-quote scope."""
    rows: list[ReadinessProductCapRow] = []
    for product in _book_products(live_bearing):
        if _product_quote(product) != quote:
            continue
        exposure = sum((product_exposure(item, product) for item in live_bearing), _ZERO)
        if exposure <= 0:
            continue
        cap = None if capital_base is None else capital_base * Decimal(per_product_fraction)
        rows.append(
            ReadinessProductCapRow(
                product_id=product,
                quote_currency=quote_currency(product) if is_spot_product_id(product) else None,
                exposure=canonical_decimal(exposure) if complete else None,
                cap=None if cap is None else canonical_decimal(cap),
                remaining=None if cap is None else canonical_decimal(cap - exposure),
            )
        )
    return tuple(rows)


def _book_products(snapshots: Sequence[DeploymentSnapshot]) -> tuple[str, ...]:
    """Every product the given books hold, work, or run, sorted."""
    products: set[str] = set()
    for snapshot in snapshots:
        products.add(snapshot.deployment.product_id)
        products.update(
            resolved_product_id(position.product_id, snapshot.deployment)
            for position in snapshot_positions(snapshot)
        )
        products.update(
            resolved_product_id(order.product_id, snapshot.deployment) for order in snapshot.orders
        )
        products.update(
            runtime.product_id for runtime in snapshot.instrument_runtimes if runtime.product_id
        )
    return tuple(sorted(product for product in products if product))


def _buy_entry_reserved(snapshot: DeploymentSnapshot, quote: SpotQuoteCurrency) -> Decimal:
    """Working buy-entry quote for one book; mirrors the risk gate's held capital.

    Sells and verified exit intents hold base units or reduce risk, so only buy
    entries reserve quote here (ADR 0106).
    """
    buys = replace(
        snapshot, orders=tuple(order for order in snapshot.orders if order.side is OrderSide.BUY)
    )
    return sum(
        (
            working_entry_notional(buys, product)
            for product in _book_products((snapshot,))
            if _product_quote(product) == quote
        ),
        _ZERO,
    )


def _product_quote(product: str) -> SpotQuoteCurrency | None:
    """Resolve a spot product's quote without inferring FX for unsupported products."""
    return quote_currency(product) if is_spot_product_id(product) else None


def _quote_exposure(snapshot: DeploymentSnapshot, quote: SpotQuoteCurrency) -> Decimal:
    """Cost-basis positions plus working entry remainders on actual same-quote products."""
    return sum(
        (
            product_exposure(snapshot, product)
            for product in _book_products((snapshot,))
            if _product_quote(product) == quote
        ),
        _ZERO,
    )


def _quote_rows(snapshot: DeploymentSnapshot) -> tuple[ReadinessQuoteExposure, ...]:
    """Split mixed-product books into exact per-quote rows; never label a cross-quote sum."""
    rows: list[ReadinessQuoteExposure] = []
    for quote in sorted({q for p in _book_products((snapshot,)) if (q := _product_quote(p))}):
        cost = sum(
            (
                position.quantity * position.entry_price
                for position in snapshot_positions(snapshot)
                if _product_quote(resolved_product_id(position.product_id, snapshot.deployment))
                == quote
            ),
            _ZERO,
        )
        exposure = _quote_exposure(snapshot, quote)
        rows.append(
            ReadinessQuoteExposure(
                quote_currency=quote,
                inventory_cost=(
                    None if _inventory_unresolved(snapshot, quote) else canonical_decimal(cost)
                ),
                working_entry_reserved=canonical_decimal(exposure - cost),
                exposure=(
                    None if _inventory_unresolved(snapshot, quote) else canonical_decimal(exposure)
                ),
            )
        )
    return tuple(rows)
