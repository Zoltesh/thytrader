"""Operator ``venue-reconciliation`` report: managed books versus the whole venue (ADR 0114).

Read-only. This report compares what every managed live book claims to hold or work
against sequential venue-wide reads of balances and nonterminal spot orders. A healthy
local ledger is *not* venue reconciliation: only the venue listing can reveal drift.

Semantics that matter financially:

- External (foreign) inventory — the venue holds more of an asset than managed books
  claim — is disclosed as information, never treated as an error, and never flattened.
- A managed inventory shortfall (the venue holds *less* than managed books claim) is a
  warning: funds may have moved, or short books owe base units.
- A managed working order absent from a *complete* venue open-order listing is an
  orphan warning. Unknown is not rejected; the operator must reconcile with the venue
  before replacing or cancelling anything. This report never creates or cancels orders.
- When either managed reads or a venue listing is incomplete, every
  comparison that depends on it is reported as unknown — never guessed — and the
  report says so explicitly (fail closed).
- Observation scope, timestamps, and listing completeness are explicit. Account
  identifiers and secrets never appear; assets are named by currency only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader import __version__
from thytrader.decimal_text import canonical_decimal
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailure
from thytrader.market_data.products import (
    SPOT_QUOTE_CURRENCIES,
    SpotQuoteCurrency,
    base_currency,
    is_spot_product_id,
    quote_currency,
)
from thytrader.operator.models import (
    PORTFOLIO_REDACTION,
    ComponentReport,
    OperatorEnvelope,
    ReportStatus,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
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

    from thytrader.portfolio.service import PortfolioService
    from thytrader.trading.models import Order
    from thytrader.trading.store import ExecutionStore

VENUE_RECONCILIATION_NOTE = (
    "A healthy local ledger is not venue reconciliation: managed inventory and working "
    "orders are compared against a fresh venue-wide listing. External (foreign) holdings "
    "and orders are disclosed, not treated as errors and not flattened. Unknown listings "
    "make dependent comparisons unknown. Reads are sequential, not atomic, and cover "
    "only the configured credential's venue visibility."
)
QUOTE_POOL_NOTE = (
    "Quote funds are a shared pool: foreign cash and managed reservations cannot be "
    "attributed exactly, so the venue balance is disclosed next to managed working buy "
    "notional instead of being reconciled."
)
_ZERO = Decimal("0")
_FOREIGN_ORDER_ROW_LIMIT = 50
_WORKING_STATUSES = frozenset({OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN})


class VenueSeverity(StrEnum):
    """How hard one venue-reconciliation finding should press on the operator."""

    INFO = "info"
    WARNING = "warning"
    UNKNOWN = "unknown"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime) -> datetime:
    """Keep report timestamps timezone-aware UTC after JSON round-trips."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("venue reconciliation timestamps must be timezone-aware UTC")
    return value.astimezone(UTC)


class VenueFinding(_FrozenModel):
    """One venue-side observation with a stable code and severity."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    severity: VenueSeverity
    deployment_id: UUID | None = None
    detail: str = Field(min_length=1, max_length=500)


class VenueListingEvidence(_FrozenModel):
    """Completeness of one venue listing read.

    ``status`` is ``complete`` only within the configured credential's visibility:
    the adapter paged the whole requested listing and every page parsed. A read
    failure or pagination anomaly leaves it ``unavailable`` and every dependent
    comparison unknown (fail closed). The adapter never returns partial rows.
    """

    status: Literal["complete", "unavailable"] = "unavailable"
    scope: Literal["not_observed", "venue_balances", "spot_order_history_nonterminal"] = (
        "not_observed"
    )
    demo: bool = False
    observed_at: datetime | None = None
    rows: int = Field(default=0, ge=0)
    failure: ExchangeReadFailure | None = None

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep listing timestamps timezone-aware UTC."""
        if value is None:
            return None
        return _require_utc(value)


