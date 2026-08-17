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

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Atomically publish one current draft and consume its mutable row."""
        ...


class DisabledStrategyPublicationStore:
    """Fail closed when immutable strategy storage is not configured."""

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Refuse strategy publication without durable storage."""
        del definition
        raise StrategyPublicationError("Strategy publication storage is unavailable.")

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Refuse atomic draft publication without durable storage."""
        del definition, expected_revision
        raise StrategyPublicationError("Strategy publication storage is unavailable.")

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """Refuse publication discovery without durable storage."""
        del include_archived
        raise StrategyPublicationError("Strategy publication catalog is unavailable.")

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Refuse archival without durable storage."""
        del strategy_fingerprint_value
        raise StrategyPublicationError("Strategy publication catalog is unavailable.")


@dataclass(frozen=True, slots=True)
class PublishedStrategy:
    """A verified immutable strategy definition addressed by content fingerprint."""

    strategy_fingerprint: str
    definition: StrategyDefinition


@dataclass(frozen=True, slots=True)
class StrategyCatalogEntry:
    """Immutable strategy evidence plus optional permanent archive marker timestamp."""

    strategy_fingerprint: str
    definition: StrategyDefinition
    archived_at: datetime | None


@runtime_checkable
class StrategyPublicationCatalog(Protocol):
    """Discover and archive immutable strategies without changing canonical evidence."""

    async def list_published(self, *, include_archived: bool) -> tuple[StrategyCatalogEntry, ...]:
        """Return active immutable strategies or complete history when explicitly requested."""
        ...

    async def archive(self, strategy_fingerprint_value: str) -> StrategyCatalogEntry:
        """Persist one permanent archive marker for an existing immutable strategy."""
        ...


@dataclass(frozen=True, slots=True)
class StrategyDatasetBinding:
    """An immutable association of exact strategy and historical dataset identities."""

    strategy_fingerprint: str
    dataset_fingerprint: str
    bound_at: datetime
