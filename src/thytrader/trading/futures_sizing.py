"""Whole-contract futures sizing and margin arithmetic shared by backtest and paper (ADR 0128).

Quantities are base-equivalent (contracts x ``contract_size``), so the spot cash-and-inventory
ledger stays correct; this module adds the contract quantum and the margin bounds that a
futures entry must satisfy after its fee. The backtest kernel and paper futures books size
with the same function, so research and paper never disagree on an entry's size.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from thytrader.trading.geometry import EntrySkipReason

FuturesSide = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class FuturesMarginTerms:
    """Contract size, margin rates, leverage and fees of one futures book.

    ``long_rate`` and ``short_rate`` are initial-margin rates (overnight, already multiplied
    by any stress). Maintenance is ``maintenance_fraction`` x initial. An entry must leave
    (equity - maintenance) / equity ≥ ``min_buffer_fraction``. ``fee_per_contract`` is USD.
    """

    contract_size: Decimal
    long_rate: Decimal
    short_rate: Decimal
    maintenance_fraction: Decimal
    min_buffer_fraction: Decimal
    max_leverage: Decimal
    fee_per_contract: Decimal

    def initial_rate(self, side: FuturesSide) -> Decimal:
        """The initial-margin rate for one side."""
        return self.long_rate if side == "long" else self.short_rate

    def whole_contracts(self, quantity: Decimal) -> Decimal:
        """Round a base quantity down to whole contracts (never up)."""
        contracts = (quantity / self.contract_size).to_integral_value(rounding=ROUND_FLOOR)
        return contracts * self.contract_size

    def maintenance(self, quantity: Decimal, price: Decimal, side: FuturesSide) -> Decimal:
        """Maintenance margin of ``quantity`` base units at ``price``."""
        return quantity * price * self.initial_rate(side) * self.maintenance_fraction

    def fee_per_unit(self) -> Decimal:
        """The per-contract fee expressed per base unit."""
        return self.fee_per_contract / self.contract_size


@dataclass(frozen=True, slots=True)
class ContractSizingLimits:
    """The strategy's own caps that a futures entry also respects."""

    max_quote_notional: Decimal
    min_quote_notional: Decimal
    max_exposure_fraction: Decimal


def size_contracts(
    terms: FuturesMarginTerms,
    limits: ContractSizingLimits,
    *,
    equity: Decimal,
    side: FuturesSide,
    limit_price: Decimal,
    requested_notional: Decimal,
    maker_fee_rate: Decimal,
) -> tuple[Decimal, bool] | EntrySkipReason:
    """Whole-contract base quantity within every futures bound, and whether a bound applied.

    With ``u`` the notional of one contract, ``f`` its entry fee, ``E`` equity and ``n``
    contracts, every bound holds after the fee is paid: ``n u <= L (E - n f)`` (leverage),
    ``n u r m <= (1 - b)(E - n f)`` (liquidation buffer), ``n u r <= X (E - n f)``
    (exposure as committed initial margin) and ``n u <= Q``. Below one contract is
    ``below_one_contract``; it is never rounded up.
    """
    if equity <= 0:
        return EntrySkipReason.INSUFFICIENT_CASH
    unit = limit_price * terms.contract_size
    if unit <= 0:
        return EntrySkipReason.ENTRY_PRICE_NOT_POSITIVE
    fee = unit * maker_fee_rate + terms.fee_per_contract
    rate = terms.initial_rate(side)
    leverage = terms.max_leverage
    room = Decimal(1) - terms.min_buffer_fraction
    exposure = limits.max_exposure_fraction
    bound = min(
        limits.max_quote_notional / unit,
        leverage * equity / (unit + leverage * fee),
        room * equity / (unit * rate * terms.maintenance_fraction + room * fee),
        exposure * equity / (unit * rate + exposure * fee),
    )
    requested = requested_notional / unit
    contracts = min(requested, bound).to_integral_value(rounding=ROUND_FLOOR)
    if contracts < 1:
        return EntrySkipReason.BELOW_ONE_CONTRACT
    if contracts * unit < limits.min_quote_notional:
        return EntrySkipReason.NOTIONAL_BELOW_MINIMUM
    return contracts * terms.contract_size, requested > bound
