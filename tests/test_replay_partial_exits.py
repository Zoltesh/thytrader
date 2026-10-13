"""Canonical partial exits exercise actual reconciliation and once-only accounting through CLI."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tests.execution.decision_support import product, strategy
from tests.test_replay_broker import SyntheticBroker
from tests.test_replay_cli import cli
from tests.test_replay_engines import json_default
from thytrader.execution.broker import SubmitResult
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.replay.broker import RecordingBroker
from thytrader.replay.execution import outcome, seed_store
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.runtime_inputs import InputTape, runtime_input_scope
from thytrader.strategies.models import strategy_fingerprint
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BOOK = UUID("019b76da-a800-7000-8000-000000000001")
ENTRY = UUID("019b76da-a800-7000-8000-000000000002")
EXIT = UUID("019b76da-a800-7000-8000-000000000003")


def starting_book() -> DeploymentSnapshot:
    """Independent synthetic long book with authoritative entry economics and a resting stop."""
    definition = strategy()
    deployment = Deployment(
        id=BOOK,
        strategy_fingerprint=strategy_fingerprint(definition),
        strategy_id=definition.strategy_id,
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("-20.1"),
        phase=RuntimePhase.OPEN,
        created_at=NOW,
        updated_at=NOW,
        allocated_capital=Decimal("100"),
        pending_stop_price=Decimal("8"),
        timeframe="1h",
    )
    entry_intent = OrderIntent(
        id=ENTRY,
        deployment_id=BOOK,
        client_order_id="synthetic-entry",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("2"),
        price=Decimal("10"),
        created_at=NOW,
        candle_starts_at=NOW,
        product_id="BTC-USD",
    )
    exit_intent = replace(
        entry_intent,
        id=EXIT,
        client_order_id="synthetic-exit",
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        price=Decimal("7.9"),
        stop_trigger_price=Decimal("8"),
    )
    entry_order = Order(
        id=ENTRY,
        deployment_id=BOOK,
        intent_id=ENTRY,
        client_order_id="synthetic-entry",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("2"),
        price=Decimal("10"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("2"),
        created_at=NOW,
        updated_at=NOW,
        venue_order_id="synthetic-entry",
        product_id="BTC-USD",
        venue_observed_at=NOW,
    )
    exit_order = replace(
        entry_order,
        id=EXIT,
        intent_id=EXIT,
        client_order_id="synthetic-exit",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("2"),
        price=Decimal("7.9"),
        stop_trigger_price=Decimal("8"),
        status=OrderStatus.OPEN,
        filled_quantity=Decimal("0"),
        venue_order_id="synthetic-exit",
    )
    fill = Fill(
        id=ENTRY,
        deployment_id=BOOK,
        order_id=ENTRY,
        venue_fill_id="synthetic-entry-fill",
        price=Decimal("10"),
        quantity=Decimal("2"),
        fee=Decimal("0.1"),
        filled_at=NOW,
        venue_order_id="synthetic-entry",
        economics_applied_at=NOW,
    )
    position = Position(
        deployment_id=BOOK,
        quantity=Decimal("2"),
        entry_price=Decimal("10"),
        stop_price=Decimal("8"),
        target_price=None,
        entered_bar=NOW,
        updated_at=NOW,
        product_id="BTC-USD",
    )
    return DeploymentSnapshot(
        deployment=deployment,
        position=position,
        positions=(position,),
        intents=(entry_intent, exit_intent),
        orders=(entry_order, exit_order),
        fills=(fill,),
    )


class PartialExitVenue(SyntheticBroker):
    """Normalized independent synthetic broker responses, not a simulator or network adapter."""

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Observe completion with the venue's executed quantity."""
        del client_order_id
        return SubmitResult(OrderStatus.FILLED, venue_order_id, filled_quantity=Decimal("2"))

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return each exit execution fragment individually, including repeated observations."""
        del product_id
        if order_id != "synthetic-exit":
            return ()
        return tuple(
            Fill(
                id=UUID(int=100 + i),
                deployment_id=BOOK,
                order_id=EXIT,
                venue_fill_id=f"synthetic-partial-{i}",
                price=Decimal(price),
                quantity=Decimal(quantity),
                fee=Decimal(fee),
                filled_at=NOW,
                venue_order_id="synthetic-exit",
            )
            for i, (price, quantity, fee) in enumerate(
                [("9.2", "0.5", "0.01"), ("9.1", "0.5", "0.01"), ("9", "1", "0.02")]
            )
        )


@pytest.mark.anyio
async def test_cli_reconciles_partial_exit_fees_once(tmp_path: Path) -> None:
    """The real reconciler closes the synthetic loss; the CLI independently reruns it."""
    definition = strategy()
    before = starting_book()
    # Keep the same independent strategy identity in this book and in the package.
    before = replace(
        before,
        deployment=replace(
            before.deployment,
            strategy_fingerprint=strategy_fingerprint(definition),
            strategy_id=definition.strategy_id,
        ),
    )
    store = await seed_store(before)
    broker = RecordingBroker(PartialExitVenue())
    tape = InputTape()
    started = datetime.now(UTC)
    with runtime_input_scope(tape):
        after = await reconcile_open_orders(before, broker=broker, store=store, cooldown_bars=0)
    ended = datetime.now(UTC)
    assert after.position is None
    assert after.deployment.cash == Decimal("-1.99")
    assert len(after.fills) == 4
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
                "expected": outcome(after).model_dump(mode="json"),
            }
        ],
    }
    source = tmp_path / "partial-exits.json"
    source.write_text(json.dumps(payload, default=json_default), encoding="utf-8")
    exported = cli("export", "--input", str(source), "--root", str(tmp_path / "packages"))
    assert exported.returncode == 0, exported.stderr
    result = cli("run", json.loads(exported.stdout)["manifest"])
    assert result.returncode == 3, result.stderr
    report = json.loads(result.stdout)
    assert report["execution"]["status"] == "match"
    assert report["execution"]["steps"][0]["actual"]["cash"] == "-1.99"
