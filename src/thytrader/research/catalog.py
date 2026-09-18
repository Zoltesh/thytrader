"""Persisted research-study catalog identities.

The catalog stores composed study documents after submit. It does not grant
paper or live authority and it does not interpolate candles or equity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from thytrader.market_data.models import DatasetTimeframe  # noqa: TC001 - Pydantic field type.
from thytrader.research.parameter_sweep import SelectionMetric  # noqa: TC001 - Pydantic field type.

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_STUDY_KINDS = (
    "oos_holdout",
    "walk_forward",
    "cross_market",
    "parameter_sweep",
    "walk_forward_optimization",
)


class StudyCatalogUnavailableError(RuntimeError):
    """Signal that durable study storage is disabled or unreachable."""


class StudyCatalogNotFoundError(LookupError):
    """Signal that one requested study identity does not exist."""


class StudyCatalogIntegrityError(RuntimeError):
    """Signal that a stored study failed canonical verification."""


class StudyCatalogSummary(BaseModel):
    """One operator-visible catalog row without child equity curves."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    study_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    plan_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    kind: str = Field(pattern=r"^[a-z_]+$")
    engine_contract_version: str
    published_at: datetime
    product_id: str
    timeframe: DatasetTimeframe
    window_count: int = Field(ge=1)
    selected_strategy_fingerprint: str | None = Field(
        default=None,
        pattern=_FINGERPRINT_PATTERN,
        exclude_if=lambda value: value is None,
    )
    mean_oos_return_fraction: str | None = None
    stitched_oos_available: bool | None = None
    selection_metric: SelectionMetric | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_serializer("published_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize catalog timestamps with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@runtime_checkable
class ResearchStudyCatalog(Protocol):
    """Persist and list composed research studies without trading authority."""

    async def persist(
        self,
        summary: StudyCatalogSummary,
        canonical_study: str,
    ) -> StudyCatalogSummary:
        """Idempotently store one assembled study document."""
        ...

    async def load(self, study_fingerprint: str) -> str:
        """Return the canonical study JSON for one fingerprint."""
        ...

    async def list_summaries(
        self,
        *,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[StudyCatalogSummary, ...]:
        """Return newest-first catalog rows without child ledgers."""
        ...

    async def find_by_plan_fingerprint(self, plan_fingerprint: str) -> str | None:
        """Return canonical study JSON when an equivalent plan already exists."""
        ...


class DisabledResearchStudyCatalog:
    """Fail-closed catalog used when PostgreSQL is unconfigured."""

    async def persist(
        self,
        summary: StudyCatalogSummary,
        canonical_study: str,
    ) -> StudyCatalogSummary:
        """Reject writes so a missing database never looks like success."""
        del summary, canonical_study
        raise StudyCatalogUnavailableError("Research study catalog is unavailable.")

    async def load(self, study_fingerprint: str) -> str:
        """Reject reads so a missing database never fabricates a study."""
        del study_fingerprint
        raise StudyCatalogUnavailableError("Research study catalog is unavailable.")

    async def list_summaries(
        self,
        *,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[StudyCatalogSummary, ...]:
        """Reject listing so disabled persistence never looks empty."""
        del kind, limit, offset
        raise StudyCatalogUnavailableError("Research study catalog is unavailable.")

    async def find_by_plan_fingerprint(self, plan_fingerprint: str) -> str | None:
        """Reject dedupe reads when durable storage is disabled."""
        del plan_fingerprint
        raise StudyCatalogUnavailableError("Research study catalog is unavailable.")


class InMemoryResearchStudyCatalog:
    """Process-local catalog for tests and API processes without PostgreSQL."""

    def __init__(self) -> None:
        """Start with no stored studies."""
        self._rows: dict[str, tuple[StudyCatalogSummary, str]] = {}

    async def persist(
        self,
        summary: StudyCatalogSummary,
        canonical_study: str,
    ) -> StudyCatalogSummary:
        """Store one study, reusing the existing row when fingerprints match."""
        existing = self._rows.get(summary.study_fingerprint)
        if existing is None:
            self._rows[summary.study_fingerprint] = (summary, canonical_study)
            return summary
        stored_summary, stored_canonical = existing
        if stored_canonical != canonical_study:
            raise StudyCatalogIntegrityError(
                "Published research study content failed integrity verification."
            )
        return stored_summary

    async def load(self, study_fingerprint: str) -> str:
        """Return one stored canonical study document."""
        existing = self._rows.get(study_fingerprint)
        if existing is None:
            raise StudyCatalogNotFoundError("Published research study was not found.")
        return existing[1]

    async def list_summaries(
        self,
        *,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[StudyCatalogSummary, ...]:
        """Return newest-first summaries, optionally filtered by study kind."""
        if kind is not None and kind not in _STUDY_KINDS:
            raise StudyCatalogIntegrityError("Unknown research study kind.")
        if limit < 1 or limit > 100:
            raise StudyCatalogIntegrityError("Study catalog limit must be between 1 and 100.")
        if offset < 0:
            raise StudyCatalogIntegrityError("Study catalog offset must not be negative.")
        rows = [
            summary
            for summary, _canonical in self._rows.values()
            if kind is None or summary.kind == kind
        ]
        rows.sort(key=lambda item: item.study_fingerprint)
        rows.sort(key=lambda item: item.published_at, reverse=True)
        return tuple(rows[offset : offset + limit])

    async def find_by_plan_fingerprint(self, plan_fingerprint: str) -> str | None:
        """Return one stored canonical study when the effective plan already exists."""
        for summary, canonical in self._rows.values():
            if summary.plan_fingerprint == plan_fingerprint:
                return canonical
        return None
