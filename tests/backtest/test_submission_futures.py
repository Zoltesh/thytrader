"""Server-side futures bindings of a backtest submission (ADR 0128, slice P1-3)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from thytrader.backtest.submission import _execution_fingerprint
from thytrader.backtest.submission_futures import FuturesBinding, bind_futures_run
from thytrader.backtest.submission_models import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    FuturesBacktestAssumptions,
)
from thytrader.evaluation.futures_spec import funding_series_fingerprint
from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.market_data.futures_observations import (
    FundingRateRecord,
    FuturesInstrumentObservation,
    instrument_observation,
)
from thytrader.market_data.instruments import InstrumentKind
from thytrader.strategies.models import StrategyDefinition

_PERP = "BIP-20DEC30-CDE"
_SEEN = datetime(2026, 10, 10, 6, 30, tzinfo=UTC)
_START = datetime(2026, 10, 10, 0, tzinfo=UTC)
_END = datetime(2026, 10, 10, 3, tzinfo=UTC)
_LISTING = Path("tests/exchanges/fixtures/coinbase_futures_listing.json")


def _observation() -> FuturesInstrumentObservation:
    """The recorded BIP perp facts from the listing fixture."""
    payload: dict[str, Any] = json.loads(_LISTING.read_text())
    row = next(item for item in payload["products"] if item["product_id"] == _PERP)
    product = parse_futures_row(row)
    assert product is not None
    return instrument_observation(product)


def _record(hour: datetime, rate: str, *, settled: bool = True) -> FundingRateRecord:
    """One stored funding row."""
    return FundingRateRecord(
        product_id=_PERP,
        funding_time=hour,
        rate=Decimal(rate),
        interval_seconds=3600,
        first_observed_at=hour,
        last_observed_at=hour,
        observation_count=1,
        revision_count=0,
        settled=settled,
        settled_at=hour + timedelta(hours=1) if settled else None,
        conflict_count=0,
        last_conflict_rate=None,
        last_conflict_at=None,
    )


class _Source:
    """An in-memory futures observation source."""

    def __init__(
        self,
        observation: FuturesInstrumentObservation | None,
        records: tuple[FundingRateRecord, ...] = (),
    ) -> None:
        self.observation = observation
        self.records = records

    async def latest_instrument(
        self, product_id: str
    ) -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Return the one observation when it matches."""
        if self.observation is None or self.observation.product_id != product_id:
            return None
        return self.observation, _SEEN

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """Return rows in ``[starts_at, ends_at)``."""
        return tuple(
            record
            for record in self.records
            if record.product_id == product_id and starts_at <= record.funding_time < ends_at
        )


def _strategy(*, future: bool = True, base: str = "BTC") -> StrategyDefinition:
    """The pinned spot golden, optionally re-targeted at the BTC perp."""
    payload: dict[str, Any] = json.loads(
        Path("tests/strategies/golden/sma_strategy_v1.json").read_text()
    )
    if future:
        payload["instrument"] = {
            "product_id": _PERP,
            "base_currency": base,
            "quote_currency": "USD",
            "kind": "future",
        }
        payload["derivatives"] = {"max_leverage": "2"}
    return StrategyDefinition.model_validate(payload)


def _hours() -> tuple[datetime, ...]:
    """Funding hours in (00:00, 03:00]."""
    return tuple(_START + timedelta(hours=offset) for offset in (1, 2, 3))


def _bind(
    definition: StrategyDefinition,
    assumptions: FuturesBacktestAssumptions | None,
    source: _Source | None,
) -> FuturesBinding | None:
    """Run the binding synchronously."""
    return asyncio.run(
        bind_futures_run(definition, assumptions, source, starts_at=_START, ends_at=_END)
    )


_FEES = FuturesBacktestAssumptions(fee_per_contract="0.15")


def test_perp_binds_contract_observed_margin_and_the_settled_series() -> None:
    """Every venue fact comes from the recorded observation and funding history."""
    records = tuple(_record(hour, "0.00001") for hour in _hours())
    binding = _bind(_strategy(), _FEES, _Source(_observation(), records))

    assert binding is not None
    assert binding.contract.product_id == _PERP
    assert binding.contract.kind == "perpetual_future"
    assert binding.contract.contract_size == "0.01"
    assert binding.contract.expires_at is None
    assert binding.contract.catalog_fingerprint == _observation().payload_fingerprint
    assert (binding.margin.long_rate, binding.margin.short_rate) == ("0.21025", "0.256625")
    assert binding.margin.source == "latest_observation"
    assert binding.margin.observed_at == _SEEN
    rates = {hour: Decimal("0.00001") for hour in _hours()}
    assert binding.funding is not None
    assert binding.funding.series_fingerprint == funding_series_fingerprint(_PERP, rates)
    assert binding.funding.settled_hours == 3


