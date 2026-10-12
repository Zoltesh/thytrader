"""Exact current-margin reserve disclosure under a declared peg, never currency addition."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.risk.models import RiskPolicyDefinition


@dataclass(frozen=True, slots=True)
class ReserveCoverage:
    """Separate denominations; missing or inexact evidence cannot certify headroom."""

    currency: str | None
    reserve_quote: str | None
    required_usd: Decimal | None
    headroom_quote: Decimal | None

    @property
    def covers(self) -> bool | None:
        """Whether the reserve covers current margin, not a proposed entry."""
        return None if self.headroom_quote is None else self.headroom_quote >= 0


def reserve_coverage(
    policy: RiskPolicyDefinition | None, initial: Decimal | None
) -> ReserveCoverage:
    """Do not round a threshold into available capacity, or link USDT to USD margin."""
    futures = None if policy is None else policy.futures
    currency = None if policy is None else policy.quote_currency
    reserve = None if futures is None else futures.live_spot_collateral_reserve_quote
    required = None
    headroom = None
    if futures is not None and initial is not None and initial.is_finite() and initial >= 0:
        try:
            with localcontext() as context:
                context.traps[Inexact] = True
                required = initial * Decimal(futures.peg_haircut)
                if not required.is_finite():
                    required = None
                if required is not None and reserve is not None and currency in {"USD", "USDC"}:
                    headroom = Decimal(reserve) - required
                    if not headroom.is_finite():
                        headroom = None
        except DecimalException:
            required = headroom = None
    return ReserveCoverage(currency, reserve, required, headroom)
