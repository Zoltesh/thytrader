"""Pagination-before-enrichment guards for the strategy library listing.

The Strategies page must pay enrichment cost (newest-backtest and bot-status
lookups) only for the rows it returns, in one batched query per store.
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
from thytrader.backtest.results import BacktestResultSummaryView
from thytrader.config import Settings
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.backtest.models import BacktestResult

_NOW = datetime(2026, 9, 22, tzinfo=UTC)


class _CountingSummaryReader:
    """Result reader that records one query per call."""

    def __init__(self, summaries: dict[UUID, BacktestResultSummaryView], *, delay: float) -> None:
        """Serve fixed newest summaries per strategy."""
        self._summaries = summaries
        self._delay = delay
        self.query_count = 0
        self.requested: list[UUID] = []

    async def list_summaries(
        self,
        *,
        run_fingerprint: str | None = None,
        strategy_fingerprint: str | None = None,
        dataset_fingerprint: str | None = None,
        strategy_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[BacktestResultSummaryView, ...]:
        """Serve one strategy's newest summary (the unbatched fallback)."""
        del run_fingerprint, strategy_fingerprint, dataset_fingerprint, limit, offset
        self.query_count += 1
        match = None if strategy_id is None else self._summaries.get(strategy_id)
        return () if match is None else (match,)

    async def newest_summaries_for_strategy_ids(
        self, strategy_ids: Sequence[UUID]
    ) -> dict[UUID, BacktestResultSummaryView]:
        """Count the batched query, then serve the newest summary per strategy."""
        self.query_count += 1
        self.requested.extend(strategy_ids)
        if self._delay:
            await asyncio.sleep(self._delay)
        return {item: view for item in strategy_ids if (view := self._summaries.get(item))}

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Fail closed; the library route never loads full results."""
        del result_fingerprint
        raise AssertionError("Library listing must not load full result documents.")


class _CountingExecutionStore(InMemoryExecutionStore):
    """Execution store that records every deployment-status query."""

    def __init__(self, *, delay: float = 0.0) -> None:
        """Start with no queries."""
        super().__init__()
        self._delay = delay
        self.query_count = 0

    async def list_by_strategy(self, strategy_id: str) -> tuple[Deployment, ...]:
        """Count the query, then serve the in-memory book."""
        self.query_count += 1
        return await super().list_by_strategy(strategy_id)

    async def list_by_strategy_ids(
        self, strategy_ids: Sequence[str]
    ) -> dict[str, tuple[Deployment, ...]]:
        """Count the batched query, then serve the in-memory book."""
        self.query_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return await super().list_by_strategy_ids(strategy_ids)


def _summary(fingerprint: str, published_at: datetime) -> BacktestResultSummaryView:
    """Build one minimal result summary view for one snapshot fingerprint."""
    tail = fingerprint[-64:]
    return BacktestResultSummaryView(
        result_fingerprint=f"sha256:{tail}",
        run_fingerprint=f"sha256:{tail}",
        strategy_fingerprint=fingerprint,
        dataset_fingerprint=f"sha256:{tail}",
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
    """Build one strategy-bound deployment row."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=strategy_id,
        product_id="BTC-USD",
        mode=mode,
        status=status,
        cash=Decimal(1000),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _library(
    count: int, *, delay: float
) -> tuple[InMemoryStrategyStore, list[UUID], _CountingSummaryReader]:
    """Seed ``count`` strategies (last is newest) plus one summary each."""
    store = InMemoryStrategyStore()
    identities: list[UUID] = []
    summaries: dict[UUID, BacktestResultSummaryView] = {}
    for index in range(count):
        snapshot = store.seed_definition(create_template_strategy())
        identity = snapshot.definition.strategy_id
        identities.append(identity)
        summaries[identity] = _summary(
            snapshot.strategy_fingerprint, _NOW - timedelta(hours=count - index)
        )
    return store, identities, _CountingSummaryReader(summaries, delay=delay)


def test_strategy_library_page_enriches_only_the_requested_page() -> None:
    """One page performs one batched lookup per store, only for returned rows."""
    store, identities, reader = _library(40, delay=0.0)
    execution = _CountingExecutionStore()
    for deployment in (
        _deployment(identities[-1], DeploymentMode.PAPER, DeploymentStatus.RUNNING),
        _deployment(identities[-1], DeploymentMode.LIVE, DeploymentStatus.STOPPED),
        _deployment(identities[-2], DeploymentMode.PAPER, DeploymentStatus.PAUSED),
    ):
        execution.deployments[deployment.id] = deployment
    app = create_app(
        Settings(_env_file=None),
        strategy_store=store,
        backtest_result_store=reader,
        execution_store=execution,
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/strategies", params={"limit": 2}).json()
    assert body["returned"] == 2
    assert body["total"] == 40
    assert body["has_more"] is True
    rows = {row["strategy_id"]: row for row in body["strategies"]}
    assert set(rows) == {str(identities[-1]), str(identities[-2])}
    newest = rows[str(identities[-1])]
    assert newest["backtest"] is not None
    assert newest["paper_live"] == {"paper": "running", "live": "stopped"}
    assert newest["active_deployment_count"] == 1
    assert rows[str(identities[-2])]["paper_live"] == {"paper": "paused", "live": "none"}
    assert reader.query_count == 1
    assert set(reader.requested) == {identities[-1], identities[-2]}
    assert execution.query_count == 1


def test_strategy_library_page_does_not_wait_for_off_page_lookups() -> None:
    """Batched enrichment costs one round trip per store regardless of library size."""
    store, _identities, reader = _library(40, delay=0.05)
    execution = _CountingExecutionStore(delay=0.05)
    app = create_app(
        Settings(_env_file=None),
        strategy_store=store,
        backtest_result_store=reader,
        execution_store=execution,
    )
    with TestClient(app) as client:
        started = datetime.now(UTC)
        response = client.get("/api/v1/strategies", params={"limit": 2})
        elapsed = datetime.now(UTC) - started
    assert response.status_code == 200
    assert response.json()["returned"] == 2
    assert elapsed.total_seconds() < 1.0, elapsed
    assert reader.query_count == 1
