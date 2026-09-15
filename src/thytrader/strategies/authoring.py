"""Safe browser-authoring defaults for the implemented conservative strategy profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import secrets
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from thytrader.strategies.models import Instrument, StrategyDefinition, StrategyStatus
from thytrader.strategies.templates import build_template_draft, parse_template_id


@dataclass(frozen=True, slots=True)
class StrategyDraft:
    """One validated editable definition plus its optimistic-concurrency revision."""

    definition: StrategyDefinition
    revision: int


@runtime_checkable
class StrategyDraftStore(Protocol):
    """Persist editable drafts without granting publication or trading authority."""

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Persist and return one validated draft definition."""
        ...

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Return every saved editable draft in stable creation order."""
        ...

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Replace a draft only when its durable revision still matches."""
        ...

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Consume a draft after it has been published as immutable evidence."""
        ...


class DisabledStrategyDraftStore:
    """Fail closed when durable strategy-draft storage is unavailable."""

    async def create_draft(self, definition: StrategyDefinition) -> StrategyDraft:
        """Refuse an unsafely ephemeral strategy draft."""
        del definition
        raise RuntimeError("Strategy draft storage is unavailable.")

    async def list_drafts(self) -> tuple[StrategyDraft, ...]:
        """Refuse draft discovery without durable storage."""
        raise RuntimeError("Strategy draft storage is unavailable.")

    async def save_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> StrategyDraft:
        """Refuse mutation without durable strategy-draft storage."""
        del definition, expected_revision
        raise RuntimeError("Strategy draft storage is unavailable.")

    async def delete_draft(self, strategy_id: UUID, version: int) -> None:
        """Refuse mutable-state removal without durable storage."""
        del strategy_id, version
        raise RuntimeError("Strategy draft storage is unavailable.")


def create_cloned_draft(
    source: StrategyDefinition, *, now: datetime | None = None
) -> StrategyDefinition:
    """Derive a fresh draft identity from immutable evidence without changing semantics."""
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = created_at.replace(microsecond=(created_at.microsecond // 1_000) * 1_000)
    payload: dict[str, Any] = source.model_dump(mode="python")
    payload.update(
        {
            "strategy_id": _uuid7(created_at),
            "version": 1,
            "name": f"{source.name} (clone)",
            "status": StrategyStatus.DRAFT,
            "created_at": created_at,
        }
    )
    return StrategyDefinition.model_validate(payload)


def create_revised_draft(
    source: StrategyDefinition, *, next_version: int, now: datetime | None = None
) -> StrategyDefinition:
    """Derive the next draft version of one strategy identity from published evidence."""
    if next_version <= source.version:
        raise ValueError("A revised draft requires a version greater than its source version.")
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = created_at.replace(microsecond=(created_at.microsecond // 1_000) * 1_000)
    payload: dict[str, Any] = source.model_dump(mode="python")
    payload.update(
        {
            "version": next_version,
            "status": StrategyStatus.DRAFT,
            "created_at": created_at,
        }
    )
    return StrategyDefinition.model_validate(payload)


def create_reference_draft(
    *,
    now: datetime | None = None,
    product_id: str = "BTC-USD",
    timeframe: str = "1h",
    template: str = "ema-trend",
) -> StrategyDefinition:
    """Construct one server-identified draft from a fail-closed research template."""
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    created_at = created_at.replace(microsecond=(created_at.microsecond // 1_000) * 1_000)
    if timeframe == "5m":
        clock: Literal["1h", "5m"] = "5m"
    elif timeframe == "1h":
        clock = "1h"
    else:
        message = "Reference drafts support only 1h and 5m timeframes."
        raise ValueError(message)
    template_id = parse_template_id(template)
    instrument = _instrument_for_product(product_id)
    return build_template_draft(
        template_id=template_id,
        strategy_id=_uuid7(created_at),
        created_at=created_at,
        instrument=instrument,
        timeframe=clock,
    )


def _instrument_for_product(product_id: str) -> Instrument:
    """Build a USD spot instrument from a product id such as ETH-USD."""
    normalized = product_id.strip().upper()
    if "-" not in normalized:
        message = "product_id must be a USD spot identifier such as ETH-USD."
        raise ValueError(message)
    base, quote = normalized.split("-", 1)
    if quote != "USD":
        message = "product_id must be a USD spot identifier such as ETH-USD."
        raise ValueError(message)
    return Instrument(product_id=normalized, base_currency=base, quote_currency="USD")


def _uuid7(created_at: datetime) -> UUID:
    """Create a UUIDv7 whose timestamp equals the supplied UTC millisecond."""
    milliseconds = int(created_at.timestamp() * 1_000)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)
