"""Per-cycle preparation of a paper futures book (ADR 0129 §4).

The execution worker binds a :class:`FuturesRuntime` (the contract binding store and the
futures observation store) for its whole run. For each futures deployment, before its bars
are processed, :func:`prepare_futures_book`:

1. loads the contract bound at start;
2. builds the margin terms from the latest observed overnight rates (no stress,
   maintenance = initial, the default liquidation buffer, the strategy's leverage);
3. for a perp, applies every funding hour the book held a position through, in order, at
   the settled rate and the close of the bar containing the hour, in one row-locked
   transaction with the cash update. It stops at the first hour whose settled rate or mark
   is missing; once that hour is past the settlement grace it is reported as overdue and
   new entries are denied until it is applied. Nothing is zero-filled.

The returned :class:`FuturesBookState` is bound with ``futures_book_scope`` while the book
is processed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
from typing import TYPE_CHECKING, Protocol

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.models import parse_candle_interval
from thytrader.trading.futures_book import (
    DEFAULT_PAPER_BUFFER_FRACTION,
    FuturesBookState,
    FuturesBookUnavailableError,
    funding_flow,
    funding_hours_held,
    funding_overdue,
)
from thytrader.trading.futures_sizing import FuturesMarginTerms
from thytrader.trading.geometry import entry_bar_bucket
from thytrader.trading.models import DeploymentMode, ExecutionStoreError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from thytrader.market_data.futures_observations import (
        FundingRateRecord,
        FuturesInstrumentObservation,
    )
    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.futures_book import BoundFuturesContract, FuturesContractStore
    from thytrader.trading.models import DeploymentSnapshot, FundingCashFlow
    from thytrader.trading.store import ExecutionStore

_logger = logging.getLogger(__name__)
_HOUR = timedelta(hours=1)
_TICK = timedelta(microseconds=1)


class FuturesObservationReader(Protocol):
    """The recorded futures facts a paper book reads each cycle."""

    async def latest_instrument(
        self, product_id: str
    ) -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Return one contract's newest recorded facts and when they were last seen."""
        ...

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """Return funding rows with ``starts_at <= funding_time < ends_at``, oldest first."""
        ...


@dataclass(frozen=True, slots=True)
class FuturesRuntime:
    """The stores a paper futures book needs, bound for the worker's run."""

    contracts: FuturesContractStore
    observations: FuturesObservationReader


_RUNTIME: ContextVar[FuturesRuntime | None] = ContextVar("futures_runtime", default=None)


@contextmanager
def futures_runtime_scope(runtime: FuturesRuntime | None) -> Iterator[None]:
    """Bind the futures stores for every book processed in this context."""
    token = _RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def current_futures_runtime() -> FuturesRuntime | None:
    """The bound futures stores, or None on an install without them."""
    return _RUNTIME.get()


async def prepare_futures_book(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    store: ExecutionStore,
    market_data: MarketDataService,
    now: datetime | None = None,
) -> tuple[FuturesBookState | None, DeploymentSnapshot]:
    """Load one futures book's state and apply its due funding; ``None`` for spot books."""
    deployment = snapshot.deployment
    if not is_futures_product_id(deployment.product_id):
        return None, snapshot
    side = "short" if strategy.entry.side == "short" else "long"
    runtime = current_futures_runtime()
    unbound = FuturesBookState(
        deployment_id=deployment.id,
        product_id=deployment.product_id,
        side=side,
        binding=None,
        margin=None,
    )
    if runtime is None or deployment.mode is not DeploymentMode.PAPER:
        return unbound, snapshot
    try:
        binding = await runtime.contracts.load_contract(deployment.id)
    except FuturesBookUnavailableError:
        _logger.warning("futures_binding_unavailable deployment_id=%s", deployment.id)
        return unbound, snapshot
    if binding is None:
        return unbound, snapshot
    margin, observed_at = await _margin_terms(runtime, binding, strategy)
    moment = now or datetime.now(UTC)
    overdue = None
    if binding.contract.kind == "perpetual_future":
        snapshot, overdue = await _settle_funding(
            snapshot, runtime=runtime, store=store, market_data=market_data, now=moment
        )
    return (
        FuturesBookState(
            deployment_id=deployment.id,
            product_id=deployment.product_id,
            side=side,
            binding=binding,
            margin=margin,
            margin_observed_at=observed_at,
            funding_overdue=overdue,
        ),
        snapshot,
    )


