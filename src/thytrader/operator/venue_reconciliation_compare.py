"""Comparison rows for venue reconciliation: managed inventory versus the venue listing.

Builds the per-asset rows (foreign holdings disclosed as information, managed
shortfalls as warnings), the quote-currency pool rows, and the order section that
separates foreign open orders from orphaned managed working orders. Comparisons that
depend on an incomplete listing are reported as unknown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from thytrader.decimal_text import canonical_decimal
from thytrader.market_data.products import (
    SPOT_QUOTE_CURRENCIES,
    SpotQuoteCurrency,
)
from thytrader.operator.venue_reconciliation_managed import _ZERO, _ManagedInventory
from thytrader.operator.venue_reconciliation_models import (
    AssetReconciliationRow,
    ForeignOpenOrderRow,
    OrderReconciliationSection,
    OrphanManagedOrderRow,
    QuoteReconciliationRow,
    VenueFinding,
    VenueListingEvidence,
    VenueSeverity,
)

if TYPE_CHECKING:
    from decimal import Decimal

_FOREIGN_ORDER_ROW_LIMIT = 50


def _unknown_asset_row(
    inventory: _ManagedInventory, currency: str, managed_net: Decimal
) -> AssetReconciliationRow:
    """One managed asset whose venue quantity is unknown because the listing failed."""
    return AssetReconciliationRow(
        currency=currency,
        managed_long_quantity=canonical_decimal(inventory.long.get(currency, _ZERO)),
        managed_short_quantity=canonical_decimal(inventory.short.get(currency, _ZERO)),
        managed_net_quantity=canonical_decimal(managed_net),
        classification="venue_unknown",
    )


def _asset_rows(
    inventory: _ManagedInventory,
    balance_rows: dict[str, tuple[Decimal, Decimal, Decimal, int]],
    complete: bool,
    findings: list[VenueFinding],
    managed_complete: bool,
) -> tuple[AssetReconciliationRow, ...]:
    """Classify every asset with managed quantity or a non-quote venue balance."""
    currencies = set(inventory.currencies())
    currencies.update(
        currency for currency in balance_rows if currency not in SPOT_QUOTE_CURRENCIES
    )
    if not managed_complete:
        return _managed_unknown_assets(currencies, balance_rows, complete)
    rows: list[AssetReconciliationRow] = []
    for currency in sorted(currencies):
        if currency in inventory.unresolved_currencies:
            rows.extend(_managed_unknown_assets({currency}, balance_rows, complete))
            continue
        managed_net = inventory.net(currency)
        venue = balance_rows.get(currency)
        if not complete:
            if managed_net == 0:
                continue
            rows.append(_unknown_asset_row(inventory, currency, managed_net))
            continue
        available, hold, total, count = venue or (_ZERO, _ZERO, _ZERO, 0)
        if managed_net == 0 and total == 0:
            continue
        if count > 1:
            findings.append(
                VenueFinding(
                    reason_code="DUPLICATE_BALANCE_ROWS",
                    severity=VenueSeverity.INFO,
                    detail=(
                        f"{count} venue balance rows for {currency} were summed. "
                        "Duplicate rows are not dropped and are not account identifiers."
                    ),
                )
            )
        foreign = total - managed_net
        classification: Literal[
            "matched", "external_inventory", "managed_exceeds_venue", "venue_unknown"
        ]
        classification = _classify_difference(foreign)
        rows.append(
            AssetReconciliationRow(
                currency=currency,
                venue_quantity=canonical_decimal(total),
                venue_available=canonical_decimal(available),
                venue_hold=canonical_decimal(hold),
                venue_rows=count,
                managed_long_quantity=canonical_decimal(inventory.long.get(currency, _ZERO)),
                managed_short_quantity=canonical_decimal(inventory.short.get(currency, _ZERO)),
                managed_net_quantity=canonical_decimal(managed_net),
                foreign_quantity=canonical_decimal(foreign),
                classification=classification,
            )
        )
        if classification == "external_inventory":
            findings.append(
                VenueFinding(
                    reason_code="EXTERNAL_INVENTORY",
                    severity=VenueSeverity.INFO,
                    detail=(
                        f"The venue holds {canonical_decimal(foreign)} more {currency} than "
                        "managed books claim. External holdings are not an error and are "
                        "never flattened or totalled into managed exposure."
                    ),
                )
            )
        elif classification == "managed_exceeds_venue":
            findings.append(
                VenueFinding(
                    reason_code="MANAGED_INVENTORY_SHORTFALL",
                    severity=VenueSeverity.WARNING,
                    detail=(
                        f"Managed books claim {canonical_decimal(managed_net)} {currency} but "
                        f"the venue holds {canonical_decimal(total)}. Funds may have moved, "
                        "or short books owe base units; reconcile before new risk."
                    ),
                )
            )
    return tuple(rows)


def _classify_difference(
    foreign: Decimal,
) -> Literal["matched", "external_inventory", "managed_exceeds_venue"]:
    """Classify a quantity difference only after both evidence sides are complete."""
    if foreign > 0:
        return "external_inventory"
    if foreign < 0:
        return "managed_exceeds_venue"
    return "matched"


def _managed_unknown_assets(
    currencies: set[str],
    balance_rows: dict[str, tuple[Decimal, Decimal, Decimal, int]],
    venue_complete: bool,
) -> tuple[AssetReconciliationRow, ...]:
    """Disclose observed venue quantities without foreign or shortfall claims."""
    rows: list[AssetReconciliationRow] = []
    for currency in sorted(currencies):
        available, hold, total, count = balance_rows.get(currency, (_ZERO, _ZERO, _ZERO, 0))
        rows.append(
            AssetReconciliationRow(
                currency=currency,
                classification="managed_unknown",
                venue_quantity=canonical_decimal(total) if venue_complete else None,
                venue_available=canonical_decimal(available) if venue_complete else None,
                venue_hold=canonical_decimal(hold) if venue_complete else None,
                venue_rows=count if venue_complete else 0,
            )
        )
    return tuple(rows)


def _quote_rows(
    inventory: _ManagedInventory,
    balance_rows: dict[str, tuple[Decimal, Decimal, Decimal, int]],
    complete: bool,
    managed_complete: bool,
) -> tuple[QuoteReconciliationRow, ...]:
    """Disclose each quote currency's venue balance beside managed buy reservations."""
    currencies: set[SpotQuoteCurrency] = set()
    for quote in {*inventory.buy_notional, *inventory.unknown_buy_quotes}:
        for candidate in SPOT_QUOTE_CURRENCIES:
            if candidate == quote:
                currencies.add(candidate)
    if complete:
        for currency in balance_rows:
            for candidate in SPOT_QUOTE_CURRENCIES:
                if candidate == currency:
                    currencies.add(candidate)
    rows: list[QuoteReconciliationRow] = []
    for quote in sorted(currencies):
        venue = balance_rows.get(quote) if complete else None
        rows.append(
            QuoteReconciliationRow(
                quote_currency=quote,
                venue_available=None if venue is None else canonical_decimal(venue[0]),
                venue_total=None if venue is None else canonical_decimal(venue[2]),
                managed_working_buy_notional=(
                    canonical_decimal(inventory.buy_notional.get(quote, _ZERO))
                    if managed_complete and quote not in inventory.unknown_buy_quotes
                    else None
                ),
            )
        )
    return tuple(rows)


