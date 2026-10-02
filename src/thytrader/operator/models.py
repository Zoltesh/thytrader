"""Versioned operator diagnostic report contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.backtest.models import (  # noqa: TC001 - Pydantic field types.
    BacktestEvaluationWindow,
    BacktestPerformanceMetrics,
)
from thytrader.execution.decisions import (  # noqa: TC001 - Pydantic field types.
    BarDecision,
    DecisionOutcome,
)
from thytrader.market_data.models import DATASET_TIMEFRAMES, DatasetTimeframe
from thytrader.market_data.products import SpotQuoteCurrency  # noqa: TC001 - Pydantic field type.
from thytrader.memory.models import MonitorSnapshot  # noqa: TC001 - Pydantic field type.
from thytrader.memory.trade_reasons import TradeReasonRecord  # noqa: TC001 - Pydantic field type.
from thytrader.ops_contract import expected_ops_contract
from thytrader.portfolios.models import (  # noqa: TC001 - Pydantic field types.
    ManagerSettings,
    PortfolioLimits,
)
from thytrader.research.catalog import StudyCatalogSummary  # noqa: TC001 - Pydantic field type.
from thytrader.strategies.indicator_catalog import ParameterKind  # noqa: TC001 - Pydantic field.

SCHEMA_VERSION: Literal["thytrader-operator-report-v1"] = "thytrader-operator-report-v1"
OPERATOR_API_PREFIX = "/api/v1/operator"
REPORT_KINDS: tuple[str, ...] = (
    "health",
    "configuration",
    "exchange",
    "market_data",
    "data_catalog",
    "products",
    "indicators",
    "strategies",
    "performance",
    "risk",
    "reconciliation",
    "runtime",
    "monitor",
    "studies",
    "trade_reasons",
    "decisions",
    "support_bundle",
    "portfolio",
    "fees",
    "portfolios",
)

SupportedTimeframe = DatasetTimeframe


class ReportStatus(StrEnum):
    """Overall and per-component diagnostic outcome."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ComponentReport(_FrozenModel):
    """One named subsystem with a stable reason code."""

    name: str = Field(min_length=1, max_length=64)
    status: ReportStatus
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    detail: str = Field(default="", max_length=500)


class RedactionMetadata(_FrozenModel):
    """Record what this report deliberately omitted."""

    secrets_redacted: bool
    raw_environment_omitted: bool
    account_identifiers_omitted: bool
    balances_omitted: bool


