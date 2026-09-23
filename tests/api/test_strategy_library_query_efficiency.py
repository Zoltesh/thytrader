"""Pagination-before-enrichment guards for the strategy library listing.

The Strategies page must pay enrichment cost (latest-backtest lookups,
deployment-status lookups) only for the rows it actually returns. A library
page that queries per-fingerprint backtests for every identity in the catalog
before slicing degrades linearly with the whole library, not with the page.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.backtest.models import BacktestSummary
from thytrader.config import Settings
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.persistence.backtest_results import BacktestResultSummaryView
from thytrader.strategies.authoring import StrategyDraft, _uuid7, create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyCatalogEntry,
    StrategyPublicationError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.backtest.models import BacktestResult

_NOW = datetime(2026, 9, 22, tzinfo=UTC)


class _DraftStore:
    """Minimal durable-draft stand-in exposing saved drafts to the library."""

    def __init__(self, drafts: tuple[StrategyDraft, ...]) -> None:
        self._drafts = drafts

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Persist and return one editable draft at revision one."""
        draft = StrategyDraft(definition=definition, revision=1)
        self._drafts = (*self._drafts, draft)
        return draft

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return every saved draft."""
        return self._drafts

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace one draft under its optimistic-concurrency revision."""
        saved = StrategyDraft(definition=definition, revision=expected_revision + 1)
        kept = tuple(
            d for d in self._drafts if str(d.definition.strategy_id) != str(definition.strategy_id)
        )
        self._drafts = (*kept, saved)
        return saved

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Remove one consumed draft identity."""
        self._drafts = tuple(
            d
            for d in self._drafts
            if str(d.definition.strategy_id) != str(strategy_id) or d.definition.version != version
        )


class _PublicationStore:
    """Minimal immutable-publication stand-in for library listing tests."""

    def __init__(self, entries: tuple[StrategyCatalogEntry, ...]) -> None:
        self._entries = entries

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Publish one definition under its canonical fingerprint."""
        return PublishedStrategy(
            strategy_fingerprint=strategy_fingerprint(definition), definition=definition
        )

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Publish one draft under its canonical fingerprint."""
        del expected_revision
        return await self.publish(definition)

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return one published definition or fail closed."""
        for entry in self._entries:
            if entry.strategy_fingerprint == strategy_fingerprint_value:
                return PublishedStrategy(
                    strategy_fingerprint=entry.strategy_fingerprint,
                    definition=entry.definition,
                )
        raise StrategyPublicationError("Published strategy was not found.")

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """Return published evidence, honoring the archive-visibility flag."""
        if include_archived:
            return self._entries
        return tuple(entry for entry in self._entries if entry.archived_at is None)

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Mark one publication archived."""
        for entry in self._entries:
            if entry.strategy_fingerprint == strategy_fingerprint_value:
                return entry
        raise StrategyPublicationError("Published strategy was not found.")


class _CountingSummaryReader:
    """Result reader that records one bounded query per submitted call."""

    def __init__(
        self,
        summaries: dict[str, BacktestResultSummaryView],
        *,
        delay: float = 0.0,
    ) -> None:
        self._summaries = summaries
        self._delay = delay
        self.query_count = 0

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Serve newest-first summaries for one indexed source filter."""
        del run_fingerprint, dataset_fingerprint
        self.query_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if strategy_fingerprint is None:
            ordered = sorted(
                self._summaries.values(),
                key=lambda view: (view.published_at, view.result_fingerprint),
                reverse=True,
            )
            return tuple(ordered[offset : offset + limit])
        match = self._summaries.get(strategy_fingerprint)
        return () if match is None else (match,)

    async def list_summaries_for_strategies(
        self,
        strategy_fingerprints: Sequence[str],
    ) -> dict[str, BacktestResultSummaryView]:
        """Count the batched query, then serve the newest summary per fingerprint."""
        self.query_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return {
            fingerprint: view
            for fingerprint in strategy_fingerprints
            if (view := self._summaries.get(fingerprint)) is not None
        }

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Fail closed; the library route never loads full results."""
        del result_fingerprint
        raise AssertionError("Library listing must not load full result documents.")


class _CountingExecutionStore(InMemoryExecutionStore):
    """Execution store that records every submitted deployment-status query."""

    def __init__(self, *, delay: float = 0.0) -> None:
        super().__init__()
        self._delay = delay
        self.query_count = 0

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Count the query, then serve the in-memory book."""
        self.query_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return await super().list_by_strategy(strategy_id)

    async def list_by_strategy_ids(
        self, strategy_ids: Sequence[str]
    ) -> dict[str, tuple[Deployment, ...]]:
        """Count the batched query, then serve the in-memory book."""
        self.query_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return await super().list_by_strategy_ids(strategy_ids)


def _definition(name: str, created_at: datetime) -> StrategyDefinition:
    """Build one distinct reference definition with a stable creation instant."""
    reference = create_reference_draft()
    return reference.model_copy(
        update={"strategy_id": _uuid7(created_at), "name": name, "created_at": created_at}
    )


def _published(definition: StrategyDefinition) -> tuple[str, StrategyDefinition]:
    """Return the canonical (fingerprint, published definition) pair."""
    published_definition = definition.model_copy(update={"status": StrategyStatus.PUBLISHED})
    return strategy_fingerprint(published_definition), published_definition