class ManagedListingEvidence(_FrozenModel):
    """Completeness of every live-book read, including stopped books with possible residuals."""

    scope: Literal["all_live_books_including_stopped"] = "all_live_books_including_stopped"
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    expected_books: int | None = Field(default=None, ge=0)
    read_books: int = Field(default=0, ge=0)
    missing_deployment_ids: tuple[UUID, ...] = ()
    unsupported_products: tuple[str, ...] = ()
    accounting_status: Literal["complete", "unresolved", "unavailable"] = "unavailable"
    unresolved_deployment_ids: tuple[UUID, ...] = ()


class AssetReconciliationRow(_FrozenModel):
    """One base asset: managed books versus the venue's total holding.

    ``venue_quantity`` sums every venue balance row of the currency (duplicate wallet
    rows are summed, and ``venue_rows`` discloses when there was more than one).
    ``foreign_quantity`` is ``venue - managed_net``: positive means external holdings
    the installation does not manage (not an error); negative means the venue holds
    less than managed books claim.
    """

    currency: str
    venue_quantity: str | None = None
    venue_available: str | None = None
    venue_hold: str | None = None
    venue_rows: int = Field(default=0, ge=0)
    managed_long_quantity: str | None = None
    managed_short_quantity: str | None = None
    managed_net_quantity: str | None = None
    foreign_quantity: str | None = None
    classification: Literal[
        "matched", "external_inventory", "managed_exceeds_venue", "venue_unknown", "managed_unknown"
    ]


class QuoteReconciliationRow(_FrozenModel):
    """One quote currency: venue balance next to managed working buy notional."""

    quote_currency: SpotQuoteCurrency
    venue_available: str | None = None
    venue_total: str | None = None
    managed_working_buy_notional: str | None = None
    note: str = QUOTE_POOL_NOTE


class ForeignOpenOrderRow(_FrozenModel):
    """One nonterminal venue order no complete managed listing claims."""

    venue_order_id: str
    client_order_id: str | None = None
    product_id: str | None = None
    side: str | None = None
    status: str | None = None


class OrphanManagedOrderRow(_FrozenModel):
    """One managed working order a complete venue listing did not return."""

    deployment_id: UUID
    order_id: UUID
    client_order_id: str
    venue_order_id: str | None = None
    product_id: str
    status: str


class OrderReconciliationSection(_FrozenModel):
    """Managed working orders versus all venue nonterminal spot orders.

    ``foreign``, ``orphan`` and ``matched`` are null when either evidence side is
    incomplete, so omitted comparisons cannot imply an empty or fully managed venue.
    """

    managed_working: int | None = Field(default=None, ge=0)
    managed_pending_submit: int | None = Field(default=None, ge=0)
    venue_open: int | None = None
    matched: int | None = None
    foreign: tuple[ForeignOpenOrderRow, ...] | None = None
    foreign_truncated: bool = False
    orphan: tuple[OrphanManagedOrderRow, ...] | None = None
    listing: VenueListingEvidence = Field(default_factory=VenueListingEvidence)


