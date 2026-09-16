"""Unit tests for why-trade journal records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
import pytest

from thytrader.memory.trade_reasons import (
    TradeReasonOrigin,
    TradeReasonRecord,
    TradeReasonRisk,
    TradeReasonSignal,
    TradeReasonSignalKind,
    TradeReasonStrategy,
)

_FP = "sha256:" + ("a" * 64)
_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _record(**overrides: object) -> TradeReasonRecord:
    """Build one valid strategy why-trade row."""
    payload: dict[str, object] = {
        "created_at": _NOW,
        "origin": TradeReasonOrigin.RUNTIME,
        "intent_id": uuid4(),
        "deployment_id": uuid4(),
        "deployment_kind": "strategy",
        "mode": "paper",
        "product_id": "BTC-USD",
        "purpose": "entry",
        "side": "buy",
        "strategy": TradeReasonStrategy(
            strategy_id=uuid4(),
            strategy_fingerprint=_FP,
            name="ref",
            version=1,
        ),
        "signal": TradeReasonSignal(
            kind=TradeReasonSignalKind.STRATEGY_ENTRY,
            last_signal="matched",
            candle_starts_at=_NOW,
            timeframe="1h",
        ),
        "risk": TradeReasonRisk(
            decision="allow",
            reason_code="ALLOWED",
            detail="Admitted by the active risk policy.",
            policy_fingerprint=_FP,
            policy_source="compiled_default",
        ),
    }
    payload.update(overrides)
    return TradeReasonRecord.model_validate(payload)


def test_strategy_books_require_full_published_identity() -> None:
    """Strategy deployments freeze id, fingerprint, name, and version together."""
    _record()
    with pytest.raises(ValidationError, match="published strategy identity"):
        TradeReasonStrategy(strategy_id=uuid4(), name="ref", version=1)
    with pytest.raises(ValidationError, match="strategy deployments require"):
        _record(strategy=None)


def test_discretionary_books_omit_published_strategy() -> None:
    """Discretionary why-trade rows cannot cite a published strategy."""
    row = _record(
        origin=TradeReasonOrigin.HUMAN,
        deployment_kind="discretionary",
        strategy=None,
        signal=TradeReasonSignal(
            kind=TradeReasonSignalKind.DISCRETIONARY,
            last_signal="discretionary",
            candle_starts_at=_NOW,
            timeframe="5m",
        ),
    )
    assert row.strategy is None
    with pytest.raises(ValidationError, match="discretionary books cannot cite"):
        _record(deployment_kind="discretionary")


def test_rejects_interpolated_timeframe_and_naive_datetimes() -> None:
    """Venue clocks only; UTC timestamps only."""
    with pytest.raises(ValidationError, match="ingested venue clock"):
        TradeReasonSignal(
            kind=TradeReasonSignalKind.STRATEGY_ENTRY,
            candle_starts_at=_NOW,
            timeframe="3h",
        )
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        _record(created_at=datetime(2026, 9, 16, 12, 0))  # noqa: DTZ001
