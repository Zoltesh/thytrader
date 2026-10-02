"""The portfolio limits the execution worker binds around one deployment (ADR 0091).

The worker loads every deployed portfolio's limits once per cycle and binds the book of
the deployment it is processing, so the closed-bar loop's entry gate can apply them
without threading another argument through every loop helper. Lookups fail closed: a
deployment tagged with a portfolio whose book is not bound (storage outage, a bug, or a
deleted portfolio) gets an unavailable book, which refuses every new entry.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from thytrader.risk.gate import PortfolioRiskBook

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.execution.models import Deployment

_SCOPE: ContextVar[PortfolioRiskBook | None] = ContextVar("portfolio_risk_scope", default=None)


@contextmanager
def portfolio_risk_scope(book: PortfolioRiskBook | None) -> Iterator[None]:
    """Bind one portfolio's limits (or none) while a deployment is processed."""
    token: Token[PortfolioRiskBook | None] = _SCOPE.set(book)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def portfolio_risk_for(deployment: Deployment) -> PortfolioRiskBook | None:
    """The bound book for a portfolio sleeve, fail-closed; None for a standalone book."""
    if deployment.portfolio_id is None:
        return None
    book = _SCOPE.get()
    if book is None or book.portfolio_id != deployment.portfolio_id:
        return PortfolioRiskBook.unavailable(deployment.portfolio_id)
    return book
