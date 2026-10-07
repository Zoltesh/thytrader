"""Target planning, priority, backoff and retry scheduling for the ingest cycle.

Chooses the cycle's watch targets, classifies and orders them from durable worker
state with their per-cycle request budgets, clears satisfied ingest requests, and
records failures with their capped exponential retry instant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from typing import TYPE_CHECKING

from thytrader.market_data.watch_coverage import island_covers_watch, safe_shift
from thytrader.market_data.watchlist import (
    INGEST_REQUEST_POLL_SECONDS,
    MarketDataWatchlistStore,
    MarketDataWatchTarget,
)
from thytrader.market_data.worker_state import (
    MarketDataWorkerAttempt,
    MarketDataWorkerFailure,
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
    validate_market_data_worker_state,
)

if TYPE_CHECKING:
    from thytrader.market_data.models import CandleInterval


# Fair share of provider requests one target may spend per worker cycle. A target with a
# pending ``ingest_requested_at`` gets the larger share; every due target is visited
# each cycle, so a long backfill never starves maintenance of the others.
INGEST_REQUESTS_PER_TARGET_CYCLE = 8
INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE = 24


async def _load_validated_state(
    state_store: MarketDataWorkerStateStore,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
) -> MarketDataWorkerState | None:
    """Load durable state and reject forged timestamps before scheduling."""
    state = await state_store.get(provider, product_id, timeframe)
    return validate_market_data_worker_state(state) if state is not None else None


async def _cycle_targets(
    watchlist: MarketDataWatchlistStore | None,
    *,
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
    lookback_hours: int,
    now: datetime,
) -> tuple[MarketDataWatchTarget, ...]:
    """Prefer enabled or requested watchlist rows, otherwise the configured default."""
    if watchlist is not None:
        listed = await watchlist.list_all()
        due = tuple(
            target for target in listed if target.enabled or target.ingest_requested_at is not None
        )
        if due:
            return due
    return (
        MarketDataWatchTarget(
            provider=provider,
            product_id=product_id,
            timeframe=timeframe,
            lookback_hours=lookback_hours,
            enabled=True,
            updated_at=now.astimezone(UTC),
        ),
    )


class _TargetPriority(IntEnum):
    """Cycle order: cheap upkeep of covered watches, requested backfill, other backfill."""

    MAINTENANCE = 0
    REQUESTED = 1
    BACKFILL = 2


_NO_REQUEST = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _TargetPlan:
    """One target's scheduling facts for the current cycle."""

    target: MarketDataWatchTarget
    priority: _TargetPriority
    in_backoff: bool
    skip_reconcile: bool
    closed_end: datetime
    position: int = field(default=0)

    @property
    def requested(self) -> bool:
        """True when an operator queued ingest for this target."""
        return self.target.ingest_requested_at is not None

    @property
    def request_budget(self) -> int:
        """Return the provider requests this target may spend this cycle."""
        if self.priority is _TargetPriority.REQUESTED:
            return INGEST_REQUESTS_PER_REQUESTED_TARGET_CYCLE
        return INGEST_REQUESTS_PER_TARGET_CYCLE

    def sort_key(self) -> tuple[int, datetime, int]:
        """Order by priority, then oldest request first, then watchlist order."""
        requested_at = self.target.ingest_requested_at or _NO_REQUEST
        if self.priority is not _TargetPriority.REQUESTED:
            requested_at = _NO_REQUEST
        return (int(self.priority), requested_at, self.position)


