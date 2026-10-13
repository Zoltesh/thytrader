"""Prospective observation is exercised through the real execution worker, not a dormant helper."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib
import json
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import Catalog, paper_book, strategy
from tests.replay.generate_fixtures import LifecycleVenue
from tests.test_replay_broker import SyntheticBroker
from tests.test_replay_cli import cli
from thytrader.execution_worker.service import _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.replay.capture import FileCapture, capture_scope
from thytrader.trading.models import DeploymentMode, Fill

if TYPE_CHECKING:
    from pathlib import Path


class EmptyVenue(SyntheticBroker):
    """A complete synthetic venue inventory with no orders or fills."""

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return an authoritative empty synthetic fill page."""
        del product_id, order_id
        return ()


@pytest.mark.anyio
@pytest.mark.parametrize("entry_signal", [False, True])
async def test_actual_worker_capture_publishes_cli_replay(
    tmp_path: Path, entry_signal: bool
) -> None:
    """The shipped worker processes a synthetic live book and publishes its observed input."""
    module = importlib.import_module("thytrader.execution_worker.strategy_step")
    assert hasattr(module, "captured_closed_bar"), "actual worker capture is not wired"

    definition = strategy(
        when={
            "all": [
                {
                    "left": {"literal": "2" if entry_signal else "0"},
                    "operator": "greater_than",
                    "right": {"literal": "1"},
                }
            ]
        }
    )
    store, before = await paper_book(definition)
    await store.save_deployment(
        replace(
            before.deployment,
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
    root = tmp_path / "capture"
    sink = FileCapture(root)
    venue = LifecycleVenue()
    with capture_scope(sink):
        await _run_cycle(
            store=store,
            publication_store=Catalog(definition),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=EmptyVenue(),
            live_broker=venue,
            quote_reader=None,
            risk_store=None,
        )
    assert sink.failure_count == 0
    manifests = list(root.glob("*/manifest.json"))
    assert len(manifests) == 1
    captured = json.loads((manifests[0].parent / "evidence.json").read_text())
    assert isinstance(captured["source"]["execution_build"], str), (
        "source build identity is missing"
    )
    assert captured["source"]["execution_build"].startswith("sha256:")
    assert captured["source"]["research_build"] is None
    measured = captured["steps"][0]["timing"]
    assert measured["computation_cpu_ns"] >= 0
    assert measured["elapsed_ns"] >= 0
    assert measured["queue_wait_ns"] is None
    assert "QUEUE_BOUNDARY_UNAVAILABLE" in measured["unavailable_reasons"]
    report = cli("run", str(manifests[0]))
    assert report.returncode == 3, report.stderr
    result = json.loads(report.stdout)
    assert result["execution"]["status"] == "match"
    assert result["evidence_kind"] == "prospective"
    if entry_signal:
        assert venue.count == 1, "actual worker did not send the signal-bearing entry exactly once"
        assert len(result["execution"]["steps"][0]["actual"]["orders"]) == 1
        mutations = [
            event
            for event in captured["steps"][0]["broker_events"]
            if event["operation"] == "place"
        ]
        assert len(mutations) == 1
        assert mutations[0]["started_at"] <= mutations[0]["completed_at"]
        assert result["boundaries"]["sizing_risk"]["status"] == "unavailable"


def test_worker_capture_has_explicit_contributor_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real worker entry point reads only the named opt-in private capture root."""
    module = importlib.import_module("thytrader.execution_worker.cli")
    assert hasattr(module, "configured_replay_capture"), (
        "capture has no supported installation wiring"
    )
    monkeypatch.setenv("THYTRADER_REPLAY_CAPTURE_ROOT", str(tmp_path / "private-capture"))
    configured = module.configured_replay_capture()
    assert configured is not None
    assert configured.root == tmp_path / "private-capture"
    assert not configured.root.exists()
    monkeypatch.delenv("THYTRADER_REPLAY_CAPTURE_ROOT")
    assert module.configured_replay_capture() is None
