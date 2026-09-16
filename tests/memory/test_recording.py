"""Record and compose why-trade rows from intent persist and the ledger."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    OrderIntent,
    OrderKind,
    OrderSide,
    RuntimePhase,
)
from thytrader.execution.store import DisabledExecutionStore
from thytrader.execution.trade_reason_scope import TradeReasonScope
from thytrader.memory.recording import (
    _record_from_submit,
    compose_trade_reasons,
    notes_from_json,
    notes_to_json,
    signal_kind_for,
)
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.memory.trade_reasons import (
    TradeReasonNote,
    TradeReasonNoteOrigin,
    TradeReasonOrigin,
    TradeReasonRecord,
    TradeReasonRisk,
    TradeReasonSignal,
    TradeReasonSignalKind,
    TradeReasonStrategy,
)

_FP = "sha256:" + ("b" * 64)
_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _record() -> TradeReasonRecord:
    """One frozen strategy why-trade row without ledger facts."""
    return TradeReasonRecord(
        created_at=_NOW,
        origin=TradeReasonOrigin.RUNTIME,
        intent_id=uuid4(),
        deployment_id=uuid4(),
        deployment_kind="strategy",
        mode="paper",
        product_id="BTC-USD",
        purpose="entry",
        side="buy",
        strategy=TradeReasonStrategy(
            strategy_id=uuid4(),
            strategy_fingerprint=_FP,
            name="ref",
            version=1,
        ),
        signal=TradeReasonSignal(
            kind=TradeReasonSignalKind.STRATEGY_ENTRY,
            last_signal="matched",
            candle_starts_at=_NOW,
            timeframe="1h",
        ),
        risk=TradeReasonRisk(
            decision="allow",
            reason_code="ALLOWED",
            detail="Admitted.",
            policy_fingerprint=_FP,
            policy_source="compiled_default",
        ),
    )


def test_signal_kind_maps_purpose_and_book() -> None:
    """Entry on a discretionary book is not a strategy_entry."""
    assert (
        signal_kind_for(IntentPurpose.ENTRY, DeploymentKind.STRATEGY)
        is TradeReasonSignalKind.STRATEGY_ENTRY
    )
    assert (
        signal_kind_for(IntentPurpose.ENTRY, DeploymentKind.DISCRETIONARY)
        is TradeReasonSignalKind.DISCRETIONARY
    )
    assert (
        signal_kind_for(IntentPurpose.STOP, DeploymentKind.STRATEGY) is TradeReasonSignalKind.STOP
    )


def test_notes_json_roundtrip() -> None:
    """Stored notes JSON revalidates as attributed notes."""
    notes = (
        TradeReasonNote(
            origin=TradeReasonNoteOrigin.HUMAN,
            body="Manual fade.",
            recorded_at=_NOW,
        ),
    )
    assert notes_from_json(notes_to_json(notes)) == notes


def test_compose_without_ledger_marks_unavailable() -> None:
    """Missing execution storage does not invent fills."""
    record = _record()
    composed = asyncio.run(compose_trade_reasons((record,), DisabledExecutionStore()))
    assert composed[0].reconcile.ledger_available is False
    assert composed[0].reconcile.fills == ()


def test_in_memory_store_ignores_duplicate_intent_ids() -> None:
    """Why-trade rows are unique per intent id."""
    store = InMemoryExperientialMemoryStore()
    first = _record()
    duplicate = first.model_copy(update={"id": uuid4()})
    assert asyncio.run(store.append_trade_reason(first)).id == first.id
    assert asyncio.run(store.append_trade_reason(duplicate)).id == first.id
    listed = asyncio.run(store.list_trade_reasons())
    assert len(listed) == 1


def test_record_from_submit_uses_intent_product_not_primary() -> None:
    """F30: a secondary-book intent must not inherit the deployment primary product."""
    now = _NOW
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint=_FP,
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        kind=DeploymentKind.STRATEGY,
        timeframe="1h",
    )
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id="eth-entry",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.5"),
        created_at=now,
        candle_starts_at=now,
        product_id="ETH-USD",
    )
    scope = TradeReasonScope(
        store=InMemoryExperientialMemoryStore(),
        policy_fingerprint=_FP,
        policy_source="compiled_default",
        risk_decision="allow",
        risk_reason_code="ALLOWED",
        risk_detail="Admitted.",
        strategy_id=deployment.strategy_id,
        strategy_fingerprint=_FP,
        strategy_name="ref",
        strategy_version=1,
        timeframe="1h",
    )
    record = _record_from_submit(
        intent=intent,
        snapshot=DeploymentSnapshot(deployment=deployment),
        scope=scope,
    )
    assert record.product_id == "ETH-USD"
