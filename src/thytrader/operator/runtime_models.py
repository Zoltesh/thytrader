"""Operator report models for strategies, deployments, and their runtime evidence.

Covers the strategy and deployment inventory, performance slices, risk and
reconciliation findings, the runtime and monitor views, and the study, why-trade,
and per-bar decision journals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from thytrader.backtest.cost_attribution import BacktestCostAttribution
from thytrader.backtest.models import BacktestEvaluationWindow, BacktestPerformanceMetrics
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.market_data.products import SpotQuoteCurrency
from thytrader.memory.models import MonitorSnapshot
from thytrader.memory.trade_reasons import TradeReasonRecord
from thytrader.operator.models import OperatorEnvelope, SupportedTimeframe, _FrozenModel
from thytrader.research.catalog import StudyCatalogSummary
from thytrader.trading.protection import ProtectionEvidenceResponse


class StrategySummary(_FrozenModel):
    """One mutable strategy without its document body (ADR 0082)."""

    strategy_id: UUID
    name: str
    revision: int
    valid: bool
    current_fingerprint: str | None
    product_id: str | None
    timeframe: SupportedTimeframe | None
    updated_at: datetime


class DeploymentBookSummary(_FrozenModel):
    """One product book without quantities or order payloads.

    ``position_state`` / ``exit_in_flight`` (ADR 0097) say whether an open book merely
    rests its protection (``open_protected``) or is exiting; ``phase`` stays raw.
    """

    product_id: str
    phase: str
    side: str | None = None
    protection_status: str
    protection: ProtectionEvidenceResponse = Field(
        description=(
            "Quantitative stop cover (ADR 0112). Includes coverage quantities. Prices, "
            "cash, and order payloads stay omitted. Paper is worker-dependent, not a "
            "venue-resting stop. Null times mean unknown."
        ),
    )
    position_state: str = "flat"
    exit_in_flight: bool = False


class DeploymentSummary(_FrozenModel):
    """One paper or live runtime without cash, quantities, or order payloads.

    ``position_state`` / ``exit_in_flight`` collapse every book (ADR 0097); they are null
    only when the report could not read the deployment's books.
    """

    deployment_id: UUID
    kind: str
    strategy_id: UUID | None
    strategy_fingerprint: str | None
    strategy_name: str | None = None
    strategy_deleted: bool = False
    timeframe: SupportedTimeframe | None = None
    mode: str
    status: str
    phase: str
    position_state: str | None = None
    exit_in_flight: bool | None = None
    product_id: str
    last_evaluated_bar: datetime | None
    mismatch_present: bool
    last_signal: str | None
    books: tuple[DeploymentBookSummary, ...] = ()
    lifecycle_command: str = "none"
    daily_loss_latched: bool = False
    drawdown_latched: bool = False
    revision: int = 0
    worker_lease_held: bool = False
    ledger_mark_complete: bool | None = None
    open_book_count: int | None = None


class PerformanceBookPayload(_FrozenModel):
    """One product book within a deployment performance slice."""

    product_id: str
    trade_count: int
    total_net_pnl: str | None = None
    mark_complete: bool


class UserOrderFeedPayload(_FrozenModel):
    """Redacted user-order WebSocket lifecycle without JWT or order payloads."""

    state: Literal["disconnected", "connecting", "connected", "stale", "reconnecting", "disabled"]
    last_message_at: datetime | None = None
    last_heartbeat_at: datetime | None = None

    @field_validator("last_message_at", "last_heartbeat_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep feed timestamps timezone-aware UTC after JSON round-trips."""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be timezone-aware UTC")
        return value.astimezone(UTC)


class StrategiesPayload(_FrozenModel):
    """Mutable strategies and runtime books visible to operators."""

    strategies: tuple[StrategySummary, ...]
    deployments: tuple[DeploymentSummary, ...]


class StrategiesReport(OperatorEnvelope):
    """Strategy catalog and runtime status without trading authority."""

    report_kind: Literal["strategies"] = "strategies"
    payload: StrategiesPayload


class PerformancePayload(_FrozenModel):
    """One backtest, paper, or live performance slice with explicit provenance.

    ``window`` (backtests only, ADR 0094) names the evaluated bars: evaluation_start,
    evaluation_end, warmup_bars, and the first and last evaluated bar.
    """

    mode: Literal["backtest", "paper", "live"]
    timeframe: SupportedTimeframe
    currency: SpotQuoteCurrency | None = None
    strategy_fingerprint: str | None
    dataset_fingerprint: str | None
    fee_treatment: str
    result_fingerprint: str | None
    deployment_id: UUID | None
    trade_count: int | None
    total_net_pnl: str | None
    total_return_fraction: str | None
    maximum_drawdown_fraction: str | None
    total_spread_cost: str | None
    evaluation_bars: int | None
    mark_complete: bool | None = None
    marked_exposure: str | None = None
    books: tuple[PerformanceBookPayload, ...] = ()
    metrics: BacktestPerformanceMetrics | None = None
    cost_attribution: BacktestCostAttribution | None = None
    window: BacktestEvaluationWindow | None = None


