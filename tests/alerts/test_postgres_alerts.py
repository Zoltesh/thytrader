"""Private disposable PostgreSQL coverage for monotone observations and delivery CAS.

THYTRADER_TEST_DATABASE_URL must identify a loopback test database, never port5439.
Each test receives its own disposable migrated database, including downgrade tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from alembic.config import Config
from pydantic import SecretStr
import pytest
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from alembic import command
from tests.alerts.test_supervision import _no_candles
from tests.execution_worker.test_safety_supervision import _deployment
from tests.persistence.test_migration_0048_strategy_root import scratch_database
from thytrader.alerts.models import (
    AlertCheck,
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.alerts.service import AlertService
from thytrader.alerts.store import AlertStoreError
from thytrader.alerts.supervision import (
    AlertThresholds,
    gather_safety_findings,
    worker_book_failure_finding,
)
from thytrader.execution.models import DeploymentStatus, LifecycleCommand
from thytrader.execution_worker.service import _pause_repeatedly_failing_books
from thytrader.memory.notify import DisabledNotificationSender
from thytrader.persistence import postgres_alerts as module
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_alerts import PostgresAlertStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.schema import deployments

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.alerts.store import AlertChange

__all__ = ["scratch_database"]
_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(_URL is None, reason="Private alert test DB required."),
]


@pytest.fixture(autouse=True)
def private_target() -> None:
    """Validate the parent before the shared scratch fixture creates any database."""
    if _URL is None:
        raise AssertionError("Private alert test DB required.")
    parsed = urlsplit(_URL)
    if (
        parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port == 5439
        or "test" not in parsed.path
    ):
        raise AssertionError("Alerts tests require a private loopback test database.")


@pytest.fixture
async def store(
    scratch_database: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[PostgresAlertStore]:
    """Migrate an owned database; no shared tables are cleared or downgraded."""
    # Suite autouse guards disable dotenv/credentials. Set only this private URL.
    monkeypatch.setenv("THYTRADER_DATABASE_URL", scratch_database)
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    engine = create_engine(SecretStr(scratch_database))
    try:
        yield PostgresAlertStore(engine)
    finally:
        await dispose(engine)


def _finding() -> SupervisionFinding:
    """Use a unique subject for each private test, without operational deployment FKs."""
    return SupervisionFinding(
        code=AlertCode.WORKER_BOOK_FAILURES,
        scope=AlertScope.WORKER,
        subject=str(uuid4()),
        severity=AlertSeverity.WARNING,
        detail="private test error",
    )


async def test_postgres_dedup_restart_watermark_and_out_of_order_recovery(
    store: PostgresAlertStore,
    scratch_database: str,
) -> None:
    """Concurrent dedup and persistent healthy watermarks survive repository reconstruction."""
    finding = _finding()
    check = AlertCheck(finding.code, finding.subject)
    applications = await asyncio.gather(
        *(store.apply_observations((finding,), (), now=_NOW, detail="") for _ in range(4))
    )
    assert sum(len(item.changes) for item in applications) == 1
    second = await store.apply_observations(
        (finding,), (), now=_NOW + timedelta(seconds=2), detail=""
    )
    assert second.changes[0].alert.occurrences == 2
    await store.apply_observations(
        (), (check,), now=_NOW + timedelta(seconds=1), detail="stale clear"
    )
    assert any(row.subject == finding.subject for row in await store.list_open_alerts())
    await store.apply_observations(
        (), (check,), now=_NOW + timedelta(seconds=3), detail="verified recovery"
    )
    await store.apply_observations(
        (finding,), (), now=_NOW + timedelta(seconds=2), detail="late failure"
    )
    assert not any(row.subject == finding.subject for row in await store.list_open_alerts())
    # New engine/repository on the same isolated database: no process-local watermark.
    engine = create_engine(SecretStr(scratch_database))
    try:
        rebuilt = PostgresAlertStore(engine)
        result = await rebuilt.apply_observations(
            (finding,), (), now=_NOW + timedelta(seconds=4), detail=""
        )
        assert result.changes[0].created
        assert result.changes[0].alert.occurrences == 1
        # Persisted failure counters do not reset when the repository is rebuilt.
        again = await rebuilt.apply_observations(
            (finding,), (), now=_NOW + timedelta(seconds=5), detail=""
        )
        assert again.changes[0].alert.occurrences == 2
    finally:
        await dispose(engine)


async def test_postgres_equal_time_failure_wins_and_failure_after_newer_recovery_is_ignored(
    store: PostgresAlertStore,
) -> None:
    """Conservative tie handling never lets an equal-time clear erase a failure."""
    finding = _finding()
    check = AlertCheck(finding.code, finding.subject)
    await store.apply_observations((), (check,), now=_NOW, detail="healthy")
    await store.apply_observations((finding,), (), now=_NOW, detail="")
    await store.apply_observations((), (check,), now=_NOW, detail="equal clear")
    assert any(row.subject == finding.subject for row in await store.list_open_alerts())


async def test_postgres_partial_pass_and_transaction_failure_do_not_clear_or_half_write(
    store: PostgresAlertStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing check is unknown, and an observation batch crash rolls back all changes."""
    a, b = _finding(), _finding()
    await store.apply_observations((a, b), (), now=_NOW, detail="")
    await store.apply_observations(
        (), (AlertCheck(a.code, a.subject),), now=_NOW + timedelta(seconds=1), detail="verified"
    )
    assert any(row.subject == b.subject for row in await store.list_open_alerts())
    original = module._record
    count = 0

    async def fail_after_first(
        connection: AsyncConnection,
        finding: SupervisionFinding,
        *,
        now: datetime,
    ) -> AlertChange:
        """Crash within the second observation, after the first row was staged."""
        nonlocal count
        count += 1
        if count == 2:
            raise SQLAlchemyError("private injected transaction failure")
        return await original(connection, finding, now=now)

    monkeypatch.setattr(module, "_record", fail_after_first)
    with pytest.raises(AlertStoreError):
        await store.apply_observations((a, b), (), now=_NOW + timedelta(seconds=2), detail="")
    assert (
        next(row for row in await store.list_open_alerts() if row.subject == b.subject).occurrences
        == 1
    )
    monkeypatch.setattr(module, "_record", original)
    retry = await store.apply_observations((a, b), (), now=_NOW + timedelta(seconds=2), detail="")
    assert len(retry.changes) == 2  # Rolled-back check watermarks did not suppress the retry.


