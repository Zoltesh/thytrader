"""Unknown, partial and warming evidence never claims safety recovery."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.alerts.test_supervision import _NOW, _candle, _deployment, _no_candles, _position
from tests.execution.protection_support import settled_snapshot
from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.alerts.supervision import (
    AlertThresholds,
    SnapshotEvidence,
    gather_safety_findings,
    verified_worker_recovery,
)
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentSnapshot,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.memory.notify import DisabledNotificationSender

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.alerts.models import SafetyEvidence
    from thytrader.alerts.supervision import ClosedCandleReader
    from thytrader.execution.models import Deployment
    from thytrader.market_data.models import Candle

pytestmark = pytest.mark.anyio


class _Snapshots:
    """A full snapshot reader with selective unavailable books."""

    def __init__(self, snapshots: tuple[DeploymentSnapshot | SnapshotEvidence, ...]) -> None:
        """Index explicit complete or partial snapshots by book."""
        self.rows = {
            (item.snapshot if isinstance(item, SnapshotEvidence) else item).deployment.id: item
            for item in snapshots
        }

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot | SnapshotEvidence:
        """Unavailable books raise, never return fabricated empty snapshots."""
        if deployment_id not in self.rows:
            raise RuntimeError("snapshot unavailable")
        return self.rows[deployment_id]


def _alert(book: Deployment, code: AlertCode, subject: str | None = None) -> SupervisionFinding:
    """One seeded finding for an exact check identity."""
    return SupervisionFinding(
        code=code,
        scope=AlertScope.DEPLOYMENT,
        subject=subject or str(book.id),
        severity=(
            AlertSeverity.CRITICAL
            if code
            in {
                AlertCode.STOP_UNCOVERED,
                AlertCode.STOP_COVERAGE_UNKNOWN,
                AlertCode.STOP_TRIGGERED_UNFILLED,
            }
            else AlertSeverity.WARNING
        ),
        detail="previous safety finding",
        deployment_id=book.id,
        product_id=book.product_id,
    )


async def _gather_and_apply(
    store: InMemoryAlertStore,
    books: tuple[Deployment, ...],
    snapshots: _Snapshots,
    *,
    candles: ClosedCandleReader = _no_candles,
    authoritative: bool = False,
) -> SafetyEvidence:
    """Rebuild the service and apply only the gatherer's explicit recovery evidence."""
    service = AlertService(store, DisabledNotificationSender(), thresholds=AlertThresholds())
    evidence = await gather_safety_findings(
        deployments=books,
        snapshots=snapshots,
        closed_candles=candles,
        now=_NOW + timedelta(seconds=1),
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
        prior_alerts=await service.open_alerts(),
        inventory_authoritative=authoritative,
    )
    await service.apply(
        evidence.findings, evaluated=evidence.evaluated, now=_NOW + timedelta(seconds=1)
    )
    return evidence


async def test_partial_snapshot_recovery_is_per_book_and_per_check_after_restart() -> None:
    """Verified book A can recover while failed book B's checks remain open."""
    store = InMemoryAlertStore()
    a, b = _deployment(), _deployment()
    await store.record(_alert(a, AlertCode.BOOK_PAUSED_MISMATCH), now=_NOW)
    await store.record(_alert(b, AlertCode.STOP_UNCOVERED, f"{b.id}:{b.product_id}"), now=_NOW)
    await _gather_and_apply(store, (a, b), _Snapshots((DeploymentSnapshot(deployment=a),)))
    open_rows = await store.list_open_alerts()
    assert len(open_rows) == 1
    assert open_rows[0].deployment_id == b.id
    assert open_rows[0].resolved_at is None


async def test_explicit_partial_collections_do_not_recover_inventory_alerts() -> None:
    """Complete row metadata can recover a mismatch while incomplete positions remain unknown."""
    store = InMemoryAlertStore()
    book = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.OPEN)
    await store.record(_alert(book, AlertCode.BOOK_PAUSED_MISMATCH), now=_NOW)
    await store.record(
        _alert(book, AlertCode.STOP_UNCOVERED, f"{book.id}:{book.product_id}"), now=_NOW
    )
    partial = SnapshotEvidence(DeploymentSnapshot(deployment=book), complete=False)
    await _gather_and_apply(store, (book,), _Snapshots((partial,)))
    assert {row.code for row in await store.list_open_alerts()} == {AlertCode.STOP_UNCOVERED}


