"""Unit tests for experiential-memory records and origin invariants."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError
import pytest

from thytrader.memory.models import (
    ActorOrigin,
    JournalEntry,
    JournalKind,
    LessonOutcome,
    PatternObservation,
    SentimentLabel,
    SentimentSnapshot,
)


def _now() -> datetime:
    """Return a fixed UTC instant for validation tests."""
    return datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_journal_requires_origin_and_rejects_lesson_without_outcome() -> None:
    """Lessons need an outcome; facts cannot carry one."""
    JournalEntry(
        occurred_at=_now(),
        origin=ActorOrigin.HUMAN,
        kind=JournalKind.NOTE,
        title="Paused paper",
        body="Stale candles.",
    )
    with pytest.raises(ValidationError, match="lesson_outcome"):
        JournalEntry(
            occurred_at=_now(),
            origin=ActorOrigin.AGENT,
            kind=JournalKind.LESSON,
            title="Sized too large",
            body="Cut size next time.",
        )
    with pytest.raises(ValidationError, match="lesson_outcome"):
        JournalEntry(
            occurred_at=_now(),
            origin=ActorOrigin.HUMAN,
            kind=JournalKind.NOTE,
            title="Note",
            body="Body",
            lesson_outcome=LessonOutcome.SUCCESS,
        )


def test_sentiment_and_pattern_reject_invalid_product() -> None:
    """Optional product ids must be BASE-USD spot products."""
    with pytest.raises(ValidationError, match="BASE-USD"):
        SentimentSnapshot(
            occurred_at=_now(),
            origin=ActorOrigin.HUMAN,
            label=SentimentLabel.BULLISH,
            product_id="btc-usd",
        )
    PatternObservation(
        occurred_at=_now(),
        origin=ActorOrigin.AGENT,
        pattern_key="morning_gap",
        name="Morning gap",
        hypothesis="Gaps fill on 1h.",
    )
    with pytest.raises(ValidationError):
        PatternObservation(
            occurred_at=_now(),
            origin=ActorOrigin.AGENT,
            pattern_key="Morning-Gap",
            name="Morning gap",
            hypothesis="Gaps fill on 1h.",
        )
