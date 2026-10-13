"""Offline replay invokes existing signal and execution engines, never a shadow implementation."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tests.execution.decision_support import candles, paper_book, product, strategy
from tests.test_replay_cli import cli
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.runtime_inputs import InputTape, runtime_input_scope

if TYPE_CHECKING:
    from pathlib import Path


def json_default(value: object) -> str:
    """Encode exact typed domain input as language-independent data for the CLI."""
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError("unsupported fixture value")


@pytest.mark.anyio
async def test_cli_runs_real_engines_on_identical_captured_inputs(tmp_path: Path) -> None:
    """A descending no-signal window independently expects no orders and unchanged cash."""
    definition = strategy()
    store, before = await paper_book(definition)
    window = candles(definition.data_requirements.warmup_bars + 1, step="-1", base="100")
    tape = InputTape()
    started = datetime.now(UTC)
    with runtime_input_scope(tape):
        await process_closed_bar(
            before,
            strategy=definition,
            product=product(),
            candles=window,
            store=store,
            broker=PaperBroker(),
            allow_new_entries=False,
        )
    ended = datetime.now(UTC)
    # Any is confined to a JSON fixture authoring boundary, not production domain logic.
    payload: dict[str, Any] = {
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
                "data_available_at": started,
                "evaluation_started_at": started,
                "decision_completed_at": ended,
                "candles": [asdict(c) for c in window],
                "risk_policy": compiled_default_risk_policy().model_dump(
                    mode="json", by_alias=True
                ),
                "allow_new_entries": False,
                "runtime_inputs": [asdict(e) for e in tape.events],
                "broker_events": [],
                "expected": {
                    "cash": "10000",
                    "phase": "flat",
                    "positions": [],
                    "orders": [],
                    "fills": [],
                },
            }
        ],
    }
    source = tmp_path / "engine-input.json"
    source.write_text(json.dumps(payload, default=json_default), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    manifest = json.loads(exported.stdout)["manifest"]
    result = cli("run", manifest)
    assert result.returncode == 3, result.stderr
    report = json.loads(result.stdout)
    assert report["execution"]["status"] == "match"
    assert report["execution"]["steps"][0]["actual"]["cash"] == "10000"
    assert report["same_config_research_execution"]["status"] == "match"
    assert report["signals"][0]["entry_condition"] == "not_matched"
