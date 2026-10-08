"""Redacted portfolio failures and the sleeve start problems they carry.

Validation errors keep a stable machine ``code`` beside the human message; the HTTP layer
maps validation to 422, conflicts to 409, a missing live acknowledgement to 428, and the
not-found errors to 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


class PortfolioError(RuntimeError):
    """Base class for redacted portfolio failures."""


class PortfolioStorageUnavailableError(PortfolioError):
    """Durable portfolio storage could not complete the request."""


class PortfolioNotFoundError(PortfolioError):
    """No portfolio exists for the requested identity."""


class PortfolioSleeveNotFoundError(PortfolioError):
    """No sleeve with the requested identity exists in the portfolio."""


class PortfolioStrategyNotFoundError(PortfolioError):
    """A sleeve names a strategy that does not exist."""


class PortfolioRevisionConflictError(PortfolioError):
    """A mutation named a revision that is no longer current."""

    def __init__(self, current_revision: int) -> None:
        """Record the durable revision the caller must reload."""
        super().__init__("Portfolio was changed by another update; reload it and try again.")
        self.current_revision = current_revision


class PortfolioValidationError(PortfolioError):
    """A mutation would break a portfolio rule; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        """Keep a stable machine code beside the human message."""
        super().__init__(message)
        self.code = code


class PortfolioConflictError(PortfolioValidationError):
    """The portfolio's deployment or proposal state refuses this action (HTTP 409)."""


class PortfolioLiveAcknowledgementError(PortfolioError):
    """A live start, resume, or approval arrived without ``i_understand_live`` (HTTP 428)."""


@dataclass(frozen=True, slots=True)
class StartProblem:
    """One sleeve problem that blocked a portfolio start (nothing was started)."""

    code: str
    message: str
    sleeve_id: UUID | None = None
    strategy_id: UUID | None = None
    strategy_name: str | None = None


class PortfolioStartRejectedError(PortfolioValidationError):
    """Every problem that blocked starting a portfolio, so nothing started (HTTP 422)."""

    def __init__(self, problems: tuple[StartProblem, ...]) -> None:
        """Keep each sleeve problem for the response body."""
        count = len(problems)
        noun = "problem" if count == 1 else "problems"
        super().__init__(
            "portfolio_start_rejected",
            f"The portfolio was not started: {count} sleeve {noun}. Nothing was started.",
        )
        self.problems = problems


class PortfolioProposalNotFoundError(PortfolioError):
    """No proposal with the requested identity exists for this portfolio."""


class PortfolioSleeveExistsError(PortfolioValidationError):
    """The strategy is already a sleeve of this portfolio."""

    def __init__(self, sleeve_id: UUID) -> None:
        """Name the existing sleeve."""
        super().__init__(
            "portfolio_sleeve_exists",
            "This strategy is already a sleeve of the portfolio; change its weight instead.",
        )
        self.sleeve_id = sleeve_id
