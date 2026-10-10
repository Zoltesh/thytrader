"""Futures terms of the backtest kernel: binding, sizing, funding, liquidation, expiry.

A futures run (ADR 0128) keeps the spot cash-and-inventory ledger because quantities are
base-equivalent (contracts x ``contract_size``). This module adds what spot never has:

- **Binding.** The run's ``instrument_contract``, ``margin`` and ``funding`` must match the
  strategy; a perp run either supplies the settled hourly funding rows whose fingerprint
  the run bound, covering every funding hour in the window, or declares a constant rate.
- **Sizing.** Whole contracts only (never rounded up), bounded by leverage, by the
  liquidation buffer after the entry fee, by ``max_strategy_exposure_fraction`` of equity
  committed as initial margin, and by ``max_quote_notional``.
- **Funding.** Each funding hour ``T`` with ``starts_at < T <= starts_at + bar`` is charged
  at the bar close when the position is still open after the bar's exits.
- **Liquidation.** Checked at the bar's adverse extreme before stops and targets; the
  position closes at that extreme as a taker.
- **Expiry.** A dated contract flattens at the open of the first bar at or after
  ``flatten_at`` and rests no entry whose fill bar would start there.

Spot runs carry no terms; every function here is a no-op for them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING

from thytrader.backtest.kernel_exits import _taker_exit_quote
from thytrader.backtest.kernel_fills import _close_position
from thytrader.backtest.kernel_state import BacktestSimulationError, _FuturesTerms
from thytrader.evaluation.futures_spec import funding_hours, funding_series_fingerprint
from thytrader.trading.geometry import EntrySkipReason

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.backtest.kernel_state import PositionSide, _Book, _Costs, _Tally
    from thytrader.backtest.models import BacktestTrade
    from thytrader.backtest.research_validity import ResearchValidityLimitCode
    from thytrader.evaluation.futures_spec import InstrumentContract
    from thytrader.evaluation.models import ResearchRunSpecification
    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition

_HOUR = timedelta(hours=1)


def _futures_terms(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    funding_rates: Mapping[datetime, Decimal] | None,
) -> _FuturesTerms | None:
    """Resolve and verify the futures terms of a run, or None for a spot run."""
    contract = specification.instrument_contract
    margin = specification.margin
    if not strategy.instrument.is_future and contract is None:
        if funding_rates is not None:
            raise BacktestSimulationError("Funding rates apply to perp-style futures runs only.")
        return None
    if not strategy.instrument.is_future or contract is None or margin is None:
        raise BacktestSimulationError(
            "FUTURES_SPEC_MISMATCH: a futures strategy needs a futures run specification "
            "and a futures run specification needs a futures strategy."
        )
    _require_contract_matches(contract, strategy)
    derivatives = strategy.derivatives
    if derivatives is None:  # pragma: no cover - document validation requires it
        raise BacktestSimulationError("A futures strategy needs a derivatives block.")
    stress = Decimal(margin.stress_multiplier)
    contract_size = Decimal(contract.contract_size)
    fee = specification.costs.fee_per_contract
    flatten_at = None
    if contract.expires_at is not None:
        hours = derivatives.flatten_before_expiry_hours
        if hours is None:
            raise BacktestSimulationError(
                "FUTURES_EXPIRY_UNSET: a dated contract needs "
                "derivatives.flatten_before_expiry_hours."
            )
        if specification.evaluation.ends_at > contract.expires_at:
            raise BacktestSimulationError(
                "FUTURES_WINDOW_PAST_EXPIRY: the evaluation window ends after "
                f"{contract.product_id} expires."
            )
        flatten_at = contract.expires_at - timedelta(hours=hours)
    rates, constant = _funding_terms(specification, contract, funding_rates)
    return _FuturesTerms(
        contract_size=contract_size,
        long_rate=Decimal(margin.long_rate) * stress,
        short_rate=Decimal(margin.short_rate) * stress,
        maintenance_fraction=Decimal(margin.maintenance_fraction_of_initial),
        min_buffer_fraction=Decimal(margin.min_liquidation_buffer_fraction),
        max_leverage=Decimal(derivatives.max_leverage),
        fee_per_contract=Decimal(fee) if fee is not None else Decimal(0),
        funding_rates=rates,
        constant_funding_rate=constant,
        flatten_at=flatten_at,
    )


def _require_contract_matches(contract: InstrumentContract, strategy: StrategyDefinition) -> None:
    """The bound contract is the strategy's product and underlying."""
    if contract.product_id != strategy.instrument.product_id:
        raise BacktestSimulationError(
            "FUTURES_SPEC_MISMATCH: instrument_contract names a different product."
        )
    if contract.underlying != strategy.instrument.base_currency:
        raise BacktestSimulationError(
            "FUTURES_SPEC_MISMATCH: instrument_contract names a different underlying."
        )


