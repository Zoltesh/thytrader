"""Ops-contract identity used to detect stale Compose images."""

from alembic.config import Config
from alembic.script import ScriptDirectory

from thytrader.market_data.models import EXECUTION_TIMEFRAMES, MAX_HISTORICAL_INTERVAL_COUNT
from thytrader.ops_contract import (
    BACKTEST_ENGINE_ID,
    DECISION_JOURNALS,
    EXPECTED_SCHEMA_REVISION,
    EXPERIENTIAL_MODEL_ENGINES,
    HTF_FILTER_RUNTIMES,
    INDICATOR_TIMEFRAME_RUNTIMES,
    INTRA_STRATEGY_PYRAMIDING,
    LIFECYCLE_COMMANDS,
    LIVE_TIMEFRAMES,
    MULTI_INSTRUMENT_DOCUMENTS,
    OPS_CONTRACT_ID,
    PAPER_DEPLOY_FEE_FIELDS,
    PAPER_TIMEFRAMES,
    TRADE_REASON_JOURNALS,
    expected_ops_contract,
    ops_contract_matches,
)


def test_ops_contract_matches_requires_payload() -> None:
    """A missing health ops_contract is a stale image, not a default match."""
    expected = expected_ops_contract()
    assert ops_contract_matches(None) is False
    assert ops_contract_matches(expected) is True
    mismatched = dict(expected)
    mismatched["id"] = "thytrader-ops-contract-v1"
    assert ops_contract_matches(mismatched) is False
    unexpected = {**expected, "unexpected": True}
    assert ops_contract_matches(unexpected) is False
    assert expected["id"] == OPS_CONTRACT_ID
    assert expected["id"] == "thytrader-ops-contract-v78"
    assert expected["same_bar_exit_precedence"] == [
        "stop",
        "take_profit",
        "signal_exit",
        "time_exit",
    ]
    assert expected["runtime_observability"] == [
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
    ]
    assert expected["research_honesty"] == [
        "result_window",
        "study_axis_values",
        "study_candidate_aggregates",
        "study_stitched_points",
        "document_issue_paths",
        "json_number_decimals",
    ]
    assert expected["strategy_library"] == [
        "tag_filter",
        "bulk_delete_by_tag",
        "clone_name",
        "origin_filter",
        "origin_counts",
    ]
    assert expected["portfolio_max_sleeves"] == 32
    assert expected["portfolio_sleeve_operations"] == ["batch_add", "create_with_sleeves"]
    assert expected["indicator_offset_runtimes"] == ["research", "paper", "live"]
    assert expected["indicator_operand_offset_runtimes"] == ["research", "paper", "live"]
    assert expected["signal_exit_runtimes"] == ["research", "paper", "live"]
    assert expected["reference_instrument_runtimes"] == ["research", "paper", "live"]
    assert expected["max_reference_instruments"] == 3
    kinds = expected["indicator_kinds"]
    assert isinstance(kinds, list)
    assert len(kinds) == 53
    assert "supertrend" in kinds
    assert expected["expected_schema_revision"] == EXPECTED_SCHEMA_REVISION
    assert expected["expected_schema_revision"] == "0071"
    assert expected["instrument_kinds"] == ["spot", "dated_future", "perpetual_future"]
    assert expected["futures_order_paths"] == []
    assert expected["futures_observations"] == [
        "instrument_catalog",
        "funding_history",
        "operator_products_kind",
        "futures_candles",
    ]
    assert expected["async_study_planning"] == "worker"
    assert expected["newest_bar_settle_seconds"] == 120
    assert expected["take_profit_kinds"] == ["reward_risk", "none"]
    assert expected["live_protection_kinds"] == ["trigger_bracket", "stop_limit"]
    assert expected["backtest_diagnostics"] == ["thytrader-backtest-diagnostics-v1"]
    assert expected["fee_suggestion_source"] == "coinbase_account"
    assert expected["bounded_deployment_reads"] == ["list", "summary", "fills", "orders"]
    assert expected["deployment_ledger_pagination"] == ["cursor"]
    assert expected["multi_book_ledger"] == ["paper", "live"]
    assert expected["portfolio_model"] == [
        "sleeves",
        "shared_limits",
        "manager_settings",
        "journal",
        "portfolio_backtest",
        "deployment",
        "portfolio_limits",
        "manager_proposals",
    ]
    assert expected["portfolio_modes"] == ["paper", "live"]
    assert expected["portfolio_backtest_contract"] == "thytrader-portfolio-backtest-v1"
    assert "max_concurrent_portfolio_backtests" not in expected
    assert expected["research_dataset_autobind"] == ["backtest", "study"]
    assert expected["study_budgets"] == {
        "sync": {"candidates": 8, "windows": 128},
        "async": {"candidates": 64, "windows": 512},
    }
    assert expected["portfolio_deployment"] == [
        "start",
        "pause",
        "resume",
        "stop",
        "sleeve_actions",
        "breaker_reset",
    ]
    assert expected["portfolio_breakers"] == [
        "PORTFOLIO_DAILY_LOSS_STOP",
        "PORTFOLIO_DRAWDOWN_STOP",
    ]
    assert expected["portfolio_proposal_kinds"] == [
        "rebalance",
        "pause_sleeve",
        "resume_sleeve",
        "add_sleeve",
    ]
    assert expected["portfolio_briefing_contract"] == "thytrader-portfolio-briefing-v1"
    assert expected["spot_quote_currencies"] == ["USD", "USDC", "USDT"]
    assert expected["catalog_health"] == [
        "bounded_gap_inspection",
        "ingest_self_complete",
        "heartbeat_during_ingest",
        "ranged_backfill",
        "explicit_watch_ingest",
        "research_lookback_ceilings",
        "no_trade_bars",
        "listing_history_floor",
        "watch_relative_complete",
    ]
    assert expected["async_backtest_job_statuses"] == [
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "expired",
    ]
    assert expected["research_job_statuses"] == expected["async_backtest_job_statuses"]
    assert "max_concurrent_research_jobs" not in expected
    assert expected["research_job_expiry_hours"] == 24
    assert expected["research_worker_pool"] == [
        "lease_claim",
        "crash_requeue",
        "process_recycle",
        "sync_long_poll",
        "job_error_codes",
        "health_queue_depth",
    ]
    assert expected["deployment_capital_fields"] == [
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
    ]
    assert expected["breaker_latch_reset"] == ["paper", "live"]
    assert expected["lifecycle_commands"] == list(LIFECYCLE_COMMANDS)
    assert expected["lifecycle_commands"] == [
        "none",
        "stop_new_entries",
        "flatten",
        "managed_shutdown",
    ]
    assert expected["paper_deploy_fee_fields"] == list(PAPER_DEPLOY_FEE_FIELDS)
    assert expected["paper_deploy_fee_fields"] == ["maker_fee_rate", "taker_fee_rate"]
    assert expected["max_historical_interval_count"] == MAX_HISTORICAL_INTERVAL_COUNT
    assert MAX_HISTORICAL_INTERVAL_COUNT == 129_600
    assert expected["backtest_engine"] == BACKTEST_ENGINE_ID == "thytrader-backtest"
    assert "backtest_engines" not in expected
    assert expected["paper_timeframes"] == list(PAPER_TIMEFRAMES)
    assert expected["live_timeframes"] == list(LIVE_TIMEFRAMES)
    assert expected["paper_timeframes"] == list(EXECUTION_TIMEFRAMES)
    assert expected["live_timeframes"] == list(EXECUTION_TIMEFRAMES)
    assert expected["paper_timeframes"] == [
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "1d",
    ]
    assert expected["live_timeframes"] == expected["paper_timeframes"]
    assert expected["htf_filter_runtimes"] == list(HTF_FILTER_RUNTIMES)
    assert expected["htf_filter_runtimes"] == ["research", "paper", "live"]
    assert expected["indicator_timeframe_runtimes"] == list(INDICATOR_TIMEFRAME_RUNTIMES)
    assert expected["indicator_timeframe_runtimes"] == ["research", "paper", "live"]
    assert expected["position_sides"] == ["long", "short"]
    assert expected["attached_entry_brackets"] == ["paper", "live"]
    assert expected["experiential_model_engines"] == list(EXPERIENTIAL_MODEL_ENGINES)
    assert expected["experiential_model_engines"] == ["thytrader-experiential-train-v1"]
    assert expected["risk_breakers"] == ["daily_loss", "drawdown"]
    assert expected["order_rate_limits"] == ["entry", "cancel"]
    assert expected["reference_price_collars"] == ["paper", "live"]
    assert expected["trade_reason_journals"] == list(TRADE_REASON_JOURNALS)
    assert expected["trade_reason_journals"] == ["paper", "live"]
    assert expected["decision_journals"] == list(DECISION_JOURNALS)
    assert expected["decision_journals"] == ["paper", "live"]
    assert expected["multi_instrument_documents"] == list(MULTI_INSTRUMENT_DOCUMENTS)
    assert expected["multi_instrument_documents"] == ["research", "paper", "live"]
    assert expected["intra_strategy_pyramiding"] == list(INTRA_STRATEGY_PYRAMIDING)
    assert expected["intra_strategy_pyramiding"] == ["research", "paper", "live"]


def test_expected_schema_revision_matches_alembic_head() -> None:
    """Every migration must update the advertised revision before it can ship."""
    migration_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert migration_head is not None
    assert expected_ops_contract()["expected_schema_revision"] == migration_head
