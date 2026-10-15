"""PostgreSQL keeps the first live-start evidence of each deployment (ADR 0143)."""

from __future__ import annotations

from datetime import UTC, datetime
import os

from pydantic import SecretStr
import pytest

from tests.persistence.test_postgres_fill_transaction import _pending_entry_book
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_live_evidence import PostgresLiveEvidenceStore
from thytrader.research.live_evidence import LiveStartEvidence
from thytrader.risk.start_safeguards import LiveEvidenceThresholds

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _evidence(reasons: tuple[str, ...]) -> LiveStartEvidence:
    """One synthetic evidence record."""
    return LiveStartEvidence(
        strategy_fingerprint="sha256:" + "a" * 64,
        assessed_at=datetime(2026, 10, 15, 12, tzinfo=UTC),
        out_of_sample_windows=1,
        out_of_sample_trades=12,
        out_of_sample_net_pnl="-3.1",
        paper_deployments=1,
        paper_days="2.1",
        thresholds=LiveEvidenceThresholds(),
        thin_reasons=reasons,
    )


@pytest.mark.anyio
async def test_postgres_records_live_start_evidence_once() -> None:
    """A second record of the same deployment is ignored; reads return the first."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    try:
        execution = PostgresExecutionStore(engine)
        store = PostgresLiveEvidenceStore(engine)
        created = await execution.create_deployment(_pending_entry_book())
        assert await store.get(created.id) is None
        first = _evidence(("12 out-of-sample trades, fewer than 30",))
        await store.record(created.id, first)
        await store.record(created.id, _evidence(()))
        assert await store.get(created.id) == first
    finally:
        await dispose(engine)
