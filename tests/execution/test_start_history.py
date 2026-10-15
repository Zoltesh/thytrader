"""A start refuses markets younger than the strategy's warmup (ADR 0140)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import Catalog
from tests.market_history import ListedHistory, listed_history
from tests.strategies.reference_support import reference_strategy
from thytrader.execution.clock_windows import deployment_clock_windows
from thytrader.execution.service import create_deployment
from thytrader.execution.start_history import (
    HISTORY_TOO_SHORT,
    HISTORY_UNCONFIRMED,
    require_start_history,
    start_history_shortfalls,
)
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.warmup_policy import with_convergence_warmup
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, ExecutionConflictError

if TYPE_CHECKING:
    from thytrader.market_data.models import CandleInterval, CandleRangeReport

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 10, 15, 2, 17, tzinfo=UTC)


def _ema200_2h() -> StrategyDefinition:
    """A converged 2h EMA trend whose slow EMA(200) needs 891 bars (about 74 days)."""
    draft = create_template_strategy(now=_NOW, product_id="BTC-USD", timeframe="2h")
    payload = draft.model_dump(mode="python")
    for indicator in payload["indicators"]:
        if indicator["kind"] == "ema":
            indicator["parameters"] = {"period": 200}
            break
    payload["data_requirements"] = {**payload["data_requirements"], "warmup_bars": 200}
    payload["data_requirements"].pop("warmup_policy")
    return with_convergence_warmup(StrategyDefinition.model_validate(payload))


async def test_a_market_older_than_every_window_passes() -> None:
    """A listing long before each window start leaves nothing short."""
    strategy = _ema200_2h()
    await require_start_history(listed_history(), strategy, now=_NOW)


async def test_a_market_younger_than_the_warmup_is_refused() -> None:
    """Thirty days of 2h history cannot seed an EMA(200) that needs about 74 days."""
    strategy = _ema200_2h()
    (decision,) = deployment_clock_windows(strategy, deploy_anchor=_NOW)
    assert decision.warmup_bars == strategy.data_requirements.warmup_bars
    young = listed_history(listed_at=_NOW - timedelta(days=30))
    with pytest.raises(ExecutionConflictError) as caught:
        await require_start_history(young, strategy, now=_NOW)
    message = str(caught.value)
    assert message.startswith(f"{HISTORY_TOO_SHORT}: ")
    assert (
        f"decision clock BTC-USD 2h needs {decision.warmup_bars} bars from "
        f"{decision.starts_at:%Y-%m-%d %H:%M} UTC" in message
    )
    assert "Nothing was started" in message


async def test_a_listing_exactly_at_the_window_start_passes() -> None:
    """The first window bar itself is enough history."""
    strategy = _ema200_2h()
    (decision,) = deployment_clock_windows(strategy, deploy_anchor=_NOW)
    exact = listed_history(listed_at=decision.starts_at)
    await require_start_history(exact, strategy, now=_NOW)
    late = listed_history(listed_at=decision.starts_at + timedelta(hours=2))
    assert await start_history_shortfalls(late, strategy, now=_NOW) == (decision,)


async def test_a_quiet_first_bar_still_counts_as_listed() -> None:
    """Earlier candles prove the listing even when the window's first bar had no trade."""
    strategy = _ema200_2h()
    (decision,) = deployment_clock_windows(strategy, deploy_anchor=_NOW)
    quiet = listed_history(quiet=frozenset({decision.starts_at}))
    await require_start_history(quiet, strategy, now=_NOW)


class _Unreadable(ListedHistory):
    """A venue whose history request fails."""

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        del product_id, interval, starts_at, ends_at, now
        raise ValueError("venue unavailable")


async def test_an_unreadable_history_refuses_the_start() -> None:
    """History that cannot be read is not assumed to exist."""
    with pytest.raises(ExecutionConflictError) as caught:
        await require_start_history(MarketDataService(_Unreadable()), _ema200_2h(), now=_NOW)
    assert str(caught.value).startswith(f"{HISTORY_UNCONFIRMED}: could not read BTC-USD 2h")


async def test_a_young_reference_series_is_named() -> None:
    """Every window counts, references included; each series is checked once."""
    strategy = with_convergence_warmup(reference_strategy())
    windows = deployment_clock_windows(strategy, deploy_anchor=_NOW)
    assert [(item.role, item.product_id, item.timeframe) for item in windows] == [
        ("decision clock", "ETH-USD", "1h"),
        ("reference btc", "BTC-USD", "1d"),
    ]
    young = listed_history(listed_at=windows[1].starts_at + timedelta(days=1))
    (short,) = await start_history_shortfalls(young, strategy, now=_NOW)
    assert short.role == "reference btc"


async def test_create_deployment_refuses_a_young_market_and_creates_nothing() -> None:
    """With market data, a start checks history after every other start check."""
    strategy = _ema200_2h()
    store = InMemoryExecutionStore()
    with pytest.raises(ExecutionConflictError, match=HISTORY_TOO_SHORT):
        await create_deployment(
            store=store,
            publication_store=Catalog(strategy),
            strategy_fingerprint=strategy_fingerprint(strategy),
            mode=DeploymentMode.PAPER,
            paper_starting_cash=Decimal("10000"),
            live_allowed=False,
            market_data=listed_history(listed_at=datetime.now(UTC) - timedelta(days=30)),
        )
    assert await store.list_deployments() == ()
