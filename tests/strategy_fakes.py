"""Shared in-memory strategy store adapters for API and runtime tests (ADR 0082)."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import TYPE_CHECKING

from thytrader.strategies.memory_store import InMemoryStrategyStore

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.strategies.snapshots import StrategySnapshot


class _SeedingSnapshots(MutableMapping[str, "StrategySnapshot"]):
    """Mapping view where assigning a snapshot seeds its strategy row."""

    def __init__(self, store: SeededStrategyStore) -> None:
        """Bind the view to one store."""
        self._store = store
        self._seen: dict[str, StrategySnapshot] = {}

    def __getitem__(self, key: str) -> StrategySnapshot:
        """Return one seeded snapshot."""
        return self._seen[key]

    def __setitem__(self, key: str, value: StrategySnapshot) -> None:
        """Seed the snapshot's definition as a strategy and snapshot."""
        seeded = self._store.seed_definition(value.definition)
        if seeded.strategy_fingerprint != key:
            raise AssertionError("seeded snapshot fingerprint does not match its key")
        self._seen[key] = seeded

    def __delitem__(self, key: str) -> None:
        """Forget one seeded key (the strategy row stays)."""
        del self._seen[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate seeded fingerprints."""
        return iter(self._seen)

    def __len__(self) -> int:
        """Count seeded fingerprints."""
        return len(self._seen)


class SeededStrategyStore(InMemoryStrategyStore):
    """In-memory strategy store with a ``published[fp] = snapshot`` seeding shortcut."""

    def __init__(self) -> None:
        """Start empty."""
        super().__init__()
        self.published = _SeedingSnapshots(self)
