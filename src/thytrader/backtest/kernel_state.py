"""Simulation state of the backtest kernel: books, resting entries, positions, costs.

The per-product book (resting entry, open position, cooldown), the entry-funnel
tally, resolved cost assumptions (with the futures terms of a futures run), the side
and exit-reason aliases, and the fail-closed ``BacktestSimulationError``. This module
imports no other kernel module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestExitCount,
    BacktestExitReason,
    BacktestFill,
    BacktestGateReason,
    BacktestSkipCount,
    BacktestTrade,
)
from thytrader.trading.futures_sizing import FuturesMarginTerms

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal

    from thytrader.backtest.broker import FillModel
    from thytrader.evaluation.stress import ExecutionStress
    from thytrader.evaluation.trace import SignalTraceRecord
    from thytrader.market_data.models import Candle
    from thytrader.trading.geometry import EntrySkipReason


ExitReason = BacktestExitReason


PositionSide = Literal["long", "short"]


class BacktestSimulationError(ValueError):
    """Report a fail-closed simulation input or unsupported execution-condition failure."""


@dataclass(frozen=True, slots=True)
class _PendingEntry:
    """A close-limit entry (or same-side add) resting until a later bar trades through it.

    ``target_price`` is None when the strategy declares no take-profit.
    """

    signal: SignalTraceRecord
    limit_price: Decimal
    quantity: Decimal
    stop_price: Decimal
    target_price: Decimal | None
    waited_bars: int
    side: PositionSide = "long"
    is_pyramid_add: bool = False
    size_capped: bool = False


@dataclass(frozen=True, slots=True)
class _Position:
    """An open position whose exits arm only after the fill bar, matching the worker.

    ``take_profit_resting`` means post-fill-bar exit management is armed; a position with
    ``target_price`` None (no take-profit) still stops, trails, and time-exits normally.
    ``unit_fee`` is the per-contract fee per unit of base quantity (futures only; None for
    spot). ``funding`` is the signed funding cash flow so far (perps only; None otherwise).
    """

    entry: BacktestFill
    stop_price: Decimal
    target_price: Decimal | None
    entered_bar_index: int
    take_profit_resting: bool = False
    trail_extreme: Decimal | None = None
    side: PositionSide = "long"
    add_count: int = 1
    unit_fee: Decimal | None = None
    funding: Decimal | None = None


@dataclass(slots=True)
class _Tally:
    """Mutable entry-funnel counters for one simulation; frozen into ``BacktestDiagnostics``."""

    signals_matched: int = 0
    entries_rested: int = 0
    entries_filled: int = 0
    entries_expired: int = 0
    entries_repriced: int = 0
    entries_refused_at_fill: int = 0
    entries_unfilled_at_end: int = 0
    entries_size_capped: int = 0
    warmup_bars: int = 0
    skipped: dict[BacktestGateReason | EntrySkipReason, int] = field(default_factory=dict)
    exits: dict[ExitReason, int] = field(default_factory=dict)

    def skip(self, reason: BacktestGateReason | EntrySkipReason) -> None:
        """Count one matched signal that rested no entry for ``reason``."""
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def closed(self, trade: BacktestTrade) -> None:
        """Count one closed trade under its exit reason."""
        reason = trade.exit.reason
        self.exits[reason] = self.exits.get(reason, 0) + 1

    def rested(self, pending: _PendingEntry) -> None:
        """Count one rested entry and whether a notional cap bound its size."""
        self.entries_rested += 1
        if pending.size_capped:
            self.entries_size_capped += 1

    def diagnostics(self) -> BacktestDiagnostics:
        """Freeze the counters, with skip reasons in stable lexicographic order."""
        return BacktestDiagnostics(
            signals_matched=self.signals_matched,
            entries_rested=self.entries_rested,
            entries_filled=self.entries_filled,
            entries_expired=self.entries_expired,
            entries_repriced=self.entries_repriced,
            entries_refused_at_fill=self.entries_refused_at_fill,
            entries_unfilled_at_end=self.entries_unfilled_at_end,
            entries_size_capped=self.entries_size_capped,
            warmup_bars=self.warmup_bars,
            skipped=tuple(
                BacktestSkipCount(reason=reason, count=count)
                for reason, count in sorted(self.skipped.items(), key=lambda item: item[0].value)
            ),
            exit_reasons=tuple(
                BacktestExitCount(reason=reason, count=count)
                for reason, count in sorted(self.exits.items())
            ),
        )


@dataclass(slots=True)
class _Book:
    """Per-product resting entry, open position, and cooldown sharing one quote balance."""

    product_id: str
    candle_by_start: Mapping[datetime, Candle]
    records: Mapping[datetime, SignalTraceRecord]
    pending: _PendingEntry | None = None
    position: _Position | None = None
    cooldown_bars: int = 0

    @property
    def is_open(self) -> bool:
        """Whether this book holds a position or a resting entry that may open one."""
        return self.position is not None or self.pending is not None


@dataclass(frozen=True, slots=True)
class _Costs:
    """Published fee, slippage, and spread-stress assumptions resolved to Decimals."""

    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    slippage_bps: Decimal
    fill_model: FillModel
    execution_stress: ExecutionStress | None = None
    futures: _FuturesTerms | None = None


@dataclass(frozen=True, slots=True)
class _FuturesTerms(FuturesMarginTerms):
    """The bound contract, margin, funding, and leverage terms of one futures run (ADR 0128).

    The margin arithmetic is the shared ``FuturesMarginTerms`` paper books use too; rates
    are already multiplied by the run's stress multiplier. ``funding_rates`` holds the
    recorded hourly rates by funding hour; ``constant_funding_rate`` replaces them when the
    run declared one; both are None for dated contracts. ``flatten_at`` is ``expires_at``
    minus ``flatten_before_expiry_hours`` for dated contracts.
    """

    funding_rates: Mapping[datetime, Decimal] | None = None
    constant_funding_rate: Decimal | None = None
    flatten_at: datetime | None = None

    @property
    def perpetual(self) -> bool:
        """Whether the contract charges hourly funding."""
        return self.funding_rates is not None or self.constant_funding_rate is not None
