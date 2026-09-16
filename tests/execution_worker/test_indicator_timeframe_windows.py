"""Paper/live extra-TF windows compose with the shipped HTF fetch path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from tests.execution.test_htf_filter import (
    _candle,
    _five_minute_htf_strategy,
    _htf_hours,
)
from tests.execution.test_indicator_timeframes import _five_minute_extra_tf_strategy, _product
from thytrader.execution_worker import service as execution_worker
from thytrader.execution_worker.service import _closed_indicator_timeframe_windows
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService


@pytest.mark.anyio
async def test_extra_windows_reuse_htf_when_clocks_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An extra TF equal to htf_filter.timeframe must not fetch a second HTF window."""
    payload = _five_minute_htf_strategy().model_dump(mode="python")
    indicators = list(payload["indicators"])
    indicators.append(
        {
            "id": "hour_sma",
            "kind": "sma",
            "input": "close",
            "parameters": {"period": 2},
            "timeframe": "1h",
        }
    )
    payload["indicators"] = indicators
    strategy = StrategyDefinition.model_validate(payload)
    htf = _htf_hours(include_partial_noon=False)

    async def _forbidden_fetch(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shared extra-TF clock must reuse the HTF window")

    monkeypatch.setattr(execution_worker, "_closed_window_for", _forbidden_fetch)
    windows = await _closed_indicator_timeframe_windows(
        cast("MarketDataService", object()),
        strategy,
        htf,
        deploy_anchor=datetime(2026, 7, 10, tzinfo=UTC),
    )
    assert windows == {"1h": htf}


@pytest.mark.anyio
async def test_extra_windows_pause_when_latest_completed_bar_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gapped extra-TF coverage is None so the worker pauses instead of evaluating."""
    strategy = _five_minute_extra_tf_strategy()
    expected_last = datetime(2026, 7, 10, 10, tzinfo=UTC)

    async def _gapped_window(
        _market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        del product_id, timeframe, warmup_bars
        candles = (
            _candle(datetime(2026, 7, 10, 8, tzinfo=UTC), "1"),
            _candle(expected_last, "50"),
        )
        return _product(), candles, expected_last

    monkeypatch.setattr(execution_worker, "_closed_window_for", _gapped_window)
    windows = await _closed_indicator_timeframe_windows(
        cast("MarketDataService", object()),
        strategy,
        (),
        deploy_anchor=datetime(2026, 7, 10, tzinfo=UTC),
    )
    assert windows is None


@pytest.mark.anyio
async def test_extra_windows_return_complete_unbound_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbound extra TFs load complete-only last-completed bars through the HTF helper."""
    strategy = _five_minute_extra_tf_strategy()
    extra = _htf_hours(include_partial_noon=False)

    async def _complete_window(
        _market_data: object,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        del product_id
        assert timeframe == "1h"
        assert warmup_bars == 2
        return _product(), extra, extra[-1].starts_at

    monkeypatch.setattr(execution_worker, "_closed_window_for", _complete_window)
    windows = await _closed_indicator_timeframe_windows(
        cast("MarketDataService", object()),
        strategy,
        (),
        deploy_anchor=datetime(2026, 7, 10, tzinfo=UTC),
    )
    assert windows == {"1h": extra}
