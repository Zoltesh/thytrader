"""Evidence comparisons name their exact scope and retain unavailable execution inputs."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from tests.test_replay_cli import cli
from tests.test_replay_partial_exits import EXIT, NOW, starting_book
from thytrader.replay.codec import validate_no_coercion
from thytrader.replay.execution import outcome, seed_store
from thytrader.replay.packages import read_input
from thytrader.replay.runner import run
from thytrader.replay.steps import comparable
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.models import DeploymentStatus, OrderStatus

FIXTURES = Path(__file__).parent / "replay" / "fixtures"


@pytest.mark.anyio
async def test_signal_agreement_does_not_certify_sizing_orders_or_build() -> None:
    """Equal current evaluators do not establish original build or execution economics parity."""
    package = read_input(FIXTURES / "no-signal.input.json")
    report = await run(package)
    data = json.loads(report.model_dump_json())
    assert data["same_config_research_execution"]["scope"] == "indicators_and_entry_signal_only"
    assert data["boundaries"]["sizing_risk"]["scope"] == "isolated_recorded_outcome_only"
    assert data["boundaries"]["orders_fills"]["status"] == "match"
    assert data["build_compatibility"]["status"] == "unavailable"
    assert "CURRENT_BUILD_DIFFERENTIAL_IS_NOT_ORIGINAL_BUILD_PARITY" in data["limitations"]


@pytest.mark.anyio
async def test_capture_limitations_survive_report_derivation() -> None:
    """Recorded dynamic-risk omissions cannot disappear behind a successful seam replay."""
    package = read_input(FIXTURES / "no-signal.input.json")
    package = package.model_copy(
        update={
            "unavailable_reasons": (
                "DYNAMIC_RISK_READS_NOT_CAPTURED",
                "PRE_BAR_RECONCILIATION_NOT_CAPTURED",
            )
        }
    )
    report = await run(package)
    assert "DYNAMIC_RISK_READS_NOT_CAPTURED" in report.limitations
    assert "PRE_BAR_RECONCILIATION_NOT_CAPTURED" in report.limitations
    assert report.boundaries.sizing_risk.status == "unavailable"


def test_text_report_names_signal_only_scope(tmp_path: Path) -> None:
    """Text users see evidence qualifications without having to inspect the schema."""
    exported = cli(
        "export",
        "--input",
        str(FIXTURES / "no-signal.input.json"),
        "--root",
        str(tmp_path / "packages"),
    )
    manifest = json.loads(exported.stdout)["manifest"]
    result = cli("run", manifest, "--format", "text")
    assert "Sizing/risk" in result.stdout
    assert "Orders/fills" in result.stdout
    assert "Build compatibility: unavailable" in result.stdout
    assert "Original comparison scope: original_research_evidence" in result.stdout


def test_risk_outcome_preserves_pause_and_latch_state() -> None:
    """Equal order cash cannot conceal a different risk pause or breaker outcome."""
    package = read_input(FIXTURES / "no-signal.input.json")
    before = package.steps[0].before
    paused = replace(
        before,
        deployment=replace(
            before.deployment,
            status=DeploymentStatus.PAUSED,
            daily_loss_latched=True,
            drawdown_latched=True,
        ),
    )
    data = json.loads(outcome(paused).model_dump_json())
    assert data["deployment_status"] == "paused"
    assert data["daily_loss_latched"] is True and data["drawdown_latched"] is True


@pytest.mark.anyio
async def test_report_exposes_normalized_method_boundaries_without_wire_claim() -> None:
    """Method invocation/response times stay observable, with queue/wire absence explicit."""
    package = read_input(FIXTURES / "usdc-lifecycle.input.json")
    data = json.loads((await run(package)).model_dump_json())
    timing = data["timing"][0]
    methods = [item for item in timing["broker_methods"] if item["operation"] == "place"]
    assert len(methods) == 1
    assert methods[0]["boundary"] == "normalized_broker_method_invocation_return"
    assert methods[0]["invoked_at"] <= methods[0]["returned_at"]
    assert "WIRE_SEND_ACK_UNAVAILABLE" in timing["unavailable_reasons"]
    assert "QUEUE_BOUNDARY_UNAVAILABLE" in timing["unavailable_reasons"]


@pytest.mark.anyio
async def test_original_rule_mismatch_keeps_missing_economics_build_visible() -> None:
    """A known rules change is not a known historical economic/build comparison."""
    package = read_input(FIXTURES / "no-signal.input.json")
    assert package.strategy is not None
    data = package.strategy.model_dump(mode="python", by_alias=True)
    data["sizing"]["risk_fraction"] = "0.01"
    original = StrategyDefinition.model_validate(data)
    report = await run(package.model_copy(update={"original_strategy": original}))
    assert report.original_research_deployed.scope == "original_strategy_rules_only"
    assert (
        "ORIGINAL_CAPITAL_COST_BUILD_EVIDENCE_MISSING" in report.original_research_deployed.reasons
    )


def test_only_declared_unset_order_timestamps_may_vanish_from_input() -> None:
    """An explicit null cancel time or settlement is fine (ADR 0141); other unknowns are not."""
    validate_no_coercion({"canceled_at": None, "settled_at": None}, {})
    validate_no_coercion({"next_tier": None, "qualification": None}, {})
    with pytest.raises(ValueError, match="unknown replay field"):
        validate_no_coercion({"bogus_at": None}, {})
    with pytest.raises(ValueError, match="unknown replay field"):
        validate_no_coercion({"canceled_at": "2026-10-15T00:00:00Z"}, {})


@pytest.mark.anyio
async def test_a_cancel_captured_before_its_time_was_recorded_round_trips() -> None:
    """Replay restores a pre-0141 cancel exactly and compares it without the newer fields."""
    book = starting_book()
    orders = tuple(
        replace(order, status=OrderStatus.CANCELED) if order.id == EXIT else order
        for order in book.orders
    )
    captured = replace(book, orders=orders)
    store = await seed_store(captured)
    restored = await store.get_deployment(captured.deployment.id)
    assert {order.id: order for order in restored.orders} == {
        order.id: order for order in captured.orders
    }
    expected = outcome(captured)
    stamped = tuple(
        replace(order, canceled_at=NOW, settled_at=NOW) if order.id == EXIT else order
        for order in orders
    )
    actual = outcome(replace(captured, orders=stamped))
    assert comparable(actual, expected) == expected
    recorded = outcome(replace(captured, orders=tuple(replace(o, canceled_at=NOW) for o in orders)))
    assert comparable(actual, recorded) == actual
    working = outcome(replace(captured, orders=book.orders))
    stamped_working = outcome(
        replace(captured, orders=tuple(replace(o, canceled_at=NOW) for o in book.orders))
    )
    assert comparable(stamped_working, working) != working
