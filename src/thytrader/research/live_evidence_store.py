"""Where each live start's evidence is kept (ADR 0143)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.research.live_evidence import LiveStartEvidence


class LiveEvidenceStoreUnavailableError(RuntimeError):
    """The live-start evidence store could not be read or written."""


class LiveEvidenceStore(Protocol):
    """Keep one evidence record per live deployment; the first one recorded stays."""

    async def record(self, deployment_id: UUID, evidence: LiveStartEvidence) -> None:
        """Store the evidence a live start was assessed on; a second record is ignored."""
        ...

    async def get(self, deployment_id: UUID) -> LiveStartEvidence | None:
        """The evidence recorded with a live deployment, or None when none was."""
        ...


class InMemoryLiveEvidenceStore:
    """Process-local evidence records for tests and database-less runs."""

    def __init__(self) -> None:
        """Start with no records."""
        self.records: dict[UUID, LiveStartEvidence] = {}

    async def record(self, deployment_id: UUID, evidence: LiveStartEvidence) -> None:
        """Keep the first record of each deployment."""
        self.records.setdefault(deployment_id, evidence)

    async def get(self, deployment_id: UUID) -> LiveStartEvidence | None:
        """The deployment's record, if any."""
        return self.records.get(deployment_id)
