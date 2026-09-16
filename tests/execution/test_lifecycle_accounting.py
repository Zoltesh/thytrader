"""F08/F09/F10/F12/F13/F21/F22: leases, capital, lifecycle, freshness, and replay."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.execution.capital import (
    apply_venue_quote,
    daily_pnl_from_day_open,
    live_sizing_cash,
    refresh_performance,
)
from thytrader.execution.freshness import entry_prerequisites, signal_still_valid
from thytrader.execution.lifecycle import (
    can_reprice_risk_up,
    command_for_status,
    entries_allowed,
    occupies_risk,
)
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    LifecycleCommand,
    Position,
    RuntimePhase,
)
from thytrader.execution_worker.service import new_closed_bars
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.risk.models import RiskDecision, RiskReasonCode

_NOW = datetime(2026, 1, 2, 15, tzinfo=UTC)


def _paper_deployment() -> Deployment:
    """Return a paper book with documented fee assumptions."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal("10000"),
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
        timeframe="1h",
    )


def _product(*, enabled: bool = True) -> MarketProduct:
    """Return a USD spot product."""
    return MarketProduct(
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=enabled,
    )


def _candle(*, hour: int, day: int = 1) -> Candle:
    """Return one complete hourly candle."""
    start = datetime(2026, 1, day, hour, tzinfo=UTC)
    close = Decimal("100") + Decimal(hour)
    return Candle(
        starts_at=start,
        open=close,
        high=close + Decimal("1"),
        low=close - Decimal("1"),
        close=close,
        volume=Decimal("10"),
    )


def test_venue_quote_does_not_overwrite_ledger_cash() -> None:
    """F12: observed venue available must not stamp strategy cash."""
    live = replace(
        _paper_deployment(),
        mode=DeploymentMode.LIVE,
        cash=Decimal("250"),
        paper_starting_cash=None,
        paper_maker_fee_rate=None,
        paper_taker_fee_rate=None,
        allocated_capital=Decimal("1000"),
    )
    stamped = apply_venue_quote(live, available=Decimal("50000"), now=_NOW)
    assert stamped.cash == Decimal("250")
    assert stamped.venue_available_quote == Decimal("50000")
    unknown = apply_venue_quote(stamped, available=None, now=_NOW)
    assert unknown.venue_available_quote is None
    assert live_sizing_cash(unknown) == Decimal("1000")


def test_unknown_live_balance_without_allocation_disables_entries() -> None:
    """F12: unknown venue quote with no allocation denies live sizing cash."""
    live = replace(
        _paper_deployment(),
        mode=DeploymentMode.LIVE,
        cash=Decimal("0"),
        paper_starting_cash=None,
        paper_maker_fee_rate=None,
        paper_taker_fee_rate=None,
        allocated_capital=None,
        venue_available_quote=None,
    )
    assert live_sizing_cash(live) is None


