"""Supervision stop-protection findings from complete deployment snapshots.

Classifies stop cover (uncovered, unknown, or voided by triggered stops), triggered but
unfilled stops, and unresolved inventory economics, and decides which prior protection
alerts complete evidence may clear. Unknown evidence never counts as a recovery.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.alerts.supervision_deadlines import _due_closed_start, _duration_for
from thytrader.trading.geometry import entry_bar_bucket
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    OrderSide,
    OrderStatus,
    PositionSide,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.trading.protection import (
    book_inventory_reasons,
    book_position_state,
    book_protection_evidence,
    live_stop_absent,
)
from thytrader.trading.protection_models import ProtectionStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.alerts.models import OperatorAlert
    from thytrader.market_data.models import Candle
    from thytrader.trading.models import DeploymentSnapshot, Order, Position


_STOP_TRIGGER_WATCH_STATUSES = frozenset(
    {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.UNKNOWN}
)


def _sticky_trigger_ids(
    deployment: Deployment, snapshot: DeploymentSnapshot, previous: Sequence[OperatorAlert]
) -> frozenset[UUID]:
    """Keep previously observed trigger failures until orders leave the watch set."""
    prior = {item.subject for item in previous if item.code is AlertCode.STOP_TRIGGERED_UNFILLED}
    return frozenset(
        order.id
        for order in snapshot.orders
        if order.status in _STOP_TRIGGER_WATCH_STATUSES
        and order.filled_quantity < order.quantity
        and f"{deployment.id}:{order.id}" in prior
    )


def _snapshot_checks(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    previous: Sequence[OperatorAlert],
    candles: Mapping[tuple[str, str], tuple[Candle, ...]],
    *,
    now: datetime,
) -> tuple[AlertCheck, ...]:
    """Authorize exact clears from full inventory and fresh non-warming candle evidence."""
    evaluated: list[AlertCheck] = []
    positions = {
        resolved_product_id(item.product_id, deployment) for item in snapshot_positions(snapshot)
    }
    products = (
        positions
        | {deployment.product_id}
        | {item.product_id for item in snapshot.instrument_runtimes}
        | {item.product_id for item in previous if item.product_id is not None}
    )
    for product in products:
        uncertain = _cover_evidence_unknown(deployment, snapshot, product, candles, now=now)
        evaluated.extend(
            AlertCheck(code, f"{deployment.id}:{product}")
            for code in (AlertCode.STOP_UNCOVERED, AlertCode.STOP_COVERAGE_UNKNOWN)
            if not uncertain
        )
    watched = {
        f"{deployment.id}:{order.id}"
        for order in snapshot.orders
        if order.status in _STOP_TRIGGER_WATCH_STATUSES and order.filled_quantity < order.quantity
    }
    # A still-unfilled triggered stop cannot recover on price rebound or missing candles.
    terminal_subjects = {f"{deployment.id}:{order.id}" for order in snapshot.orders} - watched
    terminal_subjects.update(
        item.subject
        for item in previous
        if item.code is AlertCode.STOP_TRIGGERED_UNFILLED and item.subject not in watched
    )
    evaluated.extend(
        AlertCheck(AlertCode.STOP_TRIGGERED_UNFILLED, subject)
        for subject in terminal_subjects
        if _trigger_recovery_proved(snapshot, subject, previous)
    )
    return tuple(evaluated)


def _trigger_recovery_proved(
    snapshot: DeploymentSnapshot, subject: str, previous: Sequence[OperatorAlert]
) -> bool:
    """Terminal status/removal alone cannot settle unresolved product economics."""
    product = next(
        (
            resolved_product_id(order.product_id, snapshot.deployment)
            for order in snapshot.orders
            if f"{snapshot.deployment.id}:{order.id}" == subject
        ),
        None,
    )
    if product is None:
        product = next((row.product_id for row in previous if row.subject == subject), None)
    if product is not None:
        return not book_inventory_reasons(snapshot, product_id=product)
    return not _unresolved_book_findings(snapshot)


def _unexplained_missing_positions(snapshot: DeploymentSnapshot) -> bool:
    """Legacy occupied phases without any inventory evidence prove no snapshot recovery."""
    return (
        snapshot.deployment.phase in {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}
        and not snapshot_positions(snapshot)
        and not _unresolved_book_findings(snapshot)
    )


def _unresolved_book_findings(snapshot: DeploymentSnapshot) -> tuple[SupervisionFinding, ...]:
    """Report durable missing projection/economics without claiming an executable stop."""
    deployment = snapshot.deployment
    products = {
        deployment.product_id,
        *(resolved_product_id(order.product_id, deployment) for order in snapshot.orders),
        *(resolved_product_id(row.product_id, deployment) for row in snapshot_positions(snapshot)),
        *(row.product_id for row in snapshot.instrument_runtimes),
    }
    return tuple(
        SupervisionFinding(
            code=AlertCode.STOP_COVERAGE_UNKNOWN,
            scope=AlertScope.DEPLOYMENT,
            subject=f"{deployment.id}:{product}",
            severity=AlertSeverity.WARNING,
            detail=(
                f"{deployment.mode.value} book on {product} has unresolved inventory projection, "
                "occupied runtime, or fill economics; stop quantity and cover cannot be verified. "
                "Unknown is not flatness or recovery."
            ),
            deployment_id=deployment.id,
            product_id=product,
        )
        for product in sorted(products)
        if book_inventory_reasons(snapshot, product_id=product)
    )


def _cover_evidence_unknown(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    product: str,
    candles: Mapping[tuple[str, str], tuple[Candle, ...]],
    *,
    now: datetime,
) -> bool:
    """An active live stop needs fresh bar evidence before lost cover can clear."""
    if book_inventory_reasons(snapshot, product_id=product):
        return True
    if deployment.mode is not DeploymentMode.LIVE:
        return False
    position = next(
        (
            item
            for item in snapshot_positions(snapshot)
            if resolved_product_id(item.product_id, deployment) == product
        ),
        None,
    )
    evidence = book_protection_evidence(snapshot, product_id=product, position=position, now=now)
    if evidence.status is ProtectionStatus.UNKNOWN:
        # A locally plausible order without current venue evidence is not proof
        # that a previously uncovered book recovered, even with complete candles.
        return True
    stops = tuple(
        order
        for order in snapshot.orders
        if resolved_product_id(order.product_id, deployment) == product
        and order.status in _STOP_TRIGGER_WATCH_STATUSES
        and order.stop_trigger_price is not None
    )
    if not stops:
        return False
    if deployment.timeframe is None:
        return True
    available = candles.get((product, deployment.timeframe), ())
    if not available or available[-1].starts_at < _due_closed_start(now, deployment.timeframe):
        return True
    duration = _duration_for(deployment.timeframe)
    if any(right.starts_at - left.starts_at != duration for left, right in pairwise(available)):
        return True
    return any(
        _creation_interval_unknown(order, available, deployment.timeframe) for order in stops
    )


def _creation_interval_unknown(order: Order, available: tuple[Candle, ...], timeframe: str) -> bool:
    """A missing/crossed creation bar cannot prove a stop was never consumed after creation.

    Crossing in a bar that began before creation is ambiguous, not a positive
    trigger finding. Its full range can still prove a negative when never touched.
    """
    bucket = entry_bar_bucket(order.created_at, timeframe)
    creation_bar = next((bar for bar in available if bar.starts_at == bucket), None)
    trigger = order.stop_trigger_price
    if creation_bar is None or trigger is None:
        return True
    return creation_bar.starts_at < order.created_at and (
        creation_bar.low <= trigger
        if order.side is OrderSide.SELL
        else creation_bar.high >= trigger
    )


def _protection_findings(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    triggered: frozenset[UUID],
    *,
    now: datetime,
) -> tuple[SupervisionFinding, ...]:
    """Alert uncovered or unverifiable exit cover on occupied books.

    A live book whose only resting closing-side stops have all triggered without
    filling no longer has verified cover, even though the venue still lists the
    orders as working, so ``STOP_UNCOVERED`` fires alongside the per-order
    ``STOP_TRIGGERED_UNFILLED`` finding. Unresolved inventory makes cover unknown, but a
    live book with no working stop at all stays ``STOP_UNCOVERED``.
    """
    findings: list[SupervisionFinding] = []
    for position in snapshot_positions(snapshot):
        product_id = resolved_product_id(position.product_id, deployment)
        evidence = book_protection_evidence(
            snapshot, product_id=product_id, position=position, now=now
        )
        state = book_position_state(
            snapshot,
            product_id=product_id,
            position=position,
            phase=deployment.phase,
            evidence=evidence,
        )
        status = evidence.status.value
        if status == "unknown" and live_stop_absent(
            snapshot, product_id=product_id, position=position, now=now
        ):
            status = "unprotected"
        if status not in {"unprotected", "unknown"}:
            if not _cover_voided_by_triggered_stops(
                deployment, snapshot, product_id=product_id, position=position, triggered=triggered
            ):
                continue
            findings.append(
                SupervisionFinding(
                    code=AlertCode.STOP_UNCOVERED,
                    scope=AlertScope.DEPLOYMENT,
                    subject=f"{deployment.id}:{product_id}",
                    severity=AlertSeverity.CRITICAL,
                    detail=_trigger_voided_detail(deployment, product_id, position.side.value),
                    deployment_id=deployment.id,
                    product_id=product_id,
                )
            )
            continue
        code = (
            AlertCode.STOP_UNCOVERED if status == "unprotected" else AlertCode.STOP_COVERAGE_UNKNOWN
        )
        severity = (
            AlertSeverity.CRITICAL
            if code is AlertCode.STOP_UNCOVERED and deployment.mode.value == "live"
            else AlertSeverity.WARNING
        )
        findings.append(
            SupervisionFinding(
                code=code,
                scope=AlertScope.DEPLOYMENT,
                subject=f"{deployment.id}:{product_id}",
                severity=severity,
                detail=_uncovered_detail(deployment, position, product_id, state.value),
                deployment_id=deployment.id,
                product_id=product_id,
            )
        )
    return tuple(findings)


def _cover_voided_by_triggered_stops(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    position: Position,
    triggered: frozenset[UUID],
) -> bool:
    """True when every resting closing-side stop on one product triggered unfilled.

    Only live books lose cover this way: paper stops are enforced synthetically
    on every closed bar regardless of the resting paper order. Any still-working
    closing-side order without a consumed trigger (for example a take-profit)
    keeps the book covered.
    """
    if deployment.mode is not DeploymentMode.LIVE:
        return False
    closing = OrderSide.SELL if position.side is PositionSide.LONG else OrderSide.BUY
    watching = tuple(
        order
        for order in snapshot.orders
        if resolved_product_id(order.product_id, deployment) == product_id
        and order.side is closing
        and order.status in _STOP_TRIGGER_WATCH_STATUSES
    )
    if not watching:
        return False
    return all(order.id in triggered for order in watching)


def _trigger_voided_detail(deployment: Deployment, product_id: str, side: str) -> str:
    """Describe cover lost to a triggered-but-unfilled stop without quantities."""
    return (
        f"Open {deployment.mode.value} book on {product_id} (side {side}) has no verified "
        "resting exit: its protective stop triggered on the latest closed bar without "
        "filling. Risk-reducing exits are not blocked; new entries are."
    )


def _uncovered_detail(
    deployment: Deployment, position: Position, product_id: str, state: str
) -> str:
    """Describe missing cover without quantities or prices."""
    mode = deployment.mode.value
    if state == "unknown":
        return (
            f"Open {mode} book on {product_id} has protective orders whose venue state is "
            "unreconciled; cover cannot be verified. Reconciliation continues; treat as "
            "unverified, not safe."
        )
    return (
        f"Open {mode} book on {product_id} (side {position.side.value}) has no verified "
        "resting exit: no attached bracket child and no working protective order. "
        "Risk-reducing exits are not blocked; new entries are."
    )


def _stop_trigger_findings(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    triggered: Mapping[UUID, Decimal],
) -> tuple[SupervisionFinding, ...]:
    """Alert a resting protective order whose stop trigger traded through but did not fill."""
    findings: list[SupervisionFinding] = []
    timeframe = deployment.timeframe
    if timeframe is None or not triggered:
        return ()
    for order in snapshot.orders:
        latest_close = triggered.get(order.id)
        if latest_close is None:
            continue
        trigger = order.stop_trigger_price
        if trigger is None:
            continue
        product_id = resolved_product_id(order.product_id, deployment)
        severity = (
            AlertSeverity.CRITICAL if deployment.mode.value == "live" else AlertSeverity.WARNING
        )
        findings.append(
            SupervisionFinding(
                code=AlertCode.STOP_TRIGGERED_UNFILLED,
                scope=AlertScope.DEPLOYMENT,
                subject=f"{deployment.id}:{order.id}",
                severity=severity,
                detail=(
                    f"Protective {order.side.value} stop on {product_id} triggered on the "
                    f"observed closed {timeframe} bar (trigger {trigger}, bar close "
                    f"{latest_close}, filled {order.filled_quantity} of {order.quantity}) but "
                    "remains unfilled. Supervision does not escalate to market orders; "
                    "operator review required."
                ),
                deployment_id=deployment.id,
                product_id=product_id,
            )
        )
    return tuple(findings)
