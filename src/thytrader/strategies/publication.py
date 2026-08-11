"""Immutable strategy publication and reproducible dataset-association contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.strategies.models import StrategyDefinition


class StrategyPublicationError(RuntimeError):
    """Report a redacted strategy publication or integrity failure."""


@runtime_checkable
class StrategyPublicationStore(Protocol):
    """Persist one immutable published strategy through the application boundary."""

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Publish and return one verified immutable strategy definition."""
        ...


class DisabledStrategyPublicationStore:
    """Fail closed when immutable strategy storage is not configured."""

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Refuse strategy publication without durable storage."""
        del definition
        raise StrategyPublicationError("Strategy publication storage is unavailable.")


@dataclass(frozen=True, slots=True)
class PublishedStrategy:
    """A verified immutable strategy definition addressed by content fingerprint."""

    strategy_fingerprint: str
    definition: StrategyDefinition


@dataclass(frozen=True, slots=True)
class StrategyDatasetBinding:
    """An immutable association of exact strategy and historical dataset identities."""

    strategy_fingerprint: str
    dataset_fingerprint: str
    bound_at: datetime
