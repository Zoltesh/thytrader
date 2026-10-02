"""Decision-journal store semantics: idempotent upsert, keyset paging, retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from thytrader.execution.decision_store import (
    DecisionStoreError,
    DisabledDecisionJournalStore,
    InMemoryDecisionJournalStore,
    decision_storage_label,
    decode_decision_cursor,
    encode_decision_cursor,
)
from thytrader.execution.decisions import BarDecision, DecisionOutcome
from thytrader.execution.models import DeploymentMode

_START = datetime(2026, 3, 1, tzinfo=UTC)
_STRATEGY = UUID("019b76da-a800-776d-8220-17de421ad3e1")


def make_decision(
    *,
    deployment_id: UUID,
    hour: int,
    outcome: DecisionOutcome = DecisionOutcome.NO_SIGNAL,
    product_id: str = "BTC-USD",
    strategy_id: UUID | None = _STRATEGY,
    summary: str = "No trade: RSI(14) 47.21 needs ≥ 50",
) -> BarDecision:
    """One minimal valid decision on an hourly bar."""
    bar = _START + timedelta(hours=hour)
    return BarDecision(
        deployment_id=deployment_id,
        strategy_id=strategy_id,
        product_id=product_id,
        timeframe="1h",
        mode=DeploymentMode.PAPER,
        bar_starts_at=bar,
        bar_closes_at=bar + timedelta(hours=1),
        evaluated_at=bar + timedelta(hours=1, seconds=3),
        outcome=outcome,
        reason_code=outcome.value.upper(),
        summary=summary,
    )


@pytest.mark.anyio
async def test_upsert_is_idempotent_per_deployment_product_and_bar() -> None:
    """A restart replay rewrites the same row instead of duplicating it."""
    store = InMemoryDecisionJournalStore()
    deployment = uuid4()
    await store.upsert(make_decision(deployment_id=deployment, hour=1))
    await store.upsert(
        make_decision(
            deployment_id=deployment,
            hour=1,
            outcome=DecisionOutcome.ENTRY_SIGNAL,
            summary="Entry: RSI(14) 55 ≥ 50 → buy 0.1 @ 100 (open)",
        )
    )
    rows = store.rows()
    assert len(rows) == 1
    assert rows[0].outcome is DecisionOutcome.ENTRY_SIGNAL


@pytest.mark.anyio
async def test_pages_are_newest_first_with_cursor_and_outcome_filters() -> None:
    """Keyset pages walk older bars; repeated outcomes filter the timeline."""
    store = InMemoryDecisionJournalStore()
    deployment = uuid4()
    outcomes = (
        DecisionOutcome.NO_SIGNAL,
        DecisionOutcome.ENTRY_SIGNAL,
        DecisionOutcome.HOLDING,
        DecisionOutcome.EXIT,
        DecisionOutcome.ENTRY_BLOCKED,
    )
    for hour, outcome in enumerate(outcomes):
        await store.upsert(make_decision(deployment_id=deployment, hour=hour, outcome=outcome))
    first = await store.list_for_deployment(deployment, limit=2)
    assert [row.bar_starts_at.hour for row in first.decisions] == [4, 3]
    assert first.next_cursor is not None
    second = await store.list_for_deployment(deployment, limit=2, cursor=first.next_cursor)
    assert [row.bar_starts_at.hour for row in second.decisions] == [2, 1]
    last = await store.list_for_deployment(deployment, limit=2, cursor=second.next_cursor)
    assert [row.bar_starts_at.hour for row in last.decisions] == [0]
    assert last.next_cursor is None
    trades = await store.list_for_deployment(
        deployment, limit=10, outcomes=(DecisionOutcome.ENTRY_SIGNAL, DecisionOutcome.EXIT)
    )
    assert [row.outcome for row in trades.decisions] == [
        DecisionOutcome.EXIT,
        DecisionOutcome.ENTRY_SIGNAL,
    ]


@pytest.mark.anyio
async def test_multi_instrument_rows_page_by_product_and_filter_by_product() -> None:
    """Covered products of one document share a bar but keep separate rows."""
    store = InMemoryDecisionJournalStore()
    deployment = uuid4()
    for product_id in ("BTC-USD", "ETH-USD"):
        await store.upsert(make_decision(deployment_id=deployment, hour=1, product_id=product_id))
    page = await store.list_for_deployment(deployment, limit=10)
    assert [row.product_id for row in page.decisions] == ["ETH-USD", "BTC-USD"]
    eth = await store.list_for_deployment(deployment, limit=10, product_id="ETH-USD")
    assert [row.product_id for row in eth.decisions] == ["ETH-USD"]


@pytest.mark.anyio
async def test_strategy_pages_aggregate_bots_and_filter_one_deployment() -> None:
    """The strategy view interleaves its bots newest first; a deployment narrows it."""
    store = InMemoryDecisionJournalStore()
    paper, live = uuid4(), uuid4()
    await store.upsert(make_decision(deployment_id=paper, hour=1))
    await store.upsert(make_decision(deployment_id=live, hour=2))
    await store.upsert(make_decision(deployment_id=uuid4(), hour=3, strategy_id=uuid4()))
    page = await store.list_for_strategy(_STRATEGY, limit=10)
    assert [row.deployment_id for row in page.decisions] == [live, paper]
    narrowed = await store.list_for_strategy(_STRATEGY, limit=10, deployment_id=paper)
    assert [row.deployment_id for row in narrowed.decisions] == [paper]
    recent = await store.list_recent(limit=10)
    assert len(recent.decisions) == 3


@pytest.mark.anyio
async def test_latest_before_returns_the_previous_bar_for_one_product() -> None:
    """The window anchor is the newest earlier decision of the same product."""
    store = InMemoryDecisionJournalStore()
    deployment = uuid4()
    for hour in (1, 2, 5):
        await store.upsert(make_decision(deployment_id=deployment, hour=hour))
    previous = await store.latest_before(deployment, "BTC-USD", _START + timedelta(hours=5))
    assert previous is not None
    assert previous.bar_starts_at == _START + timedelta(hours=2)
    assert await store.latest_before(deployment, "ETH-USD", _START + timedelta(hours=5)) is None


@pytest.mark.anyio
async def test_prune_keeps_newest_rows_per_bot_within_max_age_and_is_bounded() -> None:
    """Retention drops rows older than max age and beyond newest-N, in bounded passes."""
    store = InMemoryDecisionJournalStore()
    busy, quiet = uuid4(), uuid4()
    for hour in range(10):
        await store.upsert(make_decision(deployment_id=busy, hour=hour))
    await store.upsert(make_decision(deployment_id=quiet, hour=9))
    now = _START + timedelta(hours=10)
    removed = await store.prune(
        now=now, max_rows_per_deployment=4, max_age=timedelta(hours=8), batch_limit=3
    )
    assert removed == 3
    removed += await store.prune(
        now=now, max_rows_per_deployment=4, max_age=timedelta(hours=8), batch_limit=100
    )
    assert removed == 6
    kept = await store.list_for_deployment(busy, limit=20)
    assert [row.bar_starts_at.hour for row in kept.decisions] == [9, 8, 7, 6]
    assert len((await store.list_for_deployment(quiet, limit=20)).decisions) == 1


def test_cursor_round_trips_and_rejects_garbage() -> None:
    """Cursors are opaque but strictly validated."""
    decision = make_decision(deployment_id=uuid4(), hour=4)
    key = decode_decision_cursor(encode_decision_cursor(decision))
    assert key == (decision.bar_starts_at, decision.deployment_id, "BTC-USD")
    with pytest.raises(DecisionStoreError):
        decode_decision_cursor("not-a-cursor")


@pytest.mark.anyio
async def test_disabled_store_reads_empty_and_refuses_writes() -> None:
    """Without durable storage reads are empty (storage unavailable) and writes fail."""
    store = DisabledDecisionJournalStore()
    assert decision_storage_label(store) == "unavailable"
    assert decision_storage_label(InMemoryDecisionJournalStore()) == "available"
    assert (await store.list_for_deployment(uuid4(), limit=5)).decisions == ()
    assert (await store.list_recent(limit=5)).decisions == ()
    with pytest.raises(DecisionStoreError):
        await store.upsert(make_decision(deployment_id=uuid4(), hour=1))
    with pytest.raises(DecisionStoreError):
        await store.list_for_strategy(_STRATEGY, limit=5, cursor="garbage")
