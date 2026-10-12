"""Real closed-bar futures entry and protection with injected synthetic venue evidence."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import sys

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_paper_futures_books import (
    _PERP,
    _book,
    _candles,
    _product,
    _state,
    _strategy,
)
from thytrader.exchanges.fees import FeeProfile
from thytrader.execution.decision_scope import decision_observation_scope
from thytrader.execution.entry import _futures_entry_block
from thytrader.execution.entry_sizing import _size_entry_or_add
from thytrader.execution.futures_live import live_futures_account_scope
from thytrader.execution.loop import maintain_open_inventory, process_closed_bar
from thytrader.execution_worker.live_sizing import _prepare_live
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import RiskReasonCode, compiled_default_risk_policy
from thytrader.strategies.models import StrategyDefinition
from thytrader.trading.futures_book import futures_book_scope
from thytrader.trading.geometry import EntrySkipReason
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, OrderKind, OrderStatus, PositionSide


class FuturesFees:
    """Spot cash/fee reads are errors; futures fees use their own tier."""

    async def list_balances(self) -> tuple[()]:
        """Assert futures never read spot base or quote for sizing."""
        raise AssertionError("spot balances must not size a futures entry")

    async def get_futures_fee_profile(self) -> FeeProfile:
        """Return an explicitly synthetic futures tier."""
        return FeeProfile(
            maker_fee_rate=Decimal("0.001"),
            taker_fee_rate=Decimal("0.002"),
            usd_volume_30d=Decimal(0),
            fee_tier="test",
            as_of=FakeVenue().now,
        )


def clock(monkeypatch: pytest.MonkeyPatch, venue: FakeVenue) -> None:
    """Pin imported execution clocks, not transport results or trading decisions."""
    for name, module in tuple(sys.modules.items()):
        if name.startswith("thytrader.") and hasattr(module, "utc_now"):
            monkeypatch.setattr(module, "utc_now", lambda: venue.now)


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("catalog_size", ["0.01", "0.02", None])
async def test_live_futures_entry_fill_and_standalone_protection(
    monkeypatch: pytest.MonkeyPatch, side: str, catalog_size: str | None
) -> None:
    """Real loop sizes whole contracts, admits, reconciles fees and rests protection."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    payload = _strategy().model_dump(mode="python")
    payload["entry"]["side"] = side
    payload["sizing"]["max_quote_notional"] = "2.6"
    payload["sizing"]["min_quote_notional"] = "0.1"
    strategy = StrategyDefinition.model_validate(payload)
    store = InMemoryExecutionStore()
    snapshot = await _book(store, strategy)
    await store.save_deployment(
        replace(
            snapshot.deployment,
            mode=DeploymentMode.LIVE,
            cash=Decimal(0),
            initial_equity=Decimal(0),
            allocated_capital=Decimal(100),
            performance_capital_quote=Decimal(100),
        )
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
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
    account = await venue.read_account(frozenset())
    if catalog_size != "0.01":
        account = replace(
            account,
            venue=replace(
                account.venue,
                contract_sizes={} if catalog_size is None else {_PERP: Decimal(catalog_size)},
            ),
        )
    with (
        live_futures_account_scope(account),
        futures_book_scope(_state(snapshot, side=side, latest_funding_rate=Decimal("0.001"))),
        decision_observation_scope(enabled=True) as observed,
    ):
        prepared = await _prepare_live(
            snapshot,
            store=store,
            live_broker=venue.broker(),
            quote_reader=FuturesFees(),
            quote_currency="USD",
            product_id=_PERP,
            cooldown_bars=0,
        )
        assert prepared is not None
        snapshot, fees = prepared
        assert snapshot.deployment.venue_available_quote == Decimal(10000)
        rested = await process_closed_bar(
            snapshot,
            strategy=strategy,
            product=_product(),
            candles=_candles(31),
            broker=venue.broker(),
            store=store,
            risk_policy=policy,
            portfolio=(snapshot,),
            fee_profile=fees,
        )
    if catalog_size != "0.01":
        assert not rested.orders and not venue.creates
        assert observed is not None
        assert any(
            verdict.reason_code is RiskReasonCode.FUTURES_CONTRACT_DRIFT
            for verdict in observed.risk_verdicts
        )
        return
    assert len(rested.orders) == 1, observed
    assert rested.orders[0].status is OrderStatus.OPEN
    assert rested.orders[0].quantity == Decimal("0.02")
    venue.fill(rested.orders[0].venue_order_id or "")
    venue.advance()
    account = await venue.read_account(frozenset())
    with live_futures_account_scope(account), futures_book_scope(_state(rested, side=side)):
        prepared = await _prepare_live(
            rested,
            store=store,
            live_broker=venue.broker(),
            quote_reader=FuturesFees(),
            quote_currency="USD",
            product_id=_PERP,
            cooldown_bars=0,
        )
        assert prepared is not None
        filled = await maintain_open_inventory(
            prepared[0],
            strategy=strategy,
            product=_product(),
            candles=_candles(31),
            broker=venue.broker(),
            store=store,
        )
    assert filled.position is not None
    assert filled.position.side is (PositionSide.LONG if side == "long" else PositionSide.SHORT)
    assert filled.fills[0].fee == Decimal("0.15")
    assert any(
        order.kind in {OrderKind.TRIGGER_BRACKET, OrderKind.STOP_LIMIT}
        and order.status is OrderStatus.OPEN
        for order in filled.orders
    )


@pytest.mark.anyio
async def test_live_futures_sizing_uses_allocation_plus_ledger_equity() -> None:
    """A realized loss reduces the next order's margin budget instead of reusing allocation."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _book(store, strategy)
    live = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            mode=DeploymentMode.LIVE,
            cash=Decimal(-90),
            allocated_capital=Decimal(100),
        ),
    )
    equivalent = replace(snapshot, deployment=replace(snapshot.deployment, cash=Decimal(10)))
    fees = await FuturesFees().get_futures_fee_profile()
    equivalent = replace(
        equivalent,
        deployment=replace(equivalent.deployment, paper_maker_fee_rate=fees.maker_fee_rate),
    )
    with futures_book_scope(_state(snapshot)):
        actual = _size_entry_or_add(
            live,
            strategy=strategy,
            product=_product(),
            entry_price=Decimal(130),
            atr=Decimal(2),
            side=PositionSide.LONG,
            is_pyramid_add=False,
            fee_profile=fees,
        )
        expected = _size_entry_or_add(
            equivalent,
            strategy=strategy,
            product=_product(),
            entry_price=Decimal(130),
            atr=Decimal(2),
            side=PositionSide.LONG,
            is_pyramid_add=False,
        )
    assert actual == expected


@pytest.mark.anyio
async def test_live_futures_sizing_never_defaults_to_spot_fee() -> None:
    """Missing futures tier denies sizing even when the strategy has no economic guard."""
    snapshot = await _book(InMemoryExecutionStore(), _strategy())
    live = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            mode=DeploymentMode.LIVE,
            cash=Decimal(0),
            allocated_capital=Decimal(100),
        ),
    )
    with futures_book_scope(_state(snapshot)):
        actual = _size_entry_or_add(
            live,
            strategy=_strategy(),
            product=_product(),
            entry_price=Decimal(130),
            atr=Decimal(2),
            side=PositionSide.LONG,
            is_pyramid_add=False,
        )
    assert actual is EntrySkipReason.ECONOMICS_FEE_UNAVAILABLE


@pytest.mark.anyio
async def test_live_entry_requires_known_settled_funding_rate() -> None:
    """An unobserved funding rate blocks new risk even when the optional rate cap is unset."""
    snapshot = await _book(InMemoryExecutionStore(), _strategy())
    snapshot = replace(snapshot, deployment=replace(snapshot.deployment, mode=DeploymentMode.LIVE))
    with futures_book_scope(_state(snapshot)):
        denied = _futures_entry_block(snapshot, product_id=_PERP)
    assert denied is not None and denied[0] == "FUNDING_HISTORY_MISSING"
