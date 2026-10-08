"""Economic guards remain active when already approved entries are repriced."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.backtest.test_kernel import _WARMUP, _bars, _run, _strategy
from tests.execution.test_loop import _always_entry_strategy, _candles, _product, _running_snapshot
from thytrader.backtest.kernel import simulate_backtest_with_diagnostics
from thytrader.exchanges.fees import FeeProfile
from thytrader.execution.economics import EconomicEntryGuard
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, OrderStatus


def test_simulated_reprice_expires_when_the_new_price_loses_its_net_margin() -> None:
    """A once-profitable signal cannot bypass its frozen hurdle by repricing later."""
    original = _strategy()
    strategy = original.model_copy(
        update={
            "entry": original.entry.model_copy(
                update={
                    "economic_guard": EconomicEntryGuard(minimum_net_target_return_fraction="0.6")
                }
            ),
            "execution": original.execution.model_copy(
                update={"on_unfilled_entry": "reprice", "max_entry_wait_bars": 2}
            ),
        }
    )
    candles = _bars(
        *_WARMUP,
        ("14", "15", "13", "14"),
        ("16", "17", "15", "16"),
        ("18", "19", "17", "18"),
        ("18", "19", "17.5", "18"),
        ("18", "19", "17.5", "18"),
    )
    result, diagnostics = simulate_backtest_with_diagnostics(
        _run(strategy, evaluation_hours=4), strategy, candles
    )
    assert diagnostics.entries_rested == 1
    assert diagnostics.entries_expired == 1
    assert diagnostics.entries_repriced == 0
    assert result.summary.trade_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize("observed_fee", [False, True])
async def test_live_repricing_carries_the_observed_tier_end_to_end(observed_fee: bool) -> None:
    """Use the real loop, in-memory store and paper broker; no exchange or account is contacted."""
    original = _always_entry_strategy(on_unfilled_entry="reprice")
    strategy = original.model_copy(
        update={
            "entry": original.entry.model_copy(
                update={
                    "economic_guard": EconomicEntryGuard(minimum_net_target_return_fraction="0")
                }
            )
        }
    )
    store = InMemoryExecutionStore()
    broker = PaperBroker()
    snapshot = await _running_snapshot(store, strategy, paper_maker_fee_rate=Decimal("0.005"))
    first = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=_candles(30, low_offset=Decimal("0.01")),
        broker=broker,
        store=store,
    )
    assert len(first.orders) == 1
    live = replace(
        first.deployment,
        mode=DeploymentMode.LIVE,
        allocated_capital=Decimal("10000"),
        venue_available_quote=Decimal("10000"),
        pending_entry_bars=1,
    )
    await store.save_deployment(live)
    first = await store.get_deployment(live.id)
    profile = FeeProfile(
        maker_fee_rate=Decimal("0.005"),
        taker_fee_rate=Decimal("0.009"),
        usd_volume_30d=Decimal("0"),
        fee_tier="test",
        as_of=datetime.now(UTC),
    )
    after = await process_closed_bar(
        first,
        strategy=strategy,
        product=_product(),
        candles=_candles(31, low_offset=Decimal("0.01")),
        broker=broker,
        store=store,
        fee_profile=profile if observed_fee else None,
    )
    assert after.orders[0].status is OrderStatus.CANCELED
    assert len(after.orders) == (2 if observed_fee else 1)
    assert sum(order.status is OrderStatus.OPEN for order in after.orders) == int(observed_fee)
