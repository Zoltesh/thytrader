"""The worker-failure alert keeps the exception type, and only the type, as evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.alerts.supervision_rows import failure_error_type, worker_book_failure_finding
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)


def _book() -> Deployment:
    now = datetime(2026, 3, 2, 12, tzinfo=UTC)
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal(0),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        kind=DeploymentKind.STRATEGY,
        timeframe="1h",
    )


def test_the_failure_detail_round_trips_its_error_type() -> None:
    """The type written by the failure finding is read back exactly."""
    finding = worker_book_failure_finding(_book(), error_type="HTTPError")
    assert failure_error_type(finding.detail) == "HTTPError"


@pytest.mark.parametrize(
    "detail",
    [
        "",
        "Entries remain paused after worker failures; manual review required.",
        "WORKER_CONSECUTIVE_FAILURES: entries paused after 3 failed supervision cycles.",
        "Execution cycle failed. Error: not an identifier; with prose.",
    ],
)
def test_details_without_a_recorded_type_yield_none(detail: str) -> None:
    """Prose or pause notes never invent an error type."""
    assert failure_error_type(detail) is None
