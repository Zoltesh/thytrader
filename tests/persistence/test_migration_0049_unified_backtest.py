"""Live PostgreSQL tests for the 0049 unified-backtest migration (ADR 0083).

Each test creates a throwaway database. One upgrades an empty database straight to
0049; the other migrates to 0048, seeds research rows written by the retired engines
(run, result, job, study with ``engine_contract_version``) beside a strategy, snapshot,
and dataset binding, then upgrades and checks what was deleted and what was kept.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

__all__ = ["scratch_database"]

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)

_NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
_STRATEGY_ID = "01985cf0-7b60-7000-8000-00000000bb01"
_SNAPSHOT_FP = "sha256:" + "a" * 64
_DATASET_FP = "sha256:" + "d" * 64
_RUN_FP = "sha256:" + "1" * 64
_RESULT_FP = "sha256:" + "2" * 64
_STUDY_FP = "sha256:" + "3" * 64
_RESEARCH_TABLES = (
    "research_jobs",
    "research_study_strategies",
    "published_research_studies",
    "published_backtest_results",
    "published_research_run_specs",
)


async def _execute(
    database_url: str, statements: tuple[tuple[str, dict[str, object]], ...]
) -> None:
    """Run seed statements in one transaction."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for statement, params in statements:
                await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


async def _counts(database_url: str, tables: tuple[str, ...]) -> dict[str, int]:
    """Return row counts for fixed table names."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return {table: await _count(connection, table) for table in tables}
    finally:
        await engine.dispose()


async def _count(connection: AsyncConnection, table: str) -> int:
    """Count one fixed table's rows."""
    result = await connection.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
    return int(result.scalar_one())


async def _study_columns(database_url: str) -> set[str]:
    """Return the current column names of published_research_studies."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'published_research_studies'"
                )
            )
            return {str(row[0]) for row in rows}
    finally:
        await engine.dispose()


def _retired_engine_seed() -> tuple[tuple[str, dict[str, object]], ...]:
    """Build 0048-shaped rows, including documents that name retired engines."""
    run_document = json.dumps({"engine_contract_version": "thytrader-bar-backtest-v4"})
    result_document = json.dumps({"engine_contract_version": "thytrader-bar-backtest-v4"})
    job_payload = json.dumps({"engine_contract_version": "thytrader-bar-backtest-v1"})
    base = {"sid": _STRATEGY_ID, "fp": _SNAPSHOT_FP, "ds": _DATASET_FP, "at": _NOW}
    return (
        (
            "INSERT INTO strategies (strategy_id, name, document, is_valid, revision, "
            "created_at, updated_at) VALUES (:sid, 'Kept strategy', '{}', false, 1, :at, :at)",
            base,
        ),
        (
            "INSERT INTO strategy_snapshots (strategy_fingerprint, strategy_id, "
            "canonical_definition, created_at) VALUES (:fp, :sid, '{}', :at)",
            base,
        ),
        (
            "INSERT INTO strategy_dataset_bindings (strategy_fingerprint, dataset_fingerprint, "
            "strategy_id, bound_at) VALUES (:fp, :ds, :sid, :at)",
            base,
        ),
        (
            "INSERT INTO published_research_run_specs (run_fingerprint, run_id, created_at, "
            "strategy_fingerprint, strategy_id, dataset_fingerprint, canonical_specification, "
            "published_at) VALUES (:run, :rid, :at, :fp, :sid, :ds, :doc, :at)",
            {**base, "run": _RUN_FP, "rid": str(uuid4()), "doc": run_document},
        ),
        (
            "INSERT INTO published_backtest_results (result_fingerprint, run_fingerprint, "
            "strategy_fingerprint, strategy_id, dataset_fingerprint, signal_trace_fingerprint, "
            "canonical_result, published_at) VALUES (:res, :run, :fp, :sid, :ds, :res, :doc, :at)",
            {**base, "run": _RUN_FP, "res": _RESULT_FP, "doc": result_document},
        ),
        (
            "INSERT INTO research_jobs (job_id, kind, status, strategy_id, strategy_fingerprint, "
            "payload, created_at, updated_at, expires_at) VALUES (:id, 'backtest', 'queued', "
            ":sid, :fp, :doc, :at, :at, :at)",
            {**base, "id": uuid4(), "doc": job_payload},
        ),
        (
            "INSERT INTO published_research_studies (study_fingerprint, strategy_id, "
            "request_fingerprint, plan_fingerprint, kind, engine_contract_version, product_id, "
            "timeframe, window_count, canonical_study, published_at) VALUES (:s, :sid, :s, :s, "
            "'walk_forward', 'thytrader-bar-backtest-v4', 'BTC-USD', '1h', 1, '{}', :at)",
            {**base, "s": _STUDY_FP},
        ),
        (
            "INSERT INTO research_study_strategies (study_fingerprint, strategy_id) "
            "VALUES (:s, :sid)",
            {**base, "s": _STUDY_FP},
        ),
    )


def test_0049_upgrades_an_empty_database(scratch_database: str) -> None:
    """A fresh install reaches 0049 and the engine column no longer exists."""
    upgraded = _alembic(scratch_database, "0049")
    assert upgraded.returncode == 0, upgraded.stderr

    assert "engine_contract_version" not in asyncio.run(_study_columns(scratch_database))
    counts = asyncio.run(_counts(scratch_database, _RESEARCH_TABLES))
    assert set(counts.values()) == {0}


def test_0049_deletes_retired_engine_rows_and_keeps_strategies(scratch_database: str) -> None:
    """Research rows the unified model cannot verify go; strategies and bindings stay."""
    assert _alembic(scratch_database, "0048").returncode == 0
    asyncio.run(_execute(scratch_database, _retired_engine_seed()))
    before = asyncio.run(_counts(scratch_database, _RESEARCH_TABLES))
    assert set(before.values()) == {1}

    upgraded = _alembic(scratch_database, "0049")
    assert upgraded.returncode == 0, upgraded.stderr

    after = asyncio.run(_counts(scratch_database, _RESEARCH_TABLES))
    assert set(after.values()) == {0}
    kept = asyncio.run(
        _counts(scratch_database, ("strategies", "strategy_snapshots", "strategy_dataset_bindings"))
    )
    assert kept == {"strategies": 1, "strategy_snapshots": 1, "strategy_dataset_bindings": 1}
    assert "engine_contract_version" not in asyncio.run(_study_columns(scratch_database))
