"""In-kind (adoption) funding in the entry gate: which checks apply and which do not (ADR 0124).

Each skipped check is shown denying the same entry when it is quote-funded, so the test
proves the skip, not an accidentally permissive policy.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from tests.adoption_support import (
    ADOPTED_AT,
    adopted_book,
    entry_intent,
    live_book,
    working_order,
)
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    CapitalAllocation,
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    RiskVerdict,
    compiled_default_risk_policy,
)
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    OrderKind,
    OrderStatus,
)

pytestmark = pytest.mark.anyio

_STRATEGY = UUID(int=124)
_AS_OF = ADOPTED_AT + timedelta(seconds=30)
_FLAT = DeploymentSnapshot(
    deployment=replace(live_book(), performance_capital_quote=Decimal(100)), position=None
)


def _policy(**update: Any) -> RiskPolicyDefinition:
    """A wide USD policy; each test narrows one check."""
    base: dict[str, Any] = {
        "quote_currency": "USD",
        "max_portfolio_exposure_fraction": "1",
        "per_product_max_exposure_fraction": "1",
    }
    return compiled_default_risk_policy().model_copy(update={**base, **update})


def _entry(funding: str = "in_kind", **update: Any) -> ProposedEntry:
    """Adopting 100 DOGE at 0.2 into the strategy book, or the same entry bought with quote."""
    proposed = ProposedEntry(
        product_id="DOGE-USD",
        strategy_id=_STRATEGY,
        notional=Decimal(20),
        quantity=Decimal(100),
        funding="in_kind" if funding == "in_kind" else "quote",
    )
    return replace(proposed, **update)


def _observation(
    *, proposed: Decimal = Decimal("0.2"), reference: Decimal | None = Decimal("0.2")
) -> EntryObservation:
    """Same-day marks; the collar compares ``proposed`` with ``reference``."""
    return EntryObservation(
        as_of=_AS_OF,
        proposed_price=proposed,
        reference_price=reference,
        marks={"DOGE-USD": Decimal("0.2")},
    )


def _verdict(
    policy: RiskPolicyDefinition,
    proposed: ProposedEntry,
    *,
    snapshots: tuple[DeploymentSnapshot, ...] = (_FLAT,),
    quote_cash: Decimal | None = Decimal(1000),
    observation: EntryObservation | None = None,
) -> RiskVerdict:
    """Run the live entry gate."""
    return evaluate_new_entry(
        policy,
        mode=DeploymentMode.LIVE,
        proposed=proposed,
        snapshots=snapshots,
        live_quote_cash=quote_cash,
        observation=observation or _observation(),
    )


def test_quote_funding_is_the_default() -> None:
    """Existing callers keep quote-funded semantics without passing anything."""
    proposed = ProposedEntry(product_id="DOGE-USD", strategy_id=None, notional=Decimal(1))
    assert proposed.funding == "quote" and not proposed.in_kind
    assert proposed.in_kind_capital == 0


def test_in_kind_notional_joins_the_live_capital_base() -> None:
    """With 10 USD of quote, buying 20 USD of DOGE exceeds capital; adopting it does not."""
    policy = _policy()
    bought = _verdict(policy, _entry("quote"), quote_cash=Decimal(10))
    assert bought.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    adopted = _verdict(policy, _entry(), quote_cash=Decimal(10))
    assert adopted.decision is RiskDecision.ALLOW, adopted


def test_unknown_quote_cash_still_blocks_an_in_kind_entry() -> None:
    """The adopted notional never replaces an unknown venue balance."""
    verdict = _verdict(_policy(), _entry(), quote_cash=None)
    assert verdict.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED


@pytest.mark.parametrize(
    ("update", "code"),
    [
        ({"max_order_quantity": "1"}, RiskReasonCode.MAX_ORDER_QUANTITY),
        ({"max_order_notional_quote": "5"}, RiskReasonCode.MAX_ORDER_NOTIONAL),
        ({"min_available_quote_reserve": "995"}, RiskReasonCode.BALANCE_RESERVE),
    ],
)
def test_order_bounds_are_skipped(update: dict[str, str], code: RiskReasonCode) -> None:
    """Quantity, notional and quote-reserve bounds describe a venue order; none is sent."""
    policy = _policy(**update)
    assert _verdict(policy, _entry("quote")).reason_code is code
    assert _verdict(policy, _entry()).decision is RiskDecision.ALLOW


async def test_the_rate_limit_is_skipped() -> None:
    """A minute's entry budget used up elsewhere does not block an adoption."""
    store = InMemoryExecutionStore()
    busy = await store.create_deployment(live_book(product_id="BTC-USD", strategy_id=UUID(int=8)))
    intent = entry_intent(busy, minutes=0)
    snapshot = replace(
        await store.get_deployment(busy.id),
        intents=(intent,),
        orders=(working_order(intent),),
    )
    policy = _policy(max_entry_orders_per_minute=1)
    observation = EntryObservation(
        as_of=_AS_OF,
        proposed_price=Decimal("0.2"),
        reference_price=Decimal("0.2"),
        marks={"DOGE-USD": Decimal("0.2"), "BTC-USD": Decimal("0.2")},
    )
    books = (_FLAT, snapshot)
    quoted = _verdict(policy, _entry("quote"), snapshots=books, observation=observation)
    assert quoted.reason_code is RiskReasonCode.ORDER_RATE_LIMIT
    adopted = _verdict(policy, _entry(), snapshots=books, observation=observation)
    assert adopted.decision is RiskDecision.ALLOW, adopted


