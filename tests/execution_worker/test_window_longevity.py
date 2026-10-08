"""Shared-clock warmup union and derived reference coverage (ADR 0113)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from typing import cast

from pydantic import ValidationError
import pytest

from tests.execution.test_htf_filter import _five_minute_htf_strategy
from tests.strategies.reference_support import reference_payload
from tests.worker_patching import patch_worker_global
from thytrader.evaluation.models import warmup_starts_at
from thytrader.evaluation.multi_timeframe import (
    closed_bar_required_coverage,
    closed_bar_starts,
    ltf_close,
)
from thytrader.evaluation.signal_evaluator import (
    calculate_extra_indicator_rows,
    calculate_htf_indicator_rows,
)
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketDataError
from thytrader.execution.references import reference_gate
from thytrader.execution.signals import (
    evaluate_latest_entry_evidence,
    evaluate_latest_signal_exit,
)
from thytrader.execution_worker import service
from thytrader.market_data import window_cache
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import (
    MAX_HISTORICAL_INTERVAL_COUNT,
    CandleInterval,
    CandleRangeReport,
)
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.strategies.models import (
    StrategyDefinition,
    reference_data_requirements,
    signal_exit_condition,
)
from thytrader.trading.geometry import entry_bar_bucket

pytestmark = pytest.mark.anyio
_ANCHOR = datetime(2026, 10, 6, 0, 44, 25, tzinfo=UTC)


class _Clock(datetime):
    """Fix the worker's observation time independently of the deployment anchor."""

    instant = _ANCHOR

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        """Return the fixed aware worker instant."""
        del tz
        return cls.instant


