"""Record and compose why-trade rows from intent persist and the ledger."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from thytrader.execution.models import DeploymentKind, IntentPurpose
from thytrader.execution.store import DisabledExecutionStore
from thytrader.memory.recording import (
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
        signal_kind_for(IntentPurpose.STOP, DeploymentKind.STRATEGY)
        is TradeReasonSignalKind.STOP
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
