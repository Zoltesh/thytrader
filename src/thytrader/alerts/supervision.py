"""Per-cycle safety supervision findings for durable operator alerts (ADR 0115).

This module recomputes safety observations from durable execution state on every
worker cycle. It never mutates books, never invents market data, and treats
unknown evidence as incomplete (never a recovery). Findings and complete checks feed
``thytrader.alerts.service.AlertService``, which deduplicates them into durable
alert rows.

This module reads the snapshot and candle evidence and orchestrates one cycle; the
row, deadline, and stop-protection findings live in the sibling ``supervision_*``
modules.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    SafetyEvidence,
    SupervisionFinding,
)
from thytrader.alerts.supervision_deadlines import (
    _decision_deadline_finding,
    _lease_stale_finding,
    _lease_state,
    _maintenance_deadline_finding,
)
from thytrader.alerts.supervision_inputs import (
    AlertThresholds,
    ClosedCandleReader,
    DeploymentSnapshotReader,
    SnapshotEvidence,
)
from thytrader.alerts.supervision_protection import (
    _STOP_TRIGGER_WATCH_STATUSES,
    _protection_findings,
    _snapshot_checks,
    _sticky_trigger_ids,
    _stop_trigger_findings,
    _unexplained_missing_positions,
    _unresolved_book_findings,
)
from thytrader.alerts.supervision_rows import _row_findings
from thytrader.trading.models import (
    Deployment,
    OrderSide,
    resolved_product_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.alerts.models import OperatorAlert
    from thytrader.market_data.models import Candle
    from thytrader.trading.models import (
        DeploymentSnapshot,
    )

_logger = logging.getLogger(__name__)


async def gather_safety_findings(
    *,
    deployments: Sequence[Deployment],
    snapshots: DeploymentSnapshotReader,
    closed_candles: ClosedCandleReader,
    now: datetime,
    thresholds: AlertThresholds,
    worker_interval_seconds: int,
    prior_alerts: Sequence[OperatorAlert] = (),
    inventory_authoritative: bool = False,
) -> SafetyEvidence:
    """Gather findings and exact complete checks, preserving unknown evidence.

    Snapshot readers must return full snapshots (never paginated order/position
    summaries). Failed, wrong-book or stale reads prove no recovery. Subset book
    inventories default to non-authoritative; removal resolves only with an
    explicitly authoritative inventory. Empty/late/warming candle reads cannot
    clear previously uncovered cover. Triggered-unfilled orders remain unsafe
    until their full snapshot proves terminal/fill/removal, not a price rebound.
    """
    ordered: dict[tuple[str, str], SupervisionFinding] = {}
    evaluated: set[AlertCheck] = set()
    candle_cache: dict[tuple[str, str], tuple[Candle, ...]] = {}
    listed = {str(item.id) for item in deployments}
    if inventory_authoritative:
        evaluated.update(
            AlertCheck(row.code, row.subject)
            for row in prior_alerts
            if row.subject.split(":", 1)[0] not in listed and row.scope is AlertScope.DEPLOYMENT
        )
    for row in deployments:
        read = await _snapshot_or_none(snapshots, row.id)
        if read is None or read.snapshot.deployment.id != row.id:
            continue
        snapshot = read.snapshot
        deployment = snapshot.deployment
        if deployment.revision < row.revision:
            continue
        previous = tuple(
            alert for alert in prior_alerts if alert.subject.split(":", 1)[0] == str(row.id)
        )
        for finding in (*_row_findings(deployment), *_known_unresolved_findings(read)):
            _keep(ordered, finding)
        evaluated.update(AlertCheck(code, str(row.id)) for code in _ROW_CHECKS)
        _keep(ordered, _decision_deadline_finding(deployment, now=now, thresholds=thresholds))
        lease_state = _lease_state(
            deployment, now=now, worker_interval_seconds=worker_interval_seconds
        )
        _keep(
            ordered,
            _lease_stale_finding(
                deployment, worker_interval_seconds=worker_interval_seconds, state=lease_state
            ),
        )
        _keep(ordered, _maintenance_deadline_finding(deployment, state=lease_state))
        if not read.complete or not snapshot.accounting_complete:
            continue
        if _unexplained_missing_positions(snapshot):
            continue
        triggered = await _trigger_consumed_orders(
            deployment, snapshot, closed_candles, candle_cache
        )
        sticky = _sticky_trigger_ids(deployment, snapshot, previous)
        trigger_ids = frozenset(triggered) | sticky
        for finding in _protection_findings(deployment, snapshot, trigger_ids, now=now):
            _keep(ordered, finding)
        for finding in _stop_trigger_findings(deployment, snapshot, triggered):
            _keep(ordered, finding)
        evaluated.update(_snapshot_checks(deployment, snapshot, previous, candle_cache, now=now))
    return SafetyEvidence(tuple(ordered.values()), tuple(evaluated))


_ROW_CHECKS = (
    AlertCode.BOOK_PAUSED_MISMATCH,
    AlertCode.BREAKER_LATCHED,
    AlertCode.DECISION_DEADLINE_MISSED,
    AlertCode.WORKER_LEASE_STALE,
    AlertCode.MAINTENANCE_DEADLINE_MISSED,
)


def _known_unresolved_findings(read: SnapshotEvidence) -> tuple[SupervisionFinding, ...]:
    """Only retained complete inventories support positive derived projection findings."""
    if not read.complete or not read.snapshot.accounting_complete:
        return ()
    return _unresolved_book_findings(read.snapshot)


def _keep(
    ordered: dict[tuple[str, str], SupervisionFinding], finding: SupervisionFinding | None
) -> None:
    """Record the first finding for one identity."""
    if finding is None:
        return
    ordered.setdefault((finding.code.value, finding.subject), finding)


async def _trigger_consumed_orders(
    deployment: Deployment,
    snapshot: DeploymentSnapshot,
    closed_candles: ClosedCandleReader,
    candle_cache: dict[tuple[str, str], tuple[Candle, ...]],
) -> dict[UUID, Decimal]:
    """Map each protective order whose stop trigger traded to the latest close.

    A bar that began before the order existed never consumes its trigger, and
    unavailable candles consume nothing (no invented market data).
    """
    consumed: dict[UUID, Decimal] = {}
    timeframe = deployment.timeframe
    if timeframe is None:
        return consumed
    for order in snapshot.orders:
        if order.status not in _STOP_TRIGGER_WATCH_STATUSES:
            continue
        trigger = order.stop_trigger_price
        if trigger is None or order.filled_quantity >= order.quantity:
            continue
        product_id = resolved_product_id(order.product_id, deployment)
        candles = await _candles_for(
            closed_candles, candle_cache, product_id, timeframe, deployment.created_at
        )
        crossed = tuple(
            candle
            for candle in candles
            if candle.starts_at >= order.created_at
            and (candle.low <= trigger if order.side is OrderSide.SELL else candle.high >= trigger)
        )
        if crossed:
            consumed[order.id] = crossed[-1].close
    return consumed


async def _snapshot_or_none(
    snapshots: DeploymentSnapshotReader, deployment_id: UUID
) -> SnapshotEvidence | None:
    """Read explicit completeness, or accept the full ExecutionStore snapshot contract."""
    try:
        read = await snapshots.get_deployment(deployment_id)
        return read if isinstance(read, SnapshotEvidence) else SnapshotEvidence(read, complete=True)
    except Exception as error:  # noqa: BLE001 - best-effort supervision evidence only.
        _logger.warning(
            "alert_snapshot_unavailable deployment_id=%s type=%s",
            deployment_id,
            type(error).__name__,
        )
        return None


async def _candles_for(
    closed_candles: ClosedCandleReader,
    candle_cache: dict[tuple[str, str], tuple[Candle, ...]],
    product_id: str,
    timeframe: str,
    deploy_anchor: datetime,
) -> tuple[Candle, ...]:
    """Read cached closed candles for one product clock; empty on failure."""
    key = (product_id, timeframe)
    if key in candle_cache:
        return candle_cache[key]
    try:
        candles = await closed_candles(product_id, timeframe, deploy_anchor)
    except Exception as error:  # noqa: BLE001 - best-effort supervision evidence only.
        _logger.warning(
            "alert_candles_unavailable product_id=%s timeframe=%s type=%s",
            product_id,
            timeframe,
            type(error).__name__,
        )
        candles = ()
    candle_cache[key] = candles
    return candles
