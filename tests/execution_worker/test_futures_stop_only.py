"""Real validated strategy entries retain exact futures protection without a target."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

import pytest

from tests.execution.decision_support import Catalog
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_live_futures_entry import FuturesFees, clock
from tests.execution.test_paper_futures_books import (
    _PERP,
    _binding,
    _book,
    _candles,
    _Observations,
    _product,
    _record,
    _strategy,
)
from tests.worker_patching import patch_worker_global
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.execution_worker.service import _process_one
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.market_data.models import Candle, MarketProduct


class ProtectionVenue(FakeVenue):
    """Refuse valid protective requests only when a negative control enables it.

    Any models the fake transport's untrusted external JSON boundary.
    """

    reject_protection = False

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Preserve valid request generation while testing definite venue rejection."""
        configuration = (data or {}).get("order_configuration")
        if (
            self.reject_protection
            and isinstance(configuration, dict)
            and any(
                kind in configuration
                for kind in ("stop_limit_stop_limit_gtc", "trigger_bracket_gtc")
            )
        ):
            self.calls.append(("POST", path, dict(data or {})))
            return {"success": False, "error_response": {"error": "SYNTHETIC_PROTECTION_REFUSED"}}
        return super().post(path, data)


@pytest.mark.anyio
@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("with_target", [False, True])
async def test_valid_strategy_worker_protection(
    monkeypatch: pytest.MonkeyPatch,
    side: Literal["long", "short"],
    partial: bool,
    with_target: bool,
    rejected: bool,
) -> None:
    """Entry/fills create the position; paused partial-stop replacement stays protected."""

    async def window(
        *_args: object, **_kwargs: object
    ) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
        """Supply deterministic futures candles, not the spot-only demo provider."""
        return _product(), _candles(31), _candles(31)[-1].starts_at

    patch_worker_global(monkeypatch, "_closed_window", window)
    venue = ProtectionVenue()
    clock(monkeypatch, venue)
    payload = _strategy().model_dump(mode="python")
    payload["entry"]["side"] = side
    payload["exits"]["take_profit"] = (
        {"kind": "reward_risk", "multiple": "2"} if with_target else {"kind": "none"}
    )
    payload["sizing"]["min_quote_notional"] = "0.1"
    payload["sizing"]["max_quote_notional"] = "2.6"
    strategy = StrategyDefinition.model_validate(payload)
    store = InMemoryExecutionStore()
    opened = await _book(store, strategy)
    await store.save_deployment(
        replace(
            opened.deployment,
            mode=DeploymentMode.LIVE,
            cash=Decimal(0),
            initial_equity=Decimal(0),
            allocated_capital=Decimal(100),
            performance_capital_quote=Decimal(100),
        )
    )
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(opened))
    policy = compiled_default_risk_policy().model_copy(
        update={
            "futures": FuturesRiskPolicy(
                live_enabled=True,
                live_capital_usd="1000",
                product_allowlist=(_PERP,),
                max_order_contracts=2,
                live_spot_collateral_reserve_quote="1000",
            )
        }
    )

    async def cycle() -> DeploymentSnapshot:
        """Run the leased/revision-fenced worker with actual catalog, funding and adapter."""
        current = await store.get_deployment(opened.deployment.id)
        with (
            futures_runtime_scope(
                FuturesRuntime(
                    contracts,
                    _Observations((_record(venue.now - timedelta(hours=1), "0.001"),)),
                )
            ),
            live_futures_runtime_scope(FuturesLiveRuntime(venue)),
        ):
            await _process_one(
                deployment_id=current.deployment.id,
                store=store,
                publication_store=Catalog(strategy),
                market_data=MarketDataService(DemoMarketData()),
                paper_broker=PaperBroker(),
                live_broker=venue.broker(),
                quote_reader=FuturesFees(),
                risk_policy=policy,
                portfolio=(current,),
                user_feed_store=None,
                memory_store=None,
            )
        return await store.get_deployment(opened.deployment.id)

    entered = await cycle()
    assert len(entered.orders) == 1 and entered.orders[0].status is OrderStatus.OPEN
    assert entered.orders[0].venue_order_id is not None
    venue.fill(entered.orders[0].venue_order_id)
    venue.advance()
    current = await reconcile_open_orders(
        await store.get_deployment(opened.deployment.id),
        broker=venue.broker(),
        store=store,
    )
    assert current.position is not None
    assert (current.position.target_price is not None) == with_target
    if partial:
        stop = await submit_intent(
            store=store,
            broker=venue.broker(),
            deployment_id=current.deployment.id,
            product_id=_PERP,
            purpose=IntentPurpose.STOP,
            side=OrderSide.SELL if side == "long" else OrderSide.BUY,
            kind=OrderKind.STOP_LIMIT,
            quantity=current.position.quantity,
            price=current.position.stop_price
            * (Decimal("0.95") if side == "long" else Decimal("1.05")),
            stop_trigger_price=current.position.stop_price,
            candle=_candles(31)[-1],
        )
        assert stop.status is OrderStatus.OPEN and stop.venue_order_id is not None
        venue.fill(stop.venue_order_id, Decimal(1))
        venue.orders[stop.venue_order_id]["status"] = "OPEN"
        current = await store.get_deployment(opened.deployment.id)
        await store.save_deployment(replace(current.deployment, status=DeploymentStatus.PAUSED))
    venue.reject_protection = rejected
    protected = await cycle()
    assert protected.position is not None
    assert protected.position.quantity == Decimal("0.01" if partial else "0.02")
    assert protected.deployment.futures_flat_confirmation_after is None
    if partial:
        assert protected.deployment.status is DeploymentStatus.PAUSED
    working = [
        order
        for order in protected.orders
        if order.kind is (OrderKind.TRIGGER_BRACKET if with_target else OrderKind.STOP_LIMIT)
        and order.status is OrderStatus.OPEN
    ]
    if rejected:
        assert not working
        assert protected.deployment.status is DeploymentStatus.PAUSED
        assert protected.orders[-1].reject_reason == "SYNTHETIC_PROTECTION_REFUSED"
        requests = len(venue.creates)
        await cycle()
        assert len(venue.creates) == requests, "identical rejected protection stays latched"
        return
    assert len(working) == 1
    assert working[0].quantity - working[0].filled_quantity == protected.position.quantity
    intent = next(intent for intent in protected.intents if intent.id == working[0].intent_id)
    assert intent.purpose is (IntentPurpose.BRACKET if with_target else IntentPurpose.STOP)
    assert not any(order.status is OrderStatus.REJECTED for order in protected.orders)
