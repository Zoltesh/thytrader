"""Real PostgreSQL campaign concurrency, queue, worker execution, and restart checks."""

import asyncio
from datetime import UTC, datetime, timedelta
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import SecretStr
import pytest
from sqlalchemy import update

from tests.portfolios.fixtures import DATA_START, write_dataset
from thytrader.backtest.submission import BacktestStartRequest
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.schema import research_jobs
from thytrader.research.campaigns import CampaignCaseStart, CampaignCaseStatus, CampaignStart
from thytrader.research_worker.executor import ResearchJobExecutor, build_research_services
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition


def test_campaign_survives_restart_and_concurrent_refresh(tmp_path: Path) -> None:
    """Queue once under real row locks, execute the real worker, and retain bounded evidence."""
    database_url = os.environ.get("THYTRADER_INTEGRATION_DATABASE_URL")
    if database_url is None:
        pytest.skip("THYTRADER_INTEGRATION_DATABASE_URL is not configured")
    fingerprint = write_dataset(tmp_path, "BTC-USDC", "1h", start=DATA_START, count=400)

    async def exercise() -> None:
        """Use an isolated database and actual durable services, not queue mocks."""
        engine = create_engine(SecretStr(database_url))
        try:
            services = build_research_services(engine, tmp_path)
            campaign = services.campaigns
            assert campaign is not None
            strategy = await create_strategy_from_definition(
                campaign.strategies, create_template_strategy(product_id="BTC-USDC", timeframe="1h")
            )
            # The real queue uses PostgreSQL's clock to reject expired jobs.
            now = datetime.now(UTC)
            request = BacktestStartRequest(
                strategy_id=strategy.strategy_id,
                dataset_fingerprint=fingerprint,
                evaluation_start=DATA_START + timedelta(hours=200),
                evaluation_end=DATA_START + timedelta(hours=300),
                initial_quote_balance="1000",
                maker_fee_rate="0.005",
                taker_fee_rate="0.009",
                fixed_slippage_bps="5",
            )
            record = await campaign.create(
                CampaignStart(
                    name="Postgres campaign acceptance",
                    kind="historical",
                    deadline=now + timedelta(days=2),
                    cases=(CampaignCaseStart(key="baseline", request=request),),
                ),
                now=now,
            )
            refreshed = await asyncio.gather(
                campaign.refresh(record.manifest.campaign_id, now=now),
                campaign.refresh(record.manifest.campaign_id, now=now),
            )
            assert refreshed[0].cases[0].job_id == refreshed[1].cases[0].job_id
            assert refreshed[0].cases[0].status is CampaignCaseStatus.QUEUED
            owner = "campaign-test-worker"
            claimed = await services.queue.claim(
                owner, lease_seconds=3600, order=("research_jobs",)
            )
            assert claimed is not None
            assert claimed.job_id == refreshed[0].cases[0].job_id
            await ResearchJobExecutor(services, owner).execute(claimed)
            # Build completely new services, mimicking a process restart.
            restarted = build_research_services(engine, tmp_path).campaigns
            assert restarted is not None
            completed = await restarted.refresh(record.manifest.campaign_id, now=now)
            assert completed.completed
            assert completed.cases[0].result is not None
            assert completed.manifest == record.manifest
            assert completed.cases[0].status in {
                CampaignCaseStatus.PASSED,
                CampaignCaseStatus.FAILED_GATE,
                CampaignCaseStatus.INSUFFICIENT_SAMPLE,
            }
            # Observation after a deadline still accepts a job completed before it.
            observed_later = await restarted.refresh(
                record.manifest.campaign_id, now=now + timedelta(days=3)
            )
            assert observed_later.cases[0] == completed.cases[0]
            late = await campaign.create(
                CampaignStart(
                    name="Deadline cannot accept late completion",
                    kind="historical",
                    deadline=now + timedelta(hours=1),
                    cases=(CampaignCaseStart(key="late", request=request),),
                ),
                now=now,
            )
            queued_late = await campaign.refresh(late.manifest.campaign_id, now=now)
            late_job = await services.queue.claim(
                owner, lease_seconds=3600, order=("research_jobs",)
            )
            assert late_job is not None
            assert late_job.job_id == queued_late.cases[0].job_id
            await ResearchJobExecutor(services, owner).execute(late_job)
            # Model late completion explicitly; do not depend on worker execution speed.
            async with engine.begin() as connection:
                await connection.execute(
                    update(research_jobs)
                    .where(research_jobs.c.job_id == late_job.job_id)
                    .values(updated_at=late.manifest.deadline + timedelta(seconds=1))
                )
            late_result = await restarted.refresh(
                late.manifest.campaign_id, now=now + timedelta(days=3)
            )
            assert late_result.cases[0].status is CampaignCaseStatus.EXPIRED
            assert late_result.cases[0].result is None
            prospective = await campaign.create(
                CampaignStart(
                    name="Future sample gate",
                    kind="prospective",
                    deadline=now + timedelta(days=4),
                    cases=(
                        CampaignCaseStart(
                            key="future",
                            request=request.model_copy(
                                update={
                                    "dataset_fingerprint": None,
                                    "evaluation_start": now.replace(
                                        minute=0, second=0, microsecond=0
                                    )
                                    + timedelta(days=1),
                                    "evaluation_end": now.replace(minute=0, second=0, microsecond=0)
                                    + timedelta(days=3),
                                }
                            ),
                        ),
                    ),
                ),
                now=now,
            )
            waiting = await restarted.refresh(prospective.manifest.campaign_id, now=now)
            assert waiting.cases[0].status is CampaignCaseStatus.WAITING_FOR_DATA
            assert waiting.cases[0].job_id is None
            expired = await restarted.refresh(
                prospective.manifest.campaign_id, now=now + timedelta(days=5)
            )
            assert expired.cases[0].status is CampaignCaseStatus.EXPIRED
            assert expired.cases[0].job_id is None
        finally:
            await dispose(engine)

    asyncio.run(exercise())
