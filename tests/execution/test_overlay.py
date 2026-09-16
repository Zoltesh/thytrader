"""Per-product overlay must not stamp parent last_evaluated_bar mid-lockstep."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from thytrader.execution.ids import uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.execution.overlay import InstrumentScopedStore


@pytest.mark.anyio
async def test_overlay_save_does_not_copy_last_evaluated_bar_onto_parent() -> None:
    """Worker lockstep stamps the parent bar only after every covered product finishes."""
    store = InMemoryExecutionStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    overlay_bar = datetime(2026, 1, 1, 1, tzinfo=UTC)
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=None,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        paper_starting_cash=Decimal("10000"),
    )
    await store.create_deployment(deployment)
    scoped = InstrumentScopedStore(store, "ETH-USD")
    await scoped.save_deployment(replace(deployment, last_evaluated_bar=overlay_bar))
    parent = await store.get_deployment(deployment.id)
    assert parent.deployment.last_evaluated_bar is None
    eth = next(item for item in parent.instrument_runtimes if item.product_id == "ETH-USD")
    assert eth.last_evaluated_bar == overlay_bar
    assert parent.deployment.product_id == "BTC-USD"
