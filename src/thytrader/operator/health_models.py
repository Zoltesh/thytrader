"""Operator report models for process health, configuration, and exchange connectivity.

Covers the ops contract identity the CLI compares against the running API, research
worker pool liveness, and the health, configuration, and exchange reports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from thytrader.exchanges.read_errors import ExchangeReadFailure
from thytrader.operator.models import OperatorEnvelope, SupportedTimeframe, _FrozenModel
from thytrader.ops_contract import expected_ops_contract


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
    indicator_operand_offset_runtimes: tuple[Literal["research", "paper", "live"], ...]
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
        Literal["tag_filter", "bulk_delete_by_tag", "clone_name", "origin_filter", "origin_counts"],
        ...,
    ]
    async_study_planning: Literal["worker"]
    newest_bar_settle_seconds: int = Field(ge=0)
    portfolio_max_sleeves: int = Field(ge=1)
    portfolio_sleeve_operations: tuple[Literal["batch_add", "create_with_sleeves"], ...] = Field(
        description=(
            "create_with_sleeves saves initial sleeves atomically at portfolio revision 1; "
            "neither operation grants deployment authority."
        )
    )
    same_bar_exit_precedence: tuple[Literal["stop", "take_profit", "signal_exit", "time_exit"], ...]
    runtime_observability: tuple[
        Literal[
            "position_state",
            "exit_in_flight",
            "paper_live_fill_comparison",
            "paper_protection_covered",
            "book_marks",
            "fee_adjusted_book_pnl",
            "portfolio_fill_comparisons",
            "explicit_deployment_twins",
            "rule_matched_deployment_twins",
            "capital_normalized_performance",
            "exchange_read_failures",
            "audit_failure_evidence",
            "watched_market_tail_health",
            "venue_order_observations",
            "verified_utc_day_open_evidence",
            "quantitative_protection_evidence",
            "managed_venue_reconciliation",
            "capacity_readiness",
            "durable_safety_alerts",
            "execution_quality_evidence",
            "complete_fleet_inventory",
            "revision_fenced_fleet_controls",
            "backtest_bar_explanations",
            "fleet_entry_health",
        ],
        ...,
    ]
    instrument_kinds: tuple[Literal["spot", "dated_future", "perpetual_future"], ...] = Field(
        description="Instrument kinds the read-only catalog reports (ADR 0126)."
    )
    futures_order_paths: tuple[str, ...] = Field(
        max_length=0,
        description="Always empty: no surface can order a futures contract (ADR 0126).",
    )
    futures_observations: tuple[
        Literal[
            "instrument_catalog",
            "funding_history",
            "operator_products_kind",
            "futures_candles",
            "account_mirror",
            "readiness_reconciliation",
            "live_spot_collateral_gate",
            "futures_strategy_documents",
            "futures_backtest_kernel",
            "futures_backtest_submission",
            "futures_fee_preview_probe",
            "paper_futures_books",
            "futures_entry_gate",
            "paper_futures_runtime_lane",
            "account_mirror_history",
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
    failure: ExchangeReadFailure | None = None


class ExchangeReport(OperatorEnvelope):
    """Coinbase connectivity and detected key permissions."""

    report_kind: Literal["exchange"] = "exchange"
    payload: ExchangePayload
