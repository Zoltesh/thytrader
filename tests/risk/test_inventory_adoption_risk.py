"""Risk breakers against books that adopted held inventory (ADR 0124)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from tests.adoption_support import ADOPTED_AT, adopted_book
from thytrader.risk.breakers import EntryObservation, evaluate_rate_and_collar
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskDecision, RiskReasonCode, compiled_default_risk_policy
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, IntentPurpose, OrderKind

pytestmark = pytest.mark.anyio

_AS_OF = ADOPTED_AT + timedelta(seconds=20)


def _observation(doge: Decimal = Decimal("0.2")) -> EntryObservation:
    """A same-minute observation with marks for the adopted and the proposed product."""
    return EntryObservation(
        as_of=_AS_OF,
        proposed_price=Decimal(100),
        reference_price=Decimal(100),
        marks={"DOGE-USD": doge, "BTC-USD": Decimal(100)},
    )


async def test_adoption_is_neither_an_entry_order_nor_a_venue_action() -> None:
    """An adoption seconds ago leaves one-per-minute entry and venue budgets untouched."""
    policy = compiled_default_risk_policy().model_copy(
        update={"max_entry_orders_per_minute": 1, "max_venue_order_actions_per_minute": 1}
    )
    adopted = await adopted_book(InMemoryExecutionStore())
    order = adopted.orders[0]
    assert order.kind is OrderKind.ADOPTION and _AS_OF - order.created_at < timedelta(minutes=1)
    verdict = evaluate_rate_and_collar(
        policy, mode=DeploymentMode.LIVE, snapshots=(adopted,), observation=_observation()
    )
    assert verdict is None
    # Control: the same order as a venue entry exhausts both budgets.
    as_entry = replace(
        adopted,
        orders=(replace(order, kind=OrderKind.MARKETABLE, venue_order_id="v"),),
        intents=tuple(replace(item, purpose=IntentPurpose.ENTRY) for item in adopted.intents),
    )
    denied = evaluate_rate_and_collar(
        policy, mode=DeploymentMode.LIVE, snapshots=(as_entry,), observation=_observation()
    )
    assert denied is not None and denied.reason_code is RiskReasonCode.ORDER_RATE_LIMIT


async def test_adopted_losses_count_toward_the_daily_loss_breaker() -> None:
    """Truthful accounting: a marked-down adopted lot is a loss on the UTC day it was taken."""
    policy = compiled_default_risk_policy().model_copy(
        update={"daily_loss_limit_fraction": "1", "max_daily_loss_quote": "1"}
    )
    adopted = await adopted_book(InMemoryExecutionStore())
    proposed = ProposedEntry(product_id="BTC-USD", strategy_id=UUID(int=7), notional=Decimal(10))
    held = evaluate_new_entry(
        policy,
        mode=DeploymentMode.LIVE,
        proposed=proposed,
        snapshots=(adopted,),
        live_quote_cash=Decimal(10000),
        observation=_observation(),
    )
    assert held.decision is RiskDecision.ALLOW, held
    marked_down = evaluate_new_entry(
        policy,
        mode=DeploymentMode.LIVE,
        proposed=proposed,
        snapshots=(adopted,),
        live_quote_cash=Decimal(10000),
        observation=_observation(Decimal("0.18")),
    )
    assert marked_down.decision is RiskDecision.DENY
    assert marked_down.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
