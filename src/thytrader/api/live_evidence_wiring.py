"""Pick the live-start evidence store for the API process (ADR 0143)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request  # noqa: TC002 - FastAPI resolves the dependency annotation at runtime.

from thytrader.persistence.postgres_live_evidence import PostgresLiveEvidenceStore
from thytrader.research.live_evidence_store import InMemoryLiveEvidenceStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.research.live_evidence_store import LiveEvidenceStore


def live_evidence_store(engine: AsyncEngine | None) -> LiveEvidenceStore:
    """PostgreSQL when a database is configured, otherwise a process-local store."""
    return InMemoryLiveEvidenceStore() if engine is None else PostgresLiveEvidenceStore(engine)


def get_live_evidence_store(request: Request) -> LiveEvidenceStore:
    """The live-start evidence store attached during app startup (process-local if absent)."""
    store = getattr(request.app.state, "live_evidence_store", None)
    if store is None:
        store = InMemoryLiveEvidenceStore()
        request.app.state.live_evidence_store = store
    return store
