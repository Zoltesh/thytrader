"""Prospective replay input observation through the actual worker and migrated PostgreSQL store."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import os
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest

from tests.execution.decision_support import strategy
from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from tests.replay.generate_fixtures import LifecycleVenue
from tests.test_replay_cli import cli
from tests.test_replay_worker import EmptyVenue
from thytrader.execution.service import create_deployment
from thytrader.execution_worker.service import _process_one
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.replay.capture import FileCapture, capture_scope
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.trading.models import DeploymentMode, DeploymentStatus

__all__ = ["scratch_database"]

if TYPE_CHECKING:
    from pathlib import Path

URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(URL is None, reason="isolated test PostgreSQL required"),
]


async def test_worker_store_capture_replays_and_failure_does_not_repeat_sends(
    tmp_path: Path, scratch_database: str
) -> None:
    """A real fenced worker invocation persists progress despite a failed private capture write."""
    if URL is None:
        raise AssertionError("test PostgreSQL URL required")
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(SecretStr(scratch_database))
    execution = PostgresExecutionStore(engine)
    publications = PostgresStrategyStore(engine)
    try:
        definition = strategy(
            when={
                "all": [
                    {
                        "left": {"literal": "2"},
                        "operator": "greater_than",
                        "right": {"literal": "1"},
                    }
                ]
            }
        )
        record = await create_strategy_from_definition(publications, definition)
        snapshot = await publications.snapshot(record.strategy_id)
        for index in range(2):
            created = await create_deployment(
                store=execution,
                publication_store=publications,
                strategy_fingerprint=snapshot.strategy_fingerprint,
                mode=DeploymentMode.PAPER,
                paper_starting_cash=Decimal("10000"),
                live_allowed=False,
            )
            await execution.save_deployment(
                replace(
                    created,
                    mode=DeploymentMode.LIVE,
                    cash=Decimal("0"),
                    allocated_capital=Decimal("10000"),
                    venue_available_quote=Decimal("10000"),
                    initial_equity=Decimal("0"),
                    baseline_equity=Decimal("0"),
                    high_water_mark_equity=Decimal("0"),
                    paper_starting_cash=None,
                    paper_maker_fee_rate=None,
                    paper_taker_fee_rate=None,
                )
            )
            root = tmp_path / str(index)
            sink = FileCapture(root, max_packages=256 if index == 0 else 0)
            venue = LifecycleVenue()
            with capture_scope(sink):
                await _process_one(
                    deployment_id=created.id,
                    store=execution,
                    publication_store=publications,
                    market_data=MarketDataService(DemoMarketData()),
                    paper_broker=EmptyVenue(),
                    live_broker=venue,
                    quote_reader=None,
                    risk_policy=compiled_default_risk_policy(),
                    portfolio=(),
                    user_feed_store=None,
                    memory_store=None,
                )
            observed = await execution.get_deployment(created.id)
            assert observed.deployment.last_evaluated_bar is not None
            assert observed.deployment.cash == Decimal("0")
            assert len(observed.orders) == 1
            assert venue.count == 1, "capture persistence failure repeated a broker submit"
            if index == 0:
                assert sink.failure_count == 0
                manifests = list(root.glob("*/manifest.json"))
                assert len(manifests) == 1
                result = cli("run", str(manifests[0]))
                assert result.returncode == 3, result.stderr
                assert json.loads(result.stdout)["execution"]["status"] == "match"
                assert (
                    len(json.loads(result.stdout)["execution"]["steps"][0]["actual"]["orders"]) == 1
                )
            else:
                assert sink.failure_count == 1
                assert not list(root.glob("*/manifest.json"))
            await execution.save_deployment(
                replace(observed.deployment, status=DeploymentStatus.STOPPED)
            )
    finally:
        await dispose(engine)
