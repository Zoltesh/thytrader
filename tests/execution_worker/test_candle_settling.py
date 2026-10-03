"""Worker cycles wait without entries, maintain all books, and pause at an absolute deadline."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import Catalog, candles, paper_book, product, strategy
from thytrader.execution import candle_wait
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.models import DeploymentStatus
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct

pytestmark = pytest.mark.anyio
_CLOSE = datetime(2026, 3, 2, 13, tzinfo=UTC)


class _Clock(datetime):
    """UTC clock shared by repeated cycles; its instant may cross the deadline."""

    instant = _CLOSE + timedelta(seconds=60)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        """Return the fixed aware instant used by the publication-wait predicate."""
        del tz
        return cls.instant


@pytest.mark.parametrize(
    "multi,quiet_product", [(False, "BTC-USD"), (True, "BTC-USD"), (True, "ETH-USD")]
)
async def test_worker_waits_without_entries_then_pauses_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
    multi: bool,
    quiet_product: str,
) -> None:
    """A newest-bar wait preserves runtime state and maintains every covered instrument."""
    single = strategy()
    payload = single.model_dump(mode="python", by_alias=True)
    if multi:
        payload["additional_instruments"] = [
            {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
        ]
        payload["portfolio_limits"]["max_concurrent_positions"] = 2
    definition = StrategyDefinition.model_validate(payload)
    store, snapshot = await paper_book(definition)
    await store.save_deployment(replace(snapshot.deployment, created_at=_CLOSE - timedelta(days=2)))
    series = candles(13)
    _Clock.instant = _CLOSE + timedelta(seconds=60)
    maintained: list[str] = []

    async def window(
        market_data: MarketDataService,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
        as_of_closed_start: datetime | None = None,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Omit the primary newest bar while the secondary product is complete."""
        del market_data, timeframe, warmup_bars, deploy_anchor, as_of_closed_start
        current_product = replace(product(), product_id=product_id, base_currency=product_id[:3])
        return (
            current_product,
            series[:-1] if product_id == quiet_product else series,
            series[-1].starts_at,
        )

    async def maintenance(
        current: DeploymentSnapshot,
        *,
        strategy: StrategyDefinition,
        product: MarketProduct,
        candles: tuple[Candle, ...],
        broker: PaperBroker,
        store: ExecutionStore,
    ) -> DeploymentSnapshot:
        """Record supervision calls without changing flat fixture books."""
        del strategy, candles, broker, store
        maintained.append(product.product_id)
        return current

    monkeypatch.setattr(candle_wait, "datetime", _Clock)
    monkeypatch.setattr(service, "utc_now", lambda: _Clock.instant)
    monkeypatch.setattr(service, "_closed_window_for", window)
    monkeypatch.setattr(service, "maintain_open_inventory", maintenance)
    journal = InMemoryDecisionJournalStore()
    for elapsed in (60, 119, 120):
        _Clock.instant = _CLOSE + timedelta(seconds=elapsed)
        with decision_journal_scope(journal):
            await service._run_cycle(
                store=store,
                publication_store=Catalog(definition),
                market_data=MarketDataService(DemoMarketData()),
                paper_broker=PaperBroker(),
                live_broker=None,
                quote_reader=None,
                risk_store=None,
            )
        current = await store.get_deployment(snapshot.deployment.id)
        assert current.deployment.status is (
            DeploymentStatus.RUNNING if elapsed < 120 else DeploymentStatus.PAUSED
        )
        assert current.deployment.last_evaluated_bar is None
        assert current.intents == ()
        if elapsed < 120:
            assert any(row.skip_reason is DecisionSkipReason.BAR_SETTLING for row in journal.rows())
    assert {"BTC-USD", "ETH-USD"} <= set(maintained) if multi else "BTC-USD" in maintained
    assert any(row.skip_reason is DecisionSkipReason.DATA_GAP for row in journal.rows())
