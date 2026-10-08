"""Safety-finding evaluation without a database or network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.alerts.models import AlertCode
from thytrader.alerts.supervision import gather_safety_findings
from thytrader.alerts.supervision_deadlines import SETTLING_GRACE_SECONDS
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.market_data.models import Candle
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)

_NOW = datetime(2026, 3, 2, 6, 3, tzinfo=UTC)
_THRESHOLDS = AlertThresholds()
_DEFAULT_CREATED_AT = _NOW - timedelta(days=2)
_DEFAULT_LAST_EVALUATED_BAR = datetime(2026, 3, 2, 4, 0, tzinfo=UTC)
_DEFAULT_LEASE_EXPIRES_AT = _NOW + timedelta(seconds=30)


class _Snapshots:
    """Return one prebuilt snapshot for every deployment id."""

    def __init__(self, snapshot: DeploymentSnapshot) -> None:
        self.snapshot = snapshot

    async def get_deployment(self, deployment_id: object) -> DeploymentSnapshot:
        del deployment_id
        return self.snapshot


def _deployment(
    *,
    mode: DeploymentMode = DeploymentMode.PAPER,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    phase: RuntimePhase = RuntimePhase.FLAT,
    timeframe: str = "1h",
    daily_loss_latched: bool = False,
    mismatch_detail: str | None = None,
    created_at: datetime = _DEFAULT_CREATED_AT,
    last_evaluated_bar: datetime | None = _DEFAULT_LAST_EVALUATED_BAR,
    worker_lease_expires_at: datetime | None = _DEFAULT_LEASE_EXPIRES_AT,
) -> Deployment:
    """One running 1h paper book unless overridden."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=mode,
        status=status,
        cash=Decimal("10000"),
        phase=phase,
        created_at=created_at,
        updated_at=_NOW,
        kind=DeploymentKind.STRATEGY,
        timeframe=timeframe,
        last_evaluated_bar=last_evaluated_bar,
        worker_lease_expires_at=worker_lease_expires_at,
        daily_loss_latched=daily_loss_latched,
        mismatch_detail=mismatch_detail,
    )


def _position(deployment: Deployment) -> Position:
    return Position(
        deployment_id=deployment.id,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=deployment.created_at,
        updated_at=_NOW,
        side=PositionSide.LONG,
        product_id=deployment.product_id,
    )


def _candle(*, start: datetime, low: Decimal, high: Decimal, close: Decimal) -> Candle:
    return Candle(
        starts_at=start,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=Decimal("1"),
    )


async def _findings(
    deployment: Deployment, snapshot: DeploymentSnapshot | None = None
) -> set[AlertCode]:
    """Collect the alert codes one deployment produces under default clocks."""
    loaded = snapshot or DeploymentSnapshot(deployment=deployment)
    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(loaded),
        closed_candles=_no_candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
    )
    return {item.code for item in found.findings}


async def _no_candles(
    product_id: str, timeframe: str, deploy_anchor: datetime
) -> tuple[Candle, ...]:
    del product_id, timeframe, deploy_anchor
    return ()


@pytest.mark.anyio
async def test_paused_mismatch_and_breaker_latch_are_distinct_alerts() -> None:
    """Paused mismatch, breaker latch, and portfolio breaker stay distinct codes."""
    paused = _deployment(
        status=DeploymentStatus.PAUSED,
        mismatch_detail="Market-data window is gapped.",
        phase=RuntimePhase.FLAT,
    )
    latched = _deployment(daily_loss_latched=True, phase=RuntimePhase.FLAT)
    portfolio = _deployment(
        status=DeploymentStatus.PAUSED,
        mismatch_detail="PORTFOLIO_DRAWDOWN_STOP: sleeve paused",
        phase=RuntimePhase.FLAT,
    )
    assert AlertCode.BOOK_PAUSED_MISMATCH in await _findings(paused)
    assert AlertCode.BREAKER_LATCHED in await _findings(latched)
    codes = await _findings(portfolio)
    assert AlertCode.BREAKER_LATCHED in codes
    assert AlertCode.BOOK_PAUSED_MISMATCH not in codes


