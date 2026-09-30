"""Pure deterministic bar-level broker pricing for the unified backtest model.

Resting post-only maker limits fill at their posted price with no modeled slippage or
spread. Marketable (taker) legs — stop, time, and evaluation-end exits, plus the
buy-and-hold benchmark — cross half of the optional constant ``spread_bps`` stress and
then pay adverse fixed slippage. Stop triggers and open-position equity marks use the
same half-spread executable side. With ``spread_bps == 0`` every price is the raw OHLC
reference. None of this has exchange authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

_ONE = Decimal("1")
_BPS = Decimal("10000")
_HALF_SPREAD_DIVISOR = Decimal("20000")


@dataclass(frozen=True, slots=True)
class FillQuote:
    """One executable modeled fill, including the disclosed cost from its reference price."""

    reference_price: Decimal
    price: Decimal
    executable_side: Literal["ask", "bid"] | None
    spread_cost: Decimal


@dataclass(frozen=True, slots=True)
class FillModel:
    """The unified model's only broker: maker limits at the limit, taker legs across the spread.

    Attributes:
        spread_bps: Constant total bid-ask spread stress in basis points (zero disables it).
    """

    spread_bps: Decimal

    @property
    def spread_stressed(self) -> bool:
        """Whether taker fills must record spread evidence."""
        return self.spread_bps > 0

    def maker(self, limit_price: Decimal) -> FillQuote:
        """Fill one resting post-only limit exactly at its posted price."""
        return FillQuote(limit_price, limit_price, None, Decimal("0"))

    def taker_buy(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Buy at the stressed ask, then apply adverse fixed slippage from that side."""
        ask = self.ask(reference_price)
        price = ask * (_ONE + slippage_bps / _BPS)
        return self._quote(reference_price, price, "ask", ask - reference_price)

    def taker_sell(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Sell at the stressed bid, then apply adverse fixed slippage from that side."""
        bid = self.bid(reference_price)
        price = bid * (_ONE - slippage_bps / _BPS)
        return self._quote(reference_price, price, "bid", reference_price - bid)

    def bid(self, raw_price: Decimal) -> Decimal:
        """Return the executable bid for one raw OHLC reference price."""
        if not self.spread_stressed:
            return raw_price
        return raw_price * (_ONE - self._half_spread_fraction)

    def ask(self, raw_price: Decimal) -> Decimal:
        """Return the executable ask for one raw OHLC reference price."""
        if not self.spread_stressed:
            return raw_price
        return raw_price * (_ONE + self._half_spread_fraction)

    def raw_for_bid(self, bid_price: Decimal) -> Decimal:
        """Invert one bid-side threshold back to its raw OHLC reference price."""
        if not self.spread_stressed:
            return bid_price
        return bid_price / (_ONE - self._half_spread_fraction)

    def raw_for_ask(self, ask_price: Decimal) -> Decimal:
        """Invert one ask-side threshold back to its raw OHLC reference price."""
        if not self.spread_stressed:
            return ask_price
        return ask_price / (_ONE + self._half_spread_fraction)

    def mark(self, raw_price: Decimal, side: Literal["long", "short"]) -> Decimal:
        """Mark longs at liquidation bid and shorts at cover ask (raw close when unstressed)."""
        return self.ask(raw_price) if side == "short" else self.bid(raw_price)

    def _quote(
        self,
        reference_price: Decimal,
        price: Decimal,
        side: Literal["ask", "bid"],
        spread_cost: Decimal,
    ) -> FillQuote:
        """Record executable-side evidence only when spread stress is active."""
        if not self.spread_stressed:
            return FillQuote(reference_price, price, None, Decimal("0"))
        return FillQuote(reference_price, price, side, spread_cost)

    @property
    def _half_spread_fraction(self) -> Decimal:
        """Return one side of the total declared basis-point spread as a price fraction."""
        return self.spread_bps / _HALF_SPREAD_DIVISOR
