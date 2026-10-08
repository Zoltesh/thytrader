"""Preconditions and sleeve targeting for portfolio start, pause, resume, and stop.

A live start, resume, or approval needs the operator's live acknowledgement; a latched
portfolio breaker refuses starting and resuming; an action needs at least one running or
paused book. Targets are one sleeve's book or every sleeve's (plus detached books).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.portfolios.errors import (
    PortfolioConflictError,
    PortfolioLiveAcknowledgementError,
    PortfolioSleeveNotFoundError,
)
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import Deployment, DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.portfolios.deployment import PortfolioBooks, SleeveBook
    from thytrader.portfolios.models import PortfolioRuntimeState, SleeveView


NOT_DEPLOYED_MESSAGE = "No sleeve of this portfolio is running or paused."


def _require_live_acknowledgement(mode: DeploymentMode, *, acknowledged: bool) -> None:
    """Refuse a live start, resume, or approval without ``i_understand_live``."""
    if mode is DeploymentMode.LIVE and not acknowledged:
        raise PortfolioLiveAcknowledgementError(
            "Live trading spends real money: send i_understand_live=true only after the "
            "operator explicitly acknowledged live trading."
        )


def require_live_acknowledgement(mode: DeploymentMode, *, acknowledged: bool) -> None:
    """Public form of the live acknowledgement check (proposal approvals use it)."""
    _require_live_acknowledgement(mode, acknowledged=acknowledged)


def _require_breaker_clear(runtime: PortfolioRuntimeState) -> None:
    """Refuse starting or resuming while a portfolio breaker is latched."""
    if runtime.breaker_latched:
        raise PortfolioConflictError(
            "portfolio_breaker_latched",
            f"The portfolio's {_breaker_label(runtime)} breaker is latched; reset it "
            "before starting or resuming sleeves.",
        )


def _breaker_label(runtime: PortfolioRuntimeState) -> str:
    """Human name of the latched breaker."""
    if runtime.breaker_reason == "PORTFOLIO_DAILY_LOSS_STOP":
        return "daily loss"
    return "drawdown"


def _targets(
    books: PortfolioBooks, sleeve_id: UUID | None, *, include_detached: bool
) -> tuple[tuple[str, SleeveView | None, Deployment | None], ...]:
    """The books an action applies to: one sleeve's, or every sleeve's (plus detached)."""
    if sleeve_id is not None:
        book = _sleeve_book(books, sleeve_id)
        return ((book.view.strategy.name, book.view, book.deployment),)
    targets: list[tuple[str, SleeveView | None, Deployment | None]] = [
        (book.view.strategy.name, book.view, book.deployment) for book in books.sleeves
    ]
    if include_detached:
        targets.extend(
            (item.strategy_name or "removed sleeve", None, item) for item in books.detached
        )
    return tuple(targets)


def _sleeve_book(books: PortfolioBooks, sleeve_id: UUID) -> SleeveBook:
    """One sleeve's book or the sleeve-not-found error."""
    for book in books.sleeves:
        if book.view.sleeve.sleeve_id == sleeve_id:
            return book
    raise PortfolioSleeveNotFoundError("Sleeve was not found in this portfolio.")


def _require_deployed(
    targets: Sequence[tuple[str, SleeveView | None, Deployment | None]],
    *,
    sleeve_id: UUID | None,
) -> None:
    """Refuse an action on a portfolio (or sleeve) with no running or paused book."""
    if any(item is not None and occupies_running_slot(item) for _name, _view, item in targets):
        return
    if sleeve_id is not None:
        raise PortfolioConflictError(
            "portfolio_sleeve_not_deployed", "This sleeve has no running or paused bot."
        )
    raise PortfolioConflictError("portfolio_not_deployed", NOT_DEPLOYED_MESSAGE)
