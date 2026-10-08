"""Confirmation-gated research mutations with audit, without trading authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.research.dataset_binding import (
    BoundDataset,
    DatasetResolver,
    bind_backtest_datasets,
)
from thytrader.research.studies import ResearchStudy, ResearchStudyRequest, ResearchStudyService
from thytrader.research.study_start import BoundStudyStart, bind_study_start
from thytrader.strategies.authoring import create_template_strategy, new_strategy_identity
from thytrader.strategies.library import (
    BulkDeletionReport,
    StrategyDocument,
    StrategyRecord,
    StrategyStore,
    bulk_delete_strategies,
    clone_strategy,
    create_strategy_from_definition,
    import_strategy,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.backtest.results import (
        BacktestResultReader,
        BacktestResultSummaryView,
    )
    from thytrader.backtest.submission import (
        BacktestStartRequest,
        BacktestSubmissionRequest,
        BacktestSubmitter,
    )
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.research.catalog import ResearchStudyCatalog, StudyCatalogSummary
    from thytrader.research.study_start import ResearchStudyStartRequest
    from thytrader.strategies.snapshots import StrategySnapshotStore


class ResearchMutationError(RuntimeError):
    """Report a redacted research-mutation failure without trading authority."""


@dataclass(frozen=True, slots=True)
class ResearchMutator:
    """Create, save, delete, and test strategies after explicit confirmation (``--local``)."""

    strategies: StrategyStore
    publications: StrategySnapshotStore
    submitter: BacktestSubmitter
    results: BacktestResultReader
    audit: AuditEventStore
    catalog: ResearchStudyCatalog | None = None
    datasets: DatasetStore | None = None
    dataset_provider: str = "demo"

    async def create_strategy(
        self,
        *,
        product_id: str = "BTC-USD",
        timeframe: str = "1h",
        template: str = "ema-trend",
    ) -> StrategyRecord:
        """Persist a research template strategy and record an audit event."""
        definition = create_template_strategy(
            product_id=product_id,
            timeframe=timeframe,
            template=template,
        )
        record = await create_strategy_from_definition(self.strategies, definition)
        await self._audit("create_strategy", AuditEventOutcome.SUCCESS, _record_detail(record))
        return record

    async def save_strategy(
        self,
        strategy_id: UUID,
        document: StrategyDocument,
        *,
        expected_revision: int,
    ) -> StrategyRecord:
        """Save one document in place when the expected revision still matches."""
        record = await self.strategies.save(
            strategy_id, document, expected_revision=expected_revision
        )
        await self._audit("save_strategy", AuditEventOutcome.SUCCESS, _record_detail(record))
        return record

    async def import_strategy(self, document: StrategyDocument) -> StrategyRecord:
        """Create a new strategy (fresh identity) from one supplied document."""
        strategy_id, created_at = new_strategy_identity()
        record = await import_strategy(
            self.strategies, document, strategy_id=strategy_id, created_at=created_at
        )
        await self._audit("import_strategy", AuditEventOutcome.SUCCESS, _record_detail(record))
        return record

    async def clone_strategy(self, source_id: UUID, *, name: str | None = None) -> StrategyRecord:
        """Duplicate one strategy into a new identity, optionally named in the same call."""
        strategy_id, created_at = new_strategy_identity()
        record = await clone_strategy(
            self.strategies, source_id, strategy_id=strategy_id, created_at=created_at, name=name
        )
        await self._audit("clone_strategy", AuditEventOutcome.SUCCESS, _record_detail(record))
        return record

    async def delete_strategies(
        self, strategy_ids: tuple[UUID, ...], *, dry_run: bool
    ) -> BulkDeletionReport:
        """Delete (or preview deleting) strategies, auditing each committed deletion."""
        report = await bulk_delete_strategies(self.strategies, strategy_ids, dry_run=dry_run)
        for item in report.items:
            if item.outcome == "deleted":
                await self._audit(
                    "delete_strategy", AuditEventOutcome.SUCCESS, f"strategy_id={item.strategy_id}"
                )
        return report

    async def start_backtest(
        self, start: BacktestStartRequest
    ) -> tuple[str, str, str, tuple[BoundDataset, ...]]:
        """Snapshot the strategy, bind omitted datasets, and run one backtest.

        Returns the run, result, and snapshot fingerprints plus every bound dataset.
        """
        snapshot = await self.strategies.snapshot(start.strategy_id)
        resolver = self._resolver()
        bound = bind_backtest_datasets(start, snapshot.definition, resolver)
        run, result = await self.submit_backtest(bound.submission(snapshot.strategy_fingerprint))
        return run, result, snapshot.strategy_fingerprint, resolver.bindings()

    async def bind_study(self, start: ResearchStudyStartRequest) -> BoundStudyStart:
        """Snapshot strategies, derive market variants, and bind datasets and bounds."""
        return await bind_study_start(
            start,
            strategies=self.strategies,
            publications=self.publications,
            resolver=self._resolver(),
            datasets=self.datasets,
        )

    def _resolver(self) -> DatasetResolver:
        """Resolve omitted datasets from the configured ingestion provider's catalog."""
        return DatasetResolver(store=self.datasets, provider=self.dataset_provider)

    async def submit_backtest(self, request: BacktestSubmissionRequest) -> tuple[str, str]:
        """Submit one idempotent historical simulation and return immutable identities."""
        result = await self.submitter.submit(request)
        await self._audit(
            "submit_backtest",
            AuditEventOutcome.SUCCESS,
            f"run={result.run_fingerprint} result={result.result_fingerprint}",
        )
        return result.run_fingerprint, result.result_fingerprint

    async def submit_study(self, request: ResearchStudyRequest) -> ResearchStudy:
        """Submit one composed research study and record an audit event."""
        service = ResearchStudyService(
            publications=self.publications,
            submitter=self.submitter,
            results=self.results,
            catalog=self.catalog,
            datasets=self.datasets,
        )
        study = await service.submit(request)
        await self._audit(
            "submit_study",
            AuditEventOutcome.SUCCESS,
            f"study={study.study_fingerprint} kind={study.kind.value} windows={len(study.windows)}",
        )
        return study

    async def list_studies(
        self,
        *,
        kind: str | None = None,
        strategy_id: UUID | None = None,
        limit: int = 50,
    ) -> tuple[StudyCatalogSummary, ...]:
        """List newest-first persisted study catalog rows."""
        if self.catalog is None:
            raise ResearchMutationError("Research study catalog is unavailable.")
        return await self.catalog.list_summaries(kind=kind, strategy_id=strategy_id, limit=limit)

    async def show_study(self, study_fingerprint: str) -> ResearchStudy:
        """Load one persisted study document."""
        if self.catalog is None:
            raise ResearchMutationError("Research study catalog is unavailable.")
        canonical = await self.catalog.load(study_fingerprint)
        return ResearchStudy.model_validate_json(canonical)

    async def list_results(
        self,
        *,
        strategy_fingerprint: str | None,
        strategy_id: UUID | None = None,
        limit: int = 20,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """List newest-first immutable result summaries."""
        return await self.results.list_summaries(
            strategy_fingerprint=strategy_fingerprint,
            strategy_id=strategy_id,
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


def _record_detail(record: StrategyRecord) -> str:
    """Identify a strategy without including its document body."""
    return (
        f"strategy_id={record.strategy_id} revision={record.revision} "
        f"valid={record.validation.valid}"
    )
