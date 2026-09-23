"""Operator paper/live performance reports fill-ledger PnL, not fill counts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from thytrader.backtest.models import BacktestResult, BacktestSummary, EquityPoint
from thytrader.config import Settings
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
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
    from thytrader.strategies.publication import StrategyPublicationCatalog


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


class _SingleResultBacktestStore(DisabledBacktestResultStore):
    """Serve exactly one immutable result for fingerprint loads."""

    def __init__(self, result: BacktestResult) -> None:
        """Bind the served result."""
        self._result = result

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Return the bound result for its own fingerprint, else not found."""
        if result_fingerprint != self._result.strategy_fingerprint and (
            result_fingerprint != self._result.run_fingerprint
        ):
            message = "result not found"
            raise LookupError(message)
        return self._result


class _TimeframeCatalog(DisabledStrategyPublicationStore):
    """Return one published strategy whose timeframe and instrument are fixed."""

    def __init__(self, timeframe: str, product_id: str = "BTC-USD") -> None:
        """Build an in-memory published definition at the requested clock and product."""
        payload = create_reference_draft(
            now=datetime(2026, 1, 1, tzinfo=UTC),
            product_id=product_id,
        ).model_dump(mode="python")
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
    publications: StrategyPublicationCatalog | None = None,
    market_data: MarketDataService | None = None,
    backtests: DisabledBacktestResultStore | None = None,
) -> OperatorDiagnostics:
    """Build diagnostics against an in-memory execution store."""
    return OperatorDiagnostics(
        settings=Settings(_env_file=None),
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=publications or DisabledStrategyPublicationStore(),
        drafts=DisabledStrategyDraftStore(),
        backtests=backtests or DisabledBacktestResultStore(),
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
    product_id: str = "BTC-USD",
    kind: DeploymentKind = DeploymentKind.STRATEGY,
) -> Deployment:
    """Return one paper deployment with known starting cash."""
    instant = _now()
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id=product_id,
        mode=DeploymentMode.PAPER,
        status=status,
        paper_starting_cash=Decimal("10000"),
        cash=cash,
        phase=phase,
        created_at=instant,
        updated_at=instant,
        mismatch_detail=mismatch_detail,
        kind=kind,
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
        assert "0.001" in report.payload.fee_treatment
        assert "not observed Coinbase" in report.payload.fee_treatment

    asyncio.run(_scenario())


def test_zero_fees_increase_realized_pnl_versus_the_same_prices() -> None:
    """Fee-free fills at the same prices report a larger net than the fee-carrying pair."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0"))
        report = await _diagnostics(execution=store).performance(deployment_id=deployment_id)
        assert report.payload.total_net_pnl == canonical_decimal(Decimal("10"))


def test_paper_performance_fee_treatment_names_stored_rates() -> None:
    """Operator copy reports the book's paper assumptions, not Coinbase fills."""

    async def _scenario() -> None:
        store = InMemoryExecutionStore()
        instant = _now()
        deployment = Deployment(
            id=uuid4(),
            strategy_fingerprint="sha256:" + ("a" * 64),
            strategy_id=UUID(int=1),
            product_id="BTC-USD",
            mode=DeploymentMode.PAPER,
            status=DeploymentStatus.RUNNING,
            paper_starting_cash=Decimal("10000"),
            paper_maker_fee_rate=Decimal("0.0025"),
            paper_taker_fee_rate=Decimal("0.004"),
            cash=Decimal("10000"),
            phase=RuntimePhase.FLAT,
            created_at=instant,
            updated_at=instant,
        )
        await store.create_deployment(deployment)
        report = await _diagnostics(execution=store).performance(deployment_id=deployment.id)
        assert "0.0025" in report.payload.fee_treatment
        assert "0.004" in report.payload.fee_treatment
        assert "not observed Coinbase" in report.payload.fee_treatment

    asyncio.run(_scenario())

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