@pytest.mark.parametrize(
    ("proposed", "reference", "code"),
    [
        (Decimal("0.5"), Decimal("0.2"), RiskReasonCode.REFERENCE_PRICE_COLLAR),
        (Decimal("0.2"), None, RiskReasonCode.REFERENCE_PRICE_UNAVAILABLE),
    ],
)
def test_the_reference_price_collar_is_skipped(
    proposed: Decimal, reference: Decimal | None, code: RiskReasonCode
) -> None:
    """The adoption is marked at a closed candle, not priced against one."""
    observation = _observation(proposed=proposed, reference=reference)
    policy = _policy()
    assert _verdict(policy, _entry("quote"), observation=observation).reason_code is code
    adopted = _verdict(policy, _entry(), observation=observation)
    assert adopted.decision is RiskDecision.ALLOW


@pytest.mark.parametrize(
    ("update", "code"),
    [
        ({"product_allowlist": ("BTC-USD",)}, RiskReasonCode.PRODUCT_NOT_ALLOWLISTED),
        (
            {"allocations": (CapitalAllocation(strategy_id=UUID(int=9), allocated_quote="50"),)},
            RiskReasonCode.STRATEGY_NOT_ALLOCATED,
        ),
        (
            {"allocations": (CapitalAllocation(strategy_id=_STRATEGY, allocated_quote="10"),)},
            RiskReasonCode.ALLOCATION_EXCEEDED,
        ),
        ({"max_portfolio_exposure_quote": "15"}, RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED),
        ({"per_product_max_exposure_fraction": "0.01"}, RiskReasonCode.PRODUCT_EXPOSURE_EXCEEDED),
        ({"max_concurrent_open_positions": 1}, RiskReasonCode.MAX_OPEN_POSITIONS),
    ],
)
async def test_membership_slots_exposure_and_allocation_still_apply(
    update: dict[str, Any], code: RiskReasonCode
) -> None:
    """The adopted coins are real exposure, so every account and strategy cap binds."""
    occupied = await adopted_book(
        InMemoryExecutionStore(),
        book=live_book(product_id="BTC-USD", strategy_id=UUID(int=8)),
        quantity=Decimal(1),
        mark=Decimal(100),
        stop_price=Decimal(90),
        target_price=None,
    )
    observation = EntryObservation(
        as_of=_AS_OF,
        proposed_price=Decimal("0.2"),
        reference_price=Decimal("0.2"),
        marks={"DOGE-USD": Decimal("0.2"), "BTC-USD": Decimal(100)},
    )
    snapshots = (_FLAT, occupied) if code is RiskReasonCode.MAX_OPEN_POSITIONS else (_FLAT,)
    verdict = _verdict(_policy(**update), _entry(), snapshots=snapshots, observation=observation)
    assert verdict.reason_code is code, verdict


@pytest.mark.parametrize(
    ("latch", "code"),
    [
        ("daily_loss_latched", RiskReasonCode.DAILY_LOSS_LIMIT),
        ("drawdown_latched", RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT),
    ],
)
def test_daily_loss_and_drawdown_breakers_still_apply(latch: str, code: RiskReasonCode) -> None:
    """A latched breaker refuses an adoption like any entry."""
    latched = replace(_FLAT, deployment=replace(_FLAT.deployment, **{latch: True}))
    verdict = _verdict(_policy(), _entry(), snapshots=(latched,))
    assert verdict.reason_code is code


async def test_unresolved_accounting_still_applies() -> None:
    """An adoption cannot be admitted beside a same-quote book with unknown economics."""
    store = InMemoryExecutionStore()
    unknown = await store.create_deployment(live_book(product_id="BTC-USD"))
    intent = entry_intent(unknown, minutes=0)
    snapshot = replace(
        await store.get_deployment(unknown.id),
        intents=(intent,),
        orders=(
            replace(
                working_order(intent, kind=OrderKind.MARKETABLE, status=OrderStatus.FILLED),
                filled_quantity=intent.quantity,
            ),
        ),
    )
    verdict = _verdict(_policy(), _entry(), snapshots=(_FLAT, snapshot))
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert verdict.detail.startswith("Accounting incomplete")
