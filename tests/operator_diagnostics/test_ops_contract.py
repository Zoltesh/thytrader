"""Ops-contract identity used to detect stale Compose images."""

from thytrader.ops_contract import (
    BACKTEST_ENGINES,
    EXPECTED_SCHEMA_REVISION,
    LIVE_TIMEFRAMES,
    OPS_CONTRACT_ID,
    PAPER_TIMEFRAMES,
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
    assert expected["id"] == OPS_CONTRACT_ID
    assert expected["expected_schema_revision"] == EXPECTED_SCHEMA_REVISION
    assert expected["backtest_engines"] == list(BACKTEST_ENGINES)
    assert expected["paper_timeframes"] == list(PAPER_TIMEFRAMES)
    assert expected["live_timeframes"] == list(LIVE_TIMEFRAMES)