async def test_postgres_delivery_claim_crash_expiry_and_stale_ack(
    store: PostgresAlertStore,
) -> None:
    """Only one dispatcher can claim; stale acknowledgement cannot overwrite a retry."""
    change = await store.record(_finding(), now=_NOW)
    claims = await asyncio.gather(
        *(
            store.claim_delivery(
                change.alert.id,
                provider="webhook",
                now=_NOW,
                max_attempts=2,
                ttl=timedelta(seconds=60),
            )
            for _ in range(3)
        )
    )
    owned = [claim for claim in claims if claim is not None]
    assert len(owned) == 1
    first = owned[0]
    second = await store.claim_delivery(
        change.alert.id,
        provider="webhook",
        now=_NOW + timedelta(seconds=61),
        max_attempts=2,
        ttl=timedelta(seconds=60),
    )
    assert second is not None
    await store.finish_delivery(change.alert.id, first.token, status="delivered", detail="stale")
    row = next(row for row in await store.list_open_alerts() if row.id == change.alert.id)
    assert row.delivery_token == second.token
    assert row.delivery_status == "failed"
    await store.finish_delivery(
        change.alert.id, second.token, status="delivered", detail="delivered"
    )
    assert (
        await store.claim_delivery(
            change.alert.id,
            provider="webhook",
            now=_NOW + timedelta(seconds=122),
            max_attempts=2,
            ttl=timedelta(seconds=60),
        )
        is None
    )


async def test_postgres_none_provider_persists_skipped_without_consuming_retry(
    store: PostgresAlertStore,
) -> None:
    """Local delivery-disabled evidence remains durable with a zero retry budget spent."""
    change = await store.record(_finding(), now=_NOW)
    assert (
        await store.claim_delivery(
            change.alert.id, provider="none", now=_NOW, max_attempts=2, ttl=timedelta(seconds=60)
        )
        is None
    )
    row = next(row for row in await store.list_open_alerts() if row.id == change.alert.id)
    assert row.delivery_status == "skipped"
    assert row.delivery_attempts == 0
    assert "delivery disabled" in row.delivery_detail