class VenueReconciliationPayload(_FrozenModel):
    """Everything the venue-wide comparison observed, with scope made explicit."""

    observed_at: datetime
    note: str = VENUE_RECONCILIATION_NOTE
    managed_books: int | None = Field(default=None, ge=0)
    managed_listing: ManagedListingEvidence = Field(default_factory=ManagedListingEvidence)
    balances_listing: VenueListingEvidence = Field(default_factory=VenueListingEvidence)
    orders_listing: VenueListingEvidence = Field(default_factory=VenueListingEvidence)
    assets: tuple[AssetReconciliationRow, ...] = ()
    quote_currencies: tuple[QuoteReconciliationRow, ...] = ()
    orders: OrderReconciliationSection = Field(default_factory=OrderReconciliationSection)
    findings: tuple[VenueFinding, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Keep the observation instant timezone-aware UTC."""
        return _require_utc(value)


class VenueReconciliationReport(OperatorEnvelope):
    """Read-only venue-wide reconciliation; never places, cancels, or edits orders."""

    report_kind: Literal["venue_reconciliation"] = "venue_reconciliation"
    payload: VenueReconciliationPayload


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


async def build_venue_reconciliation_report(
    *,
    portfolio: PortfolioService,
    execution: ExecutionStore | None,
) -> VenueReconciliationReport:
    """Compare managed live books against fresh venue balances and open orders."""
    now = datetime.now(UTC)
    findings: list[VenueFinding] = []
    warnings: list[str] = []
    try:
        managed = await _managed_snapshots(execution, findings, warnings)
    except _VenueUnavailableError as error:
        return _failed_report(now, error.component)
    inventory = _collect_inventory(managed.snapshots)
    managed_complete = managed.evidence.status == "complete"
    balances, balance_rows = await _read_balances(portfolio, findings)
    venue_orders, orders_evidence = await _read_open_orders(portfolio, findings)
    listing_complete = balances.evidence.status == "complete"
    assets = _asset_rows(inventory, balance_rows, listing_complete, findings, managed_complete)
    quotes = _quote_rows(inventory, balance_rows, listing_complete, managed_complete)
    orders_section = _order_section(
        inventory, venue_orders, orders_evidence, findings, managed_complete
    )
    demo = balances.demo or orders_evidence.demo
    if demo:
        findings.append(
            VenueFinding(
                reason_code="DEMO_VENUE",
                severity=VenueSeverity.INFO,
                detail=(
                    "Venue listings are demo data, not account evidence; comparisons are "
                    "shown but prove nothing about the real venue."
                ),
            )
        )
    components = _components(findings, balances.evidence, orders_evidence, demo)
    return VenueReconciliationReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=PORTFOLIO_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=VenueReconciliationPayload(
            observed_at=now,
            managed_books=len(managed.snapshots) if managed_complete else None,
            managed_listing=managed.evidence,
            balances_listing=balances.evidence,
            orders_listing=orders_evidence,
            assets=assets,
            quote_currencies=quotes,
            orders=orders_section,
            findings=tuple(findings),
        ),
    )


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


def _failed_report(now: datetime, component: ComponentReport) -> VenueReconciliationReport:
    """Assemble a failed envelope without guessing venue state."""
    return VenueReconciliationReport(
        application_version=__version__,
        generated_at=now,
        overall_status=ReportStatus.FAILED,
        components=(component,),
        redaction=PORTFOLIO_REDACTION,
        recommended_next_action=recommend_next_action((component,)),
        payload=VenueReconciliationPayload(observed_at=now),
    )


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


class _BalancesRead:
    """Outcome of the venue balance listing."""

    def __init__(
        self,
        evidence: VenueListingEvidence,
        rows: dict[str, tuple[Decimal, Decimal, Decimal, int]] | None,
        demo: bool,
    ) -> None:
        """Store listing evidence plus summed rows per currency (or None when unknown)."""
        self.evidence = evidence
        self.rows = rows
        self.demo = demo


async def _read_balances(
    portfolio: PortfolioService, findings: list[VenueFinding]
) -> tuple[_BalancesRead, dict[str, tuple[Decimal, Decimal, Decimal, int]]]:
    """Read the whole venue balance listing, failing closed on any incompleteness."""
    try:
        observed = await portfolio.get_portfolio()
    except ExchangeReadError as error:
        evidence = VenueListingEvidence(
            status="unavailable", failure=error.failure, observed_at=None
        )
        findings.append(
            VenueFinding(
                reason_code="VENUE_BALANCES_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue balance listing is incomplete "
                    f"({error.failure.summary()}); inventory comparisons are unknown, "
                    "never guessed."
                ),
            )
        )
        return _BalancesRead(evidence, None, False), {}
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        evidence = VenueListingEvidence(status="unavailable", observed_at=None)
        findings.append(
            VenueFinding(
                reason_code="VENUE_BALANCES_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue balance listing is incomplete; inventory comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
        return _BalancesRead(evidence, None, False), {}
    rows: dict[str, tuple[Decimal, Decimal, Decimal, int]] = {}
    for asset in observed.assets:
        available, hold, total, count = rows.get(asset.currency, (_ZERO, _ZERO, _ZERO, 0))
        rows[asset.currency] = (
            available + asset.available,
            hold + asset.hold,
            total + asset.total,
            count + 1,
        )
    evidence = VenueListingEvidence(
        status="complete",
        scope="venue_balances",
        demo=observed.demo,
        observed_at=observed.as_of,
        rows=len(observed.assets),
    )
    return _BalancesRead(evidence, rows, observed.demo), rows


async def _read_open_orders(
    portfolio: PortfolioService, findings: list[VenueFinding]
) -> tuple[tuple[ForeignOpenOrderRow, ...] | None, VenueListingEvidence]:
    """Read all nonterminal spot orders; None means unknown, never an empty listing."""
    try:
        orders = await portfolio.list_open_orders()
    except ExchangeReadError as error:
        evidence = VenueListingEvidence(
            status="unavailable", failure=error.failure, observed_at=None
        )
        findings.append(
            VenueFinding(
                reason_code="VENUE_ORDERS_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue open-order listing is incomplete "
                    f"({error.failure.summary()}); order comparisons are unknown, never "
                    "guessed."
                ),
            )
        )
        return None, evidence
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        evidence = VenueListingEvidence(status="unavailable", observed_at=None)
        findings.append(
            VenueFinding(
                reason_code="VENUE_ORDERS_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue open-order listing is incomplete; order comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
        return None, evidence
    evidence = VenueListingEvidence(
        status="complete",
        scope="spot_order_history_nonterminal",
        demo=portfolio.demo,
        observed_at=datetime.now(UTC),
        rows=len(orders),
    )
    return (
        tuple(
            ForeignOpenOrderRow(
                venue_order_id=order.venue_order_id,
                client_order_id=order.client_order_id,
                product_id=order.product_id,
                side=order.side,
                status=order.status,
            )
            for order in orders
        ),
        evidence,
    )


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


def _components(
    findings: Sequence[VenueFinding],
    balances: VenueListingEvidence,
    orders: VenueListingEvidence,
    demo: bool,
) -> list[ComponentReport]:
    """Grade the report: warnings and unknown listings degrade; foreign holdings do not."""
    severities = {finding.severity for finding in findings}
    if VenueSeverity.WARNING in severities or VenueSeverity.UNKNOWN in severities:
        main = ComponentReport(
            name="venue_reconciliation",
            status=ReportStatus.DEGRADED,
            reason_code="VENUE_RECONCILIATION_FINDINGS",
            detail=(
                "Managed books disagree with the venue listing, or a listing is "
                "incomplete (comparisons unknown). Inspect before new risk."
            ),
        )
    else:
        main = ComponentReport(
            name="venue_reconciliation",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=(
                "Managed inventory and working orders agree with the venue listing; "
                "external holdings, if any, are disclosed as information."
            ),
        )
    components = [main]
    if balances.status == "unavailable":
        components.append(
            ComponentReport(
                name="venue_balances",
                status=ReportStatus.DEGRADED,
                reason_code="VENUE_BALANCES_LISTING_INCOMPLETE",
                detail=(
                    "The venue balance listing is incomplete; inventory comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
    else:
        components.append(
            ComponentReport(
                name="venue_balances",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE" if demo else "OK",
                detail=(
                    "Demo balance listing; not account evidence."
                    if demo
                    else f"{balances.rows} balance row(s) listed completely."
                ),
            )
        )
    if orders.status == "unavailable":
        components.append(
            ComponentReport(
                name="venue_orders",
                status=ReportStatus.DEGRADED,
                reason_code="VENUE_ORDERS_LISTING_INCOMPLETE",
                detail=(
                    "The venue open-order listing is incomplete; order comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
    else:
        components.append(
            ComponentReport(
                name="venue_orders",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE" if demo else "OK",
                detail=(
                    "Demo order listing; not account evidence."
                    if demo
                    else f"{orders.rows} nonterminal spot order(s) observed after full pagination."
                ),
            )
        )
    return components