@pytest.mark.parametrize("authoritative", [False, True])
async def test_missing_inventory_only_resolves_on_explicit_authoritative_removal(
    authoritative: bool,
) -> None:
    """Subset/unknown inventory is not deletion; explicit full inventory can prove removal."""
    store = InMemoryAlertStore()
    book = _deployment()
    await store.record(_alert(book, AlertCode.BOOK_PAUSED_MISMATCH), now=_NOW)
    await _gather_and_apply(store, (), _Snapshots(()), authoritative=authoritative)
    assert bool(await store.list_open_alerts()) is not authoritative


async def test_empty_or_raising_warming_candles_do_not_clear_lost_cover() -> None:
    """Cache warming/unavailable candles leave prior lost-cover and trigger alerts open."""
    for raises in (False, True):
        store = InMemoryAlertStore()
        book = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.OPEN)
        position = _position(book)
        order = Order(
            id=uuid4(),
            deployment_id=book.id,
            intent_id=uuid4(),
            client_order_id="safety-test",
            side=OrderSide.SELL,
            kind=OrderKind.STOP_LIMIT,
            quantity=position.quantity,
            status=OrderStatus.OPEN,
            created_at=_NOW - timedelta(hours=2),
            updated_at=_NOW,
            stop_trigger_price=Decimal("95"),
            product_id=book.product_id,
        )
        snapshot = DeploymentSnapshot(
            deployment=book, position=position, positions=(position,), orders=(order,)
        )
        await store.record(
            _alert(book, AlertCode.STOP_UNCOVERED, f"{book.id}:{book.product_id}"), now=_NOW
        )
        await store.record(
            _alert(book, AlertCode.STOP_TRIGGERED_UNFILLED, f"{book.id}:{order.id}"), now=_NOW
        )
        await store.record(
            _alert(book, AlertCode.STOP_COVERAGE_UNKNOWN, f"{book.id}:{book.product_id}"), now=_NOW
        )

        async def unavailable(
            product_id: str, timeframe: str, deploy_anchor: datetime, *, warming: bool = raises
        ) -> tuple[Candle, ...]:
            """Represent either a cold empty cache or the warming exception boundary."""
            del product_id, timeframe, deploy_anchor
            if warming:
                raise RuntimeError("window cache warming")
            return ()

        evidence = await _gather_and_apply(
            store, (book,), _Snapshots((snapshot,)), candles=unavailable
        )
        assert (
            AlertCheck(AlertCode.STOP_COVERAGE_UNKNOWN, f"{book.id}:{book.product_id}")
            not in evidence.evaluated
        )
        assert {item.code for item in await store.list_open_alerts()} >= {
            AlertCode.STOP_UNCOVERED,
            AlertCode.STOP_TRIGGERED_UNFILLED,
            AlertCode.STOP_COVERAGE_UNKNOWN,
        }


