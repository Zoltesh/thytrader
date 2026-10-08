"""Inputs to per-cycle safety supervision (ADR 0115): thresholds and evidence readers.

`AlertThresholds` holds the operator-tunable limits. `SnapshotEvidence` marks whether a
deployment read carries a complete inventory, and the two reader protocols describe how
supervision obtains deployment snapshots and closed candles without owning a store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import Candle
    from thytrader.trading.models import DeploymentSnapshot


@dataclass(frozen=True, slots=True)
class AlertThresholds:
    """Explicit, operator-tunable supervision thresholds with sane defaults.

    ``consecutive_failure_cycles`` bounds observed errors since a verified
    successful decision; unknown/no-op cycles do not reset error evidence.
    Crossing it pauses new entries (exits and reconciliation continue).
    ``decision_missed_bars`` is how many closed decision bars behind the venue
    clock a running book may lag before the missed-deadline alert fires; the
    settling grace above still applies per missed bar. ``delivery_max_attempts``
    bounds notification retries for one open alert.
    """

    consecutive_failure_cycles: int = 3
    decision_missed_bars: int = 2
    delivery_max_attempts: int = 5

    def __post_init__(self) -> None:
        """Reject thresholds that would supervise nothing or flood delivery."""
        for name in (
            "consecutive_failure_cycles",
            "decision_missed_bars",
            "delivery_max_attempts",
        ):
            value = getattr(self, name)
            if value < 1:
                message = f"{name} must be at least 1"
                raise ValueError(message)


@dataclass(frozen=True, slots=True)
class SnapshotEvidence:
    """A validated deployment row plus collection evidence completeness.

    Full ExecutionStore snapshots have complete inventories. Adapters supplying
    partial/paginated collections must use this wrapper with complete=False.
    Row checks can still recover independently; missing order/position evidence
    never clears inventory-dependent checks.
    """

    snapshot: DeploymentSnapshot
    complete: bool = False


@runtime_checkable
class DeploymentSnapshotReader(Protocol):
    """Read one deployment with orders and intents for protection classification."""

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot | SnapshotEvidence:
        """Load a full inventory or explicitly flag a partial collection read."""
        ...


@runtime_checkable
class ClosedCandleReader(Protocol):
    """Best-effort closed-candle evidence; failures yield no candles."""

    async def __call__(
        self, product_id: str, timeframe: str, deploy_anchor: datetime
    ) -> tuple[Candle, ...]:
        """Return the latest closed candles, or an empty tuple when unavailable."""
        ...
