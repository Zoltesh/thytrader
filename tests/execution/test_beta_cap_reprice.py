"""A reprice is re-checked against the BTC-beta cap (ADR 0125).

The loop runs inside ``risk_market_data_scope``, exactly as the execution worker's
``_process_one`` binds it around strategy processing, which is where reprices happen.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.beta_support import DailyBetaProvider, beta_policy
from tests.execution.test_loop import _always_entry_strategy, _candles, _running_snapshot
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.risk.accounting_evidence import risk_market_data_scope
from thytrader.risk.beta_evidence import DEFAULT_BETA_CACHE
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentSnapshot,
    DeploymentStatus,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _fresh_beta_cache() -> Iterator[None]:
    """The loop uses the process-wide β cache; isolate it per test."""
    DEFAULT_BETA_CACHE.clear()
    yield
    DEFAULT_BETA_CACHE.clear()


def _eth() -> MarketProduct:
    """ETH-USD with increments fine enough for the 100-quote test candles."""
    return MarketProduct(
        product_id="ETH-USD",
        base_currency="ETH",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


def _reprice_strategy() -> StrategyDefinition:
    """The always-entry loop strategy on ETH-USD, repricing an unfilled entry."""
    payload = _always_entry_strategy(on_unfilled_entry="reprice").model_dump(
        mode="python", by_alias=True
    )
    payload["instrument"] = {
        "product_id": "ETH-USD",
        "base_currency": "ETH",
        "quote_currency": "USD",
    }
    return StrategyDefinition.model_validate(payload)


async def _entry_then_wait(
    provider: DailyBetaProvider, *, fail_before_reprice: bool
) -> tuple[DeploymentSnapshot, str]:
    """Rest an entry, then close two bars above it so the wait expires and it reprices."""
    store = InMemoryExecutionStore()
    strategy = _reprice_strategy()
    snapshot = await _running_snapshot(store, strategy)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    warmup = _candles(30, low_offset=Decimal("0"))
    policy = beta_policy()
    with risk_market_data_scope(provider.service()):
        current = await process_closed_bar(
            snapshot,
            strategy=strategy,
            product=_eth(),
            candles=warmup,
            broker=PaperBroker(),
            store=store,
            risk_policy=policy,
        )
        original = str(current.orders[0].id)
        if fail_before_reprice:
            DEFAULT_BETA_CACHE.clear()
            provider.failing.add("ETH-USD")
        last = warmup[-1]
        bars: list[Candle] = []
        for offset in (1, 2):
            bars.append(
                Candle(
                    starts_at=last.starts_at + timedelta(hours=offset),
                    open=last.close + Decimal(10),
                    high=last.close + Decimal(12),
                    low=last.close + Decimal(9),
                    close=last.close + Decimal(10),
                    volume=Decimal(10),
                )
            )
            current = await process_closed_bar(
                current,
                strategy=strategy,
                product=_eth(),
                candles=warmup + tuple(bars),
                broker=PaperBroker(),
                store=store,
                risk_policy=policy,
            )
    return current, original


async def test_reprice_is_admitted_with_fresh_beta_evidence() -> None:
    """With ETH history readable the reprice rests a replacement entry."""
    provider = DailyBetaProvider(betas={"ETH-USD": 1.2})

    current, original = await _entry_then_wait(provider, fail_before_reprice=False)

    working = [order for order in current.orders if order.status is OrderStatus.OPEN]
    assert len(working) == 1
    assert str(working[0].id) != original
    assert "ETH-USD" in provider.requested_products()


async def test_reprice_without_beta_evidence_is_skipped_not_paused() -> None:
    """When ETH history becomes unreadable the reprice is denied; the bot keeps running."""
    provider = DailyBetaProvider(betas={"ETH-USD": 1.2})

    current, _original = await _entry_then_wait(provider, fail_before_reprice=True)

    assert [order for order in current.orders if order.status is OrderStatus.OPEN] == []
    assert current.deployment.status is DeploymentStatus.RUNNING
    assert current.deployment.phase is RuntimePhase.FLAT
    assert current.deployment.mismatch_detail is None
