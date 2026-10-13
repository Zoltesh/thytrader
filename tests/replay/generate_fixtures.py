"""Regenerate public synthetic replay vectors; financial anchors are asserted independently.

Run explicitly with ``uv run python -m tests.replay.generate_fixtures``. No account/network
inputs or settings are read. Fixture inputs and full expected outputs remain checked-in JSON.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from tests.test_replay_broker import SyntheticBroker
from tests.test_replay_validation import auxiliary_payload
from thytrader.exchanges.fees import FeeProfile
from thytrader.execution.broker import SubmitResult
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.replay.broker import RecordingBroker
from thytrader.replay.execution import outcome, seed_store
from thytrader.replay.models import ReplayPackage, canonical_bytes
from thytrader.replay.packages import Manifest
from thytrader.replay.runner import ReplayReport, run
from thytrader.replay.steps import CandleWindow, ReplayStep
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.runtime_inputs import InputTape, RuntimeInput, runtime_input_scope
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.trading.entry_latch import entry_inhibition_scope
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Callable

NOW = datetime(2026, 1, 3, 3, 1, tzinfo=UTC)
START = datetime(2026, 1, 1, tzinfo=UTC)
ROOT = Path(__file__).parent


class FixedInputs(InputTape):
    """Generate a deterministic synthetic clock/UUID schedule, unrelated to account identities."""

    def __init__(self, now: datetime) -> None:
        """Pin the observation instant separately for each causally ordered seam."""
        super().__init__()
        self.now = now

    def resolve(self, kind: Literal["clock", "uuid"], default: Callable[[], str]) -> str:
        """Record one constant synthetic time or unique UUIDv7 at that exact time."""
        del default
        value = (
            self.now.isoformat().replace("+00:00", "Z")
            if kind == "clock"
            else str(
                UUID(
                    int=(int(self.now.timestamp() * 1000) << 80)
                    | (7 << 76)
                    | (2 << 62)
                    | len(self.events)
                )
            )
        )
        self.events.append(RuntimeInput(kind, value))
        return value


class LifecycleVenue(SyntheticBroker):
    """Explicit round-number broker facts, never an OHLC fill simulator."""

    def __init__(self) -> None:
        """Start with no executions and separately scheduled response facts."""
        self.count = 0
        self.observed: dict[str, SubmitResult] = {}
        self.executions: dict[str, tuple[Fill, ...]] = {}

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Acknowledge exactly once without fabricating an immediate fill."""
        del client_order_id, product_id, side, kind, quantity, price
        del stop_trigger_price, take_profit_price
        self.count += 1
        venue = f"synthetic-lifecycle-{self.count}"
        result = SubmitResult(OrderStatus.OPEN, venue)
        self.observed[venue] = result
        return result

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Return only the explicit scheduled order state."""
        del client_order_id
        return self.observed[venue_order_id]

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Cancel only the requested synthetic identity."""
        del client_order_id
        result = SubmitResult(OrderStatus.CANCELED, venue_order_id)
        self.observed[venue_order_id] = result
        return result

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Repeat already observed executions to prove once-only accounting."""
        del product_id
        if order_id is not None:
            return self.executions.get(order_id, ())
        return tuple(fill for values in self.executions.values() for fill in values)


def window(hours: int, count: int) -> tuple[Candle, ...]:
    """Constant ten-price bars have independently known ATR=1, RSI=50 and high=10.5."""
    return tuple(
        Candle(
            START + timedelta(hours=hours * i),
            Decimal("10"),
            Decimal("10.5"),
            Decimal("9.5"),
            Decimal("10"),
            Decimal("10"),
        )
        for i in range(count)
    )


def definition() -> StrategyDefinition:
    """Public ETH-USDC strategy reads BTC-USDC and HTF/extra clocks with exact round sizing."""
    data = auxiliary_payload()["strategy"]
    data["strategy_id"] = "019b76da-a800-7000-8000-000000000010"
    data["instrument"] = {
        "product_id": "ETH-USDC",
        "base_currency": "ETH",
        "quote_currency": "USDC",
    }
    data["data_requirements"]["reference_instruments"][0]["product_id"] = "BTC-USDC"
    data["entry"]["when"] = {
        "all": [{"left": {"literal": "1"}, "operator": "greater_than", "right": {"literal": "0"}}]
    }
    data["entry"]["cooldown_bars"] = 0
    data["sizing"]["risk_fraction"] = "0.04"
    data["portfolio_limits"]["max_strategy_exposure_fraction"] = "0.5"
    data["exits"]["take_profit"] = {"kind": "none"}
    data["exits"]["trailing_stop"] = {
        "enabled": True,
        "kind": "atr_multiple",
        "atr_indicator": "atr",
        "multiple": "1",
    }
    return StrategyDefinition.model_validate_json(json.dumps(data))


async def lifecycle() -> ReplayPackage:
    """Derive lifecycle identities with actual orchestration, pin independent geometry/economics."""
    model = definition()
    product = MarketProduct(
        "ETH-USDC",
        "ETH",
        "USDC",
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("1"),
        True,
    )
    book_id = UUID("019b76da-a800-7000-8000-000000000011")
    before = DeploymentSnapshot(
        deployment=Deployment(
            id=book_id,
            strategy_fingerprint=strategy_fingerprint(model),
            product_id="ETH-USDC",
            mode=DeploymentMode.LIVE,
            phase=RuntimePhase.FLAT,
            status=DeploymentStatus.RUNNING,
            cash=Decimal("0"),
            allocated_capital=Decimal("100"),
            performance_capital_quote=Decimal("100"),
            initial_equity=Decimal("0"),
            baseline_equity=Decimal("0"),
            high_water_mark_equity=Decimal("0"),
            venue_available_quote=Decimal("100"),
            created_at=START,
            updated_at=START,
            strategy_id=model.strategy_id,
            timeframe="1h",
        ),
        position=None,
        intents=(),
        orders=(),
        fills=(),
    )
    venue = LifecycleVenue()
    steps: list[ReplayStep] = []
    fee_profile = FeeProfile(
        maker_fee_rate=Decimal("0.005"),
        taker_fee_rate=Decimal("0.005"),
        usd_volume_30d=Decimal("0"),
        fee_tier="synthetic",
        as_of=NOW,
    )

    async def advance(operation: str, bars: tuple[Candle, ...] = ()) -> DeploymentSnapshot:
        """Record one seam from the preceding actual outcome, not independently invented books."""
        nonlocal before
        store = await seed_store(before)
        at = NOW + timedelta(hours=2 if len(steps) >= 2 else 0)
        tape = FixedInputs(at)
        broker = RecordingBroker(venue)
        extra_count = 26 if len(steps) >= 2 else 25
        extras = (
            CandleWindow(
                key="2h", product_id="ETH-USDC", timeframe="2h", candles=window(2, extra_count)
            ),
        )
        references = (
            CandleWindow(
                key="btc_reference",
                product_id="BTC-USDC",
                timeframe="2h",
                candles=window(2, extra_count),
            ),
        )
        htf = window(4, 13 if len(steps) >= 2 else 12)
        with runtime_input_scope(tape), entry_inhibition_scope(DeploymentMode.LIVE, False):
            if operation == "closed_bar":
                after = await process_closed_bar(
                    before,
                    strategy=model,
                    product=product,
                    candles=bars,
                    broker=broker,
                    store=store,
                    risk_policy=compiled_default_risk_policy(),
                    portfolio=(),
                    htf_candles=htf,
                    indicator_timeframe_candles={"2h": extras[0].candles},
                    reference_candles={"btc_reference": references[0].candles},
                    fee_profile=fee_profile,
                    allow_new_entries=True,
                )
            else:
                after = await reconcile_open_orders(
                    before, broker=broker, store=store, cooldown_bars=0
                )
        events = tuple(
            event.model_copy(update={"started_at": at, "completed_at": at})
            for event in broker.events
        )
        steps.append(
            ReplayStep(
                operation=operation,
                before=before,
                product=product,
                data_available_at=at,
                evaluation_started_at=at,
                decision_completed_at=at,
                candles=bars,
                htf_candles=htf if bars else (),
                indicator_windows=extras if bars else (),
                reference_windows=references if bars else (),
                fee_profile=fee_profile,
                risk_policy=compiled_default_risk_policy(),
                allow_new_entries=True,
                entry_inhibited=False,
                runtime_inputs=tuple(tape.events),
                broker_events=events,
                expected=outcome(after),
            )
        )
        before = after
        return after

    await advance("closed_bar", window(1, 51))
    assert before.orders, repr(before.deployment)
    entry = before.orders[0]
    assert entry.quantity == Decimal("2") and entry.price == Decimal("10"), repr(entry)

    def fill(index: int, order_id: UUID, venue_id: str, price: str, qty: str, fee: str) -> Fill:
        """Explicit independent executions, not prices inferred from observed candle highs/lows."""
        return Fill(
            id=UUID(int=(int(NOW.timestamp() * 1000) << 80) | (7 << 76) | (2 << 62) | index),
            deployment_id=book_id,
            order_id=order_id,
            venue_fill_id=f"synthetic-fill-{index}",
            venue_order_id=venue_id,
            price=Decimal(price),
            quantity=Decimal(qty),
            fee=Decimal(fee),
            filled_at=NOW if index == 1001 else NOW + timedelta(hours=2),
        )

    entry_venue = entry.venue_order_id
    assert entry_venue is not None
    venue.observed[entry_venue] = SubmitResult(
        OrderStatus.FILLED, entry_venue, filled_quantity=Decimal("2")
    )
    venue.executions[entry_venue] = (fill(1001, entry.id, entry_venue, "10", "2", "0.1"),)
    await advance("reconcile")
    assert before.deployment.cash == Decimal("-20.1")
    await advance("closed_bar", window(1, 53))
    assert before.position is not None and before.position.stop_price == Decimal("9.5")
    stop = before.orders[-1]
    stop_venue = stop.venue_order_id
    assert stop_venue is not None
    partial = fill(1002, stop.id, stop_venue, "9.2", "0.5", "0.01")
    venue.observed[stop_venue] = SubmitResult(
        OrderStatus.OPEN, stop_venue, filled_quantity=Decimal("0.5")
    )
    venue.executions[stop_venue] = (partial,)
    await advance("reconcile")
    assert before.deployment.cash == Decimal("-15.51")
    assert before.position is not None and before.position.quantity == Decimal("1.5")
    await advance("reconcile")
    assert before.deployment.cash == Decimal("-15.51") and len(before.fills) == 2
    venue.observed[stop_venue] = SubmitResult(
        OrderStatus.FILLED, stop_venue, filled_quantity=Decimal("2")
    )
    venue.executions[stop_venue] = (
        partial,
        fill(1003, stop.id, stop_venue, "9.1", "0.5", "0.01"),
        fill(1004, stop.id, stop_venue, "9", "1", "0.02"),
    )
    await advance("reconcile")
    assert before.position is None and before.deployment.cash == Decimal("-1.99")
    return ReplayPackage.model_validate_json(
        json.dumps(
            {
                "schema_version": "thytrader-replay-v1",
                "evidence_kind": "synthetic",
                "source": {"execution_build": "independent-synthetic", "research_build": None},
                "strategy": model.model_dump(mode="json", by_alias=True),
                "original_strategy": None,
                "steps": [step.model_dump(mode="json") for step in steps],
                "ledger": {
                    "starting_cash": "0",
                    "observed_ending_cash": "-1.99",
                    "fills": [
                        {
                            "fill_id": f"synthetic-fill-{i}",
                            "side": side,
                            "price": price,
                            "quantity": qty,
                            "fee": fee,
                            "filled_at": (
                                NOW if i == 1001 else NOW + timedelta(hours=2)
                            ).isoformat(),
                        }
                        for i, side, price, qty, fee in [
                            (1001, "buy", "10", "2", "0.1"),
                            (1002, "sell", "9.2", "0.5", "0.01"),
                            (1003, "sell", "9.1", "0.5", "0.01"),
                            (1004, "sell", "9", "1", "0.02"),
                        ]
                    ],
                },
                "unavailable_reasons": ["ORIGINAL_RESEARCH_MISSING"],
            }
        )
    )


async def main() -> None:
    """Write actual derived evidence only after independent round-number acceptance assertions."""
    package = await lifecycle()
    (ROOT / "fixtures" / "usdc-lifecycle.input.json").write_bytes(canonical_bytes(package))
    report = await run(package)
    assert report.execution.status == "match"
    assert report.ledger is not None and report.ledger.total_fees == "0.14"
    (ROOT / "fixtures" / "usdc-lifecycle.expected.json").write_text(report.model_dump_json() + "\n")
    (ROOT / "schemas").mkdir(exist_ok=True)
    for kind, model in (
        ("package", ReplayPackage),
        ("manifest", Manifest),
        ("report", ReplayReport),
    ):
        (ROOT / "schemas" / f"{kind}.schema.json").write_text(
            json.dumps(model.model_json_schema(mode="serialization"), indent=2, sort_keys=True)
            + "\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
