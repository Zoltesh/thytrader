"""Immutable strategy publication and reproducible dataset-association contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.strategies.models import StrategyDefinition


class StrategyPublicationError(RuntimeError):
    """Report a redacted strategy publication or integrity failure."""


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
