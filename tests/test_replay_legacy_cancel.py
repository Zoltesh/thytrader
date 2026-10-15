"""A package captured before ADR 0141 still replays to a match (cancel time and settlement)."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.decision_support import product, strategy
from tests.test_replay_cli import cli
from tests.test_replay_engines import json_default
from tests.test_replay_partial_exits import PartialExitVenue, starting_book
from thytrader.execution.broker import SubmitResult
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.replay.broker import RecordingBroker
from thytrader.replay.execution import outcome, seed_store
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.runtime_inputs import InputTape, runtime_input_scope
from thytrader.strategies.models import strategy_fingerprint
from thytrader.trading.models import OrderStatus

if TYPE_CHECKING:
    from pathlib import Path


class CanceledAfterFillingVenue(PartialExitVenue):
    """Reports the stop canceled although its fills cover the whole order."""

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Canceled at the venue, with every unit already executed."""
        del client_order_id
        return SubmitResult(OrderStatus.CANCELED, venue_order_id, filled_quantity=Decimal("2"))


def _without_cancel_fields(expected: dict[str, Any]) -> dict[str, Any]:
    """The outcome as a capture from before ADR 0141 recorded it."""
    orders = [
        {key: value for key, value in order.items() if key not in {"canceled_at", "settled_at"}}
        for order in expected["orders"]
    ]
    return {**expected, "orders": orders}


@pytest.mark.anyio
async def test_a_pre_0141_cancel_that_fills_replays_to_a_match(tmp_path: Path) -> None:
    """A canceled read whose fills promote the order to FILLED compares without the new fields."""
    definition = strategy()
    before = starting_book()
    before = replace(
        before,
        deployment=replace(
            before.deployment,
            strategy_fingerprint=strategy_fingerprint(definition),
            strategy_id=definition.strategy_id,
        ),
    )
    store = await seed_store(before)
    broker = RecordingBroker(CanceledAfterFillingVenue())
    tape = InputTape()
    started = datetime.now(UTC)
    with runtime_input_scope(tape):
        after = await reconcile_open_orders(before, broker=broker, store=store, cooldown_bars=0)
    ended = datetime.now(UTC)
    (stop,) = (order for order in after.orders if order.kind.value == "stop_limit")
    assert stop.status is OrderStatus.FILLED
    assert stop.canceled_at is not None
    expected = outcome(after).model_dump(mode="json")
    payload = {
        "schema_version": "thytrader-replay-v1",
        "evidence_kind": "synthetic",
        "source": {"execution_build": "independent-synthetic", "research_build": None},
        "strategy": definition.model_dump(mode="json", by_alias=True),
        "original_strategy": None,
        "ledger": None,
        "unavailable_reasons": ["ORIGINAL_RESEARCH_MISSING"],
        "steps": [
            {
                "operation": "reconcile",
                "before": asdict(before),
                "product": asdict(product()),
                "candles": [],
                "data_available_at": started,
                "evaluation_started_at": started,
                "decision_completed_at": ended,
                "allow_new_entries": False,
                "risk_policy": compiled_default_risk_policy().model_dump(
                    mode="json", by_alias=True
                ),
                "runtime_inputs": [asdict(e) for e in tape.events],
                "broker_events": [e.model_dump(mode="json") for e in broker.events],
                "expected": _without_cancel_fields(expected),
            }
        ],
    }
    source = tmp_path / "legacy-cancel.json"
    source.write_text(json.dumps(payload, default=json_default), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    result = cli("run", json.loads(exported.stdout)["manifest"])
    report = json.loads(result.stdout)
    assert report["execution"]["status"] == "match", report["execution"]
