"""PostgreSQL repository for live-start evidence records (ADR 0143)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.schema import live_start_evidence
from thytrader.research.live_evidence import LiveStartEvidence
from thytrader.research.live_evidence_store import LiveEvidenceStoreUnavailableError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresLiveEvidenceStore:
    """Insert each live start's evidence once and read it back by deployment."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind evidence operations to one managed async engine."""
        self._engine = engine

    async def record(self, deployment_id: UUID, evidence: LiveStartEvidence) -> None:
        """Store the evidence unless the deployment already has a record."""
        try:
            statement = (
                insert(live_start_evidence)
                .values(
                    deployment_id=deployment_id,
                    strategy_fingerprint=evidence.strategy_fingerprint,
                    assessed_at=evidence.assessed_at,
                    evidence_json=evidence.model_dump_json(),
                )
                .on_conflict_do_nothing(index_elements=["deployment_id"])
            )
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except (SQLAlchemyError, OSError, ValueError) as error:
            raise LiveEvidenceStoreUnavailableError(
                "Live-start evidence storage is unavailable."
            ) from error

    async def get(self, deployment_id: UUID) -> LiveStartEvidence | None:
        """The deployment's recorded evidence, or None."""
        statement = select(live_start_evidence.c.evidence_json).where(
            live_start_evidence.c.deployment_id == deployment_id
        )
        try:
            async with self._engine.connect() as connection:
                stored = (await connection.execute(statement)).scalar_one_or_none()
        except (SQLAlchemyError, OSError) as error:
            raise LiveEvidenceStoreUnavailableError(
                "Live-start evidence storage is unavailable."
            ) from error
        if stored is None:
            return None
        try:
            return LiveStartEvidence.model_validate_json(stored)
        except ValidationError as error:
            raise LiveEvidenceStoreUnavailableError(
                "A stored live-start evidence record is malformed."
            ) from error
