"""Empty decision windows still reconcile live orders and do not invent exits."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Never
from uuid import UUID

import pytest

from tests.worker_patching import patch_worker_global
from thytrader.execution.broker import SubmitResult
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service
from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleQualityReport,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


class _NoPlaceBroker:
    """Live broker that reports one fill and rejects any create."""

    def __init__(self) -> None:
        """Start with no observed reads."""
        self.gets: list[str] = []
        self.placed = 0

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Fail the test if a missing window submits an order."""
        del client_order_id, product_id, side, kind, quantity, price
        del stop_trigger_price, take_profit_price
        self.placed += 1
        raise AssertionError("empty decision candles must not submit an order")

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """These tests do not cancel."""
        del venue_order_id, client_order_id
        raise AssertionError("empty decision candles must not cancel protection")

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Report the watched order filled."""
        del client_order_id
        self.gets.append(venue_order_id)
        return SubmitResult(
            status=OrderStatus.FILLED,
            venue_order_id=venue_order_id,
            filled_quantity=Decimal("0.01"),
        )

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return the one venue fill for the watched order."""
        del product_id
        return (
            Fill(
                id=uuid7(utc_now()),
                deployment_id=UUID(int=0),
                order_id=UUID(int=0),
                venue_fill_id="watched-fill",
                price=Decimal("100"),
                quantity=Decimal("0.01"),
                fee=Decimal("0.01"),
                filled_at=_WHEN,
                venue_order_id=order_id,
            ),
        )

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live books do not candle-match."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Unused."""
        del product_id, side
        return mark


def _strategy() -> StrategyDefinition:
    """Return a 1h template so the user-feed gate is not required."""
    draft = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC))
    return StrategyDefinition.model_validate(draft.model_dump(mode="python"))


