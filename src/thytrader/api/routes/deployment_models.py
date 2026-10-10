"""Request and response models for the paper and live deployment HTTP contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from thytrader.fleet_control.models import SUMMARY_LEDGER_OMISSION
from thytrader.trading.day_open import DailyOpeningEvidence
from thytrader.trading.models import DeploymentMode
from thytrader.trading.protection_models import ProtectionEvidenceResponse
from thytrader.trading.twins import DeploymentTwinLink


class CreateDeploymentRequest(BaseModel):
    """Start one paper or live runtime from a strategy's current (valid) rules.

    The server snapshots the definition; the response's ``strategy_fingerprint``
    names the exact rules the bot runs, even after later edits.
    """

    strategy_id: UUID
    mode: DeploymentMode
    paper_starting_cash: str | None = None
    maker_fee_rate: str | None = None
    taker_fee_rate: str | None = None
    paper_fee_per_contract: str | None = Field(
        default=None,
        description=(
            "Paper futures only (ADR 0129 §4): the fixed USD fee per contract the book pays "
            "on every fill on top of the maker/taker rate (the fees report's "
            "fee_per_contract, not the preview's all-in commission; ADR 0133). Required, "
            "with explicit maker_fee_rate and taker_fee_rate, for a futures strategy; "
            "refused for spot."
        ),
    )
    adopt_holdings: str | None = Field(
        default=None,
        pattern=r"^(all|\d+(\.\d+)?)$",
        description=(
            "Live only (ADR 0124): start the bot already holding this base quantity, or "
            "'all', of the coins the Coinbase account holds unmanaged. The book and the "
            "adoption commit together; single-instrument long strategies only."
        ),
    )
    i_understand_live: StrictBool = Field(
        default=False,
        description=(
            "Required true for mode=live (HTTP 428 live_acknowledgement_required otherwise). "
            "Send only after the operator explicitly acknowledged live trading."
        ),
    )


class ResumeDeploymentRequest(BaseModel):
    """Optional resume body; live books require the explicit live acknowledgement."""

    i_understand_live: StrictBool = Field(
        default=False,
        description="Required true to resume a live deployment (re-arms live order submission).",
    )


class PositionResponse(BaseModel):
    """One long or short product book, including protection status."""

    product_id: str
    quantity: str
    entry_price: str
    stop_price: str
    target_price: str | None = Field(
        default=None, description="Take-profit price; null when the strategy declares none."
    )
    entered_bar: str
    side: str = "long"
    trail_extreme: str | None = None
    add_count: int = 1
    signal_exit_bar: str | None = Field(
        default=None,
        description=(
            "UTC start of the closed bar whose exits.signal_exit rule matched; the book is "
            "exiting (ADR 0093). Null when no signal exit is pending."
        ),
    )
    protection_status: str = Field(
        description=(
            "flat, covered, unprotected, or unknown. Live covered requires a confirmed "
            "open stop of sufficient remaining quantity and valid geometry. A take-profit "
            "alone is not covered. Pending and unknown are not covered (ADR 0112)."
        ),
    )
    protection: ProtectionEvidenceResponse = Field(
        description=(
            "Quantitative stop cover: required, covered, and uncovered quantity, stop "
            "side and geometry, synthetic versus venue, and observed or verified time. "
            "Null times mean unknown. Paper cover is worker-dependent, not venue-resting."
        ),
    )
    position_state: str = Field(
        default="open_protected",
        description=(
            "Operator reading of this book (ADR 0097): open_protected (matching venue "
            "stop, or the paper synthetic stop), open_unprotected, open_unverified, or "
            "exiting. Prefer it over the raw phase, which reads pending_exit while "
            "protection merely rests. A take-profit alone is not open_protected."
        ),
    )
    exit_in_flight: bool = Field(
        default=False,
        description=(
            "True only when this book's exit is being sent: a working marketable exit, a "
            "matched signal exit, or a flatten. A resting TP/SL bracket is not an exit."
        ),
    )
    mark_price: str | None = Field(
        default=None,
        description=(
            "Close of the newest bar the bot evaluated for this product (ADR 0098); null "
            "when no journaled close exists or on reads that do not mark books."
        ),
    )
    marked_at: str | None = Field(
        default=None, description="UTC close time of the bar behind mark_price."
    )
    unrealized_pnl: str | None = Field(
        default=None,
        description=(
            "Gross unrealized PnL at mark_price in quote currency (signed quantity times "
            "the move from entry_price), before exit fees; null without a mark."
        ),
    )
    compatibility_focus: bool = False
    entry_fees: str | None = Field(
        default=None,
        description="Paid entry fees allocated to held quantity; null without verified evidence.",
    )
    unrealized_pnl_net: str | None = Field(
        default=None,
        description=(
            "Gross unrealized_pnl minus entry_fees; future exit fees excluded. Null without "
            "a mark and verified current-position fill evidence."
        ),
    )


class InstrumentRuntimeResponse(BaseModel):
    """Per-product overlay of the single-book runtime machine."""

    product_id: str
    phase: str
    last_evaluated_bar: str | None
    last_signal: str | None
    pending_entry_bars: int
    bars_held: int
    cooldown_bars_remaining: int
    pending_stop_price: str | None = None
    pending_target_price: str | None = None


class DeploymentBookTotalsResponse(BaseModel):
    """Collection counts that must match `positions`, working orders, and fills."""

    open_books: int = 0
    working_orders: int = 0
    fill_count: int = 0


class DeploymentCapitalResponse(BaseModel):
    """Capital accounting separate from ledger ``cash``.

    Ledger balances stay in fill-accounting units. Performance capital is a pinned
    percentage-metric budget; current sizing allocations and account risk are separate.
    Unknown venue quote is ``null`` so callers disable entries.
    """

    allocated_capital: str | None = None
    venue_available_quote: str | None = None
    reserved_buying_power: str | None = None
    inventory_cost: str | None = None
    performance_equity: str | None = None
    performance_capital_quote: str | None = None
    performance_maximum_drawdown_fraction: str | None = None
    initial_equity: str | None = None
    baseline_equity: str | None = None
    high_water_mark_equity: str | None = None
    utc_day_open_equity: str | None = Field(
        default=None, description="Preserved legacy observation, not verified midnight evidence."
    )
    risk_day_open_evidence: DailyOpeningEvidence | None = None


class OrderResponse(BaseModel):
    """One persisted venue-visible order, tagged with its Coinbase product."""

    id: UUID
    client_order_id: str
    venue_order_id: str | None
    product_id: str
    side: str
    kind: str
    quantity: str
    price: str | None
    stop_trigger_price: str | None = None
    take_profit_price: str | None = None
    filled_quantity: str
    status: str
    reject_reason: str | None
    created_at: str
    updated_at: str
    attached_child_venue_order_id: str | None = None
    parent_order_id: UUID | None = None
    pyramid_add: bool = False


class FillResponse(BaseModel):
    """One persisted fill, tagged with the parent order's product."""

    id: UUID
    order_id: UUID
    product_id: str
    venue_fill_id: str
    price: str
    quantity: str
    fee: str
    filled_at: str


