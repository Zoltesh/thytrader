"""Transactional campaign state and restart-safe child queue publication."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from thytrader.persistence.schema import research_campaigns, research_jobs
from thytrader.research.campaigns import CampaignManifest, CampaignRecord
from thytrader.research.jobs import RESEARCH_JOB_EXPIRY_HOURS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.backtest.submission import BacktestSubmissionRequest


class _StoredCampaign(BaseModel):
    """Validate dynamic SQL values before passing campaign state to application code."""

    model_config = ConfigDict(extra="ignore")
    manifest_json: str
    manifest_fingerprint: str
    state_json: str

    def record(self) -> CampaignRecord:
        """Verify the frozen manifest against its independently stored digest."""
        record = CampaignRecord.model_validate_json(self.state_json)
        manifest = CampaignManifest.model_validate_json(self.manifest_json)
        if record.manifest != manifest or record.manifest_fingerprint != self.manifest_fingerprint:
            raise ValueError("campaign publication integrity mismatch")
        return record


@dataclass(frozen=True, slots=True)
class CampaignTransaction:
    """A locked campaign update and its child jobs share one PostgreSQL transaction."""

    connection: AsyncConnection
    record: CampaignRecord

    async def queue(
        self, key: str, request: BacktestSubmissionRequest, *, strategy_id: UUID, now: datetime
    ) -> UUID:
        """Publish the one deterministic child identity atomically with campaign state."""
        job_id = uuid5(self.record.manifest.campaign_id, key)
        await self.connection.execute(
            insert(research_jobs)
            .values(
                job_id=job_id,
                kind="backtest",
                status="queued",
                strategy_id=str(strategy_id),
                strategy_fingerprint=request.strategy_fingerprint,
                payload=request.model_dump_json(),
                progress_current=0,
                progress_total=1,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(hours=RESEARCH_JOB_EXPIRY_HOURS),
                cancel_requested=False,
            )
            .on_conflict_do_nothing(index_elements=[research_jobs.c.job_id])
        )
        return job_id

    async def save(self, record: CampaignRecord) -> None:
        """Update evolving evidence while keeping the manifest bytes immutable."""
        if record.manifest != self.record.manifest:
            raise ValueError("campaign manifest cannot be changed")
        await self.connection.execute(
            update(research_campaigns)
            .where(research_campaigns.c.campaign_id == record.manifest.campaign_id)
            .values(
                state_json=record.model_dump_json(),
                updated_at=record.updated_at,
                completed=record.completed,
            )
        )


class PostgresCampaignStore:
    """Keep campaign intent and validation progress in authoritative operational storage."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use the existing shared asynchronous engine."""
        self.engine = engine

    async def create(self, record: CampaignRecord) -> None:
        """Publish frozen intent before any child can be queued."""
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(research_campaigns).values(
                    campaign_id=record.manifest.campaign_id,
                    manifest_json=record.manifest.model_dump_json(),
                    manifest_fingerprint=record.manifest_fingerprint,
                    state_json=record.model_dump_json(),
                    created_at=record.manifest.frozen_at,
                    updated_at=record.updated_at,
                    completed=False,
                )
            )

    async def get(self, campaign_id: UUID) -> CampaignRecord:
        """Read verified immutable intent and its latest evidence."""
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(research_campaigns).where(
                            research_campaigns.c.campaign_id == campaign_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise KeyError("campaign was not found")
        return _StoredCampaign.model_validate(row).record()

    async def list(self, *, limit: int = 50, pending: bool = False) -> tuple[CampaignRecord, ...]:
        """Return bounded newest records, or least recently checked unfinished records."""
        if not 1 <= limit <= 100:
            raise ValueError("campaign limit must be between 1 and 100")
        statement = select(research_campaigns)
        if pending:
            statement = statement.where(research_campaigns.c.completed.is_(False)).order_by(
                research_campaigns.c.updated_at, research_campaigns.c.campaign_id
            )
        else:
            statement = statement.order_by(research_campaigns.c.created_at.desc())
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement.limit(limit))).mappings().all()
        return tuple(_StoredCampaign.model_validate(row).record() for row in rows)

    @asynccontextmanager
    async def locked(self, campaign_id: UUID) -> AsyncIterator[CampaignTransaction]:
        """Serialize API and worker refreshes with an actual PostgreSQL row lock."""
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(research_campaigns)
                        .where(research_campaigns.c.campaign_id == campaign_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise KeyError("campaign was not found")
            yield CampaignTransaction(connection, _StoredCampaign.model_validate(row).record())
