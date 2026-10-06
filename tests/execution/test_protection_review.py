"""Conservative identity, trailing geometry and observation regressions for ADR 0112."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
import pytest

from tests.execution.test_protection_evidence import _NOW, _deployment, _order, _position
from thytrader.execution import protection
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    PositionSide,
)
from thytrader.execution.protection import (
    LOCAL_EVIDENCE_MAX_AGE,
    ProtectionStatus,
    book_protection_evidence,
    protection_evidence_response,
)


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep reporting recency independent of wall time and strategy candle frequency."""
    monkeypatch.setattr(protection, "utc_now", lambda: _NOW)


@pytest.mark.parametrize(
    ("side", "stop", "target"),
    [(PositionSide.LONG, "110", "140"), (PositionSide.SHORT, "90", "60")],
)
@pytest.mark.parametrize("mode", [DeploymentMode.PAPER, DeploymentMode.LIVE])
def test_profitable_trailing_stop_can_cross_entry(
    side: PositionSide, stop: str, target: str, mode: DeploymentMode
) -> None:
    """Trailing ratchets crossing entry remain valid when stop and target are ordered."""
    deployment = _deployment(mode=mode)
    position = _position(
        deployment, product_id="BTC-USD", side=side, entry="100", stop=stop, target=target
    )
    order = _order(deployment, position, stop=stop, price=target)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(order,))
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.COVERED
    assert evidence.stop_geometry_valid
    assert evidence.geometry_basis == "working_target"
    assert "stop_geometry_invalid" not in evidence.reasons


@pytest.mark.parametrize(
    "terminal", [OrderStatus.CANCELED, OrderStatus.FILLED, OrderStatus.REJECTED]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_newer_terminal_duplicate_never_resurrects_an_open_row(
    terminal: OrderStatus, reverse: bool
) -> None:
    """Identity folding happens before active-status filtering and ignores tuple order."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    old = _order(deployment, position, stop="3200", price="2700")
    new = replace(
        old,
        id=uuid4(),
        status=terminal,
        updated_at=_NOW + timedelta(seconds=1),
        venue_observed_at=_NOW + timedelta(seconds=1),
    )
    rows = (new, old) if reverse else (old, new)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=rows)
    evidence = book_protection_evidence(
        snapshot, product_id="BTC-USD", position=position, now=_NOW + timedelta(seconds=1)
    )
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.covered_quantity == 0
    assert not evidence.venue_resting
    assert "duplicate_order_ignored" in evidence.reasons


@pytest.mark.parametrize("reverse", [False, True])
def test_newer_unknown_duplicate_overrides_older_open(reverse: bool) -> None:
    """A latest ambiguous venue observation cannot inherit the older confirmed remainder."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    old = _order(deployment, position, stop="3200", price="2700")
    new = replace(
        old,
        id=uuid4(),
        status=OrderStatus.UNKNOWN,
        updated_at=_NOW + timedelta(seconds=1),
        venue_observed_at=None,
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(new, old) if reverse else (old, new)
    )
    evidence = book_protection_evidence(
        snapshot, product_id="BTC-USD", position=position, now=_NOW + timedelta(seconds=1)
    )
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == 0
    assert not evidence.venue_resting


@pytest.mark.parametrize("status", [OrderStatus.UNKNOWN, OrderStatus.CANCELED])
def test_tied_status_conflict_is_unverified(status: OrderStatus) -> None:
    """Conflicting observations at the same instant fail closed instead of ranking OPEN first."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    first = _order(deployment, position, stop="3200", price="2700")
    conflict = replace(first, id=uuid4(), status=status)
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(first, conflict)
    )
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == 0


def test_duplicate_partial_rows_use_latest_remainder_not_sum_or_old_maximum() -> None:
    """Newer partial-fill evidence wins; duplicate rows never increase coverage."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    first = _order(deployment, position, stop="3200", price="2700", filled="0.1")
    newer = replace(
        first,
        id=uuid4(),
        filled_quantity=Decimal("0.3"),
        updated_at=_NOW + timedelta(seconds=1),
        venue_observed_at=_NOW + timedelta(seconds=1),
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(first, newer)
    )
    evidence = book_protection_evidence(
        snapshot, product_id="BTC-USD", position=position, now=_NOW + timedelta(seconds=1)
    )
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.covered_quantity == Decimal("0.2")
    assert evidence.uncovered_quantity == Decimal("0.3")


def test_parent_child_alias_is_folded_with_newer_terminal_venue_row() -> None:
    """A missing direct venue id does not let a child alias evade newer cancellation evidence."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    parent = _order(
        deployment,
        position,
        kind=OrderKind.POST_ONLY_LIMIT,
        status=OrderStatus.FILLED,
        side=OrderSide.SELL,
        venue_order_id="entry",
    )
    parent = replace(parent, attached_child_venue_order_id="attached-stop")
    child = _order(
        deployment,
        position,
        stop="3200",
        price="2700",
        venue_order_id=None,
        parent_order_id=parent.id,
    )
    terminal = replace(
        child,
        id=uuid4(),
        venue_order_id="attached-stop",
        parent_order_id=None,
        status=OrderStatus.CANCELED,
        updated_at=_NOW + timedelta(seconds=1),
        venue_observed_at=_NOW + timedelta(seconds=1),
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(parent, child, terminal)
    )
    evidence = book_protection_evidence(
        snapshot, product_id="BTC-USD", position=position, now=_NOW + timedelta(seconds=1)
    )
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.covered_quantity == 0


def test_open_without_venue_identity_is_unverified() -> None:
    """Order's dataclass accepts OPEN without a venue id, so reporting must verify identity."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    order = _order(deployment, position, stop="3200", price="2700", venue_order_id=None)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(order,))
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == 0
    assert "venue_identity_missing" in evidence.reasons


@pytest.mark.parametrize("bad_time", ["stale", "naive", "future", "missing"])
def test_stale_missing_or_future_local_timestamp_is_not_confirmed(bad_time: str) -> None:
    """Venue age uses reporting clock; a fresh local write cannot renew any bad observation."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    times = {
        "stale": _NOW - LOCAL_EVIDENCE_MAX_AGE - timedelta(microseconds=1),
        "naive": _NOW.replace(tzinfo=None),
        "future": _NOW + timedelta(seconds=1),
        "missing": None,
    }
    order = _order(
        deployment, position, stop="3200", price="2700", venue_observed_at=times[bad_time]
    )
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(order,))
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == 0
    assert evidence.verified_at is None
    assert evidence.freshness == ("stale" if bad_time == "stale" else "unknown")
    assert evidence.evaluated_at == _NOW
    assert evidence.observation_source == (
        "venue_order_state" if bad_time in {"stale", "future"} else "persisted_order"
    )
    if bad_time == "stale":
        assert "venue_evidence_stale" in evidence.reasons
    assert evidence.observed_at != order.updated_at


def test_recent_local_timestamp_is_never_claimed_as_venue_verification() -> None:
    """A recent local write cannot prove venue observation for a legacy stop."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    order = _order(
        deployment, position, stop="3200", price="2700", updated_at=_NOW, venue_observed_at=None
    )
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(order,))
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == 0
    assert evidence.observed_at is None
    assert evidence.verified_at is None
    assert evidence.freshness == "unknown"
    assert evidence.freshness_max_age_seconds == 120
    assert "local_observation_only" in evidence.reasons


