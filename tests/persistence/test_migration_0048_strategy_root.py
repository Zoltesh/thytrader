"""Live PostgreSQL tests for the 0048 strategy-root migration (ADR 0082).

Each test creates a throwaway database, migrates it to 0047, seeds legacy rows
(drafts, publications, research evidence, a paper book, a stopped live book with
a ledger, and a risk policy with an allocation), then upgrades to 0048 and checks
what was wiped, what was kept, and that the kept live snapshot re-verifies.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from pydantic import SecretStr
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.schema import metadata
from thytrader.risk.models import (
    CapitalAllocation,
    canonical_risk_policy_bytes,
    compiled_default_risk_policy,
    definition_from_stored_json,
    risk_policy_fingerprint,
)
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, canonical_strategy_bytes

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncConnection

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_ROOT = Path(__file__).parents[2]
_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
_DATASET_FP = "sha256:" + "d" * 64
_LIVE_ID = UUID("01985cf0-7b60-7000-8000-00000000aa01")
_PAPER_ID = UUID("01985cf0-7b60-7000-8000-00000000aa02")
_LIVE_DISCRETIONARY_ID = UUID("01985cf0-7b60-7000-8000-00000000aa03")

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)


def _alembic(
    database_url: str, target: str, *, operation: Literal["upgrade", "downgrade"] = "upgrade"
) -> subprocess.CompletedProcess[str]:
    """Isolate migration logging and disable dotenv/secret inheritance in the child."""
    script = (
        "from thytrader.config import Settings; Settings.model_config['env_file'] = None; "
        "from alembic.config import Config; from alembic import command; "
        f"command.{operation}(Config('alembic.ini'), {target!r})"
    )
    return subprocess.run(  # noqa: S603 - fixed interpreter/script and explicit test-only URL
        [sys.executable, "-c", script],
        cwd=_ROOT,
        env={"PATH": os.defpath, "THYTRADER_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


@pytest.fixture
def scratch_database() -> Iterator[str]:
    """Create and later drop one uniquely named database on the test server."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    admin_url = _TEST_DATABASE_URL
    name = f"thytrader_mig_{uuid4().hex[:12]}"

    async def run(statement: str) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(run(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _legacy_canonical(definition: StrategyDefinition) -> str:
    """Render one strategy the way 0047 stored it: with version and status keys."""
    document = json.loads(canonical_strategy_bytes(definition))
    document["version"] = 1
    document["status"] = "published"
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(canonical: str) -> str:
    """Return the sha256 content address of stored canonical text."""
    return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


async def _seed_strategy_rows(connection: AsyncConnection, definition: StrategyDefinition) -> str:
    """Insert one draft, one publication, an archive marker, and research evidence."""
    canonical = _legacy_canonical(definition)
    fingerprint = _fingerprint(canonical)
    strategy_id = str(definition.strategy_id)
    params = {"sid": strategy_id, "fp": fingerprint, "doc": canonical, "at": _NOW}
    await connection.execute(
        text(
            "INSERT INTO strategy_drafts (strategy_id, version, created_at, updated_at, "
            "revision, canonical_definition) VALUES (:sid, 2, :at, :at, 1, :doc)"
        ),
        params,
    )
    await connection.execute(
        text(
            "INSERT INTO published_strategy_versions (strategy_fingerprint, strategy_id, "
            "version, created_at, canonical_definition, published_at) "
            "VALUES (:fp, :sid, 1, :at, :doc, :at)"
        ),
        params,
    )
    await connection.execute(
        text("INSERT INTO archived_strategy_versions VALUES (:fp, :at)"),
        params,
    )
    run_fp = "sha256:" + "1" * 64
    await connection.execute(
        text("INSERT INTO strategy_dataset_bindings VALUES (:fp, :ds, :at)"),
        {**params, "ds": _DATASET_FP},
    )
    await connection.execute(
        text(
            "INSERT INTO published_research_run_specs (run_fingerprint, run_id, created_at, "
            "strategy_fingerprint, dataset_fingerprint, canonical_specification, published_at) "
            "VALUES (:run, :rid, :at, :fp, :ds, '{}', :at)"
        ),
        {**params, "run": run_fp, "rid": str(uuid4()), "ds": _DATASET_FP},
    )
    await connection.execute(
        text(
            "INSERT INTO published_backtest_results (result_fingerprint, run_fingerprint, "
            "strategy_fingerprint, dataset_fingerprint, signal_trace_fingerprint, "
            "canonical_result, published_at) VALUES (:res, :run, :fp, :ds, :res, '{}', :at)"
        ),
        {**params, "run": run_fp, "res": "sha256:" + "2" * 64, "ds": _DATASET_FP},
    )
    await connection.execute(
        text(
            "INSERT INTO research_jobs (job_id, kind, status, payload, created_at, updated_at, "
            "expires_at) VALUES (:id, 'backtest', 'completed', '{}', :at, :at, :at)"
        ),
        {"id": uuid4(), "at": _NOW},
    )
    await connection.execute(
        text(
            "INSERT INTO published_research_studies (study_fingerprint, request_fingerprint, "
            "plan_fingerprint, kind, engine_contract_version, product_id, timeframe, "
            "window_count, canonical_study, published_at) VALUES (:s, :s, :s, 'walk_forward', "
            "'thytrader-bar-backtest-v1', 'BTC-USD', '1h', 1, '{}', :at)"
        ),
        {"s": "sha256:" + "3" * 64, "at": _NOW},
    )
    return fingerprint


async def _seed_book(
    connection: AsyncConnection,
    *,
    deployment_id: UUID,
    mode: str,
    status: str,
    definition: StrategyDefinition | None,
    fingerprint: str | None,
) -> None:
    """Insert one deployment with an intent, order, fill, position, and trade reason."""
    fees = ("0.001", "0.002") if mode == "paper" else (None, None)
    await connection.execute(
        text(
            "INSERT INTO deployments (id, strategy_fingerprint, strategy_id, product_id, mode, "
            "status, kind, timeframe, paper_maker_fee_rate, paper_taker_fee_rate, cash, phase, "
            "created_at, updated_at) VALUES (:id, :fp, :sid, 'BTC-USD', :mode, :status, :kind, "
            "'1h', :maker, :taker, '100', 'flat', :at, :at)"
        ),
        {
            "id": deployment_id,
            "fp": fingerprint,
            "sid": None if definition is None else str(definition.strategy_id),
            "mode": mode,
            "status": status,
            "kind": "strategy" if definition is not None else "discretionary",
            "maker": fees[0],
            "taker": fees[1],
            "at": _NOW,
        },
    )
    intent_id, order_id, parent_id = uuid4(), uuid4(), uuid4()
    await connection.execute(
        text(
            "INSERT INTO order_intents (id, deployment_id, client_order_id, purpose, side, kind, "
            "quantity, candle_starts_at, status, product_id, created_at) VALUES (:id, :dep, "
            ":coid, 'entry', 'buy', 'post_only_limit', '0.01', :at, 'filled', 'BTC-USD', :at)"
        ),
        {"id": intent_id, "dep": deployment_id, "coid": f"c-{intent_id}", "at": _NOW},
    )
    for identifier, parent in ((parent_id, None), (order_id, parent_id)):
        await connection.execute(
            text(
                "INSERT INTO execution_orders (id, deployment_id, intent_id, client_order_id, "
                "side, kind, quantity, filled_quantity, status, product_id, parent_order_id, "
                "created_at, updated_at) VALUES (:id, :dep, :intent, :coid, 'buy', "
                "'post_only_limit', '0.01', '0.01', 'filled', 'BTC-USD', :parent, :at, :at)"
            ),
            {
                "id": identifier,
                "dep": deployment_id,
                "intent": intent_id,
                "coid": f"o-{identifier}",
                "parent": parent,
                "at": _NOW,
            },
        )
    await connection.execute(
        text(
            "INSERT INTO execution_fills (id, deployment_id, order_id, venue_fill_id, price, "
            "quantity, fee, filled_at) VALUES (:id, :dep, :order, :vf, '60000', '0.01', "
            "'0.6', :at)"
        ),
        {"id": uuid4(), "dep": deployment_id, "order": order_id, "vf": f"f-{order_id}", "at": _NOW},
    )
    await connection.execute(
        text(
            "INSERT INTO execution_positions (deployment_id, product_id, quantity, entry_price, "
            "stop_price, target_price, entered_bar, updated_at) VALUES (:dep, 'BTC-USD', "
            "'0.01', '60000', '59000', '62000', :at, :at)"
        ),
        {"dep": deployment_id, "at": _NOW},
    )
    await connection.execute(
        text(
            "INSERT INTO execution_instrument_state (deployment_id, product_id, phase) "
            "VALUES (:dep, 'BTC-USD', 'open')"
        ),
        {"dep": deployment_id},
    )
    await connection.execute(
        text(
            "INSERT INTO trade_reason_records (id, created_at, origin, intent_id, deployment_id, "
            "deployment_kind, mode, product_id, purpose, side, strategy_id, "
            "strategy_fingerprint, strategy_name, strategy_version, signal_kind, "
            "candle_starts_at, risk_decision, risk_reason_code, risk_detail, "
            "policy_fingerprint, policy_source) VALUES (:id, :at, 'runtime', :intent, :dep, "
            ":kind, :mode, 'BTC-USD', 'entry', 'buy', :sid, :fp, 'Legacy', 1, "
            "'strategy_entry', :at, 'allow', 'ALLOWED', 'ok', :pol, 'compiled_default')"
        ),
        {
            "id": uuid4(),
            "at": _NOW,
            "intent": intent_id,
            "dep": deployment_id,
            "kind": "strategy" if definition is not None else "discretionary",
            "mode": mode,
            "sid": None if definition is None else definition.strategy_id,
            "fp": fingerprint,
            "pol": "sha256:" + "4" * 64,
        },
    )


async def _seed_risk_policy(connection: AsyncConnection, strategy_id: UUID) -> str:
    """Publish version 1 of a policy that allocates capital to the legacy strategy."""
    definition = compiled_default_risk_policy().model_copy(
        update={
            "version": 1,
            "allocations": (CapitalAllocation(strategy_id=strategy_id, allocated_quote="50"),),
        }
    )
    fingerprint = risk_policy_fingerprint(definition)
    await connection.execute(
        text(
            "INSERT INTO published_risk_policies VALUES (:fp, :pid, 1, :doc, :at)",
        ),
        {
            "fp": fingerprint,
            "pid": str(definition.policy_id),
            "doc": canonical_risk_policy_bytes(definition).decode("utf-8"),
            "at": _NOW,
        },
    )
    await connection.execute(
        text("INSERT INTO active_risk_policy VALUES (1, :fp, :at)"),
        {"fp": fingerprint, "at": _NOW},
    )
    return fingerprint


async def _seed_kept_rows(connection: AsyncConnection) -> None:
    """Insert rows the migration must never touch."""
    await connection.execute(
        text(
            "INSERT INTO audit_events (id, occurred_at, category, action, outcome) "
            "VALUES (:id, :at, 'runtime', 'start_live', 'success')"
        ),
        {"id": uuid4(), "at": _NOW},
    )
    await connection.execute(
        text(
            "INSERT INTO portfolio_snapshots (as_of, provider, connection_status, demo, "
            "total_usd_value, snapshot) VALUES (:at, 'coinbase', 'connected', true, 1, '{}')"
        ),
        {"at": _NOW},
    )


async def _count(connection: AsyncConnection, statement: str) -> int:
    """Return one integer aggregate."""
    return int((await connection.execute(text(statement))).scalar_one())


def test_0048_wipes_research_and_paper_but_keeps_live_ledger(scratch_database: str) -> None:
    """Upgrade on a populated 0047 database keeps live history and re-verifies its snapshot."""
    assert _alembic(scratch_database, "0047").returncode == 0
    definition = create_template_strategy(now=_NOW)

    async def seed() -> tuple[str, str]:
        engine = create_async_engine(scratch_database)
        try:
            async with engine.begin() as connection:
                fingerprint = await _seed_strategy_rows(connection, definition)
                await _seed_book(
                    connection,
                    deployment_id=_LIVE_ID,
                    mode="live",
                    status="stopped",
                    definition=definition,
                    fingerprint=fingerprint,
                )
                await _seed_book(
                    connection,
                    deployment_id=_PAPER_ID,
                    mode="paper",
                    status="running",
                    definition=definition,
                    fingerprint=fingerprint,
                )
                await _seed_book(
                    connection,
                    deployment_id=_LIVE_DISCRETIONARY_ID,
                    mode="live",
                    status="running",
                    definition=None,
                    fingerprint=None,
                )
                policy = await _seed_risk_policy(connection, definition.strategy_id)
                await _seed_kept_rows(connection)
        finally:
            await engine.dispose()
        return fingerprint, policy

    legacy_fingerprint, legacy_policy = asyncio.run(seed())
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    expected_fingerprint = _fingerprint(canonical_strategy_bytes(definition).decode("utf-8"))
    assert expected_fingerprint != legacy_fingerprint

    async def verify() -> None:
        engine = create_async_engine(scratch_database)
        try:
            async with engine.connect() as connection:
                await _verify_wiped(connection)
                await _verify_live_kept(connection, expected_fingerprint, legacy_fingerprint)
                await _verify_policy(connection, legacy_policy)
                assert await _count(connection, "SELECT COUNT(*) FROM audit_events") == 1
                assert await _count(connection, "SELECT COUNT(*) FROM portfolio_snapshots") == 1
        finally:
            await engine.dispose()
        store_engine = create_engine(SecretStr(scratch_database))
        try:
            snapshot = await PostgresStrategyStore(store_engine).load(expected_fingerprint)
        finally:
            await dispose(store_engine)
        assert snapshot.definition == definition

    asyncio.run(verify())


async def _verify_wiped(connection: AsyncConnection) -> None:
    """Retired tables are gone and research/paper rows are wiped."""
    for table in ("strategy_drafts", "published_strategy_versions", "archived_strategy_versions"):
        statement = f"SELECT COUNT(*) FROM pg_tables WHERE tablename = '{table}'"  # noqa: S608
        assert await _count(connection, statement) == 0
    for table in (
        "strategies",
        "strategy_dataset_bindings",
        "published_research_run_specs",
        "published_backtest_results",
        "published_research_studies",
        "research_study_strategies",
        "research_jobs",
    ):
        assert await _count(connection, f"SELECT COUNT(*) FROM {table}") == 0  # noqa: S608
    paper = f"'{_PAPER_ID}'"
    assert await _count(connection, f"SELECT COUNT(*) FROM deployments WHERE id = {paper}") == 0  # noqa: S608
    for table in ("order_intents", "execution_orders", "execution_fills", "trade_reason_records"):
        assert (
            await _count(connection, f"SELECT COUNT(*) FROM {table} WHERE deployment_id = {paper}")  # noqa: S608
            == 0
        )


async def _verify_live_kept(
    connection: AsyncConnection, expected_fingerprint: str, legacy_fingerprint: str
) -> None:
    """The stopped live book keeps its ledger, detached, pointing at the re-addressed snapshot."""
    row = (
        (
            await connection.execute(
                text(
                    "SELECT strategy_id, strategy_fingerprint, strategy_name, status "
                    "FROM deployments WHERE id = :id"
                ),
                {"id": _LIVE_ID},
            )
        )
        .mappings()
        .one()
    )
    assert row["strategy_id"] is None
    assert row["strategy_fingerprint"] == expected_fingerprint
    assert row["strategy_name"] == "BTC hourly EMA trend"
    assert row["status"] == "stopped"
    live = f"'{_LIVE_ID}'"
    for table in (
        "order_intents",
        "execution_orders",
        "execution_fills",
        "execution_positions",
        "execution_instrument_state",
        "trade_reason_records",
    ):
        minimum = 2 if table == "execution_orders" else 1
        assert (
            await _count(connection, f"SELECT COUNT(*) FROM {table} WHERE deployment_id = {live}")  # noqa: S608
            >= minimum
        )
    reason_fingerprint = (
        await connection.execute(
            text("SELECT strategy_fingerprint FROM trade_reason_records WHERE deployment_id = :id"),
            {"id": _LIVE_ID},
        )
    ).scalar_one()
    assert reason_fingerprint == expected_fingerprint != legacy_fingerprint
    snapshot = (
        (
            await connection.execute(
                text("SELECT strategy_id, canonical_definition FROM strategy_snapshots")
            )
        )
        .mappings()
        .one()
    )
    assert snapshot["strategy_id"] is None
    assert '"version"' not in snapshot["canonical_definition"]
    assert '"status"' not in snapshot["canonical_definition"]
    assert (
        await _count(
            connection,
            f"SELECT COUNT(*) FROM deployments WHERE id = '{_LIVE_DISCRETIONARY_ID}'",  # noqa: S608
        )
        == 1
    )


async def _verify_policy(connection: AsyncConnection, legacy_policy: str) -> None:
    """A new policy version without the wiped allocation is active; history is kept."""
    active = (
        (
            await connection.execute(
                text(
                    "SELECT p.policy_fingerprint, p.version, p.canonical_definition "
                    "FROM active_risk_policy a JOIN published_risk_policies p "
                    "ON p.policy_fingerprint = a.policy_fingerprint"
                )
            )
        )
        .mappings()
        .one()
    )
    assert active["policy_fingerprint"] != legacy_policy
    assert active["version"] == 2
    definition = definition_from_stored_json(active["canonical_definition"])
    assert definition.allocations == ()
    assert risk_policy_fingerprint(definition) == active["policy_fingerprint"]
    assert await _count(connection, "SELECT COUNT(*) FROM published_risk_policies") == 2


def test_0048_refuses_while_a_live_strategy_book_is_running(scratch_database: str) -> None:
    """A running live strategy deployment blocks the reshape instead of being orphaned."""
    assert _alembic(scratch_database, "0047").returncode == 0
    definition = create_template_strategy(now=_NOW)

    async def seed() -> None:
        engine = create_async_engine(scratch_database)
        try:
            async with engine.begin() as connection:
                fingerprint = await _seed_strategy_rows(connection, definition)
                await _seed_book(
                    connection,
                    deployment_id=_LIVE_ID,
                    mode="live",
                    status="running",
                    definition=definition,
                    fingerprint=fingerprint,
                )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    refused = _alembic(scratch_database, "head")
    assert refused.returncode != 0
    assert "Stop every running or paused live strategy deployment" in refused.stderr


def test_migrated_schema_matches_application_metadata(scratch_database: str) -> None:
    """After 0048 the database structure equals schema.py (comment-only drift excluded)."""
    assert _alembic(scratch_database, "head").returncode == 0

    def structural_diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection)
        return [
            item for item in compare_metadata(context, metadata) if not _is_comment_change(item)
        ]

    async def compare() -> list[object]:
        engine = create_async_engine(scratch_database)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(structural_diff)
        finally:
            await engine.dispose()

    assert asyncio.run(compare()) == []


def _is_comment_change(item: object) -> bool:
    """True for autogenerate entries that only change table or column comments."""
    entries = item if isinstance(item, list) else [item]
    return all(
        isinstance(entry, tuple)
        and isinstance(entry[0], str)
        and entry[0] in {"modify_comment", "add_table_comment", "remove_table_comment"}
        for entry in entries
    )