async def _plan_targets(
    targets: tuple[MarketDataWatchTarget, ...],
    state_store: MarketDataWorkerStateStore,
    cycle_now: datetime,
) -> tuple[_TargetPlan, ...]:
    """Classify each target from durable state and return them in cycle priority order."""
    plans: list[_TargetPlan] = []
    for position, target in enumerate(targets):
        prior = await _load_validated_state(
            state_store, target.provider, target.product_id, target.timeframe
        )
        closed_end = target.timeframe.align_closed_end(cycle_now)
        covers = island_covers_watch(
            covered_starts_at=None if prior is None else prior.covered_starts_at,
            covered_ends_at=None if prior is None else prior.covered_ends_at,
            island_complete=bool(prior is not None and prior.complete),
            lookback_hours=target.lookback_hours,
            interval=target.timeframe,
            closed_end=closed_end,
            product_id=target.product_id,
            now=cycle_now,
            history_floor_at=None if prior is None else prior.history_floor_at,
        )
        if covers:
            priority = _TargetPriority.MAINTENANCE
        elif target.ingest_requested_at is not None:
            priority = _TargetPriority.REQUESTED
        else:
            priority = _TargetPriority.BACKFILL
        plans.append(
            _TargetPlan(
                target=target,
                priority=priority,
                in_backoff=_in_backoff(prior, cycle_now),
                skip_reconcile=prior is not None and prior.complete and not covers,
                closed_end=closed_end,
                position=position,
            )
        )
    return tuple(sorted(plans, key=_TargetPlan.sort_key))


async def _clear_satisfied_request(
    plan: _TargetPlan,
    state_store: MarketDataWorkerStateStore,
    watchlist: MarketDataWatchlistStore,
    cycle_now: datetime,
) -> None:
    """Clear ``ingest_requested_at`` once the island spans the watch lookback."""
    target = plan.target
    state_after = await _load_validated_state(
        state_store, target.provider, target.product_id, target.timeframe
    )
    watch_done = state_after is not None and island_covers_watch(
        covered_starts_at=state_after.covered_starts_at,
        covered_ends_at=state_after.covered_ends_at,
        island_complete=state_after.complete,
        lookback_hours=target.lookback_hours,
        interval=target.timeframe,
        closed_end=plan.closed_end,
        product_id=target.product_id,
        now=cycle_now,
        history_floor_at=state_after.history_floor_at,
    )
    if watch_done:
        await watchlist.clear_ingest_request(target.provider, target.product_id, target.timeframe)


def _in_backoff(state: MarketDataWorkerState | None, now: datetime) -> bool:
    """True when a prior failure scheduled a retry after the current instant."""
    return (
        state is not None
        and state.next_retry_at is not None
        and state.next_retry_at > now.astimezone(UTC)
    )


async def _next_wait_seconds(
    state_store: MarketDataWorkerStateStore,
    targets: tuple[MarketDataWatchTarget, ...],
    cycle_now: datetime,
    interval_seconds: int,
) -> int:
    """Wait at least one second, preferring the earliest recorded retry."""
    wait_seconds = interval_seconds
    now = cycle_now.astimezone(UTC)
    for target in targets:
        state = await _load_validated_state(
            state_store, target.provider, target.product_id, target.timeframe
        )
        if state is not None and state.next_retry_at is not None:
            wait_seconds = min(
                wait_seconds,
                max(1, int((state.next_retry_at - now).total_seconds())),
            )
    return max(1, min(wait_seconds, INGEST_REQUEST_POLL_SECONDS))


async def _record_failure(
    state_store: MarketDataWorkerStateStore,
    attempt: MarketDataWorkerAttempt,
    *,
    code: str,
    message: str,
    next_retry_at: datetime,
) -> None:
    """Persist one stable redacted failure outcome."""
    await state_store.record_failure(
        MarketDataWorkerFailure(
            attempt=attempt,
            code=code,
            message=message,
            next_retry_at=next_retry_at,
        )
    )


def _next_retry_at(
    attempted_at: datetime,
    base_seconds: int,
    prior_failures: int,
    jitter_value: float,
) -> datetime:
    """Return a capped exponential retry instant with up to twenty-percent positive jitter."""
    bounded_jitter = min(max(jitter_value, 0.0), 1.0)
    base_delay = min(base_seconds * (2**prior_failures), 3_600)
    delay = base_delay + int(base_delay * 0.2 * bounded_jitter)
    return safe_shift(
        attempted_at,
        timedelta(seconds=delay),
        "Market-data worker cannot represent its retry schedule.",
    )