@pytest.mark.anyio
async def test_live_uncovered_stop_and_triggered_unfilled_do_not_escalate() -> None:
    """A live stop that traded through unfilled is cover lost, never a market escalation."""
    deployment = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.OPEN, timeframe="1h")
    position = _position(deployment)
    triggered = datetime(2026, 3, 2, 5, 0, tzinfo=UTC)
    order = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="stop-1",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.OPEN,
        created_at=triggered - timedelta(hours=2),
        updated_at=_NOW,
        stop_trigger_price=Decimal("95"),
        product_id="BTC-USD",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, position=position, positions=(position,), orders=(order,)
    )

    async def candles(
        product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        del product_id, timeframe, deploy_anchor
        triggered_candle = _candle(
            start=triggered, low=Decimal("94"), high=Decimal("101"), close=Decimal("96")
        )
        return (triggered_candle,)

    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(snapshot),
        closed_candles=candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
    )
    codes = {item.code for item in found.findings}
    assert AlertCode.STOP_UNCOVERED in codes
    triggered_alert = next(
        item for item in found.findings if item.code is AlertCode.STOP_TRIGGERED_UNFILLED
    )
    assert "does not escalate" in triggered_alert.detail
    assert "94" not in triggered_alert.detail or "95" in triggered_alert.detail


@pytest.mark.anyio
async def test_six_hour_book_inside_settling_grace_is_not_stale() -> None:
    """A 6h close plus a few seconds is not a missed decision."""
    now = datetime(2026, 3, 2, 6, 0, 30, tzinfo=UTC)
    deployment = _deployment(
        timeframe="6h",
        last_evaluated_bar=datetime(2026, 3, 1, 18, 0, tzinfo=UTC),
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(DeploymentSnapshot(deployment=deployment)),
        closed_candles=_no_candles,
        now=now,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
    )
    assert AlertCode.DECISION_DEADLINE_MISSED not in {item.code for item in found.findings}
    assert SETTLING_GRACE_SECONDS == 120


@pytest.mark.anyio
async def test_one_hour_book_two_bars_behind_after_grace_alerts() -> None:
    """A 1h book two closed bars behind its cursor is a missed decision deadline."""
    deployment = _deployment(
        timeframe="1h",
        last_evaluated_bar=datetime(2026, 3, 2, 2, 0, tzinfo=UTC),
    )
    assert AlertCode.DECISION_DEADLINE_MISSED in await _findings(deployment)


@pytest.mark.anyio
async def test_fresh_book_and_paused_book_do_not_get_decision_deadline_alerts() -> None:
    """Fresh books get a warmup window and paused books already carry a mismatch."""
    fresh = _deployment(
        created_at=_NOW - timedelta(minutes=5),
        last_evaluated_bar=None,
        worker_lease_expires_at=_NOW + timedelta(seconds=20),
    )
    paused = _deployment(
        status=DeploymentStatus.PAUSED,
        mismatch_detail="operator pause",
        last_evaluated_bar=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
    )
    assert AlertCode.DECISION_DEADLINE_MISSED not in await _findings(fresh)
    assert AlertCode.DECISION_DEADLINE_MISSED not in await _findings(paused)


@pytest.mark.anyio
async def test_unknown_lease_is_visible_and_not_treated_as_process_death() -> None:
    """A missing lease alerts as unknown evidence, never as proven process death."""
    deployment = _deployment(
        worker_lease_expires_at=None,
        phase=RuntimePhase.OPEN,
        mode=DeploymentMode.LIVE,
    )
    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(
            DeploymentSnapshot(
                deployment=deployment,
                position=_position(deployment),
                positions=(_position(deployment),),
            )
        ),
        closed_candles=_no_candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
    )
    lease = next(item for item in found.findings if item.code is AlertCode.WORKER_LEASE_STALE)
    assert "unknown" in lease.detail.lower()
    assert "not proof the worker" in lease.detail
    assert AlertCode.MAINTENANCE_DEADLINE_MISSED in {item.code for item in found.findings}


@pytest.mark.anyio
async def test_fresh_lease_on_an_open_book_is_not_a_missed_maintenance_deadline() -> None:
    """A fresh lease does not trigger the lease-timing deadline alert."""
    deployment = _deployment(
        phase=RuntimePhase.OPEN, worker_lease_expires_at=_NOW + timedelta(seconds=10)
    )
    codes = await _findings(
        deployment,
        DeploymentSnapshot(
            deployment=deployment,
            position=_position(deployment),
            positions=(_position(deployment),),
        ),
    )
    assert AlertCode.MAINTENANCE_DEADLINE_MISSED not in codes
    assert AlertCode.WORKER_LEASE_STALE not in codes


@pytest.mark.anyio
async def test_implausibly_future_lease_is_unknown_not_verified_freshness() -> None:
    """Clock skew cannot make a many-hours-future lease certify book safety."""
    deployment = _deployment(worker_lease_expires_at=_NOW + timedelta(hours=6))
    evidence = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(DeploymentSnapshot(deployment=deployment)),
        closed_candles=_no_candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
    )
    lease = next(row for row in evidence.findings if row.code is AlertCode.WORKER_LEASE_STALE)
    assert "lease age is unknown" in lease.detail
    assert "clock skew" in lease.detail
