"""API tests run without Coinbase, so paper starts read stand-in account fee rates.

Paper starts that omit fee rates read the account's Coinbase rates and are refused
when those are unavailable. The hermetic suite has no Coinbase, so every API test
gets the documented paper assumptions as the "account" rates; the existing ledger
expectations were written against them. Tests of the account path itself patch
``account_paper_fee_source`` again inside the test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from thytrader.api import paper_fees
from thytrader.execution.ledger import PAPER_MAKER_FEE_RATE, PAPER_TAKER_FEE_RATE

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.portfolio.service import PortfolioService


def _stand_in_source(_service: PortfolioService) -> PaperFeeSource:
    """Return the documented paper assumptions as the account's rates."""

    async def read() -> tuple[Decimal, Decimal]:
        return PAPER_MAKER_FEE_RATE, PAPER_TAKER_FEE_RATE

    return read


@pytest.fixture(autouse=True)
def stand_in_account_fees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give paper starts deterministic account fee rates without Coinbase."""
    monkeypatch.setattr(paper_fees, "account_paper_fee_source", _stand_in_source)
