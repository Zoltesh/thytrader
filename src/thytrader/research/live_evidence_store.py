"""Where each live start's evidence is kept (ADR 0143)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.research.live_evidence import LiveStartEvidence


RECORD_SECONDS = 5.0
"""Time recording a started bot's evidence may take before the start only warns."""

NOT_RECORDED = "LIVE_EVIDENCE_NOT_RECORDED"


class LiveEvidenceStoreUnavailableError(RuntimeError):
    """The live-start evidence store could not be read or written."""


async def record_started_evidence(
    store: LiveEvidenceStore,
    deployment_id: UUID,
    evidence: LiveStartEvidence,
    *,
    warnings: list[str],
) -> None:
    """Keep the evidence with a started bot; any failure or delay warns, never undoes it."""
    try:
        async with asyncio.timeout(RECORD_SECONDS):
            await store.record(deployment_id, evidence)
    except Exception:  # noqa: BLE001 - the bot already started; recording is advisory.
        warnings.append(
            f"{NOT_RECORDED}: the bot started, but its live-start evidence could not be "
            "stored; the evidence is in this response."
        )


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
