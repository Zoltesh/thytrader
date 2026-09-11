"""Operator paper/live performance reports fill-ledger PnL, not fill counts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from thytrader.config import Settings
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
    with_runtime,
)
from thytrader.market_data.models import Candle, CandleInterval, MarketDataPreview, MarketProduct
from thytrader.market_data.quality import analyze_candles
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.operator.models import ReportStatus
from thytrader.operator.service import OperatorDiagnostics
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.research.indicators import canonical_decimal
from thytrader.strategies.authoring import DisabledStrategyDraftStore, create_reference_draft
from thytrader.strategies.models import StrategyDefinition
from thytrader.strategies.publication import DisabledStrategyPublicationStore, PublishedStrategy

if TYPE_CHECKING:
    from thytrader.execution.store import ExecutionStore
    from thytrader.strategies.publication import StrategyPublicationStore


class _CloseProvider:
    """Return one closed candle at a configured last close."""

    def __init__(self, close: Decimal) -> None:
        """Bind the mark used as the last completed close."""
        self._close = close

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Return a one-bar preview whose close is the configured mark."""
        del product_id
        product = MarketProduct(
            "BTC-USD",
            "BTC",
            "USD",
            Decimal("0.01"),
            Decimal("0.00000001"),
            Decimal("0.01"),
            Decimal("0.0001"),
            Decimal("1"),
            True,
        )
        end = interval.align_closed_end(now)
        start = end - interval.duration
        candle = Candle(start, self._close, self._close, self._close, self._close, Decimal("1"))
        return MarketDataPreview(product, interval, now, analyze_candles((candle,), interval, now))

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Catalog is unused by deployment performance."""
        return ()


class _TimeframeCatalog(DisabledStrategyPublicationStore):
    """Return one published strategy whose timeframe is fixed."""

    def __init__(self, timeframe: str) -> None:
        """Build an in-memory published definition at the requested clock."""
        payload = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC)).model_dump(
            mode="python"
        )
        payload["timeframe"] = timeframe
        self._published = PublishedStrategy(
            strategy_fingerprint="sha256:" + ("b" * 64),
            definition=StrategyDefinition.model_validate(payload),
        )

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Ignore the fingerprint and return the fixed published strategy."""
        del strategy_fingerprint_value
        return self._published


def _diagnostics(
    *,
    execution: ExecutionStore,
    publications: StrategyPublicationStore | None = None,
    market_data: MarketDataService | None = None,
) -> OperatorDiagnostics:
    """Build diagnostics against an in-memory execution store."""
    return OperatorDiagnostics(
        settings=Settings(_env_file=None),
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=publications or DisabledStrategyPublicationStore(),
        drafts=DisabledStrategyDraftStore(),
        backtests=DisabledBacktestResultStore(),
        execution=execution,
        audit=InMemoryAuditEventStore(),
        market_data=market_data,
    )


def _now() -> datetime:
    """Return a UTC instant used by fixtures."""
    return datetime(2026, 1, 1, tzinfo=UTC)


def _deployment(
    *,
    cash: Decimal,
    phase: RuntimePhase = RuntimePhase.FLAT,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    mismatch_detail: str | None = None,
) -> Deployment:
    """Return one paper deployment with known starting cash."""
    instant = _now()
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=status,
        paper_starting_cash=Decimal("10000"),
        cash=cash,
        phase=phase,
        created_at=instant,
        updated_at=instant,
        mismatch_detail=mismatch_detail,
    )


def _order(
    *,
    deployment_id: UUID,
    side: OrderSide,
    kind: OrderKind,
    price: Decimal,
    client_order_id: str,
) -> Order:
    """Return one filled order used to join ledger sides."""
    instant = _now()
    return Order(
        id=uuid4(),
        deployment_id=deployment_id,
        intent_id=uuid4(),
        client_order_id=client_order_id,
        side=side,
        kind=kind,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=instant,
        updated_at=instant,
        price=price,
        filled_quantity=Decimal("1"),
    )


def _fill(*, deployment_id: UUID, order_id: UUID, price: Decimal, fee: Decimal, hour: int) -> Fill:
    """Return one fill at a deterministic UTC hour."""
    return Fill(
        id=uuid4(),
        deployment_id=deployment_id,
        order_id=order_id,
        venue_fill_id=f"{order_id}:{hour}",
        price=price,
        quantity=Decimal("1"),
        fee=fee,
        filled_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
    )


async def _round_trip_store(*, fee: Decimal) -> tuple[InMemoryExecutionStore, UUID]:
    """Persist one buy/sell pair and return the store plus deployment id."""
    store = InMemoryExecutionStore()
    cash = Decimal("10000") - Decimal("100") - fee + Decimal("110") - fee
    deployment = _deployment(cash=cash)
    await store.create_deployment(deployment)
    buy = _order(
        deployment_id=deployment.id,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        price=Decimal("100"),
        client_order_id="buy",
    )
    sell = _order(
        deployment_id=deployment.id,
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        price=Decimal("110"),
        client_order_id="sell",
    )
    await store.save_order(buy)
    await store.save_order(sell)
    await store.save_fill(
        _fill(
            deployment_id=deployment.id,
            order_id=buy.id,
            price=Decimal("100"),
            fee=fee,
            hour=1,
        )
    )
    await store.save_fill(
        _fill(
            deployment_id=deployment.id,
            order_id=sell.id,
            price=Decimal("110"),
            fee=fee,
            hour=2,
        )
    )
    return store, deployment.id