async def test_postgres_deleted_book_keeps_alert_history_and_requires_authoritative_clear(
    store: PostgresAlertStore,
) -> None:
    """SET NULL preserves safety history, rather than cascading away an operator's alert."""
    execution = PostgresExecutionStore(store._engine)
    book = await execution.create_deployment(
        replace(
            _deployment(),
            paper_starting_cash=Decimal("1000"),
            paper_maker_fee_rate=Decimal("0.001"),
            paper_taker_fee_rate=Decimal("0.002"),
        )
    )
    finding = SupervisionFinding(
        code=AlertCode.BOOK_PAUSED_MISMATCH,
        scope=AlertScope.DEPLOYMENT,
        subject=str(book.id),
        deployment_id=book.id,
        severity=AlertSeverity.WARNING,
        detail="test mismatch",
    )
    change = await store.record(finding, now=_NOW)
    async with store._engine.begin() as connection:
        await connection.execute(delete(deployments).where(deployments.c.id == book.id))
    row = next(item for item in await store.list_open_alerts() if item.id == change.alert.id)
    assert row.deployment_id is None
    assert row.subject == str(book.id)
    # Missing inventory is not a clear; an explicit authoritative removal is.
    await store.apply_observations((), (), now=_NOW + timedelta(seconds=1), detail="unknown")
    assert any(item.id == row.id for item in await store.list_open_alerts())
    result = await store.apply_observations(
        (),
        (AlertCheck(row.code, row.subject),),
        now=_NOW + timedelta(seconds=2),
        detail="authoritative removal",
    )
    assert result.resolved_count == 1


async def test_postgres_failure_threshold_and_fenced_pause_survive_engine_restart(
    store: PostgresAlertStore,
    scratch_database: str,
) -> None:
    """Persist the actual worker pause in PostgreSQL, then rebuild both repositories."""
    execution = PostgresExecutionStore(store._engine)
    book = await execution.create_deployment(
        replace(
            _deployment(),
            paper_starting_cash=Decimal("1000"),
            paper_maker_fee_rate=Decimal("0.001"),
            paper_taker_fee_rate=Decimal("0.002"),
        )
    )
    application = None
    for index in range(3):
        application = await store.apply_observations(
            (worker_book_failure_finding(book, error_type="RuntimeError"),),
            (),
            now=_NOW + timedelta(seconds=index),
            detail="",
        )
    assert application is not None
    await _pause_repeatedly_failing_books(
        execution, application, deployments=(book,), consecutive_failure_cycles=3
    )
    engine = create_engine(SecretStr(scratch_database))
    try:
        rebuilt_execution = PostgresExecutionStore(engine)
        rebuilt_alerts = PostgresAlertStore(engine)
        snapshot = await rebuilt_execution.get_deployment(book.id)
        assert snapshot.deployment.status is DeploymentStatus.PAUSED
        assert snapshot.deployment.lifecycle_command is LifecycleCommand.STOP_NEW_ENTRIES
        assert snapshot.deployment.revision == book.revision + 2
        evidence = await gather_safety_findings(
            deployments=(snapshot.deployment,),
            snapshots=rebuilt_execution,
            closed_candles=_no_candles,
            now=_NOW + timedelta(seconds=4),
            thresholds=AlertThresholds(),
            worker_interval_seconds=30,
            prior_alerts=await rebuilt_alerts.list_open_alerts(),
        )
        await AlertService(
            rebuilt_alerts, DisabledNotificationSender(), thresholds=AlertThresholds()
        ).apply(
            evidence.findings,
            evaluated=evidence.evaluated,
            now=_NOW + timedelta(seconds=4),
            dispatch=False,
        )
        failure = next(
            row
            for row in await rebuilt_alerts.list_open_alerts()
            if row.deployment_id == book.id and row.code is AlertCode.WORKER_BOOK_FAILURES
        )
        assert failure.occurrences == 3
        assert failure.is_open
    finally:
        await dispose(engine)


async def test_reserved_migration_0066_roundtrip_on_private_database(
    store: PostgresAlertStore,
) -> None:
    """The extended reserved migration upgrades/downgrades with its actual DDL, not create_all."""
    await asyncio.to_thread(command.downgrade, Config("alembic.ini"), "risk0065")
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    change = await store.record(_finding(), now=_NOW)
    assert change.created
    assert (
        await store.claim_delivery(
            change.alert.id, provider="none", now=_NOW, max_attempts=2, ttl=timedelta(seconds=60)
        )
        is None
    )