def _summary(strategy_fingerprint_value: str, published_at: datetime) -> BacktestResultSummaryView:
    """Build one minimal immutable result summary view for one fingerprint."""
    tail = strategy_fingerprint_value[-64:]
    return BacktestResultSummaryView(
        result_fingerprint=f"res-{strategy_fingerprint_value[-8:]}",
        run_fingerprint=f"sha256:{tail}",
        strategy_fingerprint=strategy_fingerprint_value,
        dataset_fingerprint=f"sha256:{tail}",
        engine_contract_version="thytrader-bar-backtest-v4",
        published_at=published_at,
        summary=BacktestSummary(
            initial_equity="10000",
            final_equity="10100",
            total_net_pnl="100",
            total_return_fraction="0.01",
            gross_profit="150",
            gross_loss="50",
            win_rate="0.5",
            trade_count=2,
            winning_trade_count=1,
            maximum_drawdown="50",
            maximum_drawdown_fraction="0.005",
            exposure_bars=10,
            evaluation_bars=100,
        ),
    )


def _deployment(strategy_id: UUID, mode: DeploymentMode, status: DeploymentStatus) -> Deployment:
    """Build one strategy-bound deployment row for the execution store."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=strategy_id,
        product_id="BTC-USD",
        mode=mode,
        status=status,
        cash=Decimal("1000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _library_app(
    drafts: tuple[StrategyDraft, ...],
    entries: tuple[StrategyCatalogEntry, ...],
    reader: _CountingSummaryReader,
    execution_store: InMemoryExecutionStore,
) -> TestClient:
    """Build one TestClient over the in-memory library boundaries."""
    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        strategy_draft_store=_DraftStore(drafts),
        strategy_store=_PublicationStore(entries),
        backtest_result_store=reader,
        execution_store=execution_store,
    )
    return TestClient(app, raise_server_exceptions=True)


def _catalog(count: int) -> tuple[list[StrategyDefinition], tuple[StrategyCatalogEntry, ...]]:
    """Build one newest-first catalog of distinct published identities."""
    definitions = [
        _definition(f"Perf {index}", _NOW - timedelta(hours=count - index))
        for index in range(count)
    ]
    entries = tuple(
        StrategyCatalogEntry(
            strategy_fingerprint=fingerprint,
            definition=published_definition,
            archived_at=None,
        )
        for definition in definitions
        for fingerprint, published_definition in (_published(definition),)
    )
    return definitions, entries


def _summary_map(entries: tuple[StrategyCatalogEntry, ...]) -> dict[str, BacktestResultSummaryView]:
    """Index one newest-first summary per catalog fingerprint."""
    return {
        entry.strategy_fingerprint: _summary(
            entry.strategy_fingerprint, _NOW - timedelta(hours=offset)
        )
        for offset, entry in enumerate(entries)
    }


def test_strategy_library_page_enriches_only_the_requested_page() -> None:
    """One library page performs page-bounded lookups, not catalog-wide ones."""
    definitions, entries = _catalog(40)
    drafts = tuple(
        StrategyDraft(definition=definition, revision=1) for definition in definitions[:2]
    )
    reader = _CountingSummaryReader(_summary_map(entries))
    execution_store = _CountingExecutionStore()
    for deployment in (
        _deployment(definitions[-1].strategy_id, DeploymentMode.PAPER, DeploymentStatus.RUNNING),
        _deployment(definitions[-1].strategy_id, DeploymentMode.LIVE, DeploymentStatus.STOPPED),
        _deployment(definitions[-2].strategy_id, DeploymentMode.PAPER, DeploymentStatus.RUNNING),
    ):
        execution_store.deployments[deployment.id] = deployment

    client = _library_app(drafts, entries, reader, execution_store)
    with client:
        response = client.get("/api/v1/strategies", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["returned"] == 2
    assert body["has_more"] is True
    assert body["strategies"][0]["strategy_id"] == str(definitions[-1].strategy_id)
    assert body["strategies"][0]["backtest"] is not None
    assert body["strategies"][0]["paper_live"] == {"paper": "running", "live": "stopped"}
    assert body["strategies"][1]["paper_live"] == {"paper": "running", "live": "unavailable"}
    # One page: at most one look-up per returned row per store, not per catalog row.
    assert reader.query_count <= 2, reader.query_count
    assert execution_store.query_count <= 1, execution_store.query_count


def test_strategy_library_page_does_not_wait_for_slow_off_page_lookups() -> None:
    """A page must not wait on per-fingerprint lookups for identities it drops."""
    _definitions, entries = _catalog(40)
    reader = _CountingSummaryReader(_summary_map(entries), delay=0.05)
    execution_store = _CountingExecutionStore(delay=0.05)

    client = _library_app((), entries, reader, execution_store)
    with client:
        started = datetime.now(UTC)
        response = client.get("/api/v1/strategies", params={"limit": 2})
        elapsed = datetime.now(UTC) - started

    assert response.status_code == 200
    assert response.json()["returned"] == 2
    # Catalog-wide per-fingerprint enrichment would wait ~40 x 0.05s = 2s.
    assert elapsed.total_seconds() < 1.0, elapsed
    assert reader.query_count <= 2, reader.query_count
