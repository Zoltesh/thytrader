"""Each book's stop crossing is judged on its own deploy-anchored candle window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.alerts.test_supervision import _candle, _deployment, _position
from thytrader.alerts.models import AlertCode
from thytrader.alerts.supervision import gather_safety_findings
from thytrader.alerts.supervision_inputs import AlertThresholds
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.market_data.models import Candle

_NOW = datetime(2026, 3, 2, 6, 3, tzinfo=UTC)
_CROSSED = datetime(2026, 3, 2, 3, 0, tzinfo=UTC)


class _Snapshots:
    def __init__(self, *snapshots: DeploymentSnapshot) -> None:
        self.by_id = {item.deployment.id: item for item in snapshots}

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Return the prebuilt snapshot for one book."""
        return self.by_id[deployment_id]


async def _anchored(product_id: str, timeframe: str, deploy_anchor: datetime) -> tuple[Candle, ...]:
    """Closed bars from the book's deploy anchor on; the 03:00 bar traded through 95."""
    del product_id, timeframe
    bars = (
        _candle(start=_CROSSED, low=Decimal("94"), high=Decimal("101"), close=Decimal("96")),
        _candle(
            start=_CROSSED + timedelta(hours=1),
            low=Decimal("97"),
            high=Decimal("101"),
            close=Decimal("99"),
        ),
        _candle(
            start=_CROSSED + timedelta(hours=2),
            low=Decimal("97"),
            high=Decimal("101"),
            close=Decimal("99"),
        ),
    )
    return tuple(bar for bar in bars if bar.starts_at >= deploy_anchor)


@pytest.mark.anyio
async def test_a_newer_books_window_does_not_hide_an_older_books_crossing() -> None:
    """A later-anchored window cached for a newer book must not replace an older book's."""
    newer = _deployment(
        mode=DeploymentMode.LIVE,
        phase=RuntimePhase.FLAT,
        created_at=_NOW - timedelta(hours=1, minutes=30),
    )
    older = _deployment(mode=DeploymentMode.LIVE, phase=RuntimePhase.OPEN)
    position = _position(older)
    stop = Order(
        id=uuid4(),
        deployment_id=older.id,
        intent_id=uuid4(),
        client_order_id="stop-older",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.OPEN,
        created_at=_CROSSED - timedelta(hours=1),
        updated_at=_NOW,
        stop_trigger_price=Decimal("95"),
        product_id="BTC-USD",
    )
    newer_stop = Order(
        id=uuid4(),
        deployment_id=newer.id,
        intent_id=uuid4(),
        client_order_id="stop-newer",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.OPEN,
        created_at=_NOW - timedelta(hours=1),
        updated_at=_NOW,
        stop_trigger_price=Decimal("50"),
        product_id="BTC-USD",
    )
    found = await gather_safety_findings(
        deployments=(newer, older),
        snapshots=_Snapshots(
            DeploymentSnapshot(deployment=newer, orders=(newer_stop,)),
            DeploymentSnapshot(
                deployment=older, position=position, positions=(position,), orders=(stop,)
            ),
        ),
        closed_candles=_anchored,
        now=_NOW,
        thresholds=AlertThresholds(),
        worker_interval_seconds=30,
    )
    subjects = {(item.code, item.subject) for item in found.findings}
    assert (AlertCode.STOP_TRIGGERED_UNFILLED, f"{older.id}:{stop.id}") in subjects
