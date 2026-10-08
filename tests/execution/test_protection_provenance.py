"""Protection consumes real status receipts without upgrading them to geometry audits."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from itertools import permutations
from uuid import uuid4

import pytest

from tests.execution.test_protection_evidence import _NOW, _deployment, _order, _position
from tests.execution.test_reconcile import _LookupBroker, _snapshot_with_order
from tests.trading.protection_support import settled_snapshot
from thytrader.api.routes.deployment_serializers import position_response
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading import protection
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentSnapshot, OrderKind, OrderStatus, PositionSide
from thytrader.trading.protection import book_protection_evidence
from thytrader.trading.protection_models import LOCAL_EVIDENCE_MAX_AGE, ProtectionStatus


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the deterministic reporting clock, independently of local writes."""
    monkeypatch.setattr(protection, "utc_now", lambda: _NOW)


def test_stale_receipt_and_fresh_local_write_serialize_as_unverified() -> None:
    """A local rewrite neither increases coverage nor fabricates an API venue timestamp."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    receipt = _NOW - LOCAL_EVIDENCE_MAX_AGE - timedelta(seconds=1)
    stop = _order(deployment, position, stop="3200", price="2700", venue_observed_at=receipt)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(stop,))
    body = position_response(position, snapshot, compatibility_focus=False)
    payload = body.model_dump(mode="json")["protection"]
    assert body.protection_status == "unknown"
    assert body.position_state == "open_unverified"
    assert payload["covered_quantity"] == "0"
    assert payload["observed_at"] == receipt.isoformat()
    assert payload["verified_at"] is None
    assert payload["observation_source"] == "venue_order_state"
    assert payload["freshness"] == "stale"
    assert "venue_evidence_stale" in payload["reasons"]
    assert payload["geometry_basis"] == "working_target"
    assert stop.updated_at == _NOW


def test_oldest_contributing_receipt_bounds_full_quantity_verification() -> None:
    """Distinct fresh partial stops add, but the newest stop does not refresh the older one."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    oldest = _NOW - LOCAL_EVIDENCE_MAX_AGE
    first = _order(
        deployment,
        position,
        stop="3200",
        price="2700",
        quantity="0.2",
        venue_order_id="stop-a",
        venue_observed_at=oldest,
    )
    second = _order(
        deployment, position, stop="3200", price="2700", quantity="0.3", venue_order_id="stop-b"
    )
    unrelated = _order(
        deployment,
        position,
        kind=OrderKind.POST_ONLY_LIMIT,
        price="2700",
        venue_order_id="take-profit",
        venue_observed_at=_NOW + timedelta(seconds=1),
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(first, second, unrelated)
    )
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.COVERED
    assert evidence.covered_quantity == Decimal("0.5")
    assert evidence.observed_at == _NOW
    assert evidence.verified_at == oldest
    assert evidence.freshness == "recent_venue"
    assert evidence.geometry_basis == "working_target"
    assert "observation_time_future" not in evidence.reasons
    body = position_response(position, snapshot, compatibility_focus=False)
    assert body.protection.verified_at == oldest.isoformat()


@pytest.mark.parametrize("missing", [False, True])
def test_mixed_freshness_cannot_cover_quantity_with_an_unobserved_partial(missing: bool) -> None:
    """Only the fresh fraction contributes; legacy/stale evidence cannot fill its shortfall."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    first = _order(
        deployment,
        position,
        stop="3200",
        price="2700",
        quantity="0.2",
        venue_order_id="stop-a",
        venue_observed_at=None if missing else _NOW - timedelta(seconds=121),
    )
    second = _order(
        deployment, position, stop="3200", price="2700", quantity="0.3", venue_order_id="stop-b"
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(first, second)
    )
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == Decimal("0.3")
    assert evidence.uncovered_quantity == Decimal("0.2")
    assert evidence.freshness == ("unknown" if missing else "stale")
    assert evidence.verified_at == _NOW  # This verifies only the fresh fraction, not the book.
    assert "partial_stop_quantity" in evidence.reasons
    assert "venue_stop_resting" not in evidence.reasons


@pytest.mark.parametrize("status", [OrderStatus.UNKNOWN, OrderStatus.CANCELED, OrderStatus.FILLED])
def test_local_recency_cannot_resurrect_a_duplicate_open(status: OrderStatus) -> None:
    """Even a newer local rewrite of OPEN cannot override terminal or ambiguous venue evidence."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    old = _order(
        deployment,
        position,
        stop="3200",
        price="2700",
        updated_at=_NOW + timedelta(seconds=60),
        venue_observed_at=_NOW - timedelta(seconds=30),
    )
    latest = replace(
        old,
        id=uuid4(),
        status=status,
        updated_at=_NOW,
        venue_observed_at=None if status is OrderStatus.UNKNOWN else _NOW,
    )
    for rows in permutations((old, latest)):
        snapshot = settled_snapshot(
            DeploymentSnapshot(deployment=deployment, positions=(position,), orders=rows)
        )
        evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
        assert evidence.status is (
            ProtectionStatus.UNKNOWN
            if status is OrderStatus.UNKNOWN
            else ProtectionStatus.UNPROTECTED
        )
        assert evidence.covered_quantity == 0
        assert evidence.verified_at is None