async def test_price_rebound_does_not_recover_unfilled_stop_but_confirmed_fill_does() -> None:
    """Trigger failure is sticky until durable order/position evidence confirms recovery."""
    store = InMemoryAlertStore()
    book = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.OPEN)
    position = _position(book)
    order = Order(
        id=uuid4(),
        deployment_id=book.id,
        intent_id=uuid4(),
        client_order_id="sticky-stop",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=position.quantity,
        status=OrderStatus.OPEN,
        created_at=_NOW - timedelta(hours=2),
        updated_at=_NOW,
        stop_trigger_price=Decimal("95"),
        product_id=book.product_id,
    )
    await store.record(
        _alert(book, AlertCode.STOP_UNCOVERED, f"{book.id}:{book.product_id}"), now=_NOW
    )
    await store.record(
        _alert(book, AlertCode.STOP_TRIGGERED_UNFILLED, f"{book.id}:{order.id}"), now=_NOW
    )

    async def rebound(
        product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        """A new closed candle above the old stop is not a fill."""
        del product_id, timeframe, deploy_anchor
        return (
            _candle(
                start=_NOW - timedelta(hours=1, minutes=3),
                low=Decimal("100"),
                high=Decimal("102"),
                close=Decimal("101"),
            ),
        )

    snapshot = DeploymentSnapshot(
        deployment=book, position=position, positions=(position,), orders=(order,)
    )
    await _gather_and_apply(store, (book,), _Snapshots((snapshot,)), candles=rebound)
    assert {item.code for item in await store.list_open_alerts()} >= {
        AlertCode.STOP_UNCOVERED,
        AlertCode.STOP_TRIGGERED_UNFILLED,
    }
    filled = replace(order, status=OrderStatus.FILLED, filled_quantity=order.quantity)
    flat = replace(book, phase=RuntimePhase.FLAT)
    # Strictly newer observation than the rebound pass.
    evidence = await gather_safety_findings(
        deployments=(flat,),
        snapshots=_Snapshots(
            (settled_snapshot(DeploymentSnapshot(deployment=flat, orders=(filled,))),)
        ),
        closed_candles=_no_candles,
        now=_NOW + timedelta(seconds=2),
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
        prior_alerts=await store.list_open_alerts(),
    )
    await AlertService(store, DisabledNotificationSender(), thresholds=AlertThresholds()).apply(
        evidence.findings, evaluated=evidence.evaluated, now=_NOW + timedelta(seconds=2)
    )
    assert not await store.list_open_alerts()


@pytest.mark.parametrize("crossed", [False, True])
@pytest.mark.parametrize("venue_age_seconds", [None, 0, 180], ids=["missing", "fresh", "stale"])
async def test_creation_bar_proves_negative_only_when_its_whole_range_never_touches_stop(
    crossed: bool,
    venue_age_seconds: int | None,
) -> None:
    """Pre-creation crossing is ambiguous, not a trigger or an authorized cover recovery."""
    store = InMemoryAlertStore()
    book = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.OPEN)
    position = replace(_position(book), stop_price=Decimal("95"))
    start = _NOW.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    order = Order(
        id=uuid4(),
        deployment_id=book.id,
        intent_id=uuid4(),
        client_order_id="creation-evidence",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=position.quantity,
        status=OrderStatus.OPEN,
        created_at=start + timedelta(minutes=30),
        updated_at=_NOW,
        venue_order_id="venue-creation-evidence",
        venue_observed_at=(
            None if venue_age_seconds is None else _NOW - timedelta(seconds=venue_age_seconds)
        ),
        stop_trigger_price=Decimal("95"),
        price=Decimal("94.99"),
        product_id=book.product_id,
    )
    for code in (AlertCode.STOP_UNCOVERED, AlertCode.STOP_COVERAGE_UNKNOWN):
        await store.record(
            _alert(book, code, f"{book.id}:{book.product_id}"), now=_NOW - timedelta(hours=2)
        )

    async def creation_bar(
        product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        """Provide a full closed test bar that straddles order creation."""
        del product_id, timeframe, deploy_anchor
        return (
            _candle(
                start=start,
                low=Decimal("94") if crossed else Decimal("100"),
                high=Decimal("102"),
                close=Decimal("101"),
            ),
        )

    snapshot = DeploymentSnapshot(
        deployment=book, position=position, positions=(position,), orders=(order,)
    )
    evidence = await _gather_and_apply(
        store, (book,), _Snapshots((snapshot,)), candles=creation_bar
    )
    assert AlertCode.STOP_TRIGGERED_UNFILLED not in {row.code for row in evidence.findings}
    expected = (
        {AlertCode.STOP_UNCOVERED, AlertCode.STOP_COVERAGE_UNKNOWN}
        if crossed or venue_age_seconds != 0
        else set()
    )
    assert {item.code for item in await store.list_open_alerts()} == expected


@pytest.mark.parametrize("case", ["advance", "unchanged", "backwards", "strategy", "held"])
async def test_only_verified_cursor_advancement_on_same_snapshot_can_recover_errors(
    case: str,
) -> None:
    """No-op, cursor reset, changed strategy and held pause are not successful recovery."""
    before = _deployment(last_evaluated_bar=_NOW - timedelta(hours=2))
    after = replace(before, last_evaluated_bar=_NOW - timedelta(hours=1))
    if case == "unchanged":
        after = before
    elif case == "backwards":
        after = replace(before, last_evaluated_bar=_NOW - timedelta(hours=3))
    elif case == "strategy":
        after = replace(after, strategy_id=uuid4())
    elif case == "held":
        after = replace(after, mismatch_detail="WORKER_CONSECUTIVE_FAILURES: held")
    assert verified_worker_recovery(before, after) is (case == "advance")
