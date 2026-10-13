"""A portable trailing-stop vector exercises the shipped live protection orchestration."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import candles, product, strategy
from tests.test_replay_broker import SyntheticBroker
from tests.test_replay_cli import cli
from tests.test_replay_engines import json_default
from tests.test_replay_partial_exits import starting_book
from thytrader.execution.loop import process_closed_bar
from thytrader.replay.broker import RecordingBroker
from thytrader.replay.execution import outcome, seed_store
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.runtime_inputs import InputTape, runtime_input_scope
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.anyio
async def test_cli_replays_real_trailing_ratchet_and_protection(tmp_path: Path) -> None:
    """Captured trailing computation and stop-order geometry replay through the public CLI."""
    definition = strategy()
    payload = definition.model_dump(mode="python", by_alias=True)
    payload["exits"]["take_profit"] = {"kind": "none"}
    payload["exits"]["trailing_stop"] = {
        "enabled": True,
        "kind": "atr_multiple",
        "atr_indicator": "atr",
        "multiple": "1",
    }
    definition = StrategyDefinition.model_validate(payload)
    before = starting_book()
    before = replace(
        before,
        deployment=replace(
            before.deployment,
            strategy_fingerprint=strategy_fingerprint(definition),
            strategy_id=definition.strategy_id,
        ),
        orders=before.orders[:1],
        intents=before.intents[:1],
    )
    window = candles(51, base="10", step="0.05")
    store = await seed_store(before)
    broker = RecordingBroker(SyntheticBroker())
    tape = InputTape()
    started = datetime.now(UTC)
    with runtime_input_scope(tape):
        after = await process_closed_bar(
            before,
            strategy=definition,
            product=product(),
            candles=window,
            broker=broker,
            store=store,
            allow_new_entries=False,
        )
    ended = datetime.now(UTC)
    assert after.position is not None
    assert after.position.stop_price > Decimal("8")
    assert after.position.trail_extreme == window[-1].high
    assert len(after.orders) == 2
    data = {
        "schema_version": "thytrader-replay-v1",
        "evidence_kind": "synthetic",
        "source": {"execution_build": "independent-synthetic", "research_build": None},
        "strategy": definition.model_dump(mode="json", by_alias=True),
        "original_strategy": None,
        "ledger": None,
        "unavailable_reasons": ["ORIGINAL_RESEARCH_MISSING"],
        "steps": [
            {
                "operation": "closed_bar",
                "before": asdict(before),
                "product": asdict(product()),
                "candles": [asdict(c) for c in window],
                "data_available_at": started,
                "evaluation_started_at": started,
                "decision_completed_at": ended,
                "allow_new_entries": False,
                "risk_policy": compiled_default_risk_policy().model_dump(
                    mode="json", by_alias=True
                ),
                "runtime_inputs": [asdict(e) for e in tape.events],
                "broker_events": [e.model_dump(mode="json") for e in broker.events],
                "expected": outcome(after).model_dump(mode="json"),
            }
        ],
    }
    source = tmp_path / "trailing.json"
    source.write_text(json.dumps(data, default=json_default), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    result = cli("run", json.loads(exported.stdout)["manifest"])
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["execution"]["status"] == "match"