async def _margin_terms(
    runtime: FuturesRuntime, binding: BoundFuturesContract, strategy: StrategyDefinition
) -> tuple[FuturesMarginTerms | None, datetime | None]:
    """Overnight margin from the latest observation; unknown rates stay unknown."""
    derivatives = strategy.derivatives
    if derivatives is None:
        return None, None
    try:
        latest = await runtime.observations.latest_instrument(binding.contract.product_id)
    except Exception:  # noqa: BLE001 - storage failures leave margin unknown, never zero.
        _logger.warning("futures_margin_unavailable product_id=%s", binding.contract.product_id)
        return None, None
    if latest is None:
        return None, None
    observation, seen_at = latest
    long_rate = _rate(observation.overnight_long_margin_rate)
    short_rate = _rate(observation.overnight_short_margin_rate)
    if long_rate is None or short_rate is None:
        return None, seen_at
    return (
        FuturesMarginTerms(
            contract_size=Decimal(binding.contract.contract_size),
            long_rate=long_rate,
            short_rate=short_rate,
            maintenance_fraction=Decimal(1),
            min_buffer_fraction=DEFAULT_PAPER_BUFFER_FRACTION,
            max_leverage=Decimal(derivatives.max_leverage),
            fee_per_contract=binding.fee_per_contract,
        ),
        seen_at,
    )


def _rate(raw: str | None) -> Decimal | None:
    """A positive margin rate, or None when unlisted or malformed."""
    if raw is None:
        return None
    try:
        rate = Decimal(raw)
    except InvalidOperation:
        return None
    return rate if rate.is_finite() and 0 < rate <= 1 else None


async def _settle_funding(
    snapshot: DeploymentSnapshot,
    *,
    runtime: FuturesRuntime,
    store: ExecutionStore,
    market_data: MarketDataService,
    now: datetime,
) -> tuple[DeploymentSnapshot, datetime | None]:
    """Apply due funding hours in order; return the first overdue hour still missing."""
    product_id = snapshot.deployment.product_id
    due = funding_hours_held(snapshot, product_id, through=now)
    if not due:
        return snapshot, None
    try:
        records = await runtime.observations.funding_rates(
            product_id=product_id, starts_at=due[0], ends_at=due[-1] + _HOUR
        )
    except Exception:  # noqa: BLE001 - unreadable history is missing, never zero.
        records = ()
    settled = {record.funding_time: record.rate for record in records if record.settled}
    timeframe = snapshot.deployment.timeframe
    marks = (
        {} if timeframe is None else await _hour_marks(market_data, product_id, timeframe, due, now)
    )
    flows: list[FundingCashFlow] = []
    overdue = None
    for hour in due:
        rate = settled.get(hour)
        mark = marks.get(hour)
        if rate is None or mark is None:
            if funding_overdue(hour, now):
                overdue = hour
            break
        flows.append(
            funding_flow(
                snapshot,
                product_id=product_id,
                hour=hour,
                mark_price=mark,
                rate=rate,
                applied_at=now,
            )
        )
    if flows:
        snapshot = await _apply(store, snapshot, tuple(flows))
    return snapshot, overdue


async def _apply(
    store: ExecutionStore, snapshot: DeploymentSnapshot, flows: tuple[FundingCashFlow, ...]
) -> DeploymentSnapshot:
    """Apply funding through the store's row-locked transaction."""
    apply = getattr(store, "apply_funding_transaction", None)
    if not callable(apply):
        raise ExecutionStoreError("Execution storage cannot apply funding transactions.")
    _applied, refreshed = await apply(snapshot.deployment.id, flows=flows)
    return refreshed


async def _hour_marks(
    market_data: MarketDataService,
    product_id: str,
    timeframe: str,
    hours: Sequence[datetime],
    now: datetime,
) -> dict[datetime, Decimal]:
    """The close of the closed decision bar containing each funding hour."""
    interval = parse_candle_interval(timeframe)
    starts = {hour: entry_bar_bucket(hour - _TICK, timeframe) for hour in hours}
    closed = {hour: start for hour, start in starts.items() if start + interval.duration <= now}
    if not closed:
        return {}
    first = min(closed.values())
    last_end = max(closed.values()) + interval.duration
    try:
        report = await market_data.get_range(product_id, interval, first, last_end, now)
    except Exception:  # noqa: BLE001 - a missing mark leaves the hour unapplied.
        return {}
    closes = {candle.starts_at: candle.close for candle in report.quality.candles}
    marks: dict[datetime, Decimal] = {}
    for hour, start in closed.items():
        close = closes.get(start)
        if close is not None:
            marks[hour] = close
    return marks