class OperatorEnvelope(_FrozenModel):
    """Fields required on every machine-readable operator report."""

    schema_version: Literal["thytrader-operator-report-v1"] = SCHEMA_VERSION
    application_version: str = Field(min_length=1, max_length=32)
    generated_at: datetime
    timezone: Literal["UTC"] = "UTC"
    overall_status: ReportStatus
    components: tuple[ComponentReport, ...]
    redaction: RedactionMetadata
    partial_result_warnings: tuple[str, ...] = ()
    recommended_next_action: str = Field(min_length=1, max_length=500)

    @field_validator("generated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Keep operator timestamps timezone-aware UTC after JSON round-trips."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("generated_at must be timezone-aware UTC")
        return value.astimezone(UTC)


class OpsContractPayload(_FrozenModel):
    """Content identity for CLI versus running API comparison."""

    id: str = Field(min_length=1, max_length=64)
    max_historical_interval_count: int = Field(ge=1)
    backtest_engine: str = Field(min_length=1, max_length=64)
    paper_timeframes: tuple[SupportedTimeframe, ...]
    live_timeframes: tuple[SupportedTimeframe, ...]
    htf_filter_runtimes: tuple[Literal["research", "paper", "live"], ...]
    indicator_timeframe_runtimes: tuple[Literal["research", "paper", "live"], ...]
    indicator_offset_runtimes: tuple[Literal["research", "paper", "live"], ...]
    signal_exit_runtimes: tuple[Literal["research", "paper", "live"], ...]
    reference_instrument_runtimes: tuple[Literal["research", "paper", "live"], ...]
    max_reference_instruments: int = Field(ge=1)
    indicator_kinds: tuple[str, ...]
    position_sides: tuple[Literal["long", "short"], ...]
    attached_entry_brackets: tuple[Literal["paper", "live"], ...]
    paper_deploy_fee_fields: tuple[Literal["maker_fee_rate", "taker_fee_rate"], ...]
    experiential_model_engines: tuple[str, ...]
    risk_breakers: tuple[Literal["daily_loss", "drawdown"], ...]
    order_rate_limits: tuple[Literal["entry", "cancel"], ...]
    reference_price_collars: tuple[Literal["paper", "live"], ...]
    trade_reason_journals: tuple[Literal["paper", "live"], ...]
    decision_journals: tuple[Literal["paper", "live"], ...]
    multi_instrument_documents: tuple[Literal["research", "paper", "live"], ...]
    intra_strategy_pyramiding: tuple[Literal["research", "paper", "live"], ...]
    lifecycle_commands: tuple[
        Literal["none", "stop_new_entries", "flatten", "managed_shutdown"], ...
    ]
    deployment_capital_fields: tuple[str, ...]
    breaker_latch_reset: tuple[Literal["paper", "live"], ...]
    take_profit_kinds: tuple[Literal["reward_risk", "none"], ...]
    live_protection_kinds: tuple[Literal["trigger_bracket", "stop_limit"], ...]
    backtest_diagnostics: tuple[str, ...]
    fee_suggestion_source: Literal["coinbase_account"]
    async_backtest_job_statuses: tuple[
        Literal["queued", "running", "completed", "failed", "cancelled", "expired"],
        ...,
    ]
    research_job_statuses: tuple[
        Literal["queued", "running", "completed", "failed", "cancelled", "expired"],
        ...,
    ]
    research_job_expiry_hours: int = Field(ge=1)
    research_worker_pool: tuple[
        Literal[
            "lease_claim",
            "crash_requeue",
            "process_recycle",
            "sync_long_poll",
            "job_error_codes",
            "health_queue_depth",
        ],
        ...,
    ]
    spot_quote_currencies: tuple[Literal["USD", "USDC", "USDT"], ...]
    catalog_health: tuple[str, ...]
    bounded_deployment_reads: tuple[Literal["list", "summary", "fills", "orders"], ...]
    deployment_ledger_pagination: tuple[Literal["cursor"], ...]
    multi_book_ledger: tuple[Literal["paper", "live"], ...]
    strategy_model: tuple[Literal["mutable_root", "auto_snapshot", "hard_delete"], ...]
    portfolio_model: tuple[
        Literal[
            "sleeves",
            "shared_limits",
            "manager_settings",
            "journal",
            "portfolio_backtest",
            "deployment",
            "portfolio_limits",
            "manager_proposals",
        ],
        ...,
    ]
    portfolio_modes: tuple[Literal["paper", "live"], ...]
    portfolio_backtest_contract: str = Field(min_length=1, max_length=64)
    research_dataset_autobind: tuple[Literal["backtest", "study"], ...]
    study_budgets: dict[Literal["sync", "async"], dict[Literal["candidates", "windows"], int]]
    portfolio_deployment: tuple[
        Literal["start", "pause", "resume", "stop", "sleeve_actions", "breaker_reset"], ...
    ]
    portfolio_breakers: tuple[Literal["PORTFOLIO_DAILY_LOSS_STOP", "PORTFOLIO_DRAWDOWN_STOP"], ...]
    portfolio_proposal_kinds: tuple[
        Literal["rebalance", "pause_sleeve", "resume_sleeve", "add_sleeve"], ...
    ]
    portfolio_briefing_contract: str = Field(min_length=1, max_length=64)
    research_honesty: tuple[
        Literal[
            "result_window",
            "study_axis_values",
            "study_candidate_aggregates",
            "study_stitched_points",
            "document_issue_paths",
            "json_number_decimals",
        ],
        ...,
    ]
    strategy_library: tuple[
        Literal["tag_filter", "bulk_delete_by_tag", "clone_name", "origin_filter"], ...
    ]
    portfolio_max_sleeves: int = Field(ge=1)
    portfolio_sleeve_operations: tuple[Literal["batch_add"], ...]
    same_bar_exit_precedence: tuple[Literal["stop", "take_profit", "signal_exit", "time_exit"], ...]
    runtime_observability: tuple[
        Literal[
            "position_state",
            "exit_in_flight",
            "paper_live_fill_comparison",
            "paper_protection_covered",
            "book_marks",
            "portfolio_fill_comparisons",
        ],
        ...,
    ]
    expected_schema_revision: str = Field(min_length=1, max_length=32)


def current_ops_contract() -> OpsContractPayload:
    """Build the ops contract this checkout implements."""
    return OpsContractPayload.model_validate(expected_ops_contract())


class ResearchQueueReport(_FrozenModel):
    """Queued and running research jobs, and how long the oldest queued one has waited.

    ``queued`` jobs wait for a free research worker; ``running`` jobs hold a worker.
    """

    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    oldest_queued_at: datetime | None = None
    oldest_queued_age_seconds: float | None = Field(default=None, ge=0)


class ResearchWorkerReport(_FrozenModel):
    """One research worker process's latest self-report (ADR 0092)."""

    slot: int = Field(ge=0)
    pid: int = Field(ge=1)
    state: Literal["starting", "idle", "running", "stopping"]
    live: bool
    job_id: UUID | None = None
    job_kind: Literal["backtest", "study", "portfolio_backtest"] | None = None
    jobs_completed: int = Field(ge=0)
    rss_bytes: int | None = Field(default=None, ge=0)
    started_at: datetime
    heartbeat_at: datetime
    heartbeat_age_seconds: float = Field(ge=0)


class ResearchWorkersPayload(_FrozenModel):
    """Research worker pool liveness and queue depth (ADR 0092).

    ``configured_workers`` is the pool size the workers report (null until one has
    reported); ``live_workers`` heartbeated recently. ``queue`` sums both queues;
    ``research_jobs`` holds backtests and studies, ``portfolio_backtests`` the rest.
    """

    configured_workers: int | None = Field(default=None, ge=1)
    live_workers: int = Field(ge=0)
    queue: ResearchQueueReport
    research_jobs: ResearchQueueReport
    portfolio_backtests: ResearchQueueReport
    workers: tuple[ResearchWorkerReport, ...]


class HealthPayload(_FrozenModel):
    """Process coverage included in the health report."""

    api_probed: bool
    database_configured: bool
    coinbase_credentials_configured: bool
    ops_contract: OpsContractPayload | None = None
    applied_schema_revision: str | None = None
    research_workers: ResearchWorkersPayload | None = None


class HealthReport(OperatorEnvelope):
    """API, workers, database, and exchange connectivity summary."""

    report_kind: Literal["health"] = "health"
    payload: HealthPayload


class ConfigurationPayload(_FrozenModel):
    """Redacted runtime settings safe for agents."""

    environment: str
    api_host: str
    api_port: int
    containerized: bool
    allow_remote_access: bool
    log_level: str
    snapshot_interval_seconds: int
    market_data_worker_interval_seconds: int
    market_data_worker_lookback_hours: int
    market_data_worker_product_id: str
    market_data_dataset_root: str
    execution_worker_interval_seconds: int
    database_configured: bool
    coinbase_credentials_configured: bool
    yolo_enabled: bool = False
    yolo_tiers: tuple[str, ...] = ()
    notify_provider: str = "none"
    notify_webhook_configured: bool = False
    settings_file: str = "thytrader.yaml"
    yaml_loaded: bool = False
    yaml_source_of_truth: Literal[True] = True
    effective_api_base_url: str | None = None


class ConfigurationReport(OperatorEnvelope):
    """Validated configuration without secrets or raw environment values."""

    report_kind: Literal["configuration"] = "configuration"
    payload: ConfigurationPayload


class ExchangePayload(_FrozenModel):
    """Detected exchange connection facts without balances or account ids."""

    provider: str
    connection_status: str
    demo: bool
    permissions: tuple[str, ...]
    live_credentials_configured: bool


class ExchangeReport(OperatorEnvelope):
    """Coinbase connectivity and detected key permissions."""

    report_kind: Literal["exchange"] = "exchange"
    payload: ExchangePayload


class OperatorMoneyPayload(_FrozenModel):
    """Exact money serialized as a decimal string."""

    amount: str
    currency: Literal["USD", "USDC", "USDT"]


class OperatorPortfolioAssetPayload(_FrozenModel):
    """One balance and optional quote valuation without account identifiers."""

    currency: str
    name: str
    available: str
    hold: str
    total: str
    value: OperatorMoneyPayload | None


class PortfolioPayload(_FrozenModel):
    """Point-in-time portfolio matching GET /api/v1/portfolio without secrets."""

    as_of: datetime
    demo: bool
    connection_status: str
    permissions: tuple[str, ...]
    total_value: OperatorMoneyPayload
    assets: tuple[OperatorPortfolioAssetPayload, ...]
    unvalued_assets: tuple[str, ...]

    @field_validator("as_of")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Keep portfolio timestamps timezone-aware UTC after JSON round-trips."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("as_of must be timezone-aware UTC")
        return value.astimezone(UTC)


class PortfolioReport(OperatorEnvelope):
    """Read-only portfolio snapshot for operators."""

    report_kind: Literal["portfolio"] = "portfolio"
    payload: PortfolioPayload


class PortfolioSleeveDigest(_FrozenModel):
    """One sleeve, its strategy, weight, and what blocks it (ADR 0088)."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    product_id: str | None
    timeframe: str | None
    weight_fraction: str
    issues: tuple[Literal["strategy_invalid", "quote_currency_mismatch", "product_unknown"], ...]


class PortfolioBacktestDigest(_FrozenModel):
    """The newest stored portfolio backtest's headline numbers."""

    result_fingerprint: str
    published_at: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    total_return_fraction: str
    maximum_drawdown_fraction: str
    basket_total_return_fraction: str


class PortfolioDigest(_FrozenModel):
    """One portfolio as operators see it, with its deployment and breaker state (ADR 0091)."""

    portfolio_id: UUID
    name: str
    mode: Literal["paper", "live"]
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    allocated_fraction: str
    unallocated_fraction: str
    revision: int
    sleeves: tuple[PortfolioSleeveDigest, ...]
    largest_asset: str | None
    largest_asset_weight_fraction: str | None
    largest_asset_within_limit: bool | None
    limits: PortfolioLimits
    manager: ManagerSettings
    deployable: bool = False
    deployment_state: Literal[
        "not_deployed", "running", "partially_running", "paused", "stopped"
    ] = "not_deployed"
    breaker_latched: bool = False
    breaker_reason_code: str | None = None
    pending_proposals: int = Field(default=0, ge=0)
    latest_backtest: PortfolioBacktestDigest | None
    active_backtest_jobs: int = Field(ge=0)


class EntryFillDigest(_FrozenModel):
    """One twin's entry-order outcomes (ADR 0097); decimals are canonical strings."""

    deployment_id: UUID
    portfolio_id: UUID | None = None
    status: str
    entries_rested: int = Field(ge=0)
    entries_filled: int = Field(ge=0)
    entries_expired: int = Field(ge=0)
    entries_rejected: int = Field(ge=0)
    entries_working: int = Field(ge=0)
    average_fill_vs_limit_bps: str | None = Field(
        default=None, description="Positive is worse than the posted limit; null with no fill."
    )
    average_seconds_to_fill: str | None = None
    median_seconds_to_fill: str | None = None


class PaperLiveFillComparison(_FrozenModel):
    """A paper and a live book running the same strategy snapshot, side by side (ADR 0097).

    Twins match by ``strategy_fingerprint`` (the content address of the exact rules). Paper
    waits run to the fill bar's close (a candle must trade through the limit); live waits
    end at the venue fill.
    """

    strategy_fingerprint: str
    strategy_id: UUID | None = None
    strategy_name: str | None = None
    product_id: str
    paper: EntryFillDigest
    live: EntryFillDigest


class PortfoliosPayload(_FrozenModel):
    """Every portfolio (sleeves, allocation, limits, manager settings, newest backtest).

    ``paper_live_fill_comparisons`` pairs paper and live books bound to the same strategy
    snapshot (sleeves or standalone) and compares their entry fills (ADR 0097).
    """

    portfolio_storage: Literal["available", "unavailable"]
    portfolio_backtest_contract: str
    total: int = Field(ge=0)
    portfolios: tuple[PortfolioDigest, ...] = ()
    paper_live_fill_comparisons: tuple[PaperLiveFillComparison, ...] = ()


class PortfoliosReport(OperatorEnvelope):
    """Read-only portfolio composition report without trading authority."""

    report_kind: Literal["portfolios"] = "portfolios"
    payload: PortfoliosPayload


class FeesPayload(_FrozenModel):
    """Coinbase fee tier plus the research/paper prefill (account rates) and schedule context."""

    taker_fee_rate: str
    maker_fee_rate: str
    usd_volume_30d: str
    fee_tier: str
    as_of: datetime
    source: Literal["coinbase"]
    suggested_maker_fee_rate: str | None = None
    suggested_taker_fee_rate: str | None = None
    suggestion_source: Literal["coinbase_account", "unavailable"]
    suggestion_unavailable_reason: Literal["demo_or_missing_credentials"] | None = None
    suggestion_fee_tier: str | None = None
    suggestion_schedule_tier_id: str | None = None
    suggestion_schedule_version: str | None = None
    suggestion_schedule_as_of: str | None = None
    schedule_maker_fee_rate: str | None = None
    schedule_taker_fee_rate: str | None = None
    suggestion_fetched_at: datetime | None = None

    @field_validator("as_of", "suggestion_fetched_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep fee timestamps timezone-aware UTC after JSON round-trips."""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be timezone-aware UTC")
        return value.astimezone(UTC)


class FeesReport(OperatorEnvelope):
    """Read-only fee profile for operators."""

    report_kind: Literal["fees"] = "fees"
    payload: FeesPayload


class MarketDataPayload(_FrozenModel):
    """Durable ingestion coverage for one product and dataset timeframe."""

    product_id: str
    provider: str | None
    timeframe: DatasetTimeframe = "1h"
    worker_status: str | None
    complete: bool | None
    freshness_status: str
    newest_candle_at: datetime | None
    age_seconds: int | None
    gap_count: int | None
    missing_intervals: int | None
    expected_candle_count: int | None
    received_candle_count: int | None
    content_fingerprint: str | None
    covered_starts_at: datetime | None
    covered_ends_at: datetime | None


class MarketDataReport(OperatorEnvelope):
    """Market-data freshness, gaps, and verified coverage."""

    report_kind: Literal["market_data"] = "market_data"
    payload: MarketDataPayload


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
    findings: tuple[RiskFinding, ...]


class RiskReport(OperatorEnvelope):
    """Pause, mismatch, and other fail-closed runtime observations."""

    report_kind: Literal["risk"] = "risk"
    payload: RiskPayload


class ReconciliationFinding(_FrozenModel):
    """One order or runtime reconciliation anomaly."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    deployment_id: UUID | None
    detail: str = Field(max_length=500)


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


class SupportBundlePayload(_FrozenModel):
    """Deterministic bundle of the other operator reports."""

    health: HealthReport
    configuration: ConfigurationReport
    exchange: ExchangeReport
    market_data: MarketDataReport
    strategies: StrategiesReport
    risk: RiskReport
    reconciliation: ReconciliationReport


class SupportBundleReport(OperatorEnvelope):
    """Redacted support bundle assembled from the supported report set."""

    report_kind: Literal["support_bundle"] = "support_bundle"
    payload: SupportBundlePayload


class ProductSummary(_FrozenModel):
    """One enabled spot product from the current catalog, with its order constraints.

    Increments and minimum sizes are exact decimal strings straight from the venue
    catalog: an order quantity must be a multiple of ``base_increment`` and at least
    ``base_min_size``, a limit price a multiple of ``price_increment``, and a quote
    notional at least ``quote_min_size``. ``status`` is the venue status text
    (``online`` when trading normally; ``null`` when not reported) and ``alias`` names
    the product whose order book this one shares (``null`` for a standalone book).
    """

    product_id: str
    base_currency: str
    quote_currency: str
    trading_enabled: bool
    status: str | None
    alias: str | None
    price_increment: str
    base_increment: str
    quote_increment: str
    base_min_size: str
    quote_min_size: str


class ProductsPayload(_FrozenModel):
    """Coinbase or demo USD spot products visible to agents."""

    provider: str
    products: tuple[ProductSummary, ...]


class ProductsReport(OperatorEnvelope):
    """Read-only USD spot catalog without secrets."""

    report_kind: Literal["products"] = "products"
    payload: ProductsPayload


class DatasetCoverageRow(_FrozenModel):
    """Local verified coverage plus watchlist and worker facts for one target.

    For a watched target, ``complete`` means the verified series spans the watch
    lookback (``watch_complete``); ``island_complete`` keeps the dataset-level fact. A
    two-minute dataset for a 90-day watch is not complete. Coverage is reported as
    ``watch_covered_candle_count`` of ``watch_expected_candle_count`` bars (with
    ``watch_coverage_ratio``). ``watch_sparsity`` is ``gapped`` when ``watch_complete``
    is false, even if the published island itself has zero gaps. ``watch_status``
    restates ``watch_complete`` as an operator noun so ``worker_status=succeeded``
    (latest chunk only) cannot be misread as a finished backfill.
    ``history_floor_at`` is set only when the listing search found no provider candle
    before the island (the market had not traded yet): coverage legitimately starts
    there and the watch counts as complete from that floor.
    ``synthetic_no_trade_intervals`` counts the flat zero-volume bars published for
    confirmed no-trade intervals (ADR 0095).
    """

    provider: str | None
    product_id: str
    timeframe: DatasetTimeframe
    watched: bool
    lookback_hours: int | None
    worker_status: str | None
    failure_code: str | None = None
    failure_message: str | None = None
    watch_complete: bool | None = None
    complete: bool | None
    freshness_status: str
    covered_starts_at: datetime | None
    covered_ends_at: datetime | None
    expected_candle_count: int | None
    received_candle_count: int | None
    gap_count: int | None
    missing_intervals: int | None
    content_fingerprint: str | None
    sparsity: Literal["none", "unknown", "gapped"]
    watch_sparsity: Literal["none", "unknown", "gapped"] | None = None
    watch_expected_candle_count: int | None = None
    watch_status: Literal["complete", "backfilling", "unknown"] | None = None
    history_floor_at: datetime | None = None
    island_complete: bool | None = None
    watch_covered_candle_count: int | None = None
    watch_coverage_ratio: float | None = None
    synthetic_no_trade_intervals: int | None = None


class DataCatalogPayload(_FrozenModel):
    """Agent-visible dataset catalog for Coinbase-listed complete-only coverage."""

    datasets: tuple[DatasetCoverageRow, ...]
    supported_timeframes: tuple[DatasetTimeframe, ...] = DATASET_TIMEFRAMES


class DataCatalogReport(OperatorEnvelope):
    """Local Parquet coverage joined with watchlist and worker state."""

    report_kind: Literal["data_catalog"] = "data_catalog"
    payload: DataCatalogPayload


class IndicatorParameterEntry(_FrozenModel):
    """One declared indicator parameter: bounds, builder default, and one-line help.

    Integer bounds/defaults are JSON numbers; decimal ones are canonical decimal
    strings. ``null`` bounds are unbounded. ``exclusive_minimum`` marks decimals that
    must be strictly greater than ``minimum``. ``optional`` parameters are omitted
    from documents unless set, and their ``default`` is ``null``.
    """

    name: str
    label: str
    value_type: Literal["integer", "decimal"]
    minimum: int | str | None
    maximum: int | str | None
    exclusive_minimum: bool = False
    default: int | str | None
    optional: bool = False
    help: str


class IndicatorCatalogEntry(_FrozenModel):
    """One implemented indicator kind and its canonical input/parameter shape.

    ``parameter_kind``, ``period_min``/``period_max`` (the integer-parameter bounds),
    and ``outputs`` keep their historical meaning; ``parameters`` is the complete,
    authoritative parameter list.
    """

    kind: str
    label: str
    category: Literal["trend", "momentum", "volatility", "volume", "statistical", "price"]
    summary: str
    inputs: tuple[str, ...]
    input_mode: Literal["configurable", "locked", "none"]
    default_input: str | tuple[str, ...] | None
    parameter_kind: ParameterKind = "period"
    period_min: int | None = None
    period_max: int | None = None
    parameters: tuple[IndicatorParameterEntry, ...] = ()
    constraints: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    warmup: str
    default_warmup_bars: int = Field(ge=1)
    supports_timeframe: bool
    supports_offset: bool
    supports_source: bool = Field(
        description=(
            "Whether the kind may read a reference instrument with `source` (ADR 0096); "
            "false only for constant."
        )
    )


class IndicatorsPayload(_FrozenModel):
    """Implemented indicator registry. Listed kinds are the only legal catalog."""

    indicators: tuple[IndicatorCatalogEntry, ...]


class IndicatorsReport(OperatorEnvelope):
    """Read-only list of strategy indicators the engine actually implements."""

    report_kind: Literal["indicators"] = "indicators"
    payload: IndicatorsPayload


STANDARD_REDACTION = RedactionMetadata(
    secrets_redacted=True,
    raw_environment_omitted=True,
    account_identifiers_omitted=True,
    balances_omitted=True,
)
PORTFOLIO_REDACTION = RedactionMetadata(
    secrets_redacted=True,
    raw_environment_omitted=True,
    account_identifiers_omitted=True,
    balances_omitted=False,
)