def _funding_terms(
    specification: ResearchRunSpecification,
    contract: InstrumentContract,
    funding_rates: Mapping[datetime, Decimal] | None,
) -> tuple[Mapping[datetime, Decimal] | None, Decimal | None]:
    """Verify the funding series a perp run consumed, or take its declared constant rate."""
    funding = specification.funding
    if contract.kind == "dated_future" or funding is None:
        if funding_rates is not None:
            raise BacktestSimulationError("Funding rates apply to perp-style futures runs only.")
        return None, None
    if funding.constant_rate is not None:
        if funding_rates is not None:
            raise BacktestSimulationError(
                "A constant-rate funding assumption takes no funding series."
            )
        return None, Decimal(funding.constant_rate)
    if funding_rates is None:
        raise BacktestSimulationError(
            "FUNDING_HISTORY_MISSING: the run binds a funding series but none was supplied."
        )
    hours = funding_hours(specification.evaluation.starts_at, specification.evaluation.ends_at)
    missing = [hour for hour in hours if hour not in funding_rates]
    if missing:
        raise BacktestSimulationError(
            f"FUNDING_HISTORY_MISSING: no settled funding rate for {_hour_text(missing[0])} "
            f"({len(missing)} missing hours)."
        )
    if len(funding_rates) != len(hours):
        raise BacktestSimulationError(
            "FUNDING_SERIES_MISMATCH: the funding series has hours outside the window."
        )
    if (
        len(funding_rates) != funding.settled_hours
        or funding_series_fingerprint(contract.product_id, funding_rates)
        != funding.series_fingerprint
    ):
        raise BacktestSimulationError(
            "FUNDING_SERIES_MISMATCH: the funding rows do not match the bound series."
        )
    return funding_rates, None


def _hour_text(hour: datetime) -> str:
    """Canonical ``Z`` text of one funding hour."""
    return hour.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _futures_validity_limits(
    terms: _FuturesTerms | None, bar_duration: timedelta
) -> tuple[ResearchValidityLimitCode, ...]:
    """The modeling limits every futures result discloses (empty for spot)."""
    if terms is None:
        return ()
    limits: list[ResearchValidityLimitCode] = [
        "futures_constant_margin",
        "futures_conservative_liquidation",
        "futures_shared_usdc_collateral",
    ]
    if terms.constant_funding_rate is not None:
        limits.append("futures_constant_funding")
    if terms.perpetual and bar_duration > _HOUR:
        limits.append("futures_funding_at_bar_close")
    return tuple(limits)


def _size_contracts(
    terms: _FuturesTerms,
    strategy: StrategyDefinition,
    *,
    cash: Decimal,
    side: PositionSide,
    limit_price: Decimal,
    requested_notional: Decimal,
    maker_fee_rate: Decimal,
) -> tuple[Decimal, bool] | EntrySkipReason:
    """Whole-contract base quantity within every futures bound, and whether a bound applied.

    With ``u`` the notional of one contract, ``f`` its entry fee, ``E`` equity (cash while
    flat) and ``n`` contracts, every bound holds after the fee is paid: ``n u <= L (E - n f)``
    (leverage), ``n u r m <= (1 - b)(E - n f)`` (liquidation buffer),
    ``n u r <= X (E - n f)`` (exposure as committed initial margin) and ``n u <= Q``.
    """
    if cash <= 0:
        return EntrySkipReason.INSUFFICIENT_CASH
    unit = limit_price * terms.contract_size
    fee = unit * maker_fee_rate + terms.fee_per_contract
    rate = terms.initial_rate(side)
    leverage = terms.max_leverage
    room = Decimal(1) - terms.min_buffer_fraction
    exposure = Decimal(strategy.portfolio_limits.max_strategy_exposure_fraction)
    bound = min(
        Decimal(strategy.sizing.max_quote_notional) / unit,
        leverage * cash / (unit + leverage * fee),
        room * cash / (unit * rate * terms.maintenance_fraction + room * fee),
        exposure * cash / (unit * rate + exposure * fee),
    )
    requested = requested_notional / unit
    contracts = min(requested, bound).to_integral_value(rounding=ROUND_FLOOR)
    if contracts < 1:
        return EntrySkipReason.BELOW_ONE_CONTRACT
    if contracts * unit < Decimal(strategy.sizing.min_quote_notional):
        return EntrySkipReason.NOTIONAL_BELOW_MINIMUM
    return contracts * terms.contract_size, requested > bound


