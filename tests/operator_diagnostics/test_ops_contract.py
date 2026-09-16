"""Ops-contract identity used to detect stale Compose images."""

from thytrader.market_data.models import EXECUTION_TIMEFRAMES, MAX_HISTORICAL_INTERVAL_COUNT
from thytrader.ops_contract import (
    BACKTEST_ENGINES,
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
    assert expected["id"] == "thytrader-ops-contract-v22"
    assert expected["expected_schema_revision"] == EXPECTED_SCHEMA_REVISION
    assert expected["expected_schema_revision"] == "0035"
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
    assert expected["backtest_engines"] == list(BACKTEST_ENGINES)
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
    assert expected["multi_instrument_documents"] == list(MULTI_INSTRUMENT_DOCUMENTS)
    assert expected["multi_instrument_documents"] == ["research", "paper", "live"]
    assert expected["intra_strategy_pyramiding"] == list(INTRA_STRATEGY_PYRAMIDING)
    assert expected["intra_strategy_pyramiding"] == ["research", "paper", "live"]
