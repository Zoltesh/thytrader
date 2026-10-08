"""Managed side of venue reconciliation: live book reads and inventory aggregation.

Reads every live deployment's accounting snapshot, records unreadable, unresolved, or
unsupported books as explicit findings, and aggregates managed long/short quantities,
working orders, and claimed order IDs per currency. Missing reads are never treated as
zero inventory.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.products import (
    base_currency,
    is_spot_product_id,
    quote_currency,
)
from thytrader.operator.models import (
    ComponentReport,
    ReportStatus,
)
from thytrader.operator.venue_reconciliation_models import (
    ManagedListingEvidence,
    VenueFinding,
    VenueSeverity,
)
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    OrderSide,
    OrderStatus,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.trading.protection import book_inventory_reasons
from thytrader.trading.store import DisabledExecutionStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.trading.models import Order
    from thytrader.trading.store import ExecutionStore


_ZERO = Decimal("0")


_WORKING_STATUSES = frozenset({OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN})


class _ManagedInventory:
    """Aggregated managed long/short quantities and working orders per currency."""

    def __init__(self) -> None:
        """Start every aggregate at exact zero."""
        self.long: dict[str, Decimal] = {}
        self.short: dict[str, Decimal] = {}
        self.working: list[Order] = []
        self.pending_submit: list[Order] = []
        self.buy_notional: dict[str, Decimal] = {}
        self.unknown_buy_quotes: set[str] = set()
        self.claimed_venue_ids: set[str] = set()
        self.claimed_client_ids: set[str] = set()
        self.unresolved_currencies: set[str] = set()

    def add_position(self, currency: str, quantity: Decimal, *, long: bool) -> None:
        """Accumulate one signed position into its base currency."""
        target = self.long if long else self.short
        target[currency] = target.get(currency, _ZERO) + quantity

    def add_working(self, order: Order, quote: str | None) -> None:
        """Record one working order and its reserved quote when it is a priced buy."""
        if order.venue_order_id:
            self.working.append(order)
        else:
            self.pending_submit.append(order)
        if quote is None or order.side is not OrderSide.BUY:
            return
        if order.price is None:
            self.unknown_buy_quotes.add(quote)
            return
        remaining = order.quantity - order.filled_quantity
        if remaining > 0:
            self.buy_notional[quote] = self.buy_notional.get(quote, _ZERO) + remaining * order.price

    def net(self, currency: str) -> Decimal:
        """Managed net quantity of one currency (long minus short)."""
        return self.long.get(currency, _ZERO) - self.short.get(currency, _ZERO)

    def currencies(self) -> tuple[str, ...]:
        """Every base currency with any managed position or short."""
        return tuple(sorted({*self.long, *self.short, *self.unresolved_currencies}))


class _VenueUnavailableError(RuntimeError):
    """The report cannot be built; carry the failed component."""

    def __init__(self, component: ComponentReport) -> None:
        """Store the component for the failed envelope."""
        self.component = component
        super().__init__(component.detail)


@dataclass(frozen=True, slots=True)
class _ManagedRead:
    """Known snapshots plus explicit completeness; missing reads are never zero inventory."""

    snapshots: tuple[DeploymentSnapshot, ...]
    evidence: ManagedListingEvidence


async def _managed_snapshots(
    execution: ExecutionStore | None,
    findings: list[VenueFinding],
    warnings: list[str],
) -> _ManagedRead:
    """Read every live book, including stopped books and their historical order claims.

    A stopped flat book cannot hold inventory but its order IDs still establish local
    ownership. If the venue returns one as nonterminal, it is drift, not foreign activity.
    """
    if execution is None or isinstance(execution, DisabledExecutionStore):
        raise _VenueUnavailableError(
            ComponentReport(
                name="venue_reconciliation",
                status=ReportStatus.FAILED,
                reason_code="EXECUTION_UNAVAILABLE",
                detail="No execution store is attached; managed inventory is unknown, not empty.",
            )
        )
    try:
        deployments = await execution.list_deployments()
    except Exception as error:
        raise _VenueUnavailableError(
            ComponentReport(
                name="venue_reconciliation",
                status=ReportStatus.FAILED,
                reason_code="EXECUTION_UNAVAILABLE",
                detail="Execution storage could not be read; reconciliation is unavailable.",
            )
        ) from error
    live = tuple(item for item in deployments if item.mode is DeploymentMode.LIVE)
    snapshots: list[DeploymentSnapshot] = []
    for deployment in live:
        try:
            snapshots.append(await execution.get_accounting_snapshot(deployment.id))
        except Exception:  # noqa: BLE001 - one unreadable book must not sink the report.
            findings.append(
                VenueFinding(
                    reason_code="SNAPSHOT_UNAVAILABLE",
                    severity=VenueSeverity.UNKNOWN,
                    deployment_id=deployment.id,
                    detail=(
                        "This live book could not be read; its inventory and orders are "
                        "unknown, not absent."
                    ),
                )
            )
    unreadable = len(live) - len(snapshots)
    if unreadable:
        warnings.append(
            f"{unreadable} live book(s) could not be read; the managed side is partial."
        )
    known = tuple(snapshots)
    unsupported = _unsupported_products(known)
    if unsupported:
        findings.append(
            VenueFinding(
                reason_code="MANAGED_PRODUCT_SCOPE_UNKNOWN",
                severity=VenueSeverity.UNKNOWN,
                detail="Managed inventory contains unsupported products; comparisons are unknown.",
            )
        )
    unresolved = tuple(
        item.deployment.id
        for item in known
        if not item.accounting_complete or _unresolved_products(item)
    )
    findings.extend(
        VenueFinding(
            reason_code="MANAGED_ACCOUNTING_UNRESOLVED",
            severity=VenueSeverity.UNKNOWN,
            deployment_id=deployment_id,
            detail=(
                "Retained fills/executions, occupied runtimes without positions, or incomplete "
                "accounting scope leave managed inventory unresolved. Dependent quantities "
                "cannot be classified as foreign."
            ),
        )
        for deployment_id in unresolved
    )
    incomplete_scope = any(not item.accounting_complete for item in known)
    return _ManagedRead(
        known,
        ManagedListingEvidence(
            status="partial" if unreadable or unsupported or incomplete_scope else "complete",
            accounting_status=(
                "unresolved" if unreadable or unsupported or unresolved else "complete"
            ),
            unresolved_deployment_ids=unresolved,
            unsupported_products=unsupported,
            expected_books=len(live),
            read_books=len(snapshots),
            missing_deployment_ids=tuple(
                item.id for item in live if item.id not in {s.deployment.id for s in snapshots}
            ),
        ),
    )


def _unresolved_products(snapshot: DeploymentSnapshot) -> tuple[str, ...]:
    """Name affected products without reconstructing quantities or stop geometry."""
    products = {
        snapshot.deployment.product_id,
        *(resolved_product_id(row.product_id, snapshot.deployment) for row in snapshot.orders),
        *(
            resolved_product_id(row.product_id, snapshot.deployment)
            for row in snapshot_positions(snapshot)
        ),
        *(row.product_id for row in snapshot.instrument_runtimes),
    }
    return tuple(
        sorted(
            product for product in products if book_inventory_reasons(snapshot, product_id=product)
        )
    )


def _unsupported_products(snapshots: Sequence[DeploymentSnapshot]) -> tuple[str, ...]:
    """Resolve actual managed products; unsupported inventory cannot become foreign."""
    products = {
        resolved_product_id(row.product_id, snapshot.deployment)
        for snapshot in snapshots
        for row in (*snapshot_positions(snapshot), *snapshot.orders)
    }
    return tuple(sorted(product for product in products if not is_spot_product_id(product)))


def _collect_inventory(snapshots: Sequence[DeploymentSnapshot]) -> _ManagedInventory:
    """Aggregate managed positions and working orders across every live book."""
    inventory = _ManagedInventory()
    for snapshot in snapshots:
        inventory.unresolved_currencies.update(
            base_currency(product) if is_spot_product_id(product) else product
            for product in _unresolved_products(snapshot)
        )
        for position in snapshot_positions(snapshot):
            product = resolved_product_id(position.product_id, snapshot.deployment)
            currency = base_currency(product) if is_spot_product_id(product) else product
            signed = abs(position.quantity)
            inventory.add_position(currency, signed, long=position.side.value == "long")
        for order in snapshot.orders:
            inventory.claimed_client_ids.add(order.client_order_id)
            if order.venue_order_id:
                inventory.claimed_venue_ids.add(order.venue_order_id)
            if order.status not in _WORKING_STATUSES:
                continue
            product = resolved_product_id(order.product_id, snapshot.deployment)
            quote = quote_currency(product) if is_spot_product_id(product) else None
            inventory.add_working(order, quote)
    return inventory
