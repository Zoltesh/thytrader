"""Content-addressed strategy snapshots and reproducible dataset-association contracts.

A snapshot is the canonical JSON of one strategy definition at the moment a backtest,
study, or deployment started. Its SHA-256 fingerprint is its identity, so identical
definitions deduplicate to one row. Snapshots are never edited; the mutable strategy
row (see :mod:`thytrader.strategies.library`) is what users change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.strategies.models import StrategyDefinition


class StrategySnapshotError(RuntimeError):
    """Report a redacted snapshot load, storage, or integrity failure."""


@dataclass(frozen=True, slots=True)
class StrategySnapshot:
    """A verified strategy definition addressed by its canonical content fingerprint."""

    strategy_fingerprint: str
    definition: StrategyDefinition


@runtime_checkable
class StrategySnapshotReader(Protocol):
    """Load verified snapshots only (runtime and deployment start need nothing more)."""

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Load and verify one exact snapshot by fingerprint."""
        ...


@runtime_checkable
class StrategySnapshotStore(Protocol):
    """Load verified snapshots and record derived (sweep) snapshots."""

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Load and verify one exact snapshot by fingerprint."""
        ...

    async def record_snapshot(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Idempotently store one valid definition whose strategy row already exists."""
        ...


class DisabledStrategySnapshotStore:
    """Fail closed when durable snapshot storage is not configured."""

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Refuse fingerprint loads without durable storage."""
        del strategy_fingerprint_value
        raise StrategySnapshotError("Strategy snapshot storage is unavailable.")

    async def record_snapshot(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Refuse snapshot storage without durable storage."""
        del definition
        raise StrategySnapshotError("Strategy snapshot storage is unavailable.")


@dataclass(frozen=True, slots=True)
class StrategyDatasetBinding:
    """An immutable association of exact snapshot and historical dataset identities."""

    strategy_fingerprint: str
    dataset_fingerprint: str
    bound_at: datetime
