"""Operator health and runtime grade execution cycle timing (ADR 0131)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.backtest.results import DisabledBacktestResultStore
from thytrader.config import Settings
from thytrader.market_data.worker_state import DisabledMarketDataWorkerStateStore
from thytrader.observability.execution_cycle import (
    CYCLE_PHASES,
    BookStatusCounts,
    CycleBookTiming,
    CyclePhaseTiming,
    DatabaseCallSummary,
    ExecutionCycleReport,
    VenueCallSummary,
    WindowCacheCycleState,
)
from thytrader.operator.execution_cycle_report import execution_cycle_health
from thytrader.operator.models import ReportStatus
from thytrader.operator.service import OperatorDiagnostics
from thytrader.operator.status import recommend_next_action
from thytrader.persistence.execution_cycles import (
    DisabledExecutionCycleStore,
    InMemoryExecutionCycleStore,
)
from thytrader.persistence.portfolio_history import InMemoryPortfolioHistoryStore
from thytrader.portfolio.demo import DemoExchangeAccount
from thytrader.portfolio.service import PortfolioService
from thytrader.strategies.library import DisabledStrategyStore
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.store import DisabledExecutionStore

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)


def _report(*, started_at: datetime, duration: float, interval: int = 30) -> ExecutionCycleReport:
    """One completed cycle where the books phase dominates."""
    phases = tuple(
        CyclePhaseTiming(
            name=name,
            seconds=duration * 0.8 if name == "books" else duration * 0.04,
            venue_requests=40 if name == "books" else 0,
            venue_seconds=duration * 0.6 if name == "books" else 0.0,
            db_statements=900 if name == "books" else 10,
            db_seconds=duration * 0.1 if name == "books" else 0.01,
        )
        for name in CYCLE_PHASES
    )
    book = CycleBookTiming(
        deployment_id=uuid4(),
        product_id="AAVE-USDC",
        timeframe="4h",
        status="running",
        mode="live",
        kind="strategy",
        seconds=duration * 0.1,
        venue_requests=6,
        venue_seconds=duration * 0.08,
        db_statements=30,
        db_seconds=0.2,
        window_range_requests=2,
    )
    return ExecutionCycleReport(
        cycle_id=uuid4(),
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=duration),
        duration_seconds=duration,
        interval_seconds=interval,
        deployments_listed=114,
        books=BookStatusCounts(running=47, paused=3, stopped=64),
        book_failures=0,
        slowest_phase="books",
        phases=phases,
        slowest_books=(book,),
        venue=VenueCallSummary(
            requests=40, errors=0, seconds=duration * 0.6, max_seconds=1.2, endpoints=()
        ),
        database=DatabaseCallSummary(statements=960, seconds=duration * 0.1),
        window_cache=WindowCacheCycleState(
            windows=80, cached_candles=50_000, range_requests=90, warming_events=0, warming_books=0
        ),
    )


async def test_no_store_omits_the_component() -> None:
    """A process without cycle storage (local tests) reports nothing, not healthy."""
    assert await execution_cycle_health(None, now=_NOW) is None


async def test_missing_and_unavailable_timing_degrade() -> None:
    """Missing telemetry is never healthy."""
    missing = await execution_cycle_health(InMemoryExecutionCycleStore(), now=_NOW)
    assert missing is not None
    assert missing[0].status is ReportStatus.DEGRADED
    assert missing[0].reason_code == "CYCLE_TIMING_MISSING"
    down = await execution_cycle_health(DisabledExecutionCycleStore(), now=_NOW)
    assert down is not None
    assert down[0].reason_code == "CYCLE_TIMING_UNAVAILABLE"


async def test_cycle_within_interval_is_healthy() -> None:
    """A completed cycle shorter than its interval is healthy with its duration."""
    store = InMemoryExecutionCycleStore()
    await store.complete_cycle(_report(started_at=_NOW - timedelta(seconds=20), duration=8.0))
    graded = await execution_cycle_health(store, now=_NOW)
    assert graded is not None
    component, payload = graded
    assert component.status is ReportStatus.HEALTHY
    assert component.reason_code == "CYCLE_WITHIN_BUDGET"
    assert payload is not None
    assert payload.summary.slow is False
    assert payload.summary.last_duration_seconds == 8.0


async def test_cycle_over_interval_but_within_budget_is_healthy() -> None:
    """A 45 s cycle on a 30 s interval stays within the 60 s budget health already allows."""
    store = InMemoryExecutionCycleStore()
    await store.complete_cycle(_report(started_at=_NOW - timedelta(seconds=80), duration=45.0))
    graded = await execution_cycle_health(store, now=_NOW)
    assert graded is not None
    component, payload = graded
    assert component.status is ReportStatus.HEALTHY
    assert component.reason_code == "CYCLE_WITHIN_BUDGET"
    assert "45.0s against a 30s interval (60s budget" in component.detail
    assert payload is not None
    assert payload.summary.budget_seconds == 60
    assert payload.summary.slow is False
    assert payload.recent_slow_cycles == 0


async def test_slow_completed_cycle_names_phase_and_books() -> None:
    """An overrun names the slowest phase, venue traffic and the slowest book."""
    store = InMemoryExecutionCycleStore()
    report = _report(started_at=_NOW - timedelta(seconds=200), duration=152.0)
    await store.complete_cycle(report)
    graded = await execution_cycle_health(store, now=_NOW)
    assert graded is not None
    component, payload = graded
    assert component.status is ReportStatus.DEGRADED
    assert component.reason_code == "CYCLE_SLOW"
    assert "152s against a 30s interval" in component.detail
    assert "slowest phase books" in component.detail
    assert str(report.slowest_books[0].deployment_id) in component.detail
    assert "AAVE-USDC 4h" in component.detail
    assert len(component.detail) <= 500
    assert payload is not None
    assert payload.latest == report
    assert payload.recent_slow_cycles == 1
    assert "runtime" in recommend_next_action([component])


async def test_running_cycle_past_its_interval_is_slow_even_after_a_fast_one() -> None:
    """A cycle still running past its interval degrades before it completes."""
    store = InMemoryExecutionCycleStore()
    await store.complete_cycle(_report(started_at=_NOW - timedelta(seconds=300), duration=10.0))
    await store.start_cycle(uuid4(), _NOW - timedelta(seconds=95), 30)
    graded = await execution_cycle_health(store, now=_NOW)
    assert graded is not None
    component, payload = graded
    assert component.reason_code == "CYCLE_SLOW"
    assert "current cycle has run 95.0s, over its 60s budget" in component.detail
    assert payload is not None
    assert payload.in_progress_started_at == _NOW - timedelta(seconds=95)
    assert payload.summary.in_progress_seconds == 95.0
    assert [sample.duration_seconds for sample in payload.recent] == [None, 10.0]


async def test_first_cycle_within_its_interval_is_in_progress() -> None:
    """The first cycle after a restart is healthy until it overruns."""
    store = InMemoryExecutionCycleStore()
    await store.start_cycle(uuid4(), _NOW - timedelta(seconds=5), 30)
    graded = await execution_cycle_health(store, now=_NOW)
    assert graded is not None
    assert graded[0].reason_code == "CYCLE_IN_PROGRESS"
    assert graded[0].status is ReportStatus.HEALTHY


def _diagnostics(store: InMemoryExecutionCycleStore) -> OperatorDiagnostics:
    """Diagnostics with demo services and the given cycle store."""
    return OperatorDiagnostics(
        settings=Settings(_env_file=None),
        portfolio=PortfolioService(DemoExchangeAccount(), demo=True),
        market_data_state=DisabledMarketDataWorkerStateStore(),
        history=InMemoryPortfolioHistoryStore(),
        publications=DisabledStrategySnapshotStore(),
        strategies_store=DisabledStrategyStore(),
        backtests=DisabledBacktestResultStore(),
        execution=DisabledExecutionStore(),
        audit=InMemoryAuditEventStore(),
        cycle_store=store,
    )


async def test_health_and_runtime_surface_cycle_slow() -> None:
    """Both reports carry the component; health a summary, runtime the full report."""
    store = InMemoryExecutionCycleStore()
    report = _report(started_at=datetime.now(UTC) - timedelta(seconds=200), duration=150.0)
    await store.complete_cycle(report)
    diagnostics = _diagnostics(store)
    health = await diagnostics.health()
    cycle = next(item for item in health.components if item.name == "execution_cycle")
    assert cycle.reason_code == "CYCLE_SLOW"
    assert health.overall_status is not ReportStatus.HEALTHY
    assert health.payload.execution_cycle is not None
    assert health.payload.execution_cycle.slow is True
    assert health.payload.execution_cycle.slowest_phase == "books"
    runtime = await diagnostics.runtime_report()
    assert any(item.reason_code == "CYCLE_SLOW" for item in runtime.components)
    assert runtime.payload.execution_cycle is not None
    assert runtime.payload.execution_cycle.latest == report
