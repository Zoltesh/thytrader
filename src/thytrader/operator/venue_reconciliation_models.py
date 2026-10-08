"""Report models for the operator ``venue-reconciliation`` report (ADR 0114).

Defines the finding severities, the listing and managed-read evidence, the per-asset,
per-quote, and order comparison rows, and the report envelope. Every listing carries
explicit completeness so unknown comparisons are never presented as zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.exchanges.read_errors import ExchangeReadFailure
from thytrader.market_data.products import (
    SpotQuoteCurrency,
)
from thytrader.operator.models import (
    OperatorEnvelope,
)

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
