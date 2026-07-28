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

    async def list_range(
        self,
        *,
        start: datetime | None,
        max_entries: int,
    ) -> tuple[PortfolioHistoryEntry, ...]:
        """Return newest-first range samples bounded for interactive presentation."""
        ...


class DisabledPortfolioHistoryStore:
    """Preserve portfolio refreshes while durable history is unconfigured."""

    async def record(self, portfolio: Portfolio) -> None:
        """Deliberately skip recording when durable storage is disabled."""
        del portfolio

    async def list_range(
        self,
        *,
        start: datetime | None,
        max_entries: int,
    ) -> tuple[PortfolioHistoryEntry, ...]:
        """Reject reads so disabled persistence never looks like empty history."""
        del start, max_entries
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

    async def list_range(
        self,
        *,
        start: datetime | None,
        max_entries: int,
    ) -> tuple[PortfolioHistoryEntry, ...]:
        """Return bounded newest-first range samples for deterministic tests."""
        filtered_entries = [
            entry for entry in self._entries if start is None or entry.as_of >= start
        ]
        return _sample_entries(filtered_entries, max_entries)


def _sample_entries(
    entries: list[PortfolioHistoryEntry],
    max_entries: int,
) -> tuple[PortfolioHistoryEntry, ...]:
    """Keep the range endpoints plus evenly distributed latest bucket observations."""
    if max_entries < 1:
        message = "max_entries must be positive."
        raise ValueError(message)
    if len(entries) <= max_entries:
        return tuple(reversed(entries))
    if max_entries == 1:
        return (entries[-1],)

    bucket_count = max_entries - 1
    selected_indices = {0}
    for bucket in range(1, bucket_count + 1):
        selected_indices.add((bucket * len(entries) + bucket_count - 1) // bucket_count - 1)
    return tuple(entries[index] for index in sorted(selected_indices, reverse=True))