@pytest.mark.parametrize("kind", [OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE])
@pytest.mark.parametrize("full", [True, False])
def test_intent_label_or_trigger_alone_cannot_make_a_limit_into_a_stop(
    kind: OrderKind, full: bool
) -> None:
    """Even matching stop fields on a STOP intent need an actual executable venue stop kind."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    order = _order(deployment, position, kind=kind, stop="3200", price="2700")
    intent = OrderIntent(
        id=order.intent_id,
        deployment_id=deployment.id,
        client_order_id=order.client_order_id,
        purpose=IntentPurpose.STOP,
        side=order.side,
        kind=kind,
        quantity=order.quantity,
        created_at=_NOW,
        candle_starts_at=_NOW,
        stop_trigger_price=Decimal("3200"),
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(order,),
        intents=(intent,) if full else (),
    )
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.covered_quantity == 0
    assert "unsupported_stop_kind" in evidence.reasons


@pytest.mark.parametrize("limit", [None, "3100"])
def test_missing_or_wrong_side_stop_limit_price_does_not_prove_executable_geometry(
    limit: str | None,
) -> None:
    """A buy stop-limit needs its limit at or above the trigger, not an invented entry anchor."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT, target=None)
    order = _order(deployment, position, kind=OrderKind.STOP_LIMIT, stop="3200", price=limit)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(order,))
    evidence = book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    assert evidence.status is (
        ProtectionStatus.UNKNOWN if limit is None else ProtectionStatus.UNPROTECTED
    )
    assert not evidence.stop_geometry_valid
    assert evidence.covered_quantity == 0


def test_strict_evidence_response_rejects_extra_and_nondecimal_quantities() -> None:
    """Public evidence forbids accidental fields and approximate or negative quantities."""
    deployment = _deployment()
    position = _position(deployment, product_id="BTC-USD", side=PositionSide.SHORT)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,))
    payload = protection_evidence_response(
        book_protection_evidence(snapshot, product_id="BTC-USD", position=position)
    ).model_dump()
    for value in ("NaN", "-1", "1e3", 1.5):
        with pytest.raises(ValidationError):
            protection.ProtectionEvidenceResponse.model_validate(
                {**payload, "covered_quantity": value}
            )
    with pytest.raises(ValidationError):
        protection.ProtectionEvidenceResponse.model_validate({**payload, "not_a_field": True})
