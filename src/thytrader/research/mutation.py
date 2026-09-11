"""Confirmation-gated research mutations with audit, without trading authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.strategies.authoring import StrategyDraft, StrategyDraftStore, create_reference_draft

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.backtest.submission import BacktestSubmissionRequest, BacktestSubmitter
    from thytrader.persistence.backtest_results import (
        BacktestResultReader,
        BacktestResultSummaryView,
    )
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationStore


class ResearchMutationError(RuntimeError):
    """Report a redacted research-mutation failure without trading authority."""


@dataclass(frozen=True, slots=True)
class ResearchMutator:
    """Create drafts, publish versions, and submit backtests after explicit confirmation."""

    drafts: StrategyDraftStore
    publications: StrategyPublicationStore
    submitter: BacktestSubmitter
    results: BacktestResultReader
    audit: AuditEventStore

    async def create_reference_draft(
        self,
        *,
        product_id: str = "BTC-USD",
        timeframe: str = "1h",
    ) -> StrategyDraft:
        """Persist the conservative reference draft and record an audit event."""
        definition = create_reference_draft(product_id=product_id, timeframe=timeframe)
        draft = await self.drafts.create_draft(definition)
        await self._audit("create_draft", AuditEventOutcome.SUCCESS, _draft_detail(draft))
        return draft

    async def save_draft(
        self,
        definition: StrategyDefinition,
        *,
        expected_revision: int,
    ) -> StrategyDraft:
        """Replace one draft when the expected revision still matches."""
        draft = await self.drafts.save_draft(definition, expected_revision=expected_revision)
        await self._audit("save_draft", AuditEventOutcome.SUCCESS, _draft_detail(draft))
        return draft

    async def publish(self, strategy_id: UUID) -> PublishedStrategy:
        """Publish the matching durable draft as an immutable version."""
        drafts = await self.drafts.list_drafts()
        match = next(
            (draft for draft in drafts if draft.definition.strategy_id == strategy_id),
            None,
        )
        if match is None:
            raise ResearchMutationError("Strategy draft was not found.")
        published = await self.publications.publish_draft(
            match.definition,
            expected_revision=match.revision,
        )
        await self._audit(
            "publish_strategy",
            AuditEventOutcome.SUCCESS,
            f"strategy_id={strategy_id} fingerprint={published.strategy_fingerprint}",
        )
        return published

    async def submit_backtest(self, request: BacktestSubmissionRequest) -> tuple[str, str]:
        """Submit one idempotent historical simulation and return immutable identities."""
        result = await self.submitter.submit(request)
        await self._audit(
            "submit_backtest",
            AuditEventOutcome.SUCCESS,
            f"run={result.run_fingerprint} result={result.result_fingerprint}",
        )
        return result.run_fingerprint, result.result_fingerprint

    async def list_results(
        self,
        *,
        strategy_fingerprint: str | None,
        limit: int = 20,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """List newest-first immutable result summaries."""
        return await self.results.list_summaries(
            strategy_fingerprint=strategy_fingerprint,
            limit=limit,
            offset=0,
        )

    async def _audit(self, action: str, outcome: AuditEventOutcome, detail: str) -> None:
        """Append one research audit event; disabled stores stay silent."""
        event = AuditEvent(
            occurred_at=datetime.now(UTC),
            category=AuditEventCategory.RESEARCH,
            action=action,
            outcome=outcome,
            detail=detail[:2048],
        )
        await self.audit.append(event)


def _draft_detail(draft: StrategyDraft) -> str:
    """Identify a draft without including the full strategy body."""
    return (
        f"strategy_id={draft.definition.strategy_id} "
        f"version={draft.definition.version} revision={draft.revision}"
    )
