"""Behavioral tests for browser backtest submission engine and spread semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import TypedDict, Unpack

from pydantic import ValidationError
import pytest

from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRequest,
    PostgresBacktestSubmitter,
    _broker_from_request,
    _execution_fingerprint,
)


class _NoIoStrategyStore:
    """Record whether malformed submission input reached persistence I/O."""

    def __init__(self) -> None:
        """Start without any attempted strategy load."""
        self.load_calls = 0

    async def load(self, strategy_fingerprint: str) -> None:
        """Record the forbidden load without returning strategy evidence."""
        del strategy_fingerprint
        self.load_calls += 1


class _RequestOverrides(TypedDict, total=False):
    """Typed optional overrides for one valid browser submission request."""

    strategy_fingerprint: str
    dataset_fingerprint: str
    evaluation_start: datetime
    evaluation_end: datetime
    initial_quote_balance: str
    maker_fee_rate: str
    taker_fee_rate: str
    fixed_slippage_bps: str
    engine_contract_version: str
    spread_bps: str | None


def _request(**overrides: Unpack[_RequestOverrides]) -> BacktestSubmissionRequest:
    """Build one valid submission request with optional field overrides."""
    fields: _RequestOverrides = {
        "strategy_fingerprint": "sha256:" + "a" * 64,
        "dataset_fingerprint": "sha256:" + "b" * 64,
        "evaluation_start": datetime(2026, 8, 1, tzinfo=UTC),
        "evaluation_end": datetime(2026, 8, 2, tzinfo=UTC),
        "initial_quote_balance": "10000",
        "maker_fee_rate": "0.001",
        "taker_fee_rate": "0.002",
        "fixed_slippage_bps": "10",
    }
    fields.update(overrides)
    return BacktestSubmissionRequest.model_validate(fields)


def test_v1_submission_rejects_spread() -> None:
    """The V1 contract has no broker block; a spread value is a caller error."""
    with pytest.raises(ValidationError, match="spread_bps requires"):
        _request(engine_contract_version="thytrader-bar-backtest-v1", spread_bps="5")


def test_v2_submission_requires_spread() -> None:
    """The V2 contract is undefined without an explicit constant spread."""
    with pytest.raises(ValidationError, match="spread_bps is required"):
        _request(engine_contract_version="thytrader-bar-backtest-v2")


def test_v2_submission_builds_the_cli_equivalent_broker_block() -> None:
    """Browser V2 broker assumptions are identical to the CLI's V2 broker assumptions."""
    request = _request(
        engine_contract_version="thytrader-bar-backtest-v2",
        spread_bps="8",
    )

    broker = _broker_from_request(request)
    assert broker is not None
    assert broker.model_dump(mode="python") == {
        "price_model": "constant_spread_bps",
        "spread_bps": "8",
        "fill_policy": "full",
        "trigger_evaluation": "bid_side",
        "equity_marking": "bid_close",
    }


def test_execution_fingerprint_distinguishes_engine_and_spread() -> None:
    """Equivalent browser and CLI semantics hash equally; different engines do not."""
    v1 = _request(engine_contract_version="thytrader-bar-backtest-v1")
    v1_again = _request(engine_contract_version="thytrader-bar-backtest-v1")
    v2_spread8 = _request(
        engine_contract_version="thytrader-bar-backtest-v2",
        spread_bps="8",
    )
    v2_spread12 = _request(
        engine_contract_version="thytrader-bar-backtest-v2",
        spread_bps="12",
    )

    assert _execution_fingerprint(v1) == _execution_fingerprint(v1_again)
    assert _execution_fingerprint(v1) != _execution_fingerprint(v2_spread8)
    assert _execution_fingerprint(v2_spread8) != _execution_fingerprint(v2_spread12)


def test_execution_fingerprint_embeds_the_cli_payload_shape() -> None:
    """The hash payload carries the broker block so CLI and browser dedupe together."""
    request = _request(
        engine_contract_version="thytrader-bar-backtest-v2",
        spread_bps="8",
    )
    fingerprint = _execution_fingerprint(request)

    assert fingerprint.startswith("sha256:")
    # Recompute the canonical payload to confirm the broker block participates.
    payload = {
        "bar_execution": {
            "fill_timing": "next_candle_open",
            "signal_timing": "completed_candle_close",
        },
        "broker": {
            "price_model": "constant_spread_bps",
            "spread_bps": "8",
            "fill_policy": "full",
            "trigger_evaluation": "bid_side",
            "equity_marking": "bid_close",
        },
        "capital": {
            "quote_currency": "USD",
            "initial_quote_balance": "10000",
        },
        "costs": {
            "maker_fee_rate": "0.001",
            "taker_fee_rate": "0.002",
            "fixed_slippage_bps": "10",
        },
        "dataset_fingerprint": "sha256:" + "b" * 64,
        "engine_contract_version": "thytrader-bar-backtest-v2",
        "evaluation_end": request.evaluation_end.isoformat(),
        "evaluation_start": request.evaluation_start.isoformat(),
        "random_seed": 0,
        "strategy_fingerprint": "sha256:" + "a" * 64,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert fingerprint == f"sha256:{sha256(canonical.encode()).hexdigest()}"


@pytest.mark.anyio
async def test_submitter_rejects_forged_invalid_financial_input_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service revalidates financial assumptions before loading immutable sources."""
    submitter = object.__new__(PostgresBacktestSubmitter)
    strategy_store = _NoIoStrategyStore()
    monkeypatch.setattr(submitter, "_strategy_store", strategy_store, raising=False)
    forged = _request().model_copy(update={"initial_quote_balance": "0"})

    with pytest.raises(BacktestSubmissionError, match="unavailable"):
        await submitter.submit(forged)

    assert strategy_store.load_calls == 0
