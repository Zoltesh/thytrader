"""The account's Coinbase fee rates as the default for new paper books."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request  # noqa: TC002 - FastAPI resolves dependency annotations at runtime.

from thytrader.api.dependencies import get_portfolio_service
from thytrader.exchanges.fee_schedule import suggest_research_fee_rates
from thytrader.execution.paper_fees import paper_fees_unavailable

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.portfolio.service import PortfolioService

_logger = logging.getLogger(__name__)


def account_paper_fee_source(portfolio_service: PortfolioService) -> PaperFeeSource:
    """Read the rates ``GET /api/v1/fees`` suggests from the account, never a schedule guess."""

    async def read() -> tuple[Decimal, Decimal]:
        try:
            profile = await portfolio_service.get_fee_profile()
        except Exception as error:  # noqa: BLE001 - provider failures are redacted here.
            _logger.warning("Paper fee profile read failed: %s", type(error).__name__)
            raise paper_fees_unavailable("the Coinbase fee profile could not be read") from None
        suggestion = suggest_research_fee_rates(profile=profile, demo=portfolio_service.demo)
        maker = suggestion.suggested_maker_fee_rate
        taker = suggestion.suggested_taker_fee_rate
        if suggestion.source != "coinbase_account" or maker is None or taker is None:
            reason = suggestion.unavailable_reason or "no account-reported rates"
            raise paper_fees_unavailable(reason.replace("_", " "))
        return maker, taker

    return read


def get_paper_fee_source(request: Request) -> PaperFeeSource:
    """FastAPI dependency that reads the account only when a paper start omits its rates."""

    async def read() -> tuple[Decimal, Decimal]:
        try:
            portfolio_service = get_portfolio_service(request)
        except TypeError:
            raise paper_fees_unavailable("the portfolio service is not configured") from None
        return await account_paper_fee_source(portfolio_service)()

    return read