def _order_section(
    inventory: _ManagedInventory,
    venue_orders: tuple[ForeignOpenOrderRow, ...] | None,
    evidence: VenueListingEvidence,
    findings: list[VenueFinding],
    managed_complete: bool,
) -> OrderReconciliationSection:
    """Match only when both sides are complete; pending submits can claim client IDs."""
    if venue_orders is None or not managed_complete:
        return OrderReconciliationSection(
            managed_working=len(inventory.working) if managed_complete else None,
            managed_pending_submit=len(inventory.pending_submit) if managed_complete else None,
            venue_open=None if venue_orders is None else len(venue_orders),
            matched=None,
            foreign=None,
            orphan=None,
            listing=evidence,
        )
    venue_ids = {order.venue_order_id for order in venue_orders}
    matched_ids: set[str] = set()
    orphans: list[OrphanManagedOrderRow] = []
    for order in inventory.working:
        if order.venue_order_id in venue_ids:
            matched_ids.add(order.venue_order_id)
            continue
        orphans.append(
            OrphanManagedOrderRow(
                deployment_id=order.deployment_id,
                order_id=order.id,
                client_order_id=order.client_order_id,
                venue_order_id=order.venue_order_id,
                product_id=order.product_id,
                status=order.status.value,
            )
        )
    pending_ids = {order.client_order_id for order in inventory.pending_submit}
    matched_ids.update(
        order.venue_order_id
        for order in venue_orders
        if order.client_order_id is not None and order.client_order_id in pending_ids
    )
    foreign = [
        order
        for order in venue_orders
        if order.venue_order_id not in inventory.claimed_venue_ids
        and order.client_order_id not in inventory.claimed_client_ids
    ]
    unmatched_managed = len(venue_orders) - len(foreign) - len(matched_ids)
    if unmatched_managed:
        findings.append(
            VenueFinding(
                reason_code="MANAGED_ORDER_STATUS_MISMATCH",
                severity=VenueSeverity.WARNING,
                detail=f"{unmatched_managed} venue working order(s) are claimed by local terminal "
                "records. These are not foreign orders; reconcile status before new risk.",
            )
        )
    if orphans:
        findings.append(
            VenueFinding(
                reason_code="MANAGED_ORDER_NOT_AT_VENUE",
                severity=VenueSeverity.WARNING,
                deployment_id=orphans[0].deployment_id,
                detail=(
                    f"{len(orphans)} managed working order(s) are absent from the venue's "
                    "nonterminal spot listing. Unknown is not rejected: reconcile with the "
                    "venue before "
                    "replacing or cancelling anything. This report changed nothing."
                ),
            )
        )
    if foreign:
        findings.append(
            VenueFinding(
                reason_code="EXTERNAL_OPEN_ORDERS",
                severity=VenueSeverity.INFO,
                detail=(
                    f"{len(foreign)} nonterminal venue order(s) belong to no managed book "
                    "(manual or external activity). They are disclosed, not cancelled."
                ),
            )
        )
    return OrderReconciliationSection(
        managed_working=len(inventory.working),
        managed_pending_submit=len(inventory.pending_submit),
        venue_open=len(venue_orders),
        matched=len(matched_ids),
        foreign=tuple(foreign[:_FOREIGN_ORDER_ROW_LIMIT]),
        foreign_truncated=len(foreign) > _FOREIGN_ORDER_ROW_LIMIT,
        orphan=tuple(orphans),
        listing=evidence,
    )