def _in_expiry_window(costs: _Costs, instant: datetime) -> bool:
    """Whether ``instant`` is at or past a dated contract's flatten time."""
    terms = costs.futures
    return terms is not None and terms.flatten_at is not None and instant >= terms.flatten_at


def _flatten_for_expiry(
    book: _Book,
    candle: Candle,
    *,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
    tally: _Tally,
) -> tuple[BacktestTrade | None, Decimal]:
    """Cancel the resting entry and close the position at the bar open as a taker."""
    if book.pending is not None:
        book.pending = None
        tally.entries_expired += 1
    position = book.position
    if position is None:
        return None, cash
    trade, cash = _close_position(
        position,
        candle,
        cash=cash,
        quote=_taker_exit_quote(position, candle.open, costs),
        reason="expiry",
        fee_rate=costs.taker_fee_rate,
        bar_duration=bar_duration,
    )
    book.position = None
    return trade, cash


def _liquidate(
    book: _Book,
    candle: Candle,
    *,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
) -> tuple[BacktestTrade | None, Decimal]:
    """Close at the adverse extreme when equity there falls below maintenance."""
    terms = costs.futures
    position = book.position
    if terms is None or position is None:
        return None, cash
    adverse = candle.high if position.side == "short" else candle.low
    quantity = Decimal(position.entry.quantity)
    signed = -quantity if position.side == "short" else quantity
    equity = cash + signed * costs.fill_model.mark(adverse, position.side)
    if equity >= terms.maintenance(quantity, adverse, position.side):
        return None, cash
    trade, cash = _close_position(
        position,
        candle,
        cash=cash,
        quote=_taker_exit_quote(position, adverse, costs),
        reason="liquidation",
        fee_rate=costs.taker_fee_rate,
        bar_duration=bar_duration,
    )
    book.position = None
    return trade, cash


def _charge_funding(
    book: _Book,
    candle: Candle,
    *,
    costs: _Costs,
    cash: Decimal,
    bar_duration: timedelta,
) -> Decimal:
    """Charge every funding hour that closes inside this bar to the still-open position.

    Longs pay a positive rate and shorts receive it: the cash flow is
    ``-signed quantity x bar close x rate``.
    """
    terms = costs.futures
    position = book.position
    if terms is None or position is None or not terms.perpetual:
        return cash
    hours = funding_hours(candle.starts_at, candle.starts_at + bar_duration)
    if not hours:
        return cash
    quantity = Decimal(position.entry.quantity)
    signed = -quantity if position.side == "short" else quantity
    flow = -sum(
        (signed * candle.close * _funding_rate(terms, hour) for hour in hours), start=Decimal(0)
    )
    book.position = replace(position, funding=(position.funding or Decimal(0)) + flow)
    return cash + flow


def _funding_rate(terms: _FuturesTerms, hour: datetime) -> Decimal:
    """The constant rate, or the recorded rate of one funding hour."""
    if terms.constant_funding_rate is not None:
        return terms.constant_funding_rate
    rate = None if terms.funding_rates is None else terms.funding_rates.get(hour)
    if rate is None:
        raise BacktestSimulationError(
            f"FUNDING_HISTORY_MISSING: no settled funding rate for {_hour_text(hour)}."
        )
    return rate
