"""Tests for confirmation-gated research mutations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from thytrader.persistence.audit_events import AuditEventCategory, InMemoryAuditEventStore
from thytrader.persistence.backtest_results import DisabledBacktestResultStore
from thytrader.research.mutation import ResearchMutator
from thytrader.strategies.authoring import StrategyDraft
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationError

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.backtest.submission import BacktestSubmissionRequest, BacktestSubmissionResult


class _DraftStore:
    """Minimal in-memory draft store for mutation tests."""

    def __init__(self) -> None:
        """Start empty."""
        self.drafts: dict[tuple[str, int], StrategyDraft] = {}
        self.create_calls = 0

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Store one draft."""
        self.create_calls += 1
        draft = StrategyDraft(definition=definition, revision=1)
        self.drafts[(str(definition.strategy_id), definition.version)] = draft
        return draft

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return saved drafts."""
        return tuple(self.drafts.values())

    async def save_draft(
        self,
        definition: StrategyDefinition,
        *,
        expected_revision: int,
    ) -> StrategyDraft:
        """Replace a matching draft."""
        key = (str(definition.strategy_id), definition.version)
        existing = self.drafts[key]
        if existing.revision != expected_revision:
            raise RuntimeError("Strategy draft revision conflict.")
        saved = StrategyDraft(definition=definition, revision=expected_revision + 1)
        self.drafts[key] = saved
        return saved

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Drop a draft."""
        self.drafts.pop((str(strategy_id), version), None)


class _PublicationStore:
    """Publish drafts by consuming the matching in-memory row."""

    def __init__(self, drafts: _DraftStore) -> None:
        """Bind to the draft double."""
        self._drafts = drafts
        self.published: PublishedStrategy | None = None

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Unused in this test."""
        del definition
        raise StrategyPublicationError("unused")

    async def publish_draft(
        self,
        definition: StrategyDefinition,
        *,
        expected_revision: int,
    ) -> PublishedStrategy:
        """Consume the draft and return a published fingerprint."""
        key = (str(definition.strategy_id), definition.version)
        current = self._drafts.drafts.get(key)
        if current is None or current.revision != expected_revision:
            raise StrategyPublicationError("Strategy draft was not found.")
        published_definition = StrategyDefinition.model_validate(
            {**definition.model_dump(mode="python"), "status": "published"}
        )
        result = PublishedStrategy(
            strategy_fingerprint=strategy_fingerprint(published_definition),
            definition=published_definition,
        )
        self.published = result
        self._drafts.drafts.pop(key)
        return result

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Unused in this test."""
        del strategy_fingerprint_value
        raise StrategyPublicationError("unused")


class _UnusedSubmitter:
    """Refuse accidental backtest submission in draft tests."""

    async def submit(self, request: BacktestSubmissionRequest) -> BacktestSubmissionResult:
        """Fail if called."""
        del request
        raise RuntimeError("submit should not run")


def test_create_reference_draft_records_research_audit() -> None:
    """Creating a draft must append a research audit event."""
    drafts = _DraftStore()
    audit = InMemoryAuditEventStore()
    mutator = ResearchMutator(
        drafts=drafts,
        publications=_PublicationStore(drafts),
        submitter=_UnusedSubmitter(),
        results=DisabledBacktestResultStore(),
        audit=audit,
    )
    draft = asyncio.run(mutator.create_reference_draft())
    events = asyncio.run(audit.list_recent(limit=5))
    assert draft.revision == 1
    assert drafts.create_calls == 1
    assert events[0].category is AuditEventCategory.RESEARCH
    assert events[0].action == "create_draft"


def test_publish_consumes_draft_and_returns_fingerprint() -> None:
    """Publishing uses the stored draft revision and removes the mutable row."""
    drafts = _DraftStore()
    publications = _PublicationStore(drafts)
    audit = InMemoryAuditEventStore()
    mutator = ResearchMutator(
        drafts=drafts,
        publications=publications,
        submitter=_UnusedSubmitter(),
        results=DisabledBacktestResultStore(),
        audit=audit,
    )
    draft = asyncio.run(mutator.create_reference_draft())
    published = asyncio.run(mutator.publish(draft.definition.strategy_id))
    assert published.strategy_fingerprint.startswith("sha256:")
    assert drafts.drafts == {}
    events = asyncio.run(audit.list_recent(limit=5))
    assert any(event.action == "publish_strategy" for event in events)
