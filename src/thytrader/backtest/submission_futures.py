"""Server-side futures bindings of a backtest submission (ADR 0128, slice P1-3).

A futures run never trusts the caller for venue facts. The submitter binds:

- the **contract** from the latest recorded catalog observation (its payload
  fingerprint is the run's ``catalog_fingerprint``);
- the **margin** from that observation's overnight rates (``latest_observation`` with the
  instant it was last seen), unless the caller declared explicit rates;
- the **funding** of a perp from the settled hourly history covering every funding hour of
  the window (its ``funding_series_fingerprint``), unless the caller declared a constant
  rate.

Any missing fact is a caller-input rejection naming what is missing; nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from thytrader.backtest.submission_models import BacktestSubmissionRejectedError
from thytrader.evaluation.futures_spec import (
    FundingAssumption,
    InstrumentContract,
    MarginAssumption,
    funding_hours,
    funding_series_fingerprint,
)
from thytrader.market_data.instruments import InstrumentKind

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.backtest.submission_models import FuturesBacktestAssumptions
    from thytrader.market_data.futures_observations import (
        FundingRateRecord,
        FuturesInstrumentObservation,
    )
    from thytrader.strategies.models import StrategyDefinition

_HOUR = timedelta(hours=1)


class FuturesRunSource(Protocol):
    """The recorded futures facts a submission binds (the futures observation store)."""

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
class FuturesBinding:
    """The contract, margin and funding one futures run binds."""

    contract: InstrumentContract
    margin: MarginAssumption
    funding: FundingAssumption | None

    def fingerprint_payload(self) -> dict[str, Any]:
        """The binding as JSON for the submission's execution fingerprint."""
        payload: dict[str, Any] = {
            "instrument_contract": self.contract.model_dump(mode="json"),
            "margin": self.margin.model_dump(mode="json"),
        }
        if self.funding is not None:
            payload["funding"] = self.funding.model_dump(mode="json")
        return payload


def settled_funding_rates(
    records: tuple[FundingRateRecord, ...], hours: tuple[datetime, ...]
) -> tuple[dict[datetime, Decimal], tuple[datetime, ...]]:
    """The settled rate of each funding hour, and the hours with no settled rate."""
    settled = {record.funding_time: record.rate for record in records if record.settled}
    rates = {hour: settled[hour] for hour in hours if hour in settled}
    missing = tuple(hour for hour in hours if hour not in settled)
    return rates, missing


async def load_funding_rates(
    source: FuturesRunSource, product_id: str, starts_at: datetime, ends_at: datetime
) -> tuple[dict[datetime, Decimal], tuple[datetime, ...]]:
    """Read the settled funding of every hour in (``starts_at``, ``ends_at``]."""
    hours = funding_hours(starts_at, ends_at)
    if not hours:
        return {}, ()
    records = await source.funding_rates(
        product_id=product_id, starts_at=hours[0], ends_at=hours[-1] + _HOUR
    )
    return settled_funding_rates(records, hours)


async def bind_futures_run(
    definition: StrategyDefinition,
    assumptions: FuturesBacktestAssumptions | None,
    source: FuturesRunSource | None,
    *,
    starts_at: datetime,
    ends_at: datetime,
) -> FuturesBinding | None:
    """Bind a futures strategy's contract, margin and funding; None for spot strategies."""
    if not definition.instrument.is_future:
        if assumptions is not None:
            raise BacktestSubmissionRejectedError(
                "futures assumptions apply to futures strategies only."
            )
        return None
    if assumptions is None:
        raise BacktestSubmissionRejectedError(
            "FUTURES_ASSUMPTIONS_REQUIRED: a futures backtest needs a futures block with "
            "at least fee_per_contract."
        )
    if source is None:
        raise BacktestSubmissionRejectedError(
            "FUTURES_OBSERVATIONS_UNAVAILABLE: this process has no futures observation store."
        )
    product_id = definition.instrument.product_id
    latest = await source.latest_instrument(product_id)
    if latest is None:
        raise BacktestSubmissionRejectedError(
            f"FUTURES_CONTRACT_UNOBSERVED: {product_id} has no recorded catalog observation."
        )
    observation, seen_at = latest
    contract = _contract(observation, definition)
    margin = _margin(observation, assumptions, seen_at)
    funding = None
    if contract.kind == "perpetual_future":
        funding = await _funding(source, assumptions, product_id, starts_at, ends_at)
    return FuturesBinding(contract=contract, margin=margin, funding=funding)


