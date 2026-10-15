"""Starting a strategy saved before the convergence warmup policy adopts it (ADR 0142)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, cast

import pytest

from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.strategies.models import StrategyDefinition, canonical_strategy_bytes
from thytrader.strategies.start_adoption import adopt_warmup_policy_for_start
from thytrader.strategies.warmup_policy import without_warmup_policy

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.strategies.library import StrategyDocument, StrategyRecord, StrategyStore

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 10, 15, tzinfo=UTC)


async def _legacy(store: InMemoryStrategyStore) -> StrategyDefinition:
    """Store one strategy as a database row from before the policy would hold it."""
    record = await create_strategy_from_definition(store, create_template_strategy(now=_NOW))
    assert record.definition is not None
    document = json.loads(canonical_strategy_bytes(record.definition))
    legacy = StrategyDefinition.model_validate(without_warmup_policy(document))
    # The memory store always converges on save; PostgreSQL keeps such rows from before.
    store._records[record.strategy_id] = replace(
        record, definition=legacy, document=without_warmup_policy(document)
    )
    return legacy


async def test_a_pre_policy_strategy_is_re_saved_before_its_start() -> None:
    """The unchanged document is saved once, adopting the policy, and the revision moves on."""
    store = InMemoryStrategyStore()
    legacy = await _legacy(store)
    before = await store.get(legacy.strategy_id)
    assert await adopt_warmup_policy_for_start(store, legacy.strategy_id)
    after = await store.get(legacy.strategy_id)
    assert after.revision == before.revision + 1
    assert after.definition is not None
    assert after.definition.data_requirements.warmup_policy == "convergence"
    snapshot = await store.snapshot(legacy.strategy_id)
    assert snapshot.definition.data_requirements.warmup_policy == "convergence"
    assert not await adopt_warmup_policy_for_start(store, legacy.strategy_id)


async def test_a_strategy_that_cannot_converge_is_left_alone() -> None:
    """A warmup above the declarable cap keeps the strategy as saved; its start then warns."""
    store = InMemoryStrategyStore()
    record = await create_strategy_from_definition(store, create_template_strategy(now=_NOW))
    assert record.definition is not None
    document = json.loads(canonical_strategy_bytes(record.definition))
    document["indicators"].append(
        {
            "id": "slow_kama",
            "kind": "kama",
            "input": "close",
            "parameters": {"period": 10, "fast_period": 2, "slow_period": 500},
        }
    )
    saved = await store.save(record.strategy_id, document, expected_revision=record.revision)
    assert saved.definition is not None
    assert saved.definition.data_requirements.warmup_policy is None
    assert not await adopt_warmup_policy_for_start(store, record.strategy_id)
    assert (await store.get(record.strategy_id)).revision == saved.revision


class _RacingStore:
    """A store whose strategy another writer changes right after each read."""

    def __init__(self, inner: InMemoryStrategyStore) -> None:
        self.inner = inner

    async def get(self, strategy_id: UUID) -> StrategyRecord:
        record = await self.inner.get(strategy_id)
        self.inner._records[strategy_id] = replace(record, revision=record.revision + 1)
        return record

    async def save(
        self, strategy_id: UUID, document: StrategyDocument, *, expected_revision: int
    ) -> StrategyRecord:
        return await self.inner.save(strategy_id, document, expected_revision=expected_revision)


async def test_a_concurrent_edit_wins_over_the_adoption() -> None:
    """A writer that changed the strategy meanwhile is never overwritten."""
    store = InMemoryStrategyStore()
    legacy = await _legacy(store)
    racing = _RacingStore(store)
    assert not await adopt_warmup_policy_for_start(
        cast("StrategyStore", racing), legacy.strategy_id
    )
    current = await store.get(legacy.strategy_id)
    assert current.definition is not None
    assert current.definition.data_requirements.warmup_policy is None