def _btc() -> MarketProduct:
    """Return BTC-USD increments."""
    return MarketProduct(
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


class _DataProvider:
    """Explicit provider evidence: no candles unless the test supplies a traded bar."""

    def __init__(self, candle: Candle | None = None) -> None:
        """Bind an optional most-recent provider candle."""
        self.candle = candle

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Supply verified product constraints."""
        return (_btc(),)

    def _quality(self) -> CandleQualityReport:
        """Describe only the supplied evidence, never synthesize missing data."""
        candles = () if self.candle is None else (self.candle,)
        return CandleQualityReport(
            candles=candles,
            candle_count=len(candles),
            gap_count=0,
            missing_intervals=0,
            latest_completed_at=None if self.candle is None else self.candle.starts_at,
            is_stale=False,
        )

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Provide a fresh observation of the explicit evidence."""
        assert product_id == "BTC-USD"
        return MarketDataPreview(
            product=_btc(), interval=interval, as_of=now, quality=self._quality()
        )

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Provide no other historical data."""
        del now
        assert product_id == "BTC-USD"
        return CandleRangeReport(
            starts_at=starts_at,
            ends_at=ends_at,
            requested_candle_count=int((ends_at - starts_at) / interval.duration),
            quality=self._quality(),
            complete=False,
        )


@pytest.mark.anyio
async def test_empty_decision_window_reconciles_live_orders_without_an_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing candles pause entries and still ingest a watched live fill."""
    strategy = _strategy()
    store = InMemoryExecutionStore()
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        created_at=now,
        updated_at=now,
        timeframe="1h",
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
    )
    await store.create_deployment(deployment)
    await store.save_order(
        Order(
            id=uuid7(now),
            deployment_id=deployment.id,
            intent_id=uuid7(now),
            client_order_id="watched-entry",
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("100"),
            status=OrderStatus.OPEN,
            created_at=now,
            updated_at=now,
            venue_order_id="watched-entry",
            product_id="BTC-USD",
        )
    )
    broker = _NoPlaceBroker()

    async def _empty_window(
        market_data: MarketDataService,
        definition: StrategyDefinition,
        *,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Expose the missing newest bar without fabricating candles."""
        del market_data, definition, deploy_anchor
        return _btc(), (), _WHEN

    patch_worker_global(monkeypatch, "_closed_window", _empty_window)
    snapshot = await store.get_deployment(deployment.id)
    await service._advance_strategy(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=MarketDataService(_DataProvider()),
        paper_broker=PaperBroker(),
        live_broker=broker,
        quote_reader=None,
        risk_policy=compiled_default_risk_policy(),
        portfolio=(snapshot,),
        user_feed_store=None,
        memory_store=None,
    )
    stored = await store.get_deployment(deployment.id)
    assert broker.placed == 0
    assert broker.gets == ["watched-entry"]
    assert stored.deployment.status is DeploymentStatus.PAUSED
    assert stored.deployment.mismatch_detail == (
        "Market-data window is gapped or missing the latest closed bar."
    )
    assert stored.fills[0].economics_applied_at is not None
    assert stored.position is not None


class _ProtectionBroker(_NoPlaceBroker):
    """Only persisted-price native protection may be submitted during a no-entry wait."""

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Reject new entries and invented prices; allow exact stored protection."""
        del client_order_id, quantity, take_profit_price
        assert product_id == "BTC-USD"
        assert kind is OrderKind.TRIGGER_BRACKET
        assert side is OrderSide.SELL
        assert price == Decimal("120")
        assert stop_trigger_price == Decimal("90")
        self.placed += 1
        return SubmitResult(status=OrderStatus.OPEN, venue_order_id="protected")


def _fresh_candle() -> Candle:
    """Supply explicit traded evidence, not demo/invented fallback market data."""
    interval = CandleInterval.ONE_HOUR
    return Candle(
        starts_at=interval.align_closed_end(utc_now()) - interval.duration,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1"),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status", [DeploymentStatus.RUNNING, DeploymentStatus.PAUSED])
@pytest.mark.parametrize("clock", ["decision", "htf", "extra", "discretionary"])
async def test_cold_cache_warming_reconciles_without_pausing_or_advancing(
    monkeypatch: pytest.MonkeyPatch,
    status: DeploymentStatus,
    clock: str,
) -> None:
    """Cold rebuilds are not data gaps; deliberate pauses and evaluated cursors survive."""
    strategy = _strategy()
    store = InMemoryExecutionStore()
    now = utc_now()
    kind = DeploymentKind.DISCRETIONARY if clock == "discretionary" else DeploymentKind.STRATEGY
    detail = "Operator paused entries." if status is DeploymentStatus.PAUSED else None
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=None
        if kind is DeploymentKind.DISCRETIONARY
        else strategy_fingerprint(strategy),
        strategy_id=None if kind is DeploymentKind.DISCRETIONARY else strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=status,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        created_at=now,
        updated_at=now,
        timeframe="1h",
        kind=kind,
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
        mismatch_detail=detail,
    )
    await store.create_deployment(deployment)
    await store.save_order(
        Order(
            id=uuid7(now),
            deployment_id=deployment.id,
            intent_id=uuid7(now),
            client_order_id="watched-entry",
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("100"),
            status=OrderStatus.OPEN,
            created_at=now,
            updated_at=now,
            venue_order_id="watched-entry",
            product_id="BTC-USD",
        )
    )

    async def _warming(*args: object, **kwargs: object) -> Never:
        """Expose budget exhaustion, never partial or absent exchange history."""
        del args, kwargs
        raise WindowCacheWarmingError(
            product_id="BTC-USD",
            interval=CandleInterval.ONE_HOUR,
            starts_at=_WHEN,
            scanned_through=_WHEN,
            requested_end=now,
            range_requests=1,
        )

    loader = {
        "decision": "_closed_window",
        "htf": "_closed_htf_window",
        "extra": "_closed_indicator_timeframe_windows",
        "discretionary": "_closed_window_for",
    }[clock]
    patch_worker_global(monkeypatch, loader, _warming)
    # HTF/extra rebuilding has a verified decision candle, but no complete indicator history.
    provider = _DataProvider(_fresh_candle() if clock in {"htf", "extra"} else None)
    broker = _ProtectionBroker()
    snapshot = await store.get_deployment(deployment.id)
    if kind is DeploymentKind.DISCRETIONARY:
        await service._process_discretionary(
            snapshot,
            store=store,
            market_data=MarketDataService(provider),
            paper_broker=PaperBroker(),
            live_broker=broker,
            quote_reader=None,
            user_feed_store=None,
            risk_policy=compiled_default_risk_policy(),
            memory_store=None,
        )
    else:
        await service._advance_strategy(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=MarketDataService(provider),
            paper_broker=PaperBroker(),
            live_broker=broker,
            quote_reader=None,
            risk_policy=compiled_default_risk_policy(),
            portfolio=(snapshot,),
            user_feed_store=None,
            memory_store=None,
        )
    current = await store.get_deployment(deployment.id)
    assert "watched-entry" in broker.gets
    assert current.fills[0].economics_applied_at is not None
    assert current.position is not None
    assert current.deployment.status is status
    assert current.deployment.mismatch_detail == detail
    assert current.deployment.last_evaluated_bar is None
    assert broker.placed == (1 if clock in {"htf", "extra"} else 0)


