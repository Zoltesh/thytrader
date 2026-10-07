"""Execution-worker ports: venue read protocols and the shared worker logger.

``_logger`` keeps the historical ``thytrader.execution_worker.service`` name so log
records emitted by every worker-cycle module keep their original ``logger`` field.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance


_logger = logging.getLogger("thytrader.execution_worker.service")


class QuoteBalanceReader(Protocol):
    """Read quote cash for live sizing."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return non-empty exchange balances."""
        ...


class LiveFeeProfileReader(Protocol):
    """Fetch the venue maker/taker tier used to size live entries."""

    async def get_fee_profile(self) -> FeeProfile:
        """Return the latest Coinbase fee profile."""
        ...
