"""A live discretionary book with no live broker pauses; it never runs on the paper broker."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.execution_worker.test_missing_decision_candles import _btc, _DataProvider
from tests.worker_patching import patch_worker_global
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service
from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("due", [False, True])
async def test_live_discretionary_book_without_live_broker_pauses_and_never_paper_fills(
    monkeypatch: pytest.MonkeyPatch, due: bool
) -> None:
    """L2: a candle crossing the resting live entry must not book a simulated paper fill."""
    store = InMemoryExecutionStore()
    now = utc_now()
    interval = CandleInterval.ONE_HOUR
    last_start = interval.align_closed_end(now) - interval.duration
    crossing = Candle(
        starts_at=last_start,
        open=Decimal("101"),
        high=Decimal("102"),
        low=Decimal("95"),
        close=Decimal("101"),
        volume=Decimal("1"),
    )
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        created_at=now - timedelta(days=1),
        updated_at=now,
        timeframe="1h",
        kind=DeploymentKind.DISCRETIONARY,
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
        last_evaluated_bar=None if due else last_start,
    )
    await store.create_deployment(deployment)
    await store.save_order(
        Order(
            id=uuid7(now),
            deployment_id=deployment.id,
            intent_id=uuid7(now),
            client_order_id="live-entry",
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("100"),
            status=OrderStatus.OPEN,
            created_at=now,
            updated_at=now,
            venue_order_id="live-entry",
            product_id="BTC-USD",
        )
    )

    async def _window(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return _btc(), (crossing,), last_start

    patch_worker_global(monkeypatch, "_closed_window_for", _window)

    await service._process_discretionary(
        await store.get_deployment(deployment.id),
        store=store,
        market_data=MarketDataService(_DataProvider(None)),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        user_feed_store=None,
        risk_policy=compiled_default_risk_policy(),
        memory_store=None,
    )

    current = await store.get_deployment(deployment.id)
    assert current.fills == ()
    assert current.position is None
    assert current.orders[0].status is OrderStatus.OPEN
    assert current.deployment.status is DeploymentStatus.PAUSED
    assert current.deployment.mismatch_detail == "Live broker is unavailable."
