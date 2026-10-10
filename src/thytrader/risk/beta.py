"""Pure BTC-beta estimation from settled daily candles (ADR 0125).

β is the ordinary least-squares slope of a product's daily log returns on its reference's
(``BTC-<quote>``) daily log returns, aligned by bar start. It is an analytic estimate, so
the arithmetic is binary floating point; the result crosses into ``Decimal`` once, rounded
up to 0.01 and clamped to [0, 3]. Missing, short, invalid or stale history is reported as
``BetaUnavailable`` and never replaced by a default: the gate denies on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from itertools import pairwise
import math
import statistics
from typing import TYPE_CHECKING

from thytrader.market_data.products import quote_currency

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from thytrader.market_data.models import Candle

BETA_REFERENCE_BASE = "BTC"
BETA_LOOKBACK = timedelta(days=90)
"""Returns whose closing bar starts within this span of the newest common bar are used."""
MIN_BETA_RETURNS = 60
BETA_MIN = Decimal("0")
BETA_MAX = Decimal("3")
BETA_STEP = Decimal("0.01")
_FLOAT_NOISE_STEP = Decimal("0.000000001")
"""Float slopes are first rounded half-even here so ceiling rounding ignores binary noise."""
BETA_STALE_AFTER = timedelta(hours=48)
"""An estimate whose last daily bar closed longer ago than this is unavailable."""
_DAY = timedelta(days=1)


class BetaUnavailableReason(StrEnum):
    """Why no usable β exists for one product."""

    INSUFFICIENT_HISTORY = "insufficient_history"
    INVALID_HISTORY = "invalid_history"
    STALE = "stale"
    FETCH_FAILED = "fetch_failed"
    NOT_LOADED = "not_loaded"


@dataclass(frozen=True, slots=True)
class BetaEstimate:
    """One β of a product against its reference.

    ``last_close`` is when the newest daily bar used closed (its start plus one day), or
    ``None`` for the reference itself, whose β is 1 by definition and never goes stale.
    """

    product_id: str
    reference_id: str
    beta: Decimal
    returns: int
    last_close: datetime | None


@dataclass(frozen=True, slots=True)
class BetaUnavailable:
    """No usable β for one product; new risk that depends on it is denied."""

    product_id: str
    reference_id: str
    reason: BetaUnavailableReason
    returns: int = 0
    last_close: datetime | None = None

    def describe(self) -> str:
        """Return the short cause used in a denial detail."""
        match self.reason:
            case BetaUnavailableReason.INSUFFICIENT_HISTORY:
                return f"insufficient_history n={self.returns}<{MIN_BETA_RETURNS}"
            case BetaUnavailableReason.STALE:
                stamp = "unknown" if self.last_close is None else self.last_close.isoformat()
                return f"stale last_close={stamp}"
            case (
                BetaUnavailableReason.INVALID_HISTORY
                | BetaUnavailableReason.FETCH_FAILED
                | BetaUnavailableReason.NOT_LOADED
            ):
                return self.reason.value


type BetaResult = BetaEstimate | BetaUnavailable


@dataclass(frozen=True, slots=True)
class BetaEvidence:
    """β results loaded for one entry decision, keyed by product id.

    A product the loader was not asked about is ``NOT_LOADED``, never a default β.
    """

    results: Mapping[str, BetaResult]

    def result_for(self, product_id: str) -> BetaResult:
        """Return the loaded result for ``product_id`` (the reference is always β 1)."""
        loaded = self.results.get(product_id)
        if loaded is not None:
            return loaded
        if product_id == beta_reference(product_id):
            return reference_beta(product_id)
        return _unavailable(product_id, BetaUnavailableReason.NOT_LOADED)


def beta_reference(product_id: str) -> str:
    """Return the BTC product in ``product_id``'s quote, e.g. BTC-USDC for SOL-USDC."""
    return f"{BETA_REFERENCE_BASE}-{quote_currency(product_id)}"


def reference_beta(reference_id: str) -> BetaEstimate:
    """Return the reference's own β of exactly 1, which needs no history."""
    return BetaEstimate(
        product_id=reference_id,
        reference_id=reference_id,
        beta=Decimal(1),
        returns=0,
        last_close=None,
    )


