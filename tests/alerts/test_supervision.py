"""Safety-finding evaluation without a database or network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    OperatorAlert,
)
from thytrader.alerts.supervision import gather_safety_findings
from thytrader.alerts.supervision_deadlines import SETTLING_GRACE_SECONDS
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.market_data.models import Candle
from thytrader.trading.models import (
    TERMINAL_READ_UNCONFIRMED_REASON,
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    Order,
    OrderIntent,
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
async def test_a_marked_historical_stop_does_not_mask_voided_cover() -> None:
    """A live stop that traded through still alerts beside a venue-closed historical stop."""
    deployment = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.OPEN, timeframe="1h")
    position = _position(deployment)
    triggered = datetime(2026, 3, 2, 5, 0, tzinfo=UTC)

    def stop(client_order_id: str, status: OrderStatus, reason: str | None) -> Order:
        return Order(
            id=uuid4(),
            deployment_id=deployment.id,
            intent_id=uuid4(),
            client_order_id=client_order_id,
            side=OrderSide.SELL,
            kind=OrderKind.STOP_LIMIT,
            quantity=Decimal("0.01"),
            status=status,
            created_at=triggered - timedelta(hours=2),
            updated_at=_NOW,
            stop_trigger_price=Decimal("95"),
            product_id="BTC-USD",
            reject_reason=reason,
        )

    live = stop("stop-live", OrderStatus.OPEN, None)
    old = stop("stop-old", OrderStatus.UNKNOWN, TERMINAL_READ_UNCONFIRMED_REASON)
    snapshot = DeploymentSnapshot(
        deployment=deployment, position=position, positions=(position,), orders=(live, old)
    )

    async def candles(
        product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        del product_id, timeframe, deploy_anchor
        return (
            _candle(start=triggered, low=Decimal("94"), high=Decimal("101"), close=Decimal("96")),
        )

    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(snapshot),
        closed_candles=candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
    )
    subjects = {(item.code, item.subject) for item in found.findings}
    assert (AlertCode.STOP_TRIGGERED_UNFILLED, f"{deployment.id}:{live.id}") in subjects
    assert (AlertCode.STOP_TRIGGERED_UNFILLED, f"{deployment.id}:{old.id}") not in subjects
    # The marked order is still unknown cover, so cover is reported unverified, never safe.
    codes = {code for code, _ in subjects}
    assert codes & {AlertCode.STOP_UNCOVERED, AlertCode.STOP_COVERAGE_UNKNOWN}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "reason", "alerted"),
    [
        (OrderStatus.CANCELED, None, False),
        (OrderStatus.UNKNOWN, TERMINAL_READ_UNCONFIRMED_REASON, False),
        (OrderStatus.UNKNOWN, None, True),
    ],
)
async def test_only_possibly_working_stops_raise_triggered_unfilled(
    status: OrderStatus, reason: str | None, alerted: bool
) -> None:
    """Only a possibly working stop whose trigger traded is a live incident.

    A stop the venue already closed is history even while a later read failure leaves it
    UNKNOWN (``TERMINAL_READ_UNCONFIRMED_REASON``); a plain UNKNOWN stop may still work.
    """
    deployment = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.FLAT, timeframe="1h")
    triggered = datetime(2026, 3, 2, 5, 0, tzinfo=UTC)
    order = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="stop-old",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        status=status,
        reject_reason=reason,
        created_at=triggered - timedelta(hours=2),
        updated_at=_NOW,
        stop_trigger_price=Decimal("95"),
        product_id="BTC-USD",
    )

    async def candles(
        product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        del product_id, timeframe, deploy_anchor
        return (
            _candle(start=triggered, low=Decimal("94"), high=Decimal("101"), close=Decimal("96")),
        )

    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(DeploymentSnapshot(deployment=deployment, orders=(order,))),
        closed_candles=candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
    )
    assert (AlertCode.STOP_TRIGGERED_UNFILLED in {item.code for item in found.findings}) is alerted


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


def _entry_intent(order: Order) -> OrderIntent:
    """The ENTRY intent an entry order was placed under."""
    return OrderIntent(
        id=order.intent_id,
        deployment_id=order.deployment_id,
        client_order_id=order.client_order_id,
        purpose=IntentPurpose.ENTRY,
        side=order.side,
        kind=order.kind,
        quantity=order.quantity,
        created_at=order.created_at,
        candle_starts_at=order.created_at,
    )


@pytest.mark.anyio
async def test_a_resting_entry_carrying_its_bracket_stop_is_not_a_triggered_stop() -> None:
    """An unfilled entry's stop is bracket metadata, not protection; an old alert clears."""
    deployment = _deployment(
        mode=DeploymentMode.LIVE, phase=RuntimePhase.PENDING_ENTRY, timeframe="1h"
    )
    created = datetime(2026, 3, 2, 3, 0, tzinfo=UTC)
    entry = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="entry-1",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal("100"),
        status=OrderStatus.OPEN,
        created_at=created,
        updated_at=_NOW,
        stop_trigger_price=Decimal("95"),
        take_profit_price=Decimal("120"),
        product_id="BTC-USD",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, orders=(entry,), intents=(_entry_intent(entry),)
    )
    subject = f"{deployment.id}:{entry.id}"

    async def candles(
        product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        del product_id, timeframe, deploy_anchor
        return tuple(
            _candle(
                start=created + timedelta(hours=hour),
                low=Decimal("101"),
                high=Decimal("102"),
                close=Decimal("101"),
            )
            for hour in range(3)
        )

    prior = OperatorAlert(
        id=uuid4(),
        code=AlertCode.STOP_TRIGGERED_UNFILLED,
        scope=AlertScope.DEPLOYMENT,
        subject=subject,
        severity=AlertSeverity.CRITICAL,
        detail="old",
        deployment_id=deployment.id,
        product_id="BTC-USD",
        first_seen_at=created,
        last_seen_at=created,
        occurrences=1,
    )
    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(snapshot),
        closed_candles=candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
        prior_alerts=(prior,),
    )
    assert AlertCode.STOP_TRIGGERED_UNFILLED not in {item.code for item in found.findings}
    assert AlertCheck(AlertCode.STOP_TRIGGERED_UNFILLED, subject) in found.evaluated


