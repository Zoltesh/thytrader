"""Daily-loss marks are read only for held products, and a failed read stays unknown."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from thytrader.execution_worker.strategy_step_common import _portfolio_marks
from thytrader.market_data.models import Candle
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Position,
    PositionSide,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.market_data.service import MarketDataService

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 3, 2, 6, tzinfo=UTC)


def _snapshot(product_id: str, *, status: DeploymentStatus, held: bool) -> DeploymentSnapshot:
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("c" * 64),
        strategy_id=uuid4(),
        product_id=product_id,
        mode=DeploymentMode.PAPER,
        status=status,
        cash=Decimal("1000"),
        phase=RuntimePhase.OPEN if held else RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        kind=DeploymentKind.STRATEGY,
        timeframe="1h",
    )
    if not held:
        return DeploymentSnapshot(deployment=deployment)
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal("1"),
        entry_price=Decimal("10"),
        stop_price=Decimal("9"),
        target_price=None,
        entered_bar=_NOW,
        updated_at=_NOW,
        side=PositionSide.LONG,
        product_id=product_id,
    )
    return DeploymentSnapshot(deployment=deployment, position=position, positions=(position,))


class _Market:
    """Previews by product; a listed product's preview raises like an unavailable market."""

    def __init__(self, failing: set[str]) -> None:
        self.failing = failing
        self.requested: list[str] = []

    async def get_preview(self, product_id: str, interval: object) -> SimpleNamespace:
        del interval
        self.requested.append(product_id)
        if product_id in self.failing:
            raise RuntimeError("market unavailable")
        candle = Candle(
            starts_at=_NOW,
            open=Decimal("11"),
            high=Decimal("12"),
            low=Decimal("10"),
            close=Decimal("11"),
            volume=Decimal("1"),
        )
        return SimpleNamespace(quality=SimpleNamespace(candles=(candle,)))


async def test_a_retired_flat_book_is_never_marked_and_cannot_fail_this_bar() -> None:
    """A stopped flat book on an unavailable market does not stall an unrelated book's bar."""
    market = _Market(failing={"RETIRED-USD"})
    marks = await _portfolio_marks(
        cast("MarketDataService", market),
        portfolio=(
            _snapshot("RETIRED-USD", status=DeploymentStatus.STOPPED, held=False),
            _snapshot("ETH-USD", status=DeploymentStatus.RUNNING, held=True),
        ),
        current_product_id="BTC-USD",
        current_close=Decimal("100"),
    )
    assert marks == {"BTC-USD": Decimal("100"), "ETH-USD": Decimal("11")}
    assert "RETIRED-USD" not in market.requested


async def test_a_failed_mark_on_held_inventory_stays_unknown() -> None:
    """A held product whose read fails is left unmarked for admission to treat as unknown."""
    market = _Market(failing={"ETH-USD"})
    marks = await _portfolio_marks(
        cast("MarketDataService", market),
        portfolio=(_snapshot("ETH-USD", status=DeploymentStatus.RUNNING, held=True),),
        current_product_id="BTC-USD",
        current_close=Decimal("100"),
    )
    assert marks == {"BTC-USD": Decimal("100")}
