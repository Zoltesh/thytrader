"""Execution preferences and identity-bearing metadata annotations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from thytrader.strategies.schema.primitives import _FrozenModel


class ExecutionPreferences(_FrozenModel):
    """Declare venue-neutral execution preferences for later runtimes.

    Entries are always post-only maker limits in backtest, paper, and live.
    ``marketable_limit`` is retired: it parses only so persisted snapshots keep verifying
    byte-for-byte, and the strategy library rejects it on save (``authoring_issues``).
    """

    entry_preference: Literal["maker_only", "marketable_limit"]
    max_entry_wait_bars: int = Field(ge=1, le=50)
    on_unfilled_entry: Literal["cancel", "reprice"]


class StrategyMetadata(_FrozenModel):
    """Bounded human annotations that are included in immutable identity."""

    tags: tuple[str, ...] = Field(default=(), max_length=20)
    notes: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("tags", "notes")
    @classmethod
    def validate_annotations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique, non-empty bounded annotation text."""
        if len(value) != len(set(value)):
            raise ValueError("metadata values must be unique")
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("metadata values must contain 1 to 500 visible characters")
        return value