def _contract(
    observation: FuturesInstrumentObservation, definition: StrategyDefinition
) -> InstrumentContract:
    """The run's contract from one recorded observation, checked against the strategy."""
    if observation.underlying != definition.instrument.base_currency:
        raise BacktestSubmissionRejectedError(
            f"FUTURES_UNDERLYING_MISMATCH: {observation.product_id} settles on "
            f"{observation.underlying}, not {definition.instrument.base_currency}."
        )
    perpetual = observation.kind is InstrumentKind.PERPETUAL_FUTURE
    return InstrumentContract(
        product_id=observation.product_id,
        kind="perpetual_future" if perpetual else "dated_future",
        underlying=observation.underlying,
        contract_size=observation.contract_size,
        expires_at=None if perpetual else observation.venue_expiry_at,
        listed_expiry=observation.listed_expiry,
        catalog_fingerprint=observation.payload_fingerprint,
    )


def _margin(
    observation: FuturesInstrumentObservation,
    assumptions: FuturesBacktestAssumptions,
    seen_at: datetime,
) -> MarginAssumption:
    """Observed overnight rates, or the caller's explicit pair, with optional stress."""
    options: dict[str, str] = {}
    if assumptions.margin_stress_multiplier is not None:
        options["stress_multiplier"] = assumptions.margin_stress_multiplier
    if assumptions.maintenance_fraction_of_initial is not None:
        options["maintenance_fraction_of_initial"] = assumptions.maintenance_fraction_of_initial
    if assumptions.min_liquidation_buffer_fraction is not None:
        options["min_liquidation_buffer_fraction"] = assumptions.min_liquidation_buffer_fraction
    try:
        if assumptions.long_margin_rate is not None and assumptions.short_margin_rate is not None:
            return MarginAssumption(
                long_rate=assumptions.long_margin_rate,
                short_rate=assumptions.short_margin_rate,
                source="explicit",
                **options,
            )
        long_rate = observation.overnight_long_margin_rate
        short_rate = observation.overnight_short_margin_rate
        if long_rate is None or short_rate is None:
            raise BacktestSubmissionRejectedError(
                f"FUTURES_MARGIN_UNKNOWN: the catalog lists no overnight margin rates for "
                f"{observation.product_id}; declare long_margin_rate and short_margin_rate."
            )
        return MarginAssumption(
            long_rate=long_rate,
            short_rate=short_rate,
            source="latest_observation",
            observed_at=seen_at.astimezone(UTC),
            **options,
        )
    except ValueError as error:
        if isinstance(error, BacktestSubmissionRejectedError):
            raise
        raise BacktestSubmissionRejectedError(f"Invalid futures margin: {error}") from error


async def _funding(
    source: FuturesRunSource,
    assumptions: FuturesBacktestAssumptions,
    product_id: str,
    starts_at: datetime,
    ends_at: datetime,
) -> FundingAssumption:
    """A declared constant rate, or the settled series covering every window hour."""
    try:
        if assumptions.funding_constant_rate is not None:
            return FundingAssumption(constant_rate=assumptions.funding_constant_rate)
    except ValueError as error:
        raise BacktestSubmissionRejectedError(f"Invalid funding rate: {error}") from error
    rates, missing = await load_funding_rates(source, product_id, starts_at, ends_at)
    if missing:
        first = missing[0].isoformat().replace("+00:00", "Z")
        raise BacktestSubmissionRejectedError(
            f"FUNDING_HISTORY_MISSING: no settled funding rate for {product_id} at {first} "
            f"({len(missing)} of the window's hours are missing). Funding history starts when "
            "the poller started; choose a later window or declare funding_constant_rate."
        )
    return FundingAssumption(
        series_fingerprint=funding_series_fingerprint(product_id, rates),
        settled_hours=len(rates),
    )