def test_an_unsettled_or_absent_hour_rejects_the_perp_run() -> None:
    """The newest hour is still revisable, so it counts as missing and is named."""
    first, second, third = _hours()
    records = (
        _record(first, "0.00001"),
        _record(second, "0.00001"),
        _record(third, "0", settled=False),
    )
    missing = r"FUNDING_HISTORY_MISSING.*03:00:00Z"
    with pytest.raises(BacktestSubmissionRejectedError, match=missing):
        _bind(_strategy(), _FEES, _Source(_observation(), records))
    with pytest.raises(BacktestSubmissionRejectedError, match=r"01:00:00Z .3 of"):
        _bind(_strategy(), _FEES, _Source(_observation()))


def test_declared_constants_replace_observed_margin_and_recorded_funding() -> None:
    """Explicit margin rates and a constant funding rate bind without history."""
    assumptions = FuturesBacktestAssumptions(
        fee_per_contract="0.15",
        long_margin_rate="0.3",
        short_margin_rate="0.35",
        margin_stress_multiplier="1.5",
        funding_constant_rate="0.00002",
    )
    binding = _bind(_strategy(), assumptions, _Source(_observation()))

    assert binding is not None
    assert binding.margin.source == "explicit"
    assert binding.margin.observed_at is None
    assert binding.margin.stress_multiplier == "1.5"
    assert binding.funding is not None
    assert binding.funding.constant_rate == "0.00002"
    assert binding.funding.series_fingerprint is None


def test_dated_contracts_bind_their_expiry_and_no_funding() -> None:
    """A dated contract carries the venue expiry and never funding."""
    dated = replace(
        _observation(),
        kind=InstrumentKind.DATED_FUTURE,
        venue_expiry_at=datetime(2030, 12, 20, 16, tzinfo=UTC),
        funding_interval_seconds=None,
    )
    binding = _bind(_strategy(), _FEES, _Source(dated))

    assert binding is not None
    assert binding.contract.kind == "dated_future"
    assert binding.contract.expires_at == datetime(2030, 12, 20, 16, tzinfo=UTC)
    assert binding.funding is None


@pytest.mark.parametrize(
    ("definition", "assumptions", "source", "message"),
    [
        (_strategy(future=False), _FEES, None, "futures strategies only"),
        (_strategy(), None, _Source(_observation()), "FUTURES_ASSUMPTIONS_REQUIRED"),
        (_strategy(), _FEES, None, "FUTURES_OBSERVATIONS_UNAVAILABLE"),
        (_strategy(), _FEES, _Source(None), "FUTURES_CONTRACT_UNOBSERVED"),
        (_strategy(base="ETH"), _FEES, _Source(_observation()), "FUTURES_UNDERLYING_MISMATCH"),
        (
            _strategy(),
            _FEES,
            _Source(replace(_observation(), overnight_long_margin_rate=None)),
            "FUTURES_MARGIN_UNKNOWN",
        ),
    ],
)
def test_missing_futures_facts_are_named_rejections(
    definition: StrategyDefinition,
    assumptions: FuturesBacktestAssumptions | None,
    source: _Source | None,
    message: str,
) -> None:
    """Nothing is guessed: each missing fact rejects the submission by name."""
    with pytest.raises(BacktestSubmissionRejectedError, match=message):
        _bind(definition, assumptions, source)


def test_spot_strategies_bind_nothing() -> None:
    """A spot submission without a futures block is unchanged."""
    assert _bind(_strategy(future=False), None, None) is None


def test_explicit_margin_rates_come_in_pairs() -> None:
    """One explicit rate without the other is refused at the request boundary."""
    with pytest.raises(ValueError, match="set together"):
        FuturesBacktestAssumptions(fee_per_contract="0.15", long_margin_rate="0.3")


def test_execution_fingerprint_covers_the_futures_binding() -> None:
    """A new observation or funding hour is a new run; spot fingerprints ignore futures."""
    request = BacktestSubmissionRequest.model_validate(
        {
            "strategy_fingerprint": "sha256:" + "1" * 64,
            "dataset_fingerprint": "sha256:" + "2" * 64,
            "evaluation_start": _START,
            "evaluation_end": _END,
            "initial_quote_balance": "1000",
            "maker_fee_rate": "0",
            "taker_fee_rate": "0.0005",
            "fixed_slippage_bps": "2",
            "futures": {"fee_per_contract": "0.15"},
        }
    )
    records = tuple(_record(hour, "0.00001") for hour in _hours())
    binding = _bind(_strategy(), _FEES, _Source(_observation(), records))
    changed = tuple(_record(hour, "0.00002") for hour in _hours())
    other = _bind(_strategy(), _FEES, _Source(_observation(), changed))

    assert binding is not None
    assert other is not None
    first = _execution_fingerprint(request, "USD", futures=binding)
    assert first != _execution_fingerprint(request, "USD", futures=other)
    assert first != _execution_fingerprint(request, "USD")