def test_paper_performance_reports_published_usdc_quote_currency() -> None:
    """A strategy deployment on a USDC book labels PnL in USDC, not USD."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
        snapshot = await store.get_deployment(deployment_id)
        await store.save_deployment(
            replace(snapshot.deployment, product_id="BTC-USDC", updated_at=_now())
        )
        report = await _diagnostics(
            execution=store, publications=_TimeframeCatalog("5m", product_id="BTC-USDC")
        ).performance(deployment_id=deployment_id)
        assert report.payload.currency == "USDC"
        assert report.payload.timeframe == "5m"
        assert report.payload.total_net_pnl == canonical_decimal(Decimal("9.8"))

    asyncio.run(_scenario())


def test_paper_performance_reports_published_usd_quote_currency() -> None:
    """A strategy deployment on a USD book labels PnL in USD, never USDC by default."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
        snapshot = await store.get_deployment(deployment_id)
        await store.save_deployment(
            replace(snapshot.deployment, product_id="BTC-USD", updated_at=_now())
        )
        report = await _diagnostics(
            execution=store, publications=_TimeframeCatalog("5m", product_id="BTC-USD")
        ).performance(deployment_id=deployment_id)
        assert report.payload.currency == "USD"

    asyncio.run(_scenario())


def test_discretionary_performance_derives_currency_from_product_id() -> None:
    """A discretionary book has no published instrument; the product quote is used."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
        snapshot = await store.get_deployment(deployment_id)
        await store.save_deployment(
            replace(
                snapshot.deployment,
                product_id="SOL-USDT",
                updated_at=_now(),
                strategy_fingerprint=None,
                strategy_id=None,
                kind=DeploymentKind.DISCRETIONARY,
            )
        )
        report = await _diagnostics(
            execution=store, publications=DisabledStrategyPublicationStore()
        ).performance(deployment_id=deployment_id)
        assert report.payload.currency == "USDT"
        assert report.payload.timeframe == "1h"

    asyncio.run(_scenario())


def test_paper_performance_with_unloadable_publication_reports_unknown_currency() -> None:
    """A strategy book whose publication cannot be loaded says so instead of guessing."""

    async def _scenario() -> None:
        store, deployment_id = await _round_trip_store(fee=Decimal("0.1"))
        report = await _diagnostics(
            execution=store, publications=DisabledStrategyPublicationStore()
        ).performance(deployment_id=deployment_id)
        assert report.payload.currency is None
        assert report.payload.total_net_pnl == canonical_decimal(Decimal("9.8"))
        assert any("currency" in warning.lower() for warning in report.partial_result_warnings)

    asyncio.run(_scenario())


def test_backtest_performance_carries_published_quote_currency() -> None:
    """Backtest performance evidence labels PnL with the published strategy quote."""

    async def _scenario() -> None:
        fingerprint = "sha256:" + "c" * 64
        point = EquityPoint(
            candle_starts_at=datetime(2026, 1, 1, tzinfo=UTC),
            cash="100",
            base_quantity="0",
            mark_price="100",
            equity="100",
        )
        result = BacktestResult(
            schema_version="1.0",
            engine_contract_version="thytrader-bar-backtest-v1",
            run_fingerprint=fingerprint,
            strategy_fingerprint="sha256:" + "b" * 64,
            dataset_fingerprint=fingerprint,
            signal_trace_fingerprint=fingerprint,
            trades=(),
            equity_curve=(point,),
            summary=BacktestSummary(
                initial_equity="100",
                final_equity="100",
                total_net_pnl="0",
                total_return_fraction="0",
                gross_profit="0",
                gross_loss="0",
                win_rate="0",
                trade_count=0,
                winning_trade_count=0,
                maximum_drawdown="10",
                maximum_drawdown_fraction="0.1",
                exposure_bars=0,
                evaluation_bars=1,
            ),
        )
        report = await _diagnostics(
            execution=InMemoryExecutionStore(),
            publications=_TimeframeCatalog("1h", product_id="ETH-USDC"),
            backtests=_SingleResultBacktestStore(result),
        ).performance(result_fingerprint=fingerprint)
        assert report.payload.currency == "USDC"

    asyncio.run(_scenario())
