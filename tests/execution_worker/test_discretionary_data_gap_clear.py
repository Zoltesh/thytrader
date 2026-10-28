"""A desk (discretionary) book's data-gap pause clears once its window is whole (ADR 0146)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.execution_worker.test_missing_decision_candles import _btc, _NoPlaceBroker
from tests.worker_patching import patch_worker_global
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    LifecycleCommand,
    RuntimePhase,
)

_GAP = "Market-data window is gapped or missing the latest closed bar."
_START = datetime(2026, 1, 5, tzinfo=UTC)


def _bars(count: int) -> tuple[Candle, ...]:
    """Contiguous closed 1h bars from ``_START``."""
    return tuple(
        Candle(
            starts_at=_START + timedelta(hours=index),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(5),
        )
        for index in range(count)
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: DeploymentMode = DeploymentMode.PAPER,
    detail: str = _GAP,
    expected_last: datetime | None = None,
    lifecycle: LifecycleCommand = LifecycleCommand.NONE,
) -> tuple[Deployment, list[str]]:
    """Process one cycle of a flat desk book paused with ``detail`` on a whole window."""
    candles = _bars(4)
    last = candles[-1].starts_at if expected_last is None else expected_last

    async def _window(
        *args: object, **kwargs: object
    ) -> tuple[object, tuple[Candle, ...], datetime]:
        del args, kwargs
        return _btc(), candles, last

    patch_worker_global(monkeypatch, "_closed_window_for", _window)
    store = InMemoryExecutionStore()
    deployment = Deployment(
        id=uuid7(_START),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=mode,
        status=DeploymentStatus.PAUSED,
        cash=Decimal("1000"),
        phase=RuntimePhase.FLAT,
        created_at=_START,
        updated_at=_START,
        timeframe="1h",
        kind=DeploymentKind.DISCRETIONARY,
        mismatch_detail=detail,
        lifecycle_command=lifecycle,
        last_evaluated_bar=candles[1].starts_at,
    )
    await store.create_deployment(deployment)
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        await service._process_discretionary(
            await store.get_deployment(deployment.id),
            store=store,
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=_NoPlaceBroker() if mode is DeploymentMode.LIVE else None,
            quote_reader=None,
            user_feed_store=None,
            risk_policy=compiled_default_risk_policy(),
            memory_store=None,
        )
    current = (await store.get_deployment(deployment.id)).deployment
    return current, [event.action for event in await audit.list_recent(limit=50)]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", [DeploymentMode.PAPER, DeploymentMode.LIVE])
async def test_a_desk_book_data_gap_pause_clears_once_the_window_is_whole(
    monkeypatch: pytest.MonkeyPatch, mode: DeploymentMode
) -> None:
    """Paper and live desk books resume after their due bars ran on a whole window."""
    current, actions = await _run(monkeypatch, mode=mode)
    assert current.status is DeploymentStatus.RUNNING
    assert current.mismatch_detail is None
    assert current.last_evaluated_bar == _bars(4)[-1].starts_at
    assert "data_gap_pause_cleared" in actions


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("detail", "lifecycle"),
    [
        ("Operator paused entries.", LifecycleCommand.NONE),
        ("Order reconciliation is unconfirmed: order read failed.", LifecycleCommand.NONE),
        (_GAP, LifecycleCommand.STOP_NEW_ENTRIES),
    ],
)
async def test_other_desk_pauses_never_clear_with_the_window(
    monkeypatch: pytest.MonkeyPatch, detail: str, lifecycle: LifecycleCommand
) -> None:
    """Only a pause whose sole reason is the window gap clears; everything else stays."""
    current, actions = await _run(monkeypatch, detail=detail, lifecycle=lifecycle)
    assert current.status is DeploymentStatus.PAUSED
    assert current.mismatch_detail == detail
    assert "data_gap_pause_cleared" not in actions


@pytest.mark.anyio
async def test_a_still_gapped_window_keeps_the_desk_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the newest closed bar is still missing the pause stays."""
    current, actions = await _run(monkeypatch, expected_last=_bars(5)[-1].starts_at)
    assert current.status is DeploymentStatus.PAUSED
    assert current.mismatch_detail == _GAP
    assert "data_gap_pause_cleared" not in actions


@pytest.mark.anyio
async def test_a_gap_never_replaces_another_desk_pause_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A book paused for another fault keeps that reason through a gap, so it never resumes."""
    other = "Entry order is unconfirmed; reconcile before retrying."
    gapped, _ = await _run(monkeypatch, detail=other, expected_last=_bars(5)[-1].starts_at)
    assert gapped.status is DeploymentStatus.PAUSED
    assert gapped.mismatch_detail == other
