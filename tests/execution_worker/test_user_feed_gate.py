"""Sub-hour live pauses unless the authenticated user-order feed is connected and fresh."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.execution.user_feed_state import (
    InMemoryUserOrderFeedStateStore,
    UserOrderFeedSnapshot,
    UserOrderFeedState,
)
from thytrader.execution_worker.service import (
    _pause_five_minute_live_if_feed_down,
    _user_feed_connected,
)
from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint


def _published(*, timeframe: str) -> StrategyDefinition:
    """Return a published reference strategy on one execution clock."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["status"] = StrategyStatus.PUBLISHED.value
    payload["timeframe"] = timeframe
    return StrategyDefinition.model_validate(payload)


async def _live_snapshot(store: InMemoryExecutionStore, strategy: StrategyDefinition) -> None:
    """Insert one running live deployment for the published strategy."""
    now = utc_now()
    await store.create_deployment(
        Deployment(
            id=uuid7(now),
            strategy_fingerprint=strategy_fingerprint(strategy),
            strategy_id=strategy.strategy_id,
            product_id="BTC-USD",
            mode=DeploymentMode.LIVE,
            status=DeploymentStatus.RUNNING,
            cash=Decimal("0"),
            phase=RuntimePhase.FLAT,
            created_at=now,
            updated_at=now,
        )
    )


def _connected_snapshot(*, heartbeat_age_seconds: float) -> UserOrderFeedSnapshot:
    """Return a connected user-feed snapshot with a heartbeat of the given age."""
    now = datetime.now(UTC)
    heartbeat = now - timedelta(seconds=heartbeat_age_seconds)
    return UserOrderFeedSnapshot(
        state=UserOrderFeedState.CONNECTED,
        last_message_at=heartbeat,
        last_heartbeat_at=heartbeat,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_user_feed_connected_requires_fresh_heartbeat() -> None:
    """Stale or missing heartbeats are not a connected 5m-live feed."""
    store = InMemoryUserOrderFeedStateStore()
    assert await _user_feed_connected(None) is False
    assert await _user_feed_connected(store) is False
    await store.record(_connected_snapshot(heartbeat_age_seconds=1))
    assert await _user_feed_connected(store) is True
    await store.record(_connected_snapshot(heartbeat_age_seconds=31))
    assert await _user_feed_connected(store) is False


@pytest.mark.anyio
async def test_five_minute_live_pauses_when_user_feed_is_down() -> None:
    """5m live must pause before evaluating bars when the user feed is not connected."""
    store = InMemoryExecutionStore()
    strategy = _published(timeframe="5m")
    await _live_snapshot(store, strategy)
    snapshot = (await store.list_deployments())[0]
    loaded = await store.get_deployment(snapshot.id)
    paused = await _pause_five_minute_live_if_feed_down(
        loaded,
        timeframe=strategy.timeframe,
        store=store,
        user_feed_store=InMemoryUserOrderFeedStateStore(),
    )
    assert paused is True
    updated = await store.get_deployment(snapshot.id)
    assert updated.deployment.status is DeploymentStatus.PAUSED
    assert updated.deployment.mismatch_detail == "User-order feed is not connected."


@pytest.mark.anyio
async def test_hourly_live_does_not_pause_for_a_down_user_feed() -> None:
    """1h live still reconciles through REST when the user feed is down."""
    store = InMemoryExecutionStore()
    strategy = _published(timeframe="1h")
    await _live_snapshot(store, strategy)
    snapshot = (await store.list_deployments())[0]
    loaded = await store.get_deployment(snapshot.id)
    paused = await _pause_five_minute_live_if_feed_down(
        loaded,
        timeframe=strategy.timeframe,
        store=store,
        user_feed_store=InMemoryUserOrderFeedStateStore(),
    )
    assert paused is False
    updated = await store.get_deployment(snapshot.id)
    assert updated.deployment.status is DeploymentStatus.RUNNING


@pytest.mark.anyio
async def test_one_minute_live_pauses_when_user_feed_is_down() -> None:
    """1m live uses the same user-feed gate as other sub-hour clocks."""
    store = InMemoryExecutionStore()
    strategy = _published(timeframe="1m")
    await _live_snapshot(store, strategy)
    snapshot = (await store.list_deployments())[0]
    loaded = await store.get_deployment(snapshot.id)
    paused = await _pause_five_minute_live_if_feed_down(
        loaded,
        timeframe=strategy.timeframe,
        store=store,
        user_feed_store=InMemoryUserOrderFeedStateStore(),
    )
    assert paused is True
    updated = await store.get_deployment(snapshot.id)
    assert updated.deployment.status is DeploymentStatus.PAUSED
    assert updated.deployment.mismatch_detail == "User-order feed is not connected."


@pytest.mark.anyio
async def test_two_hour_live_does_not_pause_for_a_down_user_feed() -> None:
    """2h live still reconciles through REST when the user feed is down."""
    store = InMemoryExecutionStore()
    strategy = _published(timeframe="2h")
    await _live_snapshot(store, strategy)
    snapshot = (await store.list_deployments())[0]
    loaded = await store.get_deployment(snapshot.id)
    paused = await _pause_five_minute_live_if_feed_down(
        loaded,
        timeframe=strategy.timeframe,
        store=store,
        user_feed_store=InMemoryUserOrderFeedStateStore(),
    )
    assert paused is False
    updated = await store.get_deployment(snapshot.id)
    assert updated.deployment.status is DeploymentStatus.RUNNING
