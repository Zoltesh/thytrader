"""A feed-only sub-hour live pause clears itself once the user-order feed is healthy."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.execution_worker.test_user_feed_gate import (
    _connected_snapshot,
    _live_snapshot,
    _published,
)
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.ids import utc_now
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentStatus, LifecycleCommand, with_runtime
from thytrader.execution.user_feed_state import InMemoryUserOrderFeedStateStore
from thytrader.execution_worker.service import (
    USER_FEED_PAUSE_DETAIL,
    _pause_five_minute_live_if_feed_down,
)


async def _feed_paused_store() -> tuple[InMemoryExecutionStore, InMemoryUserOrderFeedStateStore]:
    """Return a 5m live book paused only by a down feed, plus the feed store."""
    store = InMemoryExecutionStore()
    await _live_snapshot(store, _published(timeframe="5m"))
    feed = InMemoryUserOrderFeedStateStore()
    deployment = (await store.list_deployments())[0]
    paused = await _pause_five_minute_live_if_feed_down(
        await store.get_deployment(deployment.id),
        timeframe="5m",
        store=store,
        user_feed_store=feed,
    )
    assert paused is True
    return store, feed


@pytest.mark.anyio
async def test_feed_only_pause_auto_clears_with_audit_when_feed_recovers() -> None:
    """Healthy feed + feed-only pause resumes RUNNING and records the transition."""
    store, feed = await _feed_paused_store()
    deployment = (await store.list_deployments())[0]
    await feed.record(_connected_snapshot(heartbeat_age_seconds=1))
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        blocked = await _pause_five_minute_live_if_feed_down(
            await store.get_deployment(deployment.id),
            timeframe="5m",
            store=store,
            user_feed_store=feed,
        )
    assert blocked is False
    current = await store.get_deployment(deployment.id)
    assert current.deployment.status is DeploymentStatus.RUNNING
    assert current.deployment.mismatch_detail is None
    actions = [event.action for event in await audit.list_recent(limit=5)]
    assert actions == ["user_feed_pause_cleared"]


@pytest.mark.anyio
async def test_stale_heartbeat_keeps_feed_pause() -> None:
    """A connected-but-stale feed is not healthy; the pause stays."""
    store, feed = await _feed_paused_store()
    deployment = (await store.list_deployments())[0]
    await feed.record(_connected_snapshot(heartbeat_age_seconds=31))
    blocked = await _pause_five_minute_live_if_feed_down(
        await store.get_deployment(deployment.id),
        timeframe="5m",
        store=store,
        user_feed_store=feed,
    )
    assert blocked is True
    current = await store.get_deployment(deployment.id)
    assert current.deployment.status is DeploymentStatus.PAUSED


@pytest.mark.anyio
@pytest.mark.parametrize(
    "variant",
    ["operator_pause", "other_mismatch", "daily_loss_latch", "drawdown_latch"],
)
async def test_other_pause_reasons_are_never_auto_cleared(variant: str) -> None:
    """Operator pauses, other mismatches, and breaker latches survive feed recovery."""
    store, feed = await _feed_paused_store()
    deployment = (await store.list_deployments())[0]
    loaded = (await store.get_deployment(deployment.id)).deployment
    if variant == "operator_pause":
        changed = with_runtime(
            loaded, updated_at=utc_now(), lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES
        )
    elif variant == "other_mismatch":
        changed = with_runtime(
            loaded, updated_at=utc_now(), mismatch_detail="Filled order has no REST fills."
        )
    elif variant == "daily_loss_latch":
        changed = with_runtime(loaded, updated_at=utc_now(), daily_loss_latched=True)
    else:
        changed = with_runtime(loaded, updated_at=utc_now(), drawdown_latched=True)
    await store.save_deployment(changed)
    await feed.record(_connected_snapshot(heartbeat_age_seconds=1))
    await _pause_five_minute_live_if_feed_down(
        await store.get_deployment(deployment.id),
        timeframe="5m",
        store=store,
        user_feed_store=feed,
    )
    current = await store.get_deployment(deployment.id)
    assert current.deployment.status is DeploymentStatus.PAUSED


@pytest.mark.anyio
async def test_feed_down_never_overwrites_an_existing_pause_reason() -> None:
    """A book already paused for another reason keeps that reason while the feed is down."""
    store = InMemoryExecutionStore()
    await _live_snapshot(store, _published(timeframe="5m"))
    deployment = (await store.list_deployments())[0]
    loaded = (await store.get_deployment(deployment.id)).deployment
    other = "Order submit is unconfirmed and has no venue id."
    await store.save_deployment(
        replace(loaded, status=DeploymentStatus.PAUSED, mismatch_detail=other)
    )
    blocked = await _pause_five_minute_live_if_feed_down(
        await store.get_deployment(deployment.id),
        timeframe="5m",
        store=store,
        user_feed_store=InMemoryUserOrderFeedStateStore(),
    )
    assert blocked is True
    current = await store.get_deployment(deployment.id)
    assert current.deployment.mismatch_detail == other
    assert current.deployment.mismatch_detail != USER_FEED_PAUSE_DETAIL