def test_losing_live_ledger_keeps_initial_equity_baseline() -> None:
    """F12/P13: a losing round trip must not report zero drawdown versus a zero baseline."""
    deployment = replace(
        _paper_deployment(),
        cash=Decimal("9000"),
        initial_equity=Decimal("10000"),
        baseline_equity=Decimal("10000"),
        high_water_mark_equity=Decimal("10000"),
        utc_day_open_equity=Decimal("10000"),
        utc_day_open_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    refreshed = refresh_performance(
        DeploymentSnapshot(deployment=deployment), mark_price=Decimal("100"), now=_NOW
    )
    assert refreshed.performance_equity == Decimal("9000")
    assert daily_pnl_from_day_open(refreshed, equity=Decimal("9000")) == Decimal("-1000")
    assert refreshed.high_water_mark_equity == Decimal("10000")


def test_utc_midnight_rolls_day_open_without_trades() -> None:
    """F13: overnight inventory snapshots a new UTC day-open equity."""
    deployment = replace(
        _paper_deployment(),
        cash=Decimal("8000"),
        performance_equity=Decimal("8000"),
        utc_day_open_equity=Decimal("10000"),
        utc_day_open_at=datetime(2026, 1, 1, tzinfo=UTC),
        high_water_mark_equity=Decimal("11000"),
        initial_equity=Decimal("10000"),
    )
    next_day = datetime(2026, 1, 2, 0, 5, tzinfo=UTC)
    refreshed = refresh_performance(
        DeploymentSnapshot(deployment=deployment), mark_price=None, now=next_day
    )
    assert refreshed.utc_day_open_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert refreshed.utc_day_open_equity == Decimal("8000")
    assert refreshed.high_water_mark_equity == Decimal("11000")


def test_paused_and_latched_books_cannot_reprice_risk_up() -> None:
    """F10: paused and latched books must not reprice remaining entries."""
    running = _paper_deployment()
    paused = replace(
        running,
        status=DeploymentStatus.PAUSED,
        lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
    )
    latched = replace(running, daily_loss_latched=True)
    assert can_reprice_risk_up(running) is True
    assert can_reprice_risk_up(paused) is False
    assert can_reprice_risk_up(latched) is False
    assert entries_allowed(paused) is False


def test_stop_defaults_to_managed_shutdown_not_flatten() -> None:
    """F09: HTTP stop keeps residual occupancy; flatten is explicit."""
    assert command_for_status(DeploymentStatus.STOPPED) is LifecycleCommand.MANAGED_SHUTDOWN
    assert command_for_status(DeploymentStatus.STOPPED, flatten=True) is LifecycleCommand.FLATTEN
    assert command_for_status(DeploymentStatus.PAUSED) is LifecycleCommand.STOP_NEW_ENTRIES
    stopped_open = DeploymentSnapshot(
        deployment=replace(
            _paper_deployment(), status=DeploymentStatus.STOPPED, phase=RuntimePhase.OPEN
        ),
        position=Position(
            deployment_id=uuid4(),
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_NOW,
            updated_at=_NOW,
        ),
    )
    assert occupies_risk(stopped_open) is True


def test_stale_mark_and_disabled_product_deny_entries() -> None:
    """F22: day-old closes and disabled products fail closed."""
    candle = _candle(hour=0)
    now = datetime(2026, 1, 3, tzinfo=UTC)
    stale = entry_prerequisites(product=_product(), candle=candle, now=now, timeframe="1h")
    disabled = entry_prerequisites(
        product=_product(enabled=False),
        candle=_candle(hour=14),
        now=_NOW,
        timeframe="1h",
    )
    assert stale.decision is RiskDecision.DENY
    assert stale.reason_code is RiskReasonCode.STALE_MARK
    assert disabled.reason_code is RiskReasonCode.PRODUCT_DISABLED


def test_historical_signals_expire_and_latest_bar_may_enter() -> None:
    """F21: only the latest still-valid closed bar may enter after downtime."""
    candles = tuple(_candle(hour=index) for index in range(4))
    due = new_closed_bars(
        candles,
        last_evaluated_bar=candles[0].starts_at,
        expected_last_start=candles[-1].starts_at,
        bar_duration=timedelta(hours=1),
    )
    assert due is not None
    assert [item.starts_at for item in due] == [item.starts_at for item in candles[1:]]
    now = datetime(2026, 1, 1, 3, 10, tzinfo=UTC)
    old = signal_still_valid(candle=candles[1], timeframe="1h", now=now)
    latest = signal_still_valid(candle=candles[-1], timeframe="1h", now=now)
    assert old is False
    assert latest is True


@pytest.mark.anyio
async def test_worker_lease_serializes_and_expires() -> None:
    """F08: a live holder wins; an expired lease can be taken over."""
    store = InMemoryExecutionStore()
    deployment = replace(
        _paper_deployment(),
        kind=DeploymentKind.DISCRETIONARY,
        strategy_fingerprint=None,
        strategy_id=None,
        timeframe="1h",
    )
    await store.create_deployment(deployment)
    first = await store.acquire_worker_lease(
        deployment.id, holder="worker-a", now=_NOW, ttl=timedelta(seconds=45)
    )
    blocked = await store.acquire_worker_lease(
        deployment.id,
        holder="worker-b",
        now=_NOW + timedelta(seconds=10),
        ttl=timedelta(seconds=45),
    )
    taken = await store.acquire_worker_lease(
        deployment.id,
        holder="worker-b",
        now=_NOW + timedelta(seconds=46),
        ttl=timedelta(seconds=45),
    )
    assert first is not None
    assert blocked is None
    assert taken is not None
    assert taken.worker_lease_holder == "worker-b"


@pytest.mark.anyio
async def test_revision_fence_rejects_stale_running_snapshot() -> None:
    """F08: pause cannot be overwritten by a stale RUNNING write."""
    store = InMemoryExecutionStore()
    created = await store.create_deployment(_paper_deployment())
    paused = replace(created, status=DeploymentStatus.PAUSED)
    saved = await store.save_deployment(paused, expected_revision=created.revision)
    stale = replace(created, status=DeploymentStatus.RUNNING, cash=Decimal("1"))
    with pytest.raises(ExecutionConflictError, match="revision"):
        await store.save_deployment(stale, expected_revision=created.revision)
    loaded = await store.get_deployment(created.id)
    assert loaded.deployment.status is DeploymentStatus.PAUSED
    assert loaded.deployment.revision == saved.revision
    assert loaded.deployment.cash == Decimal("10000")