class PerformanceReport(OperatorEnvelope):
    """Performance evidence that distinguishes backtest, paper, and live."""

    report_kind: Literal["performance"] = "performance"
    payload: PerformancePayload


class RiskFinding(_FrozenModel):
    """One operational risk observation with a stable code."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    deployment_id: UUID | None
    detail: str = Field(max_length=500)


class RiskPayload(_FrozenModel):
    """Observed runtime risk plus the effective registry identity."""

    risk_policy_registry: Literal["available"]
    policy_source: Literal["compiled_default", "published"]
    policy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    max_concurrent_running_deployments: int = Field(ge=1, le=32)
    max_concurrent_open_positions: int = Field(ge=1, le=32)
    product_allowlist: tuple[str, ...] = ()
    paper_running_deployments: int = Field(ge=0)
    live_running_deployments: int = Field(ge=0)
    paper_open_positions: int = Field(ge=0)
    live_open_positions: int = Field(ge=0)
    daily_loss_limit_fraction: str
    max_strategy_drawdown_fraction: str
    max_entry_orders_per_minute: int = Field(ge=1, le=1000)
    max_cancellations_per_minute: int = Field(ge=1, le=1000)
    reference_price_collar_fraction: str
    allow_intra_strategy_pyramiding: bool
    max_daily_loss_quote: str | None = None
    max_portfolio_exposure_quote: str | None = None
    max_venue_order_actions_per_minute: int | None = None
    max_order_quantity: str | None = None
    max_order_notional_quote: str | None = None
    min_available_quote_reserve: str | None = None
    findings: tuple[RiskFinding, ...]


class RiskReport(OperatorEnvelope):
    """Pause, mismatch, and other fail-closed runtime observations."""

    report_kind: Literal["risk"] = "risk"
    payload: RiskPayload


class AuditFailureEvidence(_FrozenModel):
    """One historical failure and an explicitly matched later recovery, when observed."""

    event_id: UUID
    occurred_at: datetime
    action: str
    provider: str | None
    product_id: str | None
    recovery_status: Literal["recovered", "unresolved", "unknown"]
    recovery_event_id: UUID | None = None
    recovered_at: datetime | None = None


class ReconciliationFinding(_FrozenModel):
    """One order or runtime reconciliation anomaly."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    deployment_id: UUID | None
    detail: str = Field(max_length=500)
    audit_event: AuditFailureEvidence | None = None


class ReconciliationPayload(_FrozenModel):
    """Local runtime mismatches that require operator attention."""

    findings: tuple[ReconciliationFinding, ...]


class ReconciliationReport(OperatorEnvelope):
    """Rejected, unknown, or unreconciled runtime conditions."""

    report_kind: Literal["reconciliation"] = "reconciliation"
    payload: ReconciliationPayload


class RuntimePayload(_FrozenModel):
    """Paper/live status plus risk and reconciliation findings, without cash."""

    deployments: tuple[DeploymentSummary, ...]
    risk_findings: tuple[RiskFinding, ...]
    reconciliation_findings: tuple[ReconciliationFinding, ...]
    user_order_feed: UserOrderFeedPayload | None = None


class RuntimeReport(OperatorEnvelope):
    """Read-only combined view of paper and live runtimes."""

    report_kind: Literal["runtime"] = "runtime"
    payload: RuntimePayload


class MonitorReport(OperatorEnvelope):
    """Read-only composite of deployments, journals, why-trade records, and notify."""

    report_kind: Literal["monitor"] = "monitor"
    payload: MonitorSnapshot


class StudiesPayload(_FrozenModel):
    """Persisted composed research-study catalog rows without child equity."""

    study_catalog: Literal["available", "unavailable"]
    studies: tuple[StudyCatalogSummary, ...] = ()


class StudiesReport(OperatorEnvelope):
    """Operator-visible research-study catalog without trading authority."""

    report_kind: Literal["studies"] = "studies"
    payload: StudiesPayload


class TradeReasonsPayload(_FrozenModel):
    """Same why-trade records as the memory HTTP contract, wrapped for operators."""

    storage: Literal["available", "unavailable"]
    trade_reasons: tuple[TradeReasonRecord, ...]


class TradeReasonsReport(OperatorEnvelope):
    """Read-only why-trade journals for human and agent review."""

    report_kind: Literal["trade_reasons"] = "trade_reasons"
    payload: TradeReasonsPayload


class DecisionsPayload(_FrozenModel):
    """Same per-bar decision records as the deployment/strategy decision HTTP pages."""

    storage: Literal["available", "unavailable"]
    deployment_id: UUID | None = None
    strategy_id: UUID | None = None
    outcomes: tuple[DecisionOutcome, ...] = ()
    decisions: tuple[BarDecision, ...]
    next_cursor: str | None = None
    retention_max_rows_per_deployment: int = Field(ge=1)
    retention_max_age_days: int = Field(ge=1)


class DecisionsReport(OperatorEnvelope):
    """Read-only per-bar decision timeline: what each bot decided on every bar, and why."""

    report_kind: Literal["decisions"] = "decisions"
    payload: DecisionsPayload
