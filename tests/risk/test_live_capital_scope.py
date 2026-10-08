"""Regressions for account capital versus per-bot allocations and live startup baselines."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.execution.capital import live_capital_base
from thytrader.execution.loop import _entry_verdict
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_new_entry, evaluate_runtime_breakers
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    CapitalAllocation,
    RiskDecision,
    RiskReasonCode,
    compiled_default_risk_policy,
)
from thytrader.trading.memory import InMemoryExecutionStore
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
    PositionSide,
    RuntimePhase,
)

_NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


def _snapshot(*, product: str = "BTC-USD", cost: str = "0") -> DeploymentSnapshot:
    """A reconciled live book with separate zero-based ledger and quote balance."""
    inventory = Decimal(cost)
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=uuid4(),
        product_id=product,
        timeframe="1h",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        phase=RuntimePhase.OPEN if inventory else RuntimePhase.FLAT,
        cash=-inventory,
        allocated_capital=Decimal("20"),
        venue_available_quote=Decimal("100"),
        initial_equity=Decimal("0"),
        high_water_mark_equity=Decimal("0"),
        utc_day_open_equity=Decimal("0"),
        utc_day_open_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
    )
    position = (
        Position(
            deployment_id=deployment.id,
            product_id=product,
            quantity=inventory / Decimal("100"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_NOW,
            updated_at=_NOW,
        )
        if inventory
        else None
    )
    return DeploymentSnapshot(deployment=deployment, position=position)


def _observation() -> EntryObservation:
    """Complete current and sibling marks with no unrealized loss."""
    return EntryObservation(
        as_of=_NOW,
        proposed_price=Decimal("100"),
        reference_price=Decimal("100"),
        marks={"BTC-USD": Decimal("100"), "ETH-USD": Decimal("100")},
    )


@pytest.mark.anyio
async def test_small_bot_allocation_does_not_cap_other_bots_account_exposure() -> None:
    """A small probe may enter beside another book while its own allocation still binds."""
    probe = _snapshot()
    peer = _snapshot(product="ETH-USD", cost="16")
    strategy_id = probe.deployment.strategy_id
    assert strategy_id is not None
    policy = compiled_default_risk_policy().model_copy(
        update={
            "max_portfolio_exposure_quote": "60",
            "allocations": (CapitalAllocation(strategy_id=strategy_id, allocated_quote="20"),),
        }
    )
    assert live_capital_base(probe.deployment) == Decimal("100")
    store = InMemoryExecutionStore()
    for snapshot in (probe, peer):
        await store.create_deployment(snapshot.deployment)
        await store.save_position(snapshot.position, deployment_id=snapshot.deployment.id)
    verdict = await _entry_verdict(
        probe,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("7"),
        risk_policy=policy,
        portfolio=(peer,),
        observation=_observation(),
    )
    assert verdict.decision is RiskDecision.ALLOW
    tighter = policy.model_copy(update={"max_portfolio_exposure_quote": "20"})
    refused = await _entry_verdict(
        probe,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("7"),
        risk_policy=tighter,
        portfolio=(peer,),
        observation=_observation(),
    )
    assert refused.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    assert "existing=16" in refused.detail
    assert "proposed=7" in refused.detail
    assert "cap=20" in refused.detail
    assert "capital=116" in refused.detail
    allocation = policy.model_copy(
        update={"allocations": (CapitalAllocation(strategy_id=strategy_id, allocated_quote="5"),)}
    )
    refused = await _entry_verdict(
        probe,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("7"),
        risk_policy=allocation,
        portfolio=(peer,),
    )
    assert refused.reason_code is RiskReasonCode.ALLOCATION_EXCEEDED
    assert "allocation=5" in refused.detail


@pytest.mark.parametrize("status", [DeploymentStatus.RUNNING, DeploymentStatus.STOPPED])
def test_owned_inventory_capital_counts_stopped_residuals(status: DeploymentStatus) -> None:
    """The account fraction includes managed long cost, even after managed shutdown."""
    book = _snapshot(cost="40")
    book = replace(book, deployment=replace(book.deployment, status=status))
    policy = compiled_default_risk_policy().model_copy(
        update={"max_portfolio_exposure_fraction": "0.5"}
    )
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("ETH-USD", uuid4(), Decimal("30")),
        snapshots=(book,),
        live_quote_cash=Decimal("100"),
    )
    assert verdict.decision is RiskDecision.ALLOW
    refused = evaluate_new_entry(
        policy,
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("ETH-USD", uuid4(), Decimal("31")),
        snapshots=(book,),
        live_quote_cash=Decimal("100"),
    )
    assert refused.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    assert "cap=70.0" in refused.detail


def test_short_proceeds_and_buy_protection_do_not_inflate_account_capital() -> None:
    """Spot-sale proceeds are already in quote; a working cover is not an entry reserve."""
    book = _snapshot(cost="16")
    assert book.position is not None
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=book.deployment.id,
        client_order_id="cover",
        purpose=IntentPurpose.TAKE_PROFIT,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        product_id="BTC-USD",
        created_at=_NOW,
        candle_starts_at=_NOW,
    )
    order = Order(
        id=uuid4(),
        deployment_id=book.deployment.id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=intent.side,
        kind=intent.kind,
        quantity=intent.quantity,
        price=intent.price,
        status=OrderStatus.OPEN,
        product_id="BTC-USD",
        created_at=_NOW,
        updated_at=_NOW,
    )
    book = replace(
        book,
        position=replace(book.position, side=PositionSide.SHORT),
        intents=(intent,),
        orders=(order,),
    )
    verdict = evaluate_new_entry(
        compiled_default_risk_policy().model_copy(
            update={"max_portfolio_exposure_fraction": "0.2"}
        ),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("ETH-USD", uuid4(), Decimal("5")),
        snapshots=(book,),
        live_quote_cash=Decimal("100"),
    )
    assert verdict.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    assert "capital=100" in verdict.detail
    assert "cap=20.0" in verdict.detail


def test_pending_buy_remainder_is_quote_capital_without_double_counting_partial_fill() -> None:
    """Available quote excludes held entry funds; filled inventory and remainder each count once."""
    book = _snapshot(cost="40")
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=book.deployment.id,
        client_order_id="partial-entry",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        product_id="BTC-USD",
        created_at=_NOW,
        candle_starts_at=_NOW,
    )
    order = Order(
        id=uuid4(),
        deployment_id=book.deployment.id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=intent.side,
        kind=intent.kind,
        quantity=intent.quantity,
        filled_quantity=Decimal("0.4"),
        price=intent.price,
        status=OrderStatus.OPEN,
        product_id="BTC-USD",
        created_at=_NOW,
        updated_at=_NOW,
    )
    fill = Fill(
        id=uuid4(),
        deployment_id=book.deployment.id,
        order_id=order.id,
        venue_fill_id="applied-partial-entry",
        price=Decimal("100"),
        quantity=Decimal("0.4"),
        fee=Decimal("0"),
        filled_at=_NOW,
        economics_applied_at=_NOW,
    )
    book = replace(book, intents=(intent,), orders=(order,), fills=(fill,))
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("ETH-USD", uuid4(), Decimal("1")),
        snapshots=(book,),
        live_quote_cash=Decimal("0"),
    )
    assert verdict.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    assert "existing=100.0" in verdict.detail
    assert "capital=100.0" in verdict.detail
    unknown = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("ETH-USD", uuid4(), Decimal("1")),
        snapshots=(book,),
        live_quote_cash=None,
    )
    assert unknown.decision is RiskDecision.DENY


@pytest.mark.anyio
async def test_allocation_cannot_replace_unknown_venue_quote() -> None:
    """A funded-looking bot allocation is not evidence of a healthy account balance."""
    book = _snapshot()
    book = replace(book, deployment=replace(book.deployment, venue_available_quote=None))
    store = InMemoryExecutionStore()
    await store.create_deployment(book.deployment)
    verdict = await _entry_verdict(
        book,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("7"),
        risk_policy=compiled_default_risk_policy(),
        portfolio=(),
    )
    assert verdict.reason_code is RiskReasonCode.VENUE_BALANCE_UNKNOWN


def test_zero_live_baseline_remains_valid_without_day_open_stamp() -> None:
    """Zero is a valid live ledger baseline, including older books before their day-open stamp."""
    book = _snapshot()
    book = replace(book, deployment=replace(book.deployment, utc_day_open_equity=None))
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("BTC-USD", book.deployment.strategy_id, Decimal("7")),
        snapshots=(book,),
        live_quote_cash=Decimal("100"),
        observation=_observation(),
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_missing_baseline_identifies_the_flat_book_without_inventing_a_mark_failure() -> None:
    """Historical unknown baselines stay fail-closed and name the unavailable evidence."""
    book = _snapshot()
    book = replace(
        book, deployment=replace(book.deployment, initial_equity=None, utc_day_open_equity=None)
    )
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry("BTC-USD", book.deployment.strategy_id, Decimal("7")),
        snapshots=(book,),
        live_quote_cash=Decimal("100"),
        observation=_observation(),
    )
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert str(book.deployment.id) in verdict.detail
    assert "baseline" in verdict.detail
    assert "inventory marks" not in verdict.detail


def test_runtime_daily_loss_uses_the_same_account_capital_as_entry_gate() -> None:
    """An unrelated small allocation cannot lower the mode-wide fractional daily-loss cap."""
    small = _snapshot()
    loss = _snapshot(product="ETH-USD")
    loss = replace(loss, deployment=replace(loss.deployment, cash=Decimal("-5")))
    policy = compiled_default_risk_policy().model_copy(
        update={"daily_loss_limit_fraction": "0.1", "max_daily_loss_quote": "20"}
    )
    verdict = evaluate_runtime_breakers(
        policy,
        mode=DeploymentMode.LIVE,
        snapshot=small,
        snapshots=(small, loss),
        live_quote_cash=live_capital_base(small.deployment),
        observation=_observation(),
    )
    assert verdict.decision is RiskDecision.ALLOW
    refused = evaluate_runtime_breakers(
        policy.model_copy(update={"max_daily_loss_quote": "4"}),
        mode=DeploymentMode.LIVE,
        snapshot=small,
        snapshots=(small, loss),
        live_quote_cash=live_capital_base(small.deployment),
        observation=_observation(),
    )
    assert refused.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