@pytest.mark.anyio
async def test_empty_decision_window_can_maintain_protection_from_a_verified_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing signal window does not prevent native protection at persisted stop/target."""
    strategy = _strategy()
    store = InMemoryExecutionStore()
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        created_at=now,
        updated_at=now,
        timeframe="1h",
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
    )
    await store.create_deployment(deployment)
    await store.save_order(
        Order(
            id=uuid7(now),
            deployment_id=deployment.id,
            intent_id=uuid7(now),
            client_order_id="watched-entry",
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("100"),
            status=OrderStatus.OPEN,
            created_at=now,
            updated_at=now,
            venue_order_id="watched-entry",
            product_id="BTC-USD",
        )
    )

    async def _empty(
        market_data: MarketDataService,
        definition: StrategyDefinition,
        *,
        deploy_anchor: datetime,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Report a real missing decision window, independently of preview evidence."""
        del market_data, definition, deploy_anchor
        return _btc(), (), _WHEN

    patch_worker_global(monkeypatch, "_closed_window", _empty)
    broker = _ProtectionBroker()
    snapshot = await store.get_deployment(deployment.id)
    await service._advance_strategy(
        snapshot,
        strategy=strategy,
        store=store,
        market_data=MarketDataService(_DataProvider(_fresh_candle())),
        paper_broker=PaperBroker(),
        live_broker=broker,
        quote_reader=None,
        risk_policy=compiled_default_risk_policy(),
        portfolio=(snapshot,),
        user_feed_store=None,
        memory_store=None,
    )
    current = await store.get_deployment(deployment.id)
    assert broker.placed == 1
    assert current.deployment.status is DeploymentStatus.PAUSED
    assert current.deployment.mismatch_detail == service._MISSING_DECISION_CANDLES
    assert current.deployment.last_evaluated_bar is None


@pytest.mark.anyio
@pytest.mark.parametrize("kind", [DeploymentKind.STRATEGY, DeploymentKind.DISCRETIONARY])
async def test_stopped_dispatch_never_repauses_when_strategy_storage_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    kind: DeploymentKind,
) -> None:
    """Discretionary and unavailable-snapshot shutdowns must still reach stopped supervision."""
    store = InMemoryExecutionStore()
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint="sha256:" + "1" * 64 if kind is DeploymentKind.STRATEGY else None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.STOPPED,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        kind=kind,
        timeframe="1h",
    )
    await store.create_deployment(deployment)
    seen: list[DeploymentSnapshot] = []

    async def _stopped(
        snapshot: DeploymentSnapshot,
        *,
        strategy: StrategyDefinition | None,
        store: ExecutionStore,
        market_data: MarketDataService,
        paper_broker: Broker,
        live_broker: Broker | None,
    ) -> None:
        """Record the handler's durable lifecycle status without touching a venue."""
        del store, market_data, paper_broker, live_broker
        assert strategy is None
        seen.append(snapshot)

    monkeypatch.setattr(service, "_process_stopped", _stopped)
    await service._process_one(
        deployment_id=deployment.id,
        store=store,
        publication_store=DisabledStrategySnapshotStore(),
        market_data=MarketDataService(_DataProvider()),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        risk_policy=compiled_default_risk_policy(),
        portfolio=(),
        user_feed_store=None,
        memory_store=None,
    )
    assert len(seen) == 1
    assert seen[0].deployment.status is DeploymentStatus.STOPPED
    if kind is DeploymentKind.STRATEGY:
        assert "snapshot is unavailable" in (seen[0].deployment.mismatch_detail or "")
    else:
        assert seen[0].deployment.mismatch_detail is None