class DeploymentLedgerSummaryResponse(BaseModel):
    """Aggregate fill-ledger statistics without loading every historical fill."""

    trade_count: int
    total_net_pnl: str | None = None
    total_return_fraction: str | None = None
    mark_complete: bool
    marked_exposure: str | None = None


class DeploymentResponse(BaseModel):
    """One deployment plus every product book, runtime overlay, and related evidence."""

    id: UUID
    strategy_fingerprint: str | None
    strategy_id: UUID | None
    strategy_name: str | None = Field(
        default=None, description="Strategy name captured at start; kept after deletion."
    )
    strategy_deleted: bool = Field(
        default=False,
        description="True for a kept (stopped live) book whose strategy was deleted.",
    )
    portfolio_id: UUID | None = Field(
        default=None,
        description="The portfolio this bot is a sleeve of (ADR 0091); null for a standalone bot.",
    )
    kind: str
    timeframe: str | None
    product_id: str
    mode: str
    status: str
    phase: str = Field(
        description=(
            "Raw worker state machine (flat, pending_entry, open, pending_exit). pending_exit "
            "includes an open book whose TP/SL protection merely rests; read position_state."
        )
    )
    position_state: str = Field(
        default="flat",
        description=(
            "Operator reading across books (ADR 0097): flat, entering, open_protected, "
            "open_unprotected, open_unverified, or exiting (the worst book wins)."
        ),
    )
    exit_in_flight: bool = Field(
        default=False, description="True when any book's exit is being sent (ADR 0097)."
    )
    cash: str
    paper_starting_cash: str | None
    maker_fee_rate: str | None = None
    taker_fee_rate: str | None = None
    last_evaluated_bar: str | None
    last_signal: str | None
    mismatch_detail: str | None
    pending_entry_bars: int
    bars_held: int
    lifecycle_command: str
    daily_loss_latched: bool
    drawdown_latched: bool
    revision: int
    worker_lease_held: bool
    created_at: str
    updated_at: str
    position: PositionResponse | None = Field(
        default=None,
        description=(
            "Compatibility-only focused book: the primary product when that book is "
            "open, otherwise the sole open book. Always includes product_id. Read "
            "`positions` for the full inventory."
        ),
    )
    positions: tuple[PositionResponse, ...] = ()
    instrument_runtimes: tuple[InstrumentRuntimeResponse, ...] = ()
    book_totals: DeploymentBookTotalsResponse = Field(default_factory=DeploymentBookTotalsResponse)
    capital: DeploymentCapitalResponse = Field(default_factory=DeploymentCapitalResponse)
    ledger: DeploymentLedgerSummaryResponse | None = None
    orders: tuple[OrderResponse, ...] = ()
    fills: tuple[FillResponse, ...] = ()
    detail: Literal["summary", "full"] = "summary"
    historical_orders_included: bool = False
    historical_fills_included: bool = False
    ledger_omission: str | None = SUMMARY_LEDGER_OMISSION


class DeploymentListResponse(BaseModel):
    """Stable created-at inventory page without historical orders or fills.

    ``has_more`` is exact for this snapshot. Pass the returned ``as_of`` on the
    next offset page so a deployment created during the walk cannot shift rows.
    """

    deployments: tuple[DeploymentResponse, ...]
    limit: int
    offset: int
    returned: int
    has_more: bool
    total: int
    order: str
    as_of: str
    fingerprint: str
    next_cursor: str | None


class FillListResponse(BaseModel):
    """One cursor page of fills for one deployment."""

    fills: tuple[FillResponse, ...]
    limit: int
    returned: int
    next_cursor: str | None = None


class OrderListResponse(BaseModel):
    """One cursor page of orders for one deployment."""

    orders: tuple[OrderResponse, ...]
    limit: int
    returned: int
    next_cursor: str | None = None


class LinkTwinRequest(BaseModel):
    """Name the intended counterpart; trading instructions are rejected."""

    model_config = ConfigDict(extra="forbid")
    counterpart_deployment_id: UUID


class DeploymentTwinResponse(BaseModel):
    """Expose the current saved pair or an explicit unlinked state."""

    deployment_id: UUID
    twin: DeploymentTwinLink | None