@pytest.mark.anyio
async def test_a_false_entry_stop_alert_clears_while_the_entry_fill_is_still_importing() -> None:
    """The entry never was protection, so its alert needs no economics proof to clear."""
    deployment = _deployment(
        mode=DeploymentMode.LIVE, phase=RuntimePhase.PENDING_ENTRY, timeframe="1h"
    )
    created = datetime(2026, 3, 2, 3, 0, tzinfo=UTC)
    entry = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="entry-2",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        filled_quantity=Decimal("0.005"),
        price=Decimal("100"),
        status=OrderStatus.OPEN,
        created_at=created,
        updated_at=_NOW,
        stop_trigger_price=Decimal("95"),
        take_profit_price=Decimal("120"),
        product_id="BTC-USD",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, orders=(entry,), intents=(_entry_intent(entry),)
    )
    subject = f"{deployment.id}:{entry.id}"
    prior = OperatorAlert(
        id=uuid4(),
        code=AlertCode.STOP_TRIGGERED_UNFILLED,
        scope=AlertScope.DEPLOYMENT,
        subject=subject,
        severity=AlertSeverity.CRITICAL,
        detail="old",
        deployment_id=deployment.id,
        product_id="BTC-USD",
        first_seen_at=created,
        last_seen_at=created,
        occurrences=1,
    )
    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(snapshot),
        closed_candles=_no_candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
        prior_alerts=(prior,),
    )
    assert AlertCheck(AlertCode.STOP_TRIGGERED_UNFILLED, subject) in found.evaluated


@pytest.mark.anyio
async def test_an_entry_carrying_bracket_metadata_never_holds_cover_recovery() -> None:
    """A flat book's old cover alert recovers though a new entry rests with its bracket stop."""
    deployment = _deployment(
        mode=DeploymentMode.LIVE, phase=RuntimePhase.PENDING_ENTRY, timeframe="1h"
    )
    entry = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="entry-3",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal("100"),
        status=OrderStatus.OPEN,
        created_at=_NOW - timedelta(hours=1),
        updated_at=_NOW,
        stop_trigger_price=Decimal("95"),
        take_profit_price=Decimal("120"),
        product_id="BTC-USD",
    )
    subject = f"{deployment.id}:BTC-USD"
    prior = OperatorAlert(
        id=uuid4(),
        code=AlertCode.STOP_UNCOVERED,
        scope=AlertScope.DEPLOYMENT,
        subject=subject,
        severity=AlertSeverity.CRITICAL,
        detail="old",
        deployment_id=deployment.id,
        product_id="BTC-USD",
        first_seen_at=_NOW - timedelta(hours=2),
        last_seen_at=_NOW - timedelta(hours=2),
        occurrences=1,
    )
    found = await gather_safety_findings(
        deployments=(deployment,),
        snapshots=_Snapshots(
            DeploymentSnapshot(
                deployment=deployment, orders=(entry,), intents=(_entry_intent(entry),)
            )
        ),
        closed_candles=_no_candles,
        now=_NOW,
        thresholds=_THRESHOLDS,
        worker_interval_seconds=30,
        prior_alerts=(prior,),
    )
    assert AlertCheck(AlertCode.STOP_UNCOVERED, subject) in found.evaluated
