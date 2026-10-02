"""Last-bar marks and gross unrealized PnL for open books (ADR 0098)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.execution.book_marks import (
    last_bar_marks,
    marks_by_deployment,
    signed_unrealized_pnl,
    unrealized_pnl,
)
from thytrader.execution.decision_store import DisabledDecisionJournalStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Position,
    PositionSide,
    RuntimePhase,
)

_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _snapshot(*, side: PositionSide) -> DeploymentSnapshot:
    """One paper bot holding 2 ETH from 3,000 on the given side."""
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="ETH-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("1000"),
        phase=RuntimePhase.OPEN,
        created_at=_AT,
        updated_at=_AT,
    )
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal("2"),
        entry_price=Decimal("3000"),
        stop_price=Decimal("2900"),
        target_price=None,
        entered_bar=_AT,
        updated_at=_AT,
        side=side,
        product_id="ETH-USD",
    )
    return DeploymentSnapshot(deployment=deployment, positions=(position,))


def test_unrealized_pnl_is_signed_by_side() -> None:
    """A long gains when the mark rises; a short gains when it falls."""
    long_book = _snapshot(side=PositionSide.LONG).positions[0]
    short_book = _snapshot(side=PositionSide.SHORT).positions[0]
    assert unrealized_pnl(long_book, Decimal("3010")) == Decimal(20)
    assert unrealized_pnl(short_book, Decimal("3010")) == Decimal(-20)
    assert signed_unrealized_pnl(
        quantity=Decimal("2"),
        entry_price=Decimal("3000"),
        side=PositionSide.SHORT,
        mark=Decimal("2990"),
    ) == Decimal(20)


@pytest.mark.anyio
async def test_a_journal_outage_leaves_books_unmarked() -> None:
    """A disabled journal yields no marks instead of failing the read."""
    snapshot = _snapshot(side=PositionSide.LONG)
    journal = DisabledDecisionJournalStore()
    assert await last_bar_marks(journal, snapshot) == {}
    assert await marks_by_deployment(journal, (snapshot,)) == {}
