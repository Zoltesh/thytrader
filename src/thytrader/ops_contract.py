"""Agent/API content identity that package version 0.1.0 cannot express.

Health and `/health/ready` advertise this contract. Operator, data, research, and
runtime CLIs compare it to the copy compiled into this module. A missing or unequal
payload means the running API image is older than the CLI, even when both strings
say 0.1.0. Do not default-fill a missing payload. Do not treat matching `0.1.0` as
current.

Bump `OPS_CONTRACT_ID` whenever paper/live timeframes, backtest engines, the
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
promotion evidence, or supported
spot quote currencies change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.models import EXECUTION_TIMEFRAMES, MAX_HISTORICAL_INTERVAL_COUNT
from thytrader.market_data.products import SPOT_QUOTE_CURRENCIES

if TYPE_CHECKING:
    from collections.abc import Mapping

OPS_CONTRACT_ID = "thytrader-ops-contract-v38"
EXPECTED_SCHEMA_REVISION = "0047"
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
    "initial_equity",
    "baseline_equity",
    "high_water_mark_equity",
    "utc_day_open_equity",
)
BACKTEST_ENGINES: tuple[str, ...] = (
    "thytrader-bar-backtest-v1",
    "thytrader-bar-backtest-v2",
    "thytrader-bar-backtest-v3",
    "thytrader-bar-backtest-v4",
)
RESEARCH_JOB_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "expired",
)
MAX_CONCURRENT_RESEARCH_JOBS = 2
RESEARCH_JOB_EXPIRY_HOURS = 24
CATALOG_HEALTH: tuple[str, ...] = (
    "bounded_gap_inspection",
    "ingest_self_complete",
    "heartbeat_during_ingest",
)
EXPERIENTIAL_MODEL_ENGINES: tuple[str, ...] = ("thytrader-experiential-train-v1",)
PAPER_TIMEFRAMES: tuple[str, ...] = EXECUTION_TIMEFRAMES
LIVE_TIMEFRAMES: tuple[str, ...] = EXECUTION_TIMEFRAMES
HTF_FILTER_RUNTIMES: tuple[str, ...] = ("research", "paper", "live")
INDICATOR_TIMEFRAME_RUNTIMES: tuple[str, ...] = ("research", "paper", "live")
POSITION_SIDES: tuple[str, ...] = ("long", "short")
ATTACHED_ENTRY_BRACKETS: tuple[str, ...] = ("paper", "live")
PAPER_DEPLOY_FEE_FIELDS: tuple[str, ...] = ("maker_fee_rate", "taker_fee_rate")
RISK_BREAKERS: tuple[str, ...] = ("daily_loss", "drawdown")
ORDER_RATE_LIMITS: tuple[str, ...] = ("entry", "cancel")
REFERENCE_PRICE_COLLARS: tuple[str, ...] = ("paper", "live")
TRADE_REASON_JOURNALS: tuple[str, ...] = ("paper", "live")
MULTI_INSTRUMENT_DOCUMENTS: tuple[str, ...] = ("research", "paper", "live")
INTRA_STRATEGY_PYRAMIDING: tuple[str, ...] = ("research", "paper", "live")
LIFECYCLE_COMMANDS: tuple[str, ...] = ("none", "stop_new_entries", "flatten", "managed_shutdown")
BREAKER_LATCH_RESET: tuple[str, ...] = ("paper", "live")
STALE_IMAGE_REBUILD = "Rebuild and restart with `make run`."


def expected_ops_contract() -> dict[str, object]:
    """Return the CLI/API ops contract this checkout implements."""
    return {
        "id": OPS_CONTRACT_ID,
        "max_historical_interval_count": MAX_HISTORICAL_INTERVAL_COUNT,
        "backtest_engines": list(BACKTEST_ENGINES),
        "paper_timeframes": list(PAPER_TIMEFRAMES),
        "live_timeframes": list(LIVE_TIMEFRAMES),
        "htf_filter_runtimes": list(HTF_FILTER_RUNTIMES),
        "indicator_timeframe_runtimes": list(INDICATOR_TIMEFRAME_RUNTIMES),
        "position_sides": list(POSITION_SIDES),
        "attached_entry_brackets": list(ATTACHED_ENTRY_BRACKETS),
        "paper_deploy_fee_fields": list(PAPER_DEPLOY_FEE_FIELDS),
        "experiential_model_engines": list(EXPERIENTIAL_MODEL_ENGINES),
        "risk_breakers": list(RISK_BREAKERS),
        "order_rate_limits": list(ORDER_RATE_LIMITS),
        "reference_price_collars": list(REFERENCE_PRICE_COLLARS),
        "trade_reason_journals": list(TRADE_REASON_JOURNALS),
        "multi_instrument_documents": list(MULTI_INSTRUMENT_DOCUMENTS),
        "intra_strategy_pyramiding": list(INTRA_STRATEGY_PYRAMIDING),
        "lifecycle_commands": list(LIFECYCLE_COMMANDS),
        "deployment_capital_fields": list(DEPLOYMENT_CAPITAL_FIELDS),
        "breaker_latch_reset": list(BREAKER_LATCH_RESET),
        "async_backtest_job_statuses": list(RESEARCH_JOB_STATUSES),
        "research_job_statuses": list(RESEARCH_JOB_STATUSES),
        "max_concurrent_research_jobs": MAX_CONCURRENT_RESEARCH_JOBS,
        "research_job_expiry_hours": RESEARCH_JOB_EXPIRY_HOURS,
        "spot_quote_currencies": list(SPOT_QUOTE_CURRENCIES_FIELD),
        "catalog_health": list(CATALOG_HEALTH),
        "bounded_deployment_reads": list(BOUNDED_DEPLOYMENT_READS),
        "deployment_ledger_pagination": list(DEPLOYMENT_LEDGER_PAGINATION),
        "multi_book_ledger": list(MULTI_BOOK_LEDGER),
        "expected_schema_revision": EXPECTED_SCHEMA_REVISION,
    }


def ops_contract_matches(payload: Mapping[str, object] | None) -> bool:
    """True only when a health payload exactly equals this checkout's ops contract."""
    if payload is None:
        return False
    return dict(payload) == expected_ops_contract()
