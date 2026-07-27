"""Typed contracts for append-only portfolio valuation history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from thytrader.portfolio.models import Portfolio


class PortfolioHistoryUnavailableError(RuntimeError):
    """Signal that durable portfolio history is disabled or unavailable."""


@dataclass(frozen=True, slots=True)
class PortfolioHistoryEntry:
    """One exact portfolio valuation at a timezone-aware UTC instant."""

    as_of: datetime
    total_value: Decimal


@runtime_checkable
class PortfolioHistoryStore(Protocol):
    """Append and read immutable portfolio valuation observations."""

    async def record(self, portfolio: Portfolio) -> None:
        """Persist one successful complete portfolio snapshot."""
        ...

    async def list_recent(self, *, limit: int) -> tuple[PortfolioHistoryEntry, ...]:
        """Return newest-first valuation history with a validated maximum size."""
        ...


class DisabledPortfolioHistoryStore:
    """Preserve portfolio refreshes while durable history is unconfigured."""

    async def record(self, portfolio: Portfolio) -> None:
        """Deliberately skip recording when durable storage is disabled."""
        del portfolio

    async def list_recent(self, *, limit: int) -> tuple[PortfolioHistoryEntry, ...]:
        """Reject reads so disabled persistence never looks like empty history."""
        del limit
        raise PortfolioHistoryUnavailableError("Portfolio history is unavailable.")


class InMemoryPortfolioHistoryStore:
    """Append-only deterministic store used only by application behavior tests."""

    def __init__(self) -> None:
        """Initialize an empty in-memory timeline."""
        self._entries: list[PortfolioHistoryEntry] = []

    async def record(self, portfolio: Portfolio) -> None:
        """Append the exact total from a successful portfolio refresh."""
        self._entries.append(
            PortfolioHistoryEntry(as_of=portfolio.as_of, total_value=portfolio.total_value.amount)
        )

    async def list_recent(self, *, limit: int) -> tuple[PortfolioHistoryEntry, ...]:
        """Return entries newest first within a caller-validated bound."""
        return tuple(reversed(self._entries[-limit:]))
