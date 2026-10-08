"""Fresh unfiltered risk evidence and bounded genuine midnight-mark recovery."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.risk.opening_accounting import opening_replay, reconstruct_day_open, utc_day_start
from thytrader.trading.day_open import MidnightMark
from thytrader.trading.models import DeploymentSnapshot, ExecutionStoreError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.service import MarketDataService
    from thytrader.trading.store import ExecutionStore

_MARKET_DATA: ContextVar[MarketDataService | None] = ContextVar("risk_market_data", default=None)


@contextmanager
def risk_market_data_scope(service: MarketDataService) -> Iterator[None]:
    """Bind read-only historical evidence to worker/API supervision without extra authority."""
    token = _MARKET_DATA.set(service)
    try:
        yield
    finally:
        _MARKET_DATA.reset(token)


async def accounting_snapshot(
    store: ExecutionStore, deployment_id: UUID, *, as_of: datetime
) -> DeploymentSnapshot:
    """Read current shared-book evidence; never trust a cached or product-filtered replacement."""
    snapshot = await store.get_accounting_snapshot(deployment_id)
    if not snapshot.accounting_complete:
        raise ExecutionStoreError("Risk accounting snapshot omitted sibling economics.")
    marks = await _opening_marks(snapshot, as_of=as_of)
    evidence = reconstruct_day_open(snapshot, as_of=as_of, marks=marks)
    if evidence is None:
        return snapshot
    return replace(
        snapshot, deployment=replace(snapshot.deployment, risk_day_open_evidence=evidence)
    )


async def accounting_portfolio(
    store: ExecutionStore, *, as_of: datetime
) -> tuple[DeploymentSnapshot, ...]:
    """Reload retained books so successive product reconciliation cannot be overwritten by cache."""
    deployments = await store.list_deployments()
    return tuple(
        [await accounting_snapshot(store, deployment.id, as_of=as_of) for deployment in deployments]
    )


async def _opening_marks(
    snapshot: DeploymentSnapshot, *, as_of: datetime
) -> tuple[MidnightMark, ...]:
    """Recover one actual midnight candle per overnight product, only when needed.

    Historical freshness is not current-feed freshness. Exact range, boundary and
    completeness must match; failure leaves opening evidence unknown, never a latest
    close fallback. No fills or prices are invented when a provider lacks range reads.
    """
    if reconstruct_day_open(snapshot, as_of=as_of) is not None:
        return ()
    replay = opening_replay(snapshot, as_of=as_of)
    service = _MARKET_DATA.get()
    if replay is None or service is None:
        return ()
    day_start = utc_day_start(as_of)
    # A UTC-aligned hourly close works even when the strategy's interval is multi-day.
    interval = parse_candle_interval("1h")
    start = day_start - interval.duration
    marks: list[MidnightMark] = []
    for product, quantity in sorted(replay.midnight_quantities.items()):
        if quantity == 0:
            continue
        try:
            report = await service.get_range(product, interval, start, day_start, as_of)
            candles = report.quality.candles
            if (
                report.complete
                and report.starts_at == start
                and report.ends_at == day_start
                and report.requested_candle_count == 1
                and len(candles) == 1
                and candles[0].starts_at == start
                and candles[0].close > 0
            ):
                marks.append(
                    MidnightMark(product_id=product, closes_at=day_start, price=candles[0].close)
                )
        except RuntimeError, ValueError, TypeError, OSError, AttributeError:
            # Missing historical evidence blocks entries, not protective supervision.
            continue
    return tuple(marks)
