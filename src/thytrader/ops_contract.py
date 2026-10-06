"""Agent/API content identity that package version 0.1.0 cannot express.

Health and `/health/ready` advertise this contract. Operator, data, research, and
runtime CLIs compare it to the copy compiled into this module. A missing or unequal
payload means the running API image is older than the CLI, even when both strings
say 0.1.0. Do not default-fill a missing payload. Do not treat matching `0.1.0` as
current.

Bump `OPS_CONTRACT_ID` whenever paper/live timeframes, the backtest engine identity
or its request fields (ADR 0083 collapsed the engines into one model), the
historical interval cap, the expected Alembic revision, the risk-policy
registry contract, live extras (user-order feed / native OCO),
experiential-memory persistence, experiential-model engines, discretionary-order
identity, paper/live HTF-filter evaluation, per-indicator timeframe evaluation,
spot shorting, attached entry brackets, paper deploy fee fields, risk circuit
breakers / order-rate limits / reference-price collars, the persisted
research-study catalog, trade-reason journals, multi-instrument documents, or
intra-strategy pyramiding, attached-child protection, worker leases, live
capital vs venue cash, deployment HTTP `capital` field names, durable
daily-loss/drawdown baselines, lifecycle stop/flatten/managed-shutdown commands,
supported spot quote currencies, catalog-health capabilities (bounded gap
inspection, ingest self-complete, heartbeat during ingest), bounded deployment
bounded deployment reads, deployment ledger pagination, multi-book ledger
aggregation, structured research-job failure detail, paper fill-atomic order
status (a failed fill ingest leaves the order OPEN), split-state fail-closed
pending-entry semantics, selectable USD/USDC/USDT spot quotes, operator
portfolio/fees reports, derived backtest performance metrics, paginated
strategy/result listings, batched strategy-library enrichment reads,
promotion evidence, the strategy model (mutable root strategies,
automatic snapshots, hard delete; ADR 0082), the explicit
`i_understand_live` HTTP acknowledgement on live start/resume/place-order, supported
spot quote currencies, the market-data provider-history floor (``history_floor_at``,
Alembic 0051), ranged backfill, explicit watched-only ingest, and the research
watch-lookback ceilings (ADR 0085, Alembic 0052), the implemented indicator kinds or the
operator ``indicators`` report shape, indicator bar-lag (``offset``) evaluation, the
per-bar decision journal (``bar_decisions``, Alembic 0053, ADR 0087), the portfolio model
(portfolios, sleeves, shared limits, manager settings, the portfolio journal, and the
portfolio backtest contract; ADR 0088, Alembic 0054), research dataset auto-binding,
cross-market product variants, the sync/async study budgets, the operator ``products``
constraint fields (ADR 0089), optional take-profit (``take_profit.kind: none``), live
stop-only protection (``stop_limit``), backtest diagnostics, account-rate fee
suggestions, the HTTP signal-trace route (Alembic 0055, ADR 0090), portfolio deployment
(sleeve bots tagged with ``portfolio_id``, portfolio limits in the risk gate, portfolio
breakers, manager proposals, and the manager briefing; ADR 0091, Alembic 0056), the
research worker pool (leased claims, crash re-queue, process recycling, the synchronous
long-poll with its 202 fallback, research-job error codes, and health queue depth; Alembic
0057, ADR 0092), signal-based exits (``exits.signal_exit`` across research, paper, and
live; the ``signal`` exit reason, the ``signal_exit`` intent purpose, and the durable
position exit marker; ADR 0093, Alembic 0058), research honesty and agent ergonomics
(result windows, study axis values and per-candidate aggregates, thinned stitched points,
document issue paths, JSON-number decimals, the library tag filter, bulk delete by tag,
clone names, the 32-sleeve cap, and batch sleeve adds; ADR 0094), sparse-market ingest
(confirmed no-trade bars, listing-only history floors, and watch-relative catalog
completeness; ADR 0095, Alembic 0059), read-only reference instruments
(``data_requirements.reference_instruments`` and indicator ``source`` across research,
paper, and live; reference dataset auto-binding, the ``reference_data_stale`` /
``reference_data_missing`` decision skip reasons, and the reference watch gate on deployment
start; ADR 0096), or runtime parity and observability (paper's backtest same-bar exit
precedence, ``position_state`` / ``exit_in_flight``, and the paper/live entry-fill
comparison in the operator ``portfolios`` report; ADR 0097), or the library origin filter,
paper ``protection_status``, last-bar book marks, and per-portfolio fill comparisons (ADR
0098), or operand-level indicator offsets across research, paper, and live (ADR 0099)
change. Concurrency is the
deployment's ``research_worker_count`` and is reported by operator health, not compiled
into this contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.backtest.models import BACKTEST_DIAGNOSTICS_VERSION
from thytrader.execution.candle_wait import NEWEST_BAR_SETTLE_SECONDS
from thytrader.market_data.models import EXECUTION_TIMEFRAMES, MAX_HISTORICAL_INTERVAL_COUNT
from thytrader.market_data.products import SPOT_QUOTE_CURRENCIES
from thytrader.portfolios.models import (
    BREAKER_REASONS,
    MAX_SLEEVES,
    PORTFOLIO_BACKTEST_CONTRACT,
    PORTFOLIO_BRIEFING_CONTRACT,
    PORTFOLIO_MODES,
)
from thytrader.portfolios.proposals import PROPOSAL_KINDS
from thytrader.research.models import BACKTEST_ENGINE
from thytrader.research.parameter_sweep import MAX_CANDIDATES, MAX_SYNC_CANDIDATES
from thytrader.strategies.models import MAX_REFERENCE_INSTRUMENTS, IndicatorKind

if TYPE_CHECKING:
    from collections.abc import Mapping

OPS_CONTRACT_ID = "thytrader-ops-contract-v67"
EXPECTED_SCHEMA_REVISION = "0068"
STRATEGY_MODEL: tuple[str, ...] = ("mutable_root", "auto_snapshot", "hard_delete")
PORTFOLIO_MODEL: tuple[str, ...] = (
    "sleeves",
    "shared_limits",
    "manager_settings",
    "journal",
    "portfolio_backtest",
    "deployment",
    "portfolio_limits",
    "manager_proposals",
)
PORTFOLIO_MODES_FIELD: tuple[str, ...] = PORTFOLIO_MODES
PORTFOLIO_BACKTEST_CONTRACT_ID: str = PORTFOLIO_BACKTEST_CONTRACT
PORTFOLIO_DEPLOYMENT_ACTIONS: tuple[str, ...] = (
    "start",
    "pause",
    "resume",
    "stop",
    "sleeve_actions",
    "breaker_reset",
)
PORTFOLIO_BREAKERS_FIELD: tuple[str, ...] = BREAKER_REASONS
PORTFOLIO_PROPOSAL_KINDS: tuple[str, ...] = PROPOSAL_KINDS
PORTFOLIO_BRIEFING_CONTRACT_ID: str = PORTFOLIO_BRIEFING_CONTRACT
BOUNDED_DEPLOYMENT_READS: tuple[str, ...] = ("list", "summary", "fills", "orders")
DEPLOYMENT_LEDGER_PAGINATION: tuple[str, ...] = ("cursor",)
MULTI_BOOK_LEDGER: tuple[str, ...] = ("paper", "live")
SPOT_QUOTE_CURRENCIES_FIELD: tuple[str, ...] = SPOT_QUOTE_CURRENCIES
DEPLOYMENT_CAPITAL_FIELDS: tuple[str, ...] = (
    "allocated_capital",
    "venue_available_quote",
    "reserved_buying_power",
    "inventory_cost",
    "performance_equity",
    "performance_capital_quote",
    "performance_maximum_drawdown_fraction",
    "initial_equity",
    "baseline_equity",
    "high_water_mark_equity",
    "utc_day_open_equity",
)
BACKTEST_ENGINE_ID: str = BACKTEST_ENGINE
RESEARCH_JOB_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "expired",
)
RESEARCH_JOB_EXPIRY_HOURS = 24
RESEARCH_WORKER_POOL: tuple[str, ...] = (
    "lease_claim",
    "crash_requeue",
    "process_recycle",
    "sync_long_poll",
    "job_error_codes",
    "health_queue_depth",
)
CATALOG_HEALTH: tuple[str, ...] = (
    "bounded_gap_inspection",
    "ingest_self_complete",
    "heartbeat_during_ingest",
    "ranged_backfill",
    "explicit_watch_ingest",
    "research_lookback_ceilings",
    "no_trade_bars",
    "listing_history_floor",
    "watch_relative_complete",
)
EXPERIENTIAL_MODEL_ENGINES: tuple[str, ...] = ("thytrader-experiential-train-v1",)
PAPER_TIMEFRAMES: tuple[str, ...] = EXECUTION_TIMEFRAMES
LIVE_TIMEFRAMES: tuple[str, ...] = EXECUTION_TIMEFRAMES
HTF_FILTER_RUNTIMES: tuple[str, ...] = ("research", "paper", "live")
INDICATOR_TIMEFRAME_RUNTIMES: tuple[str, ...] = ("research", "paper", "live")
INDICATOR_OFFSET_RUNTIMES: tuple[str, ...] = ("research", "paper", "live")
SIGNAL_EXIT_RUNTIMES: tuple[str, ...] = ("research", "paper", "live")
REFERENCE_INSTRUMENT_RUNTIMES: tuple[str, ...] = ("research", "paper", "live")
INDICATOR_KINDS: tuple[str, ...] = tuple(kind.value for kind in IndicatorKind)
POSITION_SIDES: tuple[str, ...] = ("long", "short")
ATTACHED_ENTRY_BRACKETS: tuple[str, ...] = ("paper", "live")
PAPER_DEPLOY_FEE_FIELDS: tuple[str, ...] = ("maker_fee_rate", "taker_fee_rate")
RISK_BREAKERS: tuple[str, ...] = ("daily_loss", "drawdown")
ORDER_RATE_LIMITS: tuple[str, ...] = ("entry", "cancel")
REFERENCE_PRICE_COLLARS: tuple[str, ...] = ("paper", "live")
TRADE_REASON_JOURNALS: tuple[str, ...] = ("paper", "live")
DECISION_JOURNALS: tuple[str, ...] = ("paper", "live")
MULTI_INSTRUMENT_DOCUMENTS: tuple[str, ...] = ("research", "paper", "live")
INTRA_STRATEGY_PYRAMIDING: tuple[str, ...] = ("research", "paper", "live")
LIFECYCLE_COMMANDS: tuple[str, ...] = ("none", "stop_new_entries", "flatten", "managed_shutdown")
BREAKER_LATCH_RESET: tuple[str, ...] = ("paper", "live")
RESEARCH_DATASET_AUTOBIND: tuple[str, ...] = ("backtest", "study")
STUDY_BUDGETS: dict[str, dict[str, int]] = {
    "sync": {"candidates": MAX_SYNC_CANDIDATES, "windows": 128},
    "async": {"candidates": MAX_CANDIDATES, "windows": 512},
}
TAKE_PROFIT_KINDS: tuple[str, ...] = ("reward_risk", "none")
LIVE_PROTECTION_KINDS: tuple[str, ...] = ("trigger_bracket", "stop_limit")
BACKTEST_DIAGNOSTICS: tuple[str, ...] = (BACKTEST_DIAGNOSTICS_VERSION,)
FEE_SUGGESTION_SOURCE = "coinbase_account"
RESEARCH_HONESTY: tuple[str, ...] = (
    "result_window",
    "study_axis_values",
    "study_candidate_aggregates",
    "study_stitched_points",
    "document_issue_paths",
    "json_number_decimals",
)
STRATEGY_LIBRARY: tuple[str, ...] = (
    "tag_filter",
    "bulk_delete_by_tag",
    "clone_name",
    "origin_filter",
    "origin_counts",
)
PORTFOLIO_SLEEVE_OPERATIONS: tuple[str, ...] = ("batch_add", "create_with_sleeves")
SAME_BAR_EXIT_PRECEDENCE: tuple[str, ...] = ("stop", "take_profit", "signal_exit", "time_exit")
RUNTIME_OBSERVABILITY: tuple[str, ...] = (
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
)
STALE_IMAGE_REBUILD = "Rebuild and restart with `make run`."


def expected_ops_contract() -> dict[str, object]:
    """Return the CLI/API ops contract this checkout implements."""
    return {
        "id": OPS_CONTRACT_ID,
        "max_historical_interval_count": MAX_HISTORICAL_INTERVAL_COUNT,
        "backtest_engine": BACKTEST_ENGINE_ID,
        "paper_timeframes": list(PAPER_TIMEFRAMES),
        "live_timeframes": list(LIVE_TIMEFRAMES),
        "htf_filter_runtimes": list(HTF_FILTER_RUNTIMES),
        "indicator_timeframe_runtimes": list(INDICATOR_TIMEFRAME_RUNTIMES),
        "indicator_offset_runtimes": list(INDICATOR_OFFSET_RUNTIMES),
        "indicator_operand_offset_runtimes": list(INDICATOR_OFFSET_RUNTIMES),
        "signal_exit_runtimes": list(SIGNAL_EXIT_RUNTIMES),
        "reference_instrument_runtimes": list(REFERENCE_INSTRUMENT_RUNTIMES),
        "max_reference_instruments": MAX_REFERENCE_INSTRUMENTS,
        "indicator_kinds": list(INDICATOR_KINDS),
        "position_sides": list(POSITION_SIDES),
        "attached_entry_brackets": list(ATTACHED_ENTRY_BRACKETS),
        "paper_deploy_fee_fields": list(PAPER_DEPLOY_FEE_FIELDS),
        "experiential_model_engines": list(EXPERIENTIAL_MODEL_ENGINES),
        "risk_breakers": list(RISK_BREAKERS),
        "order_rate_limits": list(ORDER_RATE_LIMITS),
        "reference_price_collars": list(REFERENCE_PRICE_COLLARS),
        "trade_reason_journals": list(TRADE_REASON_JOURNALS),
        "decision_journals": list(DECISION_JOURNALS),
        "multi_instrument_documents": list(MULTI_INSTRUMENT_DOCUMENTS),
        "intra_strategy_pyramiding": list(INTRA_STRATEGY_PYRAMIDING),
        "lifecycle_commands": list(LIFECYCLE_COMMANDS),
        "deployment_capital_fields": list(DEPLOYMENT_CAPITAL_FIELDS),
        "breaker_latch_reset": list(BREAKER_LATCH_RESET),
        "take_profit_kinds": list(TAKE_PROFIT_KINDS),
        "live_protection_kinds": list(LIVE_PROTECTION_KINDS),
        "backtest_diagnostics": list(BACKTEST_DIAGNOSTICS),
        "fee_suggestion_source": FEE_SUGGESTION_SOURCE,
        "async_backtest_job_statuses": list(RESEARCH_JOB_STATUSES),
        "research_job_statuses": list(RESEARCH_JOB_STATUSES),
        "research_job_expiry_hours": RESEARCH_JOB_EXPIRY_HOURS,
        "research_worker_pool": list(RESEARCH_WORKER_POOL),
        "async_study_planning": "worker",
        "newest_bar_settle_seconds": NEWEST_BAR_SETTLE_SECONDS,
        "spot_quote_currencies": list(SPOT_QUOTE_CURRENCIES_FIELD),
        "catalog_health": list(CATALOG_HEALTH),
        "bounded_deployment_reads": list(BOUNDED_DEPLOYMENT_READS),
        "deployment_ledger_pagination": list(DEPLOYMENT_LEDGER_PAGINATION),
        "multi_book_ledger": list(MULTI_BOOK_LEDGER),
        "strategy_model": list(STRATEGY_MODEL),
        "portfolio_model": list(PORTFOLIO_MODEL),
        "portfolio_modes": list(PORTFOLIO_MODES_FIELD),
        "portfolio_backtest_contract": PORTFOLIO_BACKTEST_CONTRACT_ID,
        "research_dataset_autobind": list(RESEARCH_DATASET_AUTOBIND),
        "study_budgets": {mode: dict(limits) for mode, limits in STUDY_BUDGETS.items()},
        "portfolio_deployment": list(PORTFOLIO_DEPLOYMENT_ACTIONS),
        "portfolio_breakers": list(PORTFOLIO_BREAKERS_FIELD),
        "portfolio_proposal_kinds": list(PORTFOLIO_PROPOSAL_KINDS),
        "portfolio_briefing_contract": PORTFOLIO_BRIEFING_CONTRACT_ID,
        "research_honesty": list(RESEARCH_HONESTY),
        "strategy_library": list(STRATEGY_LIBRARY),
        "portfolio_max_sleeves": MAX_SLEEVES,
        "portfolio_sleeve_operations": list(PORTFOLIO_SLEEVE_OPERATIONS),
        "same_bar_exit_precedence": list(SAME_BAR_EXIT_PRECEDENCE),
        "runtime_observability": list(RUNTIME_OBSERVABILITY),
        "expected_schema_revision": EXPECTED_SCHEMA_REVISION,
    }


def ops_contract_matches(payload: Mapping[str, object] | None) -> bool:
    """True only when a health payload exactly equals this checkout's ops contract."""
    if payload is None:
        return False
    return dict(payload) == expected_ops_contract()