def test_two_fills_report_realized_pnl_and_strategy_timeframe() -> None:
    """A closed round trip reports non-null PnL on the published strategy clock."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
        report = await _diagnostics(
            execution=store, publications=_TimeframeCatalog("5m")
        ).performance(deployment_id=deployment_id)
        assert report.payload.mode == "paper"
        assert report.payload.timeframe == "5m"
        assert report.payload.trade_count == 1
        assert report.payload.total_net_pnl == canonical_decimal(Decimal("9.8"))
        assert report.overall_status is ReportStatus.HEALTHY
        assert report.components[0].reason_code == "FILL_LEDGER"
        assert report.payload.total_return_fraction is not None
        assert report.payload.maximum_drawdown_fraction is not None

    asyncio.run(_scenario())


def test_zero_fees_increase_realized_pnl_versus_the_same_prices() -> None:
    """Fee-free fills at the same prices report a larger net than the fee-carrying pair."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0"))
        report = await _diagnostics(execution=store).performance(deployment_id=deployment_id)
        assert report.payload.total_net_pnl == canonical_decimal(Decimal("10"))

    asyncio.run(_scenario())


def test_open_position_without_mark_omits_total_pnl() -> None:
    """Open inventory with no last close stays DEGRADED and does not invent equity."""

    async def _scenario() -> None:
        store = InMemoryExecutionStore()
        deployment = _deployment(cash=Decimal("9899.9"), phase=RuntimePhase.OPEN)
        await store.create_deployment(deployment)
        buy = _order(
            deployment_id=deployment.id,
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            price=Decimal("100"),
            client_order_id="buy",
        )
        await store.save_order(buy)
        await store.save_fill(
            _fill(
                deployment_id=deployment.id,
                order_id=buy.id,
                price=Decimal("100"),
                fee=Decimal("0.1"),
                hour=1,
            )
        )
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=Decimal("1"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=Decimal("120"),
                entered_bar=datetime(2026, 1, 1, 1, tzinfo=UTC),
                updated_at=_now(),
            ),
            deployment_id=deployment.id,
        )
        report = await _diagnostics(execution=store).performance(deployment_id=deployment.id)
        assert report.payload.total_net_pnl is None
        assert report.overall_status is ReportStatus.DEGRADED
        assert report.components[0].reason_code == "MISSING_MARK"
        assert report.payload.trade_count == 0

    asyncio.run(_scenario())


def test_open_position_with_last_close_includes_unrealized() -> None:
    """A disclosed last close marks remaining quantity and reports total PnL."""

    async def _scenario() -> None:
        store = InMemoryExecutionStore()
        deployment = _deployment(cash=Decimal("9899.9"), phase=RuntimePhase.OPEN)
        await store.create_deployment(deployment)
        buy = _order(
            deployment_id=deployment.id,
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            price=Decimal("100"),
            client_order_id="buy",
        )
        await store.save_order(buy)
        await store.save_fill(
            _fill(
                deployment_id=deployment.id,
                order_id=buy.id,
                price=Decimal("100"),
                fee=Decimal("0.1"),
                hour=1,
            )
        )
        await store.save_position(
            Position(
                deployment_id=deployment.id,
                quantity=Decimal("1"),
                entry_price=Decimal("100"),
                stop_price=Decimal("90"),
                target_price=Decimal("120"),
                entered_bar=datetime(2026, 1, 1, 1, tzinfo=UTC),
                updated_at=_now(),
            ),
            deployment_id=deployment.id,
        )
        market_data = MarketDataService(_CloseProvider(Decimal("105")))
        report = await _diagnostics(execution=store, market_data=market_data).performance(
            deployment_id=deployment.id
        )
        assert report.payload.total_net_pnl == canonical_decimal(Decimal("4.9"))
        assert report.overall_status is ReportStatus.HEALTHY
        assert report.components[0].reason_code == "FILL_LEDGER"

    asyncio.run(_scenario())


def test_paused_mismatch_still_reports_realized_pnl() -> None:
    """Pause or mismatch degrades the report without inventing or dropping recorded PnL."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
        snapshot = await store.get_deployment(deployment_id)
        await store.save_deployment(
            with_runtime(
                snapshot.deployment,
                updated_at=snapshot.deployment.updated_at,
                status=DeploymentStatus.PAUSED,
                mismatch_detail="Local fills do not match venue coverage.",
            )
        )
        report = await _diagnostics(execution=store).performance(deployment_id=deployment_id)
        assert report.payload.total_net_pnl == canonical_decimal(Decimal("9.8"))
        assert report.overall_status is ReportStatus.DEGRADED
        assert report.components[0].reason_code == "STATE_MISMATCH"

    asyncio.run(_scenario())
