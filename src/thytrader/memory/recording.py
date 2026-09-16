"""Record and compose why-trade journals from intent persist and the execution ledger."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.models import (
    DeploymentKind,
    ExecutionStoreError,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderStatus,
    resolved_product_id,
)
from thytrader.execution.trade_reason_scope import current_trade_reason_scope
from thytrader.memory.store import MemoryStoreError
from thytrader.memory.trade_reasons import (
    TRADE_REASON_SCHEMA_VERSION,
    TradeReasonFillFact,
    TradeReasonNote,
    TradeReasonNoteOrigin,
    TradeReasonOrigin,
    TradeReasonReconcile,
    TradeReasonRecord,
    TradeReasonRisk,
    TradeReasonSignal,
    TradeReasonSignalKind,
    TradeReasonStrategy,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.execution.store import ExecutionStore
    from thytrader.execution.trade_reason_scope import TradeReasonScope
    from thytrader.memory.store import ExperientialMemoryStore


def signal_kind_for(
    purpose: IntentPurpose, deployment_kind: DeploymentKind
) -> TradeReasonSignalKind:
    """Map intent purpose and book kind onto a review signal kind."""
    if purpose is IntentPurpose.ENTRY:
        if deployment_kind is DeploymentKind.DISCRETIONARY:
            return TradeReasonSignalKind.DISCRETIONARY
        return TradeReasonSignalKind.STRATEGY_ENTRY
    return TradeReasonSignalKind(purpose.value)


async def maybe_record_submitted_intent(
    *,
    intent: OrderIntent,
    snapshot: DeploymentSnapshot,
) -> None:
    """Persist a why-trade row when attribution is bound. Never blocks the order path."""
    scope = current_trade_reason_scope()
    if scope is None:
        return
    record = _record_from_submit(intent=intent, snapshot=snapshot, scope=scope)
    try:
        await scope.store.append_trade_reason(record)
    except MemoryStoreError:
        return


async def compose_trade_reasons(
    records: tuple[TradeReasonRecord, ...],
    execution: ExecutionStore,
) -> tuple[TradeReasonRecord, ...]:
    """Join live order/fill facts onto frozen why-trade rows."""
    if not records:
        return ()
    snapshots: dict[UUID, DeploymentSnapshot | None] = {}
    composed: list[TradeReasonRecord] = []
    for record in records:
        if record.deployment_id not in snapshots:
            snapshots[record.deployment_id] = await _snapshot_or_none(
                execution, record.deployment_id
            )
        composed.append(_with_reconcile(record, snapshots[record.deployment_id]))
    return tuple(composed)


async def append_attributed_note(
    store: ExperientialMemoryStore,
    *,
    intent_id: UUID,
    origin: TradeReasonNoteOrigin,
    body: str,
    now: datetime | None = None,
) -> TradeReasonRecord:
    """Append one human or agent note to an existing why-trade row."""
    instant = now or utc_now()
    note = TradeReasonNote(origin=origin, body=body, recorded_at=instant)
    return await store.append_trade_reason_note(intent_id, note)


def notes_to_json(notes: tuple[TradeReasonNote, ...]) -> str:
    """Serialize notes for PostgreSQL text storage."""
    return json.dumps([item.model_dump(mode="json") for item in notes], separators=(",", ":"))


def notes_from_json(raw: str) -> tuple[TradeReasonNote, ...]:
    """Revalidate stored notes JSON."""
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise TypeError("notes_json must be a JSON array")
    return tuple(TradeReasonNote.model_validate(item) for item in parsed)


async def _snapshot_or_none(
    execution: ExecutionStore, deployment_id: UUID
) -> DeploymentSnapshot | None:
    """Load one deployment snapshot or omit ledger facts."""
    try:
        return await execution.get_deployment(deployment_id)
    except ExecutionStoreError:
        return None


def _record_from_submit(
    *,
    intent: OrderIntent,
    snapshot: DeploymentSnapshot,
    scope: TradeReasonScope,
) -> TradeReasonRecord:
    """Build one frozen why-trade row. Ledger facts are joined on read."""
    deployment = snapshot.deployment
    return TradeReasonRecord(
        schema_version=TRADE_REASON_SCHEMA_VERSION,
        id=uuid7(intent.created_at),
        created_at=intent.created_at,
        origin=TradeReasonOrigin(intent.origin.value),
        intent_id=intent.id,
        deployment_id=deployment.id,
        deployment_kind=deployment.kind.value,
        mode=deployment.mode.value,
        product_id=resolved_product_id(intent.product_id, deployment),
        purpose=intent.purpose.value,
        side=intent.side.value,
        strategy=_strategy_from_scope(scope, deployment.kind),
        signal=TradeReasonSignal(
            kind=signal_kind_for(intent.purpose, deployment.kind),
            last_signal=deployment.last_signal,
            candle_starts_at=intent.candle_starts_at,
            timeframe=scope.timeframe or deployment.timeframe,
        ),
        risk=_risk_from_scope(scope),
        notes=_notes_from_scope(scope, intent),
    )


def _strategy_from_scope(
    scope: TradeReasonScope, kind: DeploymentKind
) -> TradeReasonStrategy | None:
    """Freeze published identity for strategy books only."""
    if kind is not DeploymentKind.STRATEGY:
        return None
    return TradeReasonStrategy(
        strategy_id=scope.strategy_id,
        strategy_fingerprint=scope.strategy_fingerprint,
        name=scope.strategy_name,
        version=scope.strategy_version,
    )


def _risk_from_scope(scope: TradeReasonScope) -> TradeReasonRisk:
    """Copy bound risk-registry fields onto the frozen record."""
    source = "published" if scope.policy_source == "published" else "compiled_default"
    decision = "allow" if scope.risk_decision == "allow" else "deny"
    return TradeReasonRisk(
        decision=decision,
        reason_code=scope.risk_reason_code,
        detail=scope.risk_detail,
        policy_fingerprint=scope.policy_fingerprint,
        policy_source=source,
    )


def _notes_from_scope(scope: TradeReasonScope, intent: OrderIntent) -> tuple[TradeReasonNote, ...]:
    """Capture an optional place-order note on the entry intent only."""
    if intent.purpose is not IntentPurpose.ENTRY:
        return ()
    if not scope.discretionary_note or not scope.note_origin:
        return ()
    return (
        TradeReasonNote(
            origin=TradeReasonNoteOrigin(scope.note_origin),
            body=scope.discretionary_note,
            recorded_at=intent.created_at,
        ),
    )


def _with_reconcile(
    record: TradeReasonRecord, snapshot: DeploymentSnapshot | None
) -> TradeReasonRecord:
    """Replace ledger facts on a frozen why-trade row."""
    if snapshot is None:
        return record.model_copy(update={"reconcile": TradeReasonReconcile(ledger_available=False)})
    order = next((item for item in snapshot.orders if item.intent_id == record.intent_id), None)
    if order is None:
        return record.model_copy(update={"reconcile": TradeReasonReconcile()})
    fills = tuple(item for item in snapshot.fills if item.order_id == order.id)
    return record.model_copy(update={"reconcile": _reconcile_from(order, fills)})


def _reconcile_from(order: Order, fills: tuple[Fill, ...]) -> TradeReasonReconcile:
    """Project one order and its exact fills into the review payload."""
    facts = tuple(
        TradeReasonFillFact(
            fill_id=item.id,
            price=str(item.price),
            quantity=str(item.quantity),
            fee=str(item.fee),
            filled_at=item.filled_at,
        )
        for item in fills
    )
    return TradeReasonReconcile(
        order_id=order.id,
        order_status=order.status.value,
        filled_quantity=str(order.filled_quantity),
        reject_reason=order.reject_reason,
        unknown_timeout=order.status is OrderStatus.UNKNOWN,
        ledger_available=True,
        fills=facts,
    )
