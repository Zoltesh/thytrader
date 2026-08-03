"""Pure deterministic bar-level broker pricing models without exchange authority."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class FillQuote:
    """One executable modeled fill, including the disclosed cost from its reference price."""

    reference_price: Decimal
    price: Decimal
    executable_side: Literal["ask", "bid", "mark"]
    spread_cost: Decimal


class FillModel(Protocol):
    """Translate raw candle prices into deterministic executable prices and trigger marks."""

    def buy(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Return the adverse executable long-entry quote at one raw reference price."""
        ...

    def sell(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Return the adverse executable long-exit quote at one raw reference price."""
        ...

    def sell_trigger_price(self, raw_price: Decimal) -> Decimal:
        """Return the pre-slippage bid-side price used for V2 exit-trigger evaluation."""
        ...

    def reference_for_sell_trigger(self, executable_price: Decimal) -> Decimal:
        """Invert one sell trigger into its raw OHLC reference price."""
        ...

    def mark_price(self, raw_price: Decimal) -> Decimal:
        """Return the liquidation-value account mark for an open long position."""
        ...


@dataclass(frozen=True, slots=True)
class MarkFillModel:
    """V1 mark-price broker that reproduces the original fixed-slippage arithmetic exactly."""

    def buy(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Apply adverse fixed basis-point slippage to a mark-price long entry."""
        price = reference_price * (Decimal("1") + slippage_bps / Decimal("10000"))
        return FillQuote(reference_price, price, "mark", Decimal("0"))

    def sell(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Apply adverse fixed basis-point slippage to a mark-price long exit."""
        price = reference_price * (Decimal("1") - slippage_bps / Decimal("10000"))
        return FillQuote(reference_price, price, "mark", Decimal("0"))

    def sell_trigger_price(self, raw_price: Decimal) -> Decimal:
        """Preserve the V1 raw OHLC trigger comparison exactly."""
        return raw_price

    def reference_for_sell_trigger(self, executable_price: Decimal) -> Decimal:
        """Preserve the V1 identity mapping from trigger to raw reference price."""
        return executable_price

    def mark_price(self, raw_price: Decimal) -> Decimal:
        """Preserve the V1 midpoint-style raw close/open account mark exactly."""
        return raw_price


@dataclass(frozen=True, slots=True)
class ConstantSpreadFillModel:
    """V2 full-fill broker with one disclosed constant bid-ask spread in basis points."""

    spread_bps: Decimal

    def buy(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Buy at ask, then apply adverse slippage from that executable side."""
        ask = reference_price * (Decimal("1") + self._half_spread_fraction)
        price = ask * (Decimal("1") + slippage_bps / Decimal("10000"))
        return FillQuote(reference_price, price, "ask", ask - reference_price)

    def sell(self, reference_price: Decimal, slippage_bps: Decimal) -> FillQuote:
        """Sell at bid, then apply adverse slippage from that executable side."""
        bid = self.sell_trigger_price(reference_price)
        price = bid * (Decimal("1") - slippage_bps / Decimal("10000"))
        return FillQuote(reference_price, price, "bid", reference_price - bid)

    def sell_trigger_price(self, raw_price: Decimal) -> Decimal:
        """Evaluate long exits against the executable bid side before slippage."""
        return raw_price * (Decimal("1") - self._half_spread_fraction)

    def reference_for_sell_trigger(self, executable_price: Decimal) -> Decimal:
        """Invert bid-side trigger thresholds to their raw OHLC reference prices."""
        return executable_price / (Decimal("1") - self._half_spread_fraction)

    def mark_price(self, raw_price: Decimal) -> Decimal:
        """Mark an open long at bid-close liquidation value, before any exit slippage."""
        return self.sell_trigger_price(raw_price)

    @property
    def _half_spread_fraction(self) -> Decimal:
        """Return one side of the total declared basis-point spread as a price fraction."""
        return self.spread_bps / Decimal("20000")
