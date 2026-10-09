"""Why-trade records for in-kind inventory adoption (ADR 0124)."""

from __future__ import annotations

import pytest

from tests.adoption_support import ADOPTED_AT, live_book, records_for
from thytrader.memory.recording import _record_from_submit, signal_kind_for
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.memory.trade_reason_scope import TradeReasonScope
from thytrader.memory.trade_reasons import (
    TradeReasonNoteOrigin,
    TradeReasonOrigin,
    TradeReasonRecord,
    TradeReasonSignalKind,
)
from thytrader.trading.models import DeploymentKind, DeploymentSnapshot, IntentPurpose

_FP = "sha256:" + "d" * 64


@pytest.mark.parametrize("kind", list(DeploymentKind))
def test_adoption_maps_to_its_own_signal_kind_on_every_book(kind: DeploymentKind) -> None:
    """An adoption is neither a strategy entry nor a discretionary order (no ValueError)."""
    assert signal_kind_for(IntentPurpose.ADOPTION, kind) is TradeReasonSignalKind.ADOPTION


def test_adoption_record_keeps_purpose_signal_and_the_operator_note() -> None:
    """The adoption intent is inventory-opening, so the place-order note is journaled."""
    book = live_book(kind=DeploymentKind.DISCRETIONARY)
    intent = records_for(book).intent
    scope = TradeReasonScope(
        store=InMemoryExperientialMemoryStore(),
        policy_fingerprint=_FP,
        policy_source="published",
        risk_decision="allow",
        risk_reason_code="ALLOWED",
        risk_detail="Admitted.",
        discretionary_note="Adopt the DOGE bought by hand last week.",
        note_origin="human",
    )
    record = _record_from_submit(
        intent=intent, snapshot=DeploymentSnapshot(deployment=book), scope=scope
    )
    assert record.purpose == "adoption" and record.side == "buy"
    assert record.signal.kind is TradeReasonSignalKind.ADOPTION
    assert record.origin is TradeReasonOrigin.HUMAN and record.created_at == ADOPTED_AT
    assert [note.origin for note in record.notes] == [TradeReasonNoteOrigin.HUMAN]
    assert TradeReasonRecord.model_validate_json(record.model_dump_json()) == record
