"""Lifecycle's durable unresolved predicates also constrain shared risk accounting."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Literal

import pytest

from tests.risk.test_loss_scope import (
    _STRATEGY_B,
    _TODAY,
    _YESTERDAY,
    _deployment,
    _entry,
    _observation,
    _policy,
    _round_trip,
)
from tests.risk.test_safety_evidence import seed_accounting
from thytrader.execution.loop import _entry_verdict
from thytrader.risk.breakers import _daily_pnl
from thytrader.risk.daily_accounting import flat_day_fill_pnl
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.models import RiskDecision, RiskReasonCode
from thytrader.risk.opening_accounting import reconstruct_day_open
from thytrader.trading.fill_ledger import (
    unprojected_inventory_products,
    unsettled_fill_evidence,
)
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    InstrumentRuntime,
    IntentPurpose,
    OrderIntent,
    OrderStatus,
    RuntimePhase,
)
from thytrader.trading.overlay import InstrumentScopedStore
from thytrader.trading.protection import missing_occupied_inventory_products

Case = Literal[
    "legacy_offset", "canceled_unpublished", "filled_without_quantity", "runtime_without_position"
]


def _unresolved(case: Case, mode: DeploymentMode, *, older: bool) -> DeploymentSnapshot:
    """Prior profit and a real flat opening cannot certify missing current inventory."""
    initial = Decimal("0") if mode is DeploymentMode.LIVE else Decimal("10000")
    book = _deployment(
        mode=mode,
        product_id="ETH-USD",
        created_at=_YESTERDAY if older else _TODAY.replace(hour=0),
        cash=initial,
        initial_equity=initial,
        paper_starting_cash=None if mode is DeploymentMode.LIVE else initial,
        performance_capital_quote=Decimal("10000"),
    )
    opening = reconstruct_day_open(DeploymentSnapshot(book), as_of=_TODAY)
    assert opening is not None
    book = replace(book, risk_day_open_evidence=opening)
    if case == "runtime_without_position":
        return DeploymentSnapshot(
            book, instrument_runtimes=(InstrumentRuntime("ETH-USD", RuntimePhase.OPEN),)
        )
    tape = _round_trip(
        book,
        buy_at=_TODAY - timedelta(hours=1),
        sell_at=_TODAY - timedelta(hours=2),
        sell_price=Decimal("200"),
    )
    # This older unowned exit cannot offset a later owned entry's missing projection.
    buy, sell = tape.orders
    intents = tuple(
        OrderIntent(
            id=order.intent_id,
            deployment_id=book.id,
            client_order_id=order.client_order_id,
            purpose=purpose,
            side=order.side,
            kind=order.kind,
            quantity=order.quantity,
            price=order.price,
            created_at=order.created_at,
            candle_starts_at=order.created_at,
            product_id=order.product_id,
        )
        for order, purpose in ((buy, IntentPurpose.ENTRY), (sell, IntentPurpose.STOP))
    )
    if case == "legacy_offset":
        return replace(tape, deployment=replace(book, cash=initial + 100), intents=intents)
    order = replace(
        buy,
        status=OrderStatus.CANCELED if case == "canceled_unpublished" else OrderStatus.FILLED,
        filled_quantity=Decimal("0.004") if case == "canceled_unpublished" else Decimal("0"),
    )
    return DeploymentSnapshot(book, orders=(order,), intents=(intents[0],))


@pytest.mark.parametrize("mode", list(DeploymentMode))
@pytest.mark.parametrize("older", [False, True])
@pytest.mark.parametrize("observed", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "legacy_offset",
        "canceled_unpublished",
        "filled_without_quantity",
        "runtime_without_position",
    ],
)
def test_unresolved_evidence_blocks_even_profit_and_preverified_opening(
    mode: DeploymentMode, older: bool, observed: bool, case: Case
) -> None:
    """No incidental small loss limit or missing opening baseline masks the predicate."""
    snapshot = _unresolved(case, mode, older=older)
    if case == "legacy_offset":
        assert unprojected_inventory_products(snapshot) == ("ETH-USD",)
        assert not unsettled_fill_evidence(snapshot)
    elif case == "runtime_without_position":
        assert missing_occupied_inventory_products(snapshot) == ("ETH-USD",)
        assert not unsettled_fill_evidence(snapshot)
        assert not unprojected_inventory_products(snapshot)
    else:
        assert unsettled_fill_evidence(snapshot)
    verdict = evaluate_new_entry(
        _policy(daily_loss_limit_fraction="1", max_daily_loss_quote="1000"),
        mode=mode,
        proposed=_entry(strategy_id=_STRATEGY_B, notional=Decimal("1")),
        snapshots=(snapshot,),
        live_quote_cash=Decimal("10000") if mode is DeploymentMode.LIVE else None,
        observation=_observation() if observed else None,
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert reconstruct_day_open(snapshot, as_of=_TODAY) is None
    assert flat_day_fill_pnl(snapshot, since=_TODAY.replace(hour=0)) is None
    assert _daily_pnl(snapshot, marks={}, as_of=_TODAY) is None


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["legacy_offset", "runtime_without_position"])
@pytest.mark.parametrize("observed", [False, True])
async def test_scoped_store_retains_unprojected_sibling_after_display_fault_clears(
    case: Case, observed: bool
) -> None:
    """Reload runtime/ownership/fills: the focused empty BTC view must still deny."""
    full = _unresolved(case, DeploymentMode.LIVE, older=False)
    full = replace(
        full,
        deployment=replace(
            full.deployment,
            product_id="BTC-USD",
            mismatch_detail=None,
            venue_available_quote=Decimal("10000"),
        ),
        instrument_runtimes=(
            InstrumentRuntime("BTC-USD", RuntimePhase.FLAT),
            InstrumentRuntime(
                "ETH-USD",
                RuntimePhase.OPEN if case == "runtime_without_position" else RuntimePhase.FLAT,
            ),
        ),
    )
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    for intent in full.intents:
        await store.save_intent(intent)
    scoped = InstrumentScopedStore(store, "BTC-USD")
    focused = await scoped.get_deployment(full.deployment.id)
    assert not focused.orders and not focused.fills
    verdict = await _entry_verdict(
        focused,
        store=scoped,
        product_id="BTC-USD",
        notional=Decimal("1"),
        quantity=Decimal("0.01"),
        risk_policy=_policy(daily_loss_limit_fraction="1", max_daily_loss_quote="1000"),
        portfolio=(focused,),
        observation=_observation() if observed else None,
    )
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING


def test_foreign_quote_unresolved_history_does_not_block_usd_entry() -> None:
    """Retained foreign-quote evidence is not converted or mixed into the USD account."""
    original = _unresolved("canceled_unpublished", DeploymentMode.LIVE, older=True)
    snapshot = replace(
        original,
        deployment=replace(
            original.deployment, product_id="ETH-USDC", status=DeploymentStatus.STOPPED
        ),
        orders=tuple(replace(order, product_id="ETH-USDC") for order in original.orders),
        intents=tuple(replace(intent, product_id="ETH-USDC") for intent in original.intents),
    )
    verdict = evaluate_new_entry(
        _policy(),
        mode=DeploymentMode.LIVE,
        proposed=_entry(strategy_id=_STRATEGY_B, notional=Decimal("1")),
        snapshots=(snapshot,),
        live_quote_cash=Decimal("10000"),
        observation=_observation(),
    )
    assert verdict.decision is RiskDecision.ALLOW