@pytest.mark.parametrize("conflict", ["geometry", "quantity", "terminal", "missing"])
def test_duplicate_conflict_cannot_be_resolved_by_a_status_read(conflict: str) -> None:
    """Status receipts are not authority to resolve contradictory geometry or order histories."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    first = _order(deployment, position, stop="3200", price="2700")
    conflicts = {
        "geometry": replace(first, stop_trigger_price=Decimal("3300")),
        "quantity": replace(first, quantity=Decimal("0.6")),
        "terminal": replace(first, status=OrderStatus.CANCELED),
        "missing": replace(first, venue_observed_at=None),
    }
    later = replace(first, id=uuid4(), venue_observed_at=_NOW + timedelta(seconds=1))
    for rows in permutations((first, conflicts[conflict], later)):
        snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=rows)
        evidence = book_protection_evidence(
            snapshot, product_id="BTC-USD", position=position, now=_NOW + timedelta(seconds=1)
        )
        assert evidence.status is ProtectionStatus.UNKNOWN
        assert evidence.covered_quantity == 0
        assert evidence.verified_at is None
        assert evidence.freshness == "unknown"


def test_duplicate_partial_fill_history_must_not_regress_in_any_tuple_order() -> None:
    """A later increased remainder is contradictory even when a third row looks consistent."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    first = _order(deployment, position, stop="3200", price="2700", filled="0.3")
    regressed = replace(
        first,
        id=uuid4(),
        filled_quantity=Decimal("0.2"),
        venue_observed_at=_NOW + timedelta(seconds=1),
    )
    latest = replace(
        first,
        id=uuid4(),
        filled_quantity=Decimal("0.4"),
        venue_observed_at=_NOW + timedelta(seconds=2),
    )
    for rows in permutations((first, regressed, latest)):
        snapshot = settled_snapshot(
            DeploymentSnapshot(deployment=deployment, positions=(position,), orders=rows)
        )
        evidence = book_protection_evidence(
            snapshot, product_id="BTC-USD", position=position, now=_NOW + timedelta(seconds=2)
        )
        assert evidence.status is ProtectionStatus.UNKNOWN
        assert evidence.covered_quantity == 0
        assert evidence.verified_at is None


@pytest.mark.anyio
async def test_matched_live_read_proves_state_only_until_its_receipt_expires() -> None:
    """Successful reconciliation supplies real freshness; later bookkeeping cannot extend it."""
    store = InMemoryExecutionStore()
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    stop = _order(deployment, position, stop="3200", price="2700", venue_observed_at=None)
    bot = await _snapshot_with_order(store, stop)
    await store.save_position(position, deployment_id=bot.id)
    reconciled = await reconcile_open_orders(
        await store.get_deployment(bot.id),
        store=store,
        broker=_LookupBroker(SubmitResult(status=OrderStatus.OPEN, venue_order_id="child-1")),
    )
    stamp = reconciled.orders[0].venue_observed_at
    assert stamp is not None
    await store.save_order(replace(reconciled.orders[0], updated_at=stamp + timedelta(seconds=121)))
    reread = await store.get_deployment(bot.id)
    fresh = book_protection_evidence(
        reread, product_id="BTC-USD", position=position, now=stamp + timedelta(seconds=120)
    )
    assert fresh.status is ProtectionStatus.COVERED
    assert fresh.verified_at == fresh.observed_at == stamp
    assert fresh.observation_source == "venue_order_state"
    assert fresh.freshness == "recent_venue"
    assert fresh.geometry_basis == "working_target"  # Persisted geometry, not a venue audit.
    stale = book_protection_evidence(
        reread, product_id="BTC-USD", position=position, now=stamp + timedelta(seconds=121)
    )
    assert stale.status is ProtectionStatus.UNKNOWN
    assert stale.covered_quantity == 0
    assert stale.observed_at == stamp
    assert stale.verified_at is None
    assert stale.freshness == "stale"


@pytest.mark.anyio
@pytest.mark.parametrize("read_failure", [False, True])
async def test_unknown_reconciliation_clears_protection_receipt(
    read_failure: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consume the real reconciliation result: UNKNOWN/error clears old status evidence."""
    store = InMemoryExecutionStore()
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    stop = _order(deployment, position, stop="3200", price="2700")
    bot = await _snapshot_with_order(store, stop)
    await store.save_position(position, deployment_id=bot.id)
    broker = _LookupBroker(SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id="child-1"))

    async def fail(**_kwargs: object) -> None:
        """Simulate an unavailable read, never a real venue request."""
        raise BrokerError("Unavailable")

    if read_failure:
        monkeypatch.setattr(broker, "get_order", fail)
    reconciled = await reconcile_open_orders(
        await store.get_deployment(bot.id), store=store, broker=broker
    )
    assert reconciled.orders[0].venue_observed_at is None
    await store.save_order(replace(reconciled.orders[0], updated_at=_NOW + timedelta(seconds=60)))
    reread = await store.get_deployment(bot.id)
    evidence = book_protection_evidence(reread, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == 0
    assert evidence.observed_at is None
    assert evidence.verified_at is None
    assert evidence.freshness == "unknown"
    assert "unknown_not_confirmed" in evidence.reasons