def estimate_beta(
    product_id: str,
    product_candles: Sequence[Candle],
    reference_candles: Sequence[Candle],
) -> BetaResult:
    """Estimate ``product_id``'s β on ``BTC-<quote>`` from settled daily candles.

    Both series must be daily bars. Bars are aligned by start; a return is formed only
    between bars exactly one day apart that both series have, so a gap in either series
    drops the return that spans it. Bars with a non-positive close are skipped. At least
    ``MIN_BETA_RETURNS`` returns within ``BETA_LOOKBACK`` of the newest common bar are
    required. Conflicting duplicate bars or a constant reference make the history invalid.
    """
    reference_id = beta_reference(product_id)
    if product_id == reference_id:
        return reference_beta(reference_id)
    product_closes = _closes(product_candles)
    reference_closes = _closes(reference_candles)
    if product_closes is None or reference_closes is None:
        return _unavailable(product_id, BetaUnavailableReason.INVALID_HISTORY)
    pairs = _aligned_returns(product_closes, reference_closes)
    if len(pairs) < MIN_BETA_RETURNS:
        return _unavailable(
            product_id, BetaUnavailableReason.INSUFFICIENT_HISTORY, returns=len(pairs)
        )
    product_returns = [item[1] for item in pairs]
    reference_returns = [item[2] for item in pairs]
    variance = statistics.variance(reference_returns)
    if not math.isfinite(variance) or variance <= 0:
        return _unavailable(product_id, BetaUnavailableReason.INVALID_HISTORY, returns=len(pairs))
    slope = statistics.covariance(product_returns, reference_returns) / variance
    if not math.isfinite(slope):
        return _unavailable(product_id, BetaUnavailableReason.INVALID_HISTORY, returns=len(pairs))
    return BetaEstimate(
        product_id=product_id,
        reference_id=reference_id,
        beta=_bounded_beta(slope),
        returns=len(pairs),
        last_close=pairs[-1][0] + _DAY,
    )


def fresh_beta(result: BetaResult, *, as_of: datetime) -> BetaResult:
    """Return ``result``, or ``STALE`` when its last bar closed over 48 hours before ``as_of``."""
    if isinstance(result, BetaUnavailable) or result.last_close is None:
        return result
    if as_of - result.last_close <= BETA_STALE_AFTER:
        return result
    return BetaUnavailable(
        product_id=result.product_id,
        reference_id=result.reference_id,
        reason=BetaUnavailableReason.STALE,
        returns=result.returns,
        last_close=result.last_close,
    )


def _closes(candles: Sequence[Candle]) -> dict[datetime, Decimal] | None:
    """Map bar start to a positive close; ``None`` when one start has two different closes."""
    closes: dict[datetime, Decimal] = {}
    for candle in candles:
        if candle.close <= 0:
            continue
        prior = closes.get(candle.starts_at)
        if prior is not None and prior != candle.close:
            return None
        closes[candle.starts_at] = candle.close
    return closes


def _aligned_returns(
    product: dict[datetime, Decimal], reference: dict[datetime, Decimal]
) -> list[tuple[datetime, float, float]]:
    """Return ``(end bar start, product log return, reference log return)`` per adjacent day."""
    common = sorted(product.keys() & reference.keys())
    if not common:
        return []
    horizon = common[-1] - BETA_LOOKBACK
    pairs: list[tuple[datetime, float, float]] = []
    for previous, current in pairwise(common):
        if current - previous != _DAY or current <= horizon:
            continue
        pairs.append(
            (
                current,
                math.log(float(product[current] / product[previous])),
                math.log(float(reference[current] / reference[previous])),
            )
        )
    return pairs


def _bounded_beta(slope: float) -> Decimal:
    """Round a float slope up to 0.01 and clamp it to [0, 3].

    The slope is first rounded to 1e-9, so an exact 1.5 computed as 1.5000000000000002
    stays 1.50 instead of ceiling to 1.51. Clamping first keeps the conversion in range.
    """
    clamped = min(max(slope, float(BETA_MIN)), float(BETA_MAX))
    denoised = Decimal(repr(clamped)).quantize(_FLOAT_NOISE_STEP, rounding=ROUND_HALF_EVEN)
    return denoised.quantize(BETA_STEP, rounding=ROUND_CEILING)


def _unavailable(
    product_id: str, reason: BetaUnavailableReason, *, returns: int = 0
) -> BetaUnavailable:
    """Build an unavailable result against ``product_id``'s reference."""
    return BetaUnavailable(
        product_id=product_id,
        reference_id=beta_reference(product_id),
        reason=reason,
        returns=returns,
    )