def _pin(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    """Observe every loader call at one fixed instant."""
    monkeypatch.setattr(_Clock, "instant", instant)
    patch_worker_global(monkeypatch, "datetime", _Clock)


def _union_strategy() -> StrategyDefinition:
    """Valid 1h/4h filter EMA(50) + extra EMA(80), with declared shared warmup coverage."""
    payload = _five_minute_htf_strategy().model_dump(mode="python")
    payload["timeframe"] = "1h"
    payload["data_requirements"]["warmup_bars"] = 50
    htf = payload["htf_filter"]
    htf["timeframe"] = "4h"
    # Validation already requires the filter dataset to cover shared extra indicators.
    htf["data_requirements"]["warmup_bars"] = 80
    htf["indicators"] = [
        {"id": "htf_sma", "kind": "ema", "input": "close", "parameters": {"period": 50}}
    ]
    payload["indicators"] = [
        *payload["indicators"],
        {
            "id": "slow_ema",
            "kind": "ema",
            "input": "close",
            "parameters": {"period": 80},
            "timeframe": "4h",
        },
    ]
    return StrategyDefinition.model_validate(payload)


async def test_shared_clock_window_covers_the_longer_indicator_without_reseeding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTF window grows to the extra indicator's warmup and keeps both seeds stable."""
    _pin(monkeypatch, _ANCHOR)
    definition = _union_strategy()
    market_data = MarketDataService(DemoMarketData())
    htf = await service._closed_htf_window(market_data, definition, deploy_anchor=_ANCHOR)

    htf_filter = definition.htf_filter
    assert htf_filter is not None
    union = service._shared_clock_union_warmup(
        definition,
        timeframe="4h",
        warmup_bars=htf_filter.data_requirements.warmup_bars,
        deploy_anchor=_ANCHOR,
    )
    htf_only = service._required_clock_warmup_bars(
        definition,
        timeframe="4h",
        warmup_bars=50,
        deploy_anchor=_ANCHOR,
    )
    bucket = entry_bar_bucket(_ANCHOR, "4h")
    assert union > htf_only
    assert htf is not None
    assert htf[0].starts_at == warmup_starts_at(bucket, union, "4h")

    fetches = 0
    provider = cast("DemoMarketData", market_data._provider)
    original_fetch = provider.get_historical_range

    async def _counting(
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        nonlocal fetches
        fetches += 1
        return await original_fetch(product_id, interval, starts_at, ends_at, now)

    monkeypatch.setattr(provider, "get_historical_range", _counting)
    extra = await service._closed_indicator_timeframe_windows(
        market_data, definition, htf, deploy_anchor=_ANCHOR
    )
    assert fetches == 0
    assert extra == {"4h": htf}

    decision = await service._closed_window(market_data, definition, deploy_anchor=_ANCHOR)
    latest = decision[1][-1]
    evaluation_end = ltf_close(latest.starts_at, definition.timeframe)
    on_union = calculate_extra_indicator_rows(
        definition,
        {"4h": htf},
        htf,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=evaluation_end,
    )
    own_start = warmup_starts_at(
        bucket,
        service._required_clock_warmup_bars(
            definition,
            timeframe="4h",
            warmup_bars=80,
            deploy_anchor=_ANCHOR,
        ),
        "4h",
    )
    on_own_window = calculate_extra_indicator_rows(
        definition,
        {"4h": tuple(candle for candle in htf if candle.starts_at >= own_start)},
        htf,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=evaluation_end,
    )
    assert on_union["4h"][htf[-1].starts_at] == on_own_window["4h"][htf[-1].starts_at]
    assert calculate_htf_indicator_rows(
        definition,
        htf,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=evaluation_end,
    ) == calculate_htf_indicator_rows(
        definition,
        tuple(candle for candle in htf if candle.starts_at >= own_start),
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=evaluation_end,
    )


def _indicators(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return the mutable indicator list of one payload."""
    return cast("list[dict[str, object]]", payload["indicators"])


def _lagged_reference(timeframe: str, reference_timeframe: str) -> StrategyDefinition:
    """Reference strategy whose EMA reads three bars of operand lag on its own clock."""
    payload = reference_payload(timeframe=timeframe, reference_timeframe=reference_timeframe)
    _indicators(payload)[2] = {
        "id": "btc_sma",
        "kind": "ema",
        "input": "close",
        "parameters": {"period": 5},
        "offset": 1,
        "source": "btc",
    }
    entry = cast("dict[str, object]", payload["entry"])
    when = cast("dict[str, object]", entry["when"])
    comparisons = cast("list[dict[str, object]]", when["all"])
    comparisons[0]["right"] = {"indicator": "btc_sma", "offset": 3}
    comparisons[0]["operator"] = "crosses_above"
    exits = cast("dict[str, object]", payload["exits"])
    exits["signal_exit"] = {
        "when": {
            "all": [
                {
                    "left": {"indicator": "btc_close"},
                    "operator": "less_than",
                    "right": {"indicator": "btc_sma", "offset": 3},
                }
            ]
        }
    }
    return StrategyDefinition.model_validate(payload)


def _first_decision(anchor: datetime, timeframe: str) -> tuple[datetime, datetime]:
    """Return the first evaluated decision bar's open and close for one anchor."""
    interval = CandleInterval(timeframe)
    start = entry_bar_bucket(anchor, timeframe) - interval.duration
    return start, ltf_close(start, timeframe)


@pytest.mark.parametrize(
    ("anchor_offset", "decision", "reference"),
    [
        (timedelta(hours=1, minutes=44), "1h", "4h"),
        (timedelta(hours=4), "1h", "4h"),
        (timedelta(minutes=44), "1h", "1h"),
        (timedelta(minutes=44), "4h", "4h"),
    ],
    ids=["mid-bucket", "rollover", "same-clock", "same-clock-coarse"],
)
async def test_reference_window_matches_required_coverage_and_gate(
    monkeypatch: pytest.MonkeyPatch,
    anchor_offset: timedelta,
    decision: str,
    reference: str,
) -> None:
    """Reference windows derive from the coverage helper and pass the entry gate."""
    anchor = datetime(2026, 10, 6, tzinfo=UTC) + anchor_offset
    _pin(monkeypatch, anchor + timedelta(hours=1))
    definition = _lagged_reference(decision, reference)
    market_data = MarketDataService(DemoMarketData())
    windows = await service._closed_reference_windows(market_data, definition, deploy_anchor=anchor)

    requirement = reference_data_requirements(definition)[0]
    candles = windows[requirement.reference_id]
    decision_start, decision_close = _first_decision(anchor, decision)
    required = closed_bar_starts(
        evaluation_starts_at=decision_start,
        evaluation_ends_at=decision_close,
        timeframe=reference,
        warmup_bars=requirement.warmup_bars,
    )
    assert candles[0].starts_at == required[0]
    assert required[-1] <= candles[-1].starts_at
    assert reference_gate(definition, windows, decision_close=decision_close) is None

    coverage_start, _coverage_end = closed_bar_required_coverage(
        evaluation_starts_at=decision_start,
        evaluation_ends_at=decision_close,
        timeframe=reference,
        warmup_bars=requirement.warmup_bars,
    )
    legacy_start = warmup_starts_at(
        entry_bar_bucket(anchor, reference), requirement.warmup_bars + 1, reference
    )
    assert candles[0].starts_at == coverage_start
    if decision == "1h" and reference == "4h" and anchor_offset == timedelta(hours=1, minutes=44):
        # Mid-bucket the blanket warmup+1 fetches one bar the gate never reads.
        assert candles[0].starts_at > legacy_start
    else:
        # The suspected reference rollover underfetch is NOT reproducible: legacy +1
        # already covers every required previous mapped bar on these legal clocks.
        assert candles[0].starts_at == legacy_start

    _product, legacy, _expected = await service._closed_window_for(
        MarketDataService(DemoMarketData()),
        product_id=requirement.product_id,
        timeframe=reference,
        warmup_bars=requirement.warmup_bars + 1,
        deploy_anchor=anchor,
    )
    _product, decision_bars, _expected = await service._closed_window(
        market_data, definition, deploy_anchor=anchor
    )
    first_bars = tuple(bar for bar in decision_bars if bar.starts_at <= decision_start)
    evidence = evaluate_latest_entry_evidence(definition, first_bars, reference_candles=windows)
    legacy_evidence = evaluate_latest_entry_evidence(
        definition, first_bars, reference_candles={requirement.reference_id: legacy}
    )
    assert evidence == legacy_evidence
    assert evidence.previous is not None
    assert evidence.current["btc_sma@3"] is not None
    assert evidence.previous["btc_sma@3"] is not None
    assert evaluate_latest_signal_exit(
        definition, first_bars, reference_candles=windows
    ) == evaluate_latest_signal_exit(
        definition, first_bars, reference_candles={requirement.reference_id: legacy}
    )


async def test_reference_window_covers_later_exit_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signal exit on a later bar reads the same reference window and stays covered."""
    anchor = datetime(2026, 10, 6, 0, 44, tzinfo=UTC)
    _pin(monkeypatch, anchor + timedelta(hours=6))
    definition = _lagged_reference("1h", "4h")
    assert signal_exit_condition(definition.exits) is not None
    market_data = MarketDataService(DemoMarketData())
    windows = await service._closed_reference_windows(market_data, definition, deploy_anchor=anchor)

    exit_bar = entry_bar_bucket(anchor, "1h") + timedelta(hours=4)
    exit_close = ltf_close(exit_bar, "1h")
    requirement = reference_data_requirements(definition)[0]
    required = closed_bar_starts(
        evaluation_starts_at=exit_bar,
        evaluation_ends_at=exit_close,
        timeframe="4h",
        warmup_bars=requirement.warmup_bars,
    )
    present = {candle.starts_at for candle in windows[requirement.reference_id]}
    assert set(required) <= present
    assert reference_gate(definition, windows, decision_close=exit_close) is None


class _DropNewest(DemoMarketData):
    """Omit the newest closed bar of one reference series."""

    def __init__(self, product_id: str, interval: CandleInterval) -> None:
        """Name the series whose newest bar disappears."""
        self._product_id = product_id
        self._interval = interval

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Drop the newest bar of the named series; everything else is complete."""
        report = await super().get_historical_range(product_id, interval, starts_at, ends_at, now)
        if product_id != self._product_id or interval is not self._interval:
            return report
        return analyze_range(report.quality.candles[:-1], interval, starts_at, ends_at, now)


async def test_reference_gate_stays_fail_closed_when_coverage_is_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing newest reference bar still blocks new entries and never fabricates one."""
    anchor = datetime(2026, 10, 6, 0, 44, tzinfo=UTC)
    _pin(monkeypatch, anchor + timedelta(hours=1))
    definition = _lagged_reference("1h", "4h")
    market_data = MarketDataService(_DropNewest("BTC-USD", CandleInterval.FOUR_HOURS))
    windows = await service._closed_reference_windows(market_data, definition, deploy_anchor=anchor)

    _decision_start, decision_close = _first_decision(anchor, "1h")
    gate = reference_gate(definition, windows, decision_close=decision_close)
    assert gate is not None
    assert all(candle.volume > 0 for candle in windows["btc"])


async def test_failed_reference_fetch_keeps_exits_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reference the provider cannot serve is empty, so the gate skips entries only."""
    anchor = datetime(2026, 10, 6, 0, 44, tzinfo=UTC)
    _pin(monkeypatch, anchor + timedelta(hours=1))
    definition = _lagged_reference("1h", "4h")

    async def _broken(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("reference feed down")

    patch_worker_global(monkeypatch, "_closed_window_for", _broken)
    windows = await service._closed_reference_windows(
        MarketDataService(DemoMarketData()), definition, deploy_anchor=anchor
    )
    assert windows == {"btc": ()}
    gate = reference_gate(
        definition, windows, decision_close=ltf_close(entry_bar_bucket(anchor, "1h"), "1h")
    )
    assert gate is not None


async def test_signal_exit_windows_reuse_the_union_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped book's exit rule reads the same union window as live evaluation."""
    _pin(monkeypatch, _ANCHOR)
    definition = _union_strategy()
    payload = definition.model_dump(mode="python")
    payload["exits"]["signal_exit"] = {
        "when": {
            "all": [
                {
                    "left": {"indicator": "slow_ema"},
                    "operator": "less_than",
                    "right": {"literal": "1"},
                }
            ]
        }
    }
    definition = StrategyDefinition.model_validate(payload)
    htf_filter = definition.htf_filter
    assert htf_filter is not None
    market_data = MarketDataService(DemoMarketData())

    htf, extra = await service._signal_exit_windows(market_data, definition, deploy_anchor=_ANCHOR)
    assert htf
    assert extra == {"4h": htf}
    assert htf[0].starts_at == warmup_starts_at(
        entry_bar_bucket(_ANCHOR, "4h"),
        service._shared_clock_union_warmup(
            definition,
            timeframe="4h",
            warmup_bars=htf_filter.data_requirements.warmup_bars,
            deploy_anchor=_ANCHOR,
        ),
        "4h",
    )
    assert all(candle.close > 0 for candle in htf)


async def test_short_shared_clock_window_is_not_blindly_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An otherwise healthy but undersized shared-clock input fetches union history."""
    _pin(monkeypatch, _ANCHOR)
    definition = _union_strategy()
    market_data = MarketDataService(DemoMarketData())
    htf = await service._closed_htf_window(market_data, definition, deploy_anchor=_ANCHOR)
    assert htf is not None
    short = htf[-51:]
    windows = await service._closed_indicator_timeframe_windows(
        market_data, definition, short, deploy_anchor=_ANCHOR
    )
    assert windows == {"4h": htf}
    assert len(windows["4h"]) > len(short)


async def test_shared_clock_as_of_does_not_reuse_future_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later loaded HTF union is cut to the earlier lockstep decision cap."""
    _pin(monkeypatch, _ANCHOR + timedelta(hours=8))
    definition = _union_strategy()
    market_data = MarketDataService(DemoMarketData())
    htf = await service._closed_htf_window(market_data, definition, deploy_anchor=_ANCHOR)
    assert htf is not None
    cap = datetime(2026, 10, 5, 20, tzinfo=UTC)
    windows = await service._closed_indicator_timeframe_windows(
        market_data, definition, htf, deploy_anchor=_ANCHOR, as_of_closed_start=cap
    )
    assert windows is not None
    assert windows["4h"][-1].starts_at == cap
    assert all(bar.starts_at <= cap for bar in windows["4h"])


async def test_htf_and_extra_cold_cache_warming_is_not_a_missing_data_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Budget exhaustion propagates typed transient progress, never None for a false pause."""
    _pin(monkeypatch, _ANCHOR)
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 2)
    monkeypatch.setattr(window_cache, "MAX_RANGE_REQUESTS_PER_CYCLE", 1)
    definition = _union_strategy()
    market_data = MarketDataService(DemoMarketData())
    with pytest.raises(WindowCacheWarmingError) as caught:
        await service._closed_htf_window(market_data, definition, deploy_anchor=_ANCHOR)
    assert caught.value.interval is CandleInterval.FOUR_HOURS
    assert caught.value.scanned_through > caught.value.starts_at
    with pytest.raises(WindowCacheWarmingError):
        await service._closed_indicator_timeframe_windows(
            market_data, definition, (), deploy_anchor=_ANCHOR
        )


async def test_reference_cold_cache_warming_remains_a_per_entry_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unfinished reference prefix cannot match, but it does not request a bot pause."""
    _pin(monkeypatch, _ANCHOR)
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 2)
    monkeypatch.setattr(window_cache, "MAX_RANGE_REQUESTS_PER_CYCLE", 1)
    definition = _lagged_reference("1h", "4h")
    windows = await service._closed_reference_windows(
        MarketDataService(DemoMarketData()), definition, deploy_anchor=_ANCHOR
    )
    assert windows == {"btc": ()}
    _first, close = _first_decision(_ANCHOR, "1h")
    assert reference_gate(definition, windows, decision_close=close) is not None


class _BoundedDemo(DemoMarketData):
    """Hermetic provider that enforces Coinbase's actual single-range hard interval bound."""

    def __init__(self) -> None:
        """Record top-level range requests without any venue connection."""
        self.calls: list[tuple[datetime, datetime]] = []

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Reject the former whole-lifetime range; accept validated small synthetic ranges."""
        if (ends_at - starts_at) // interval.duration > MAX_HISTORICAL_INTERVAL_COUNT:
            raise CoinbaseMarketDataError("Historical range exceeds the adapter request bound.")
        self.calls.append((starts_at, ends_at))
        return await super().get_historical_range(product_id, interval, starts_at, ends_at, now)


async def test_real_loader_passes_adapter_bound_after_ninety_days_without_sliding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual worker/service/cache wiring serves the exact prefix the old adapter rejected."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    interval = CandleInterval.ONE_MINUTE
    count = MAX_HISTORICAL_INTERVAL_COUNT + 1
    end = base + count * interval.duration
    now = end + timedelta(seconds=30)
    _pin(monkeypatch, now)
    provider = _BoundedDemo()
    with pytest.raises(CoinbaseMarketDataError, match="request bound"):
        await provider.get_historical_range("BTC-USD", interval, base, end, now)
    market_data = MarketDataService(provider)
    for _attempt in range(100):
        before = len(provider.calls)
        try:
            _product, candles, expected = await service._closed_window_for(
                market_data,
                product_id="BTC-USD",
                timeframe="1m",
                warmup_bars=2,
                deploy_anchor=base + 2 * interval.duration,
            )
        except WindowCacheWarmingError:
            assert len(provider.calls) - before <= window_cache.MAX_RANGE_REQUESTS_PER_CYCLE
            continue
        break
    else:
        pytest.fail("real worker loader did not finish bounded cold rebuild")
    assert len(candles) == count
    assert candles[0].starts_at == base
    assert candles[-1].starts_at == expected == end - interval.duration
    assert all((stop - start) // interval.duration <= 350 for start, stop in provider.calls)
    uncached = await DemoMarketData().get_historical_range("BTC-USD", interval, base, end, now)
    assert candles == uncached.quality.candles


def test_strategy_validation_already_rejects_underdeclared_shared_warmup() -> None:
    """The defensive union fix must not loosen accepted snapshot/research validation."""
    payload = _union_strategy().model_dump(mode="python")
    payload["htf_filter"]["data_requirements"]["warmup_bars"] = 50
    with pytest.raises(ValidationError, match="HTF warmup_bars must cover extra indicators"):
        StrategyDefinition.model_validate(payload)
