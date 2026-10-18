"""Worker cycles wait without entries, maintain all books, and pause at an absolute deadline."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import Catalog, candles, paper_book, product, strategy
from tests.worker_patching import patch_worker_global
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution import candle_wait
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.models import DeploymentStatus, LifecycleCommand

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

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
    patch_worker_global(monkeypatch, "utc_now", lambda: _Clock.instant)
    patch_worker_global(monkeypatch, "_closed_window_for", window)
    patch_worker_global(monkeypatch, "maintain_open_inventory", maintenance)
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


_BARS = 260
"""A rising series long enough to leave warmup, so the template's entry rule fires."""


def _close(index: int) -> datetime:
    """The close of 1h bar ``index`` of the fixture series."""
    return candles(_BARS)[index].starts_at + timedelta(hours=1)


async def _gap_then_whole(
    monkeypatch: pytest.MonkeyPatch,
    *,
    before_resume: dict[str, object] | None = None,
    multi: bool = False,
    secondary_gap_in_recovery: bool = False,
    htf_gap_in_recovery: bool = False,
) -> tuple[DeploymentSnapshot, DeploymentSnapshot, InMemoryAuditEventStore]:
    """Pause on a quiet newest bar, optionally alter the book, recover, then run one more bar.

    ``secondary_gap_in_recovery`` keeps a hole in the second product's window during the
    recovering cycle; ``htf_gap_in_recovery`` makes the HTF window unavailable then. Returns the
    book after the recovery cycle and after the following bar.
    """
    definition = strategy()
    if multi:
        payload = definition.model_dump(mode="python", by_alias=True)
        payload["additional_instruments"] = [
            {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
        ]
        payload["portfolio_limits"]["max_concurrent_positions"] = 2
        definition = StrategyDefinition.model_validate(payload)
    store, snapshot = await paper_book(definition)
    full = candles(_BARS)
    await store.save_deployment(
        replace(snapshot.deployment, created_at=full[0].starts_at - timedelta(days=1))
    )
    window_candles: list[tuple[Candle, ...]] = [full[: _BARS - 3]]
    expected: list[datetime] = [full[_BARS - 3].starts_at]

    async def window(
        market_data: MarketDataService,
        *,
        product_id: str,
        timeframe: str,
        warmup_bars: int,
        deploy_anchor: datetime,
        as_of_closed_start: datetime | None = None,
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """The newest bar is missing on the first close and the window is whole afterwards."""
        del market_data, timeframe, warmup_bars, deploy_anchor, as_of_closed_start
        selected = window_candles[-1]
        if product_id == "ETH-USD" and secondary_gap_in_recovery and len(window_candles) == 2:
            selected = (*selected[:-3], *selected[-2:])
        current = replace(product(), product_id=product_id, base_currency=product_id[:3])
        return current, selected, expected[-1]

    async def htf(*args: object, **kwargs: object) -> tuple[Candle, ...] | None:
        """No HTF filter: an empty window, or unavailable during the recovering cycle."""
        del args, kwargs
        return None if htf_gap_in_recovery and len(window_candles) == 2 else ()

    monkeypatch.setattr(candle_wait, "datetime", _Clock)
    patch_worker_global(monkeypatch, "utc_now", lambda: _Clock.instant)
    patch_worker_global(monkeypatch, "_closed_window_for", window)
    patch_worker_global(monkeypatch, "_closed_htf_window", htf)
    audit = InMemoryAuditEventStore()

    async def cycle(instant: datetime) -> DeploymentSnapshot:
        _Clock.instant = instant
        with execution_audit_scope(audit), decision_journal_scope(InMemoryDecisionJournalStore()):
            await service._run_cycle(
                store=store,
                publication_store=Catalog(definition),
                market_data=MarketDataService(DemoMarketData()),
                paper_broker=PaperBroker(),
                live_broker=None,
                quote_reader=None,
                risk_store=None,
            )
        return await store.get_deployment(snapshot.deployment.id)

    # Bars 0..115 are evaluated normally; 116 is quiet at its close and the book pauses.
    await cycle(_close(_BARS - 4) + timedelta(seconds=60))
    paused = await cycle(_close(_BARS - 3) + timedelta(seconds=120))
    assert paused.deployment.status is DeploymentStatus.PAUSED
    if before_resume:
        await store.save_deployment(replace(paused.deployment, **before_resume))
    intents_before = len(paused.intents)
    # One bar later the window is whole: catch-up (116) and newest (117) are both due.
    window_candles.append(full[: _BARS - 1])
    expected.append(full[_BARS - 2].starts_at)
    recovered = await cycle(_close(_BARS - 2) + timedelta(seconds=60))
    assert len(recovered.intents) == intents_before, "no entry or reprice during catch-up"
    window_candles.append(full)
    expected.append(full[_BARS - 1].starts_at)
    following = await cycle(_close(_BARS - 1) + timedelta(seconds=60))
    return recovered, following, audit


async def test_a_data_gap_pause_clears_itself_once_the_window_is_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch-up runs paused without entries; then the book resumes and enters on the next bar."""
    recovered, following, audit = await _gap_then_whole(monkeypatch)
    assert recovered.deployment.status is DeploymentStatus.RUNNING
    assert recovered.deployment.mismatch_detail is None
    assert recovered.deployment.last_evaluated_bar == candles(_BARS)[_BARS - 2].starts_at
    actions = [event.action for event in await audit.list_recent(limit=500)]
    assert actions.count("data_gap_pause_cleared") == 1
    assert len(following.intents) > len(recovered.intents)


@pytest.mark.parametrize(
    "change",
    [
        {"mismatch_detail": None},
        {"lifecycle_command": LifecycleCommand.STOP_NEW_ENTRIES},
        {"daily_loss_latched": True},
        {"drawdown_latched": True},
        {
            "mismatch_detail": (
                "HTF market-data window is gapped or missing the latest completed HTF bar."
            )
        },
    ],
)
async def test_other_pauses_never_clear_with_the_window(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    """Operator pauses, breaker latches and other data pauses stay until the operator acts."""
    recovered, following, audit = await _gap_then_whole(monkeypatch, before_resume=change)
    assert recovered.deployment.status is DeploymentStatus.PAUSED
    assert following.deployment.status is DeploymentStatus.PAUSED
    actions = [event.action for event in await audit.list_recent(limit=500)]
    assert "data_gap_pause_cleared" not in actions


async def test_multi_instrument_books_keep_the_sticky_data_gap_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lockstep books are out of scope: the pause stays even after every window is whole."""
    recovered, following, audit = await _gap_then_whole(
        monkeypatch, multi=True, secondary_gap_in_recovery=True
    )
    assert recovered.deployment.status is DeploymentStatus.PAUSED
    assert following.deployment.status is DeploymentStatus.PAUSED
    actions = [event.action for event in await audit.list_recent(limit=500)]
    assert "data_gap_pause_cleared" not in actions


async def test_an_htf_gap_while_recovering_defers_the_clear_to_a_whole_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No clear while the HTF window is gapped; one clear after the next whole evaluation."""
    recovered, following, audit = await _gap_then_whole(monkeypatch, htf_gap_in_recovery=True)
    assert recovered.deployment.status is DeploymentStatus.PAUSED
    assert following.deployment.status is DeploymentStatus.RUNNING
    actions = [event.action for event in await audit.list_recent(limit=500)]
    assert actions.count("data_gap_pause_cleared") == 1
