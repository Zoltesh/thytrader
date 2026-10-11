"""Public live-admission Decimal boundaries; no venue or persistence is involved."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_DOWN, Context, Decimal, Inexact, getcontext, localcontext
from typing import TYPE_CHECKING

import pytest

from tests.risk.test_futures_gate_caps import _CONTRACT, _NOW, _PERP, _book, _observation, _state
from tests.risk.test_futures_live import book, policy, venue
from thytrader.evaluation.futures_spec import InstrumentContract
from thytrader.risk.futures_live import FuturesVenueEvidence, LiveFuturesStart
from thytrader.risk.gate import evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskPolicyDefinition, RiskReasonCode, RiskVerdict
from thytrader.trading.futures_book import FuturesBookState, futures_book_scope
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    PositionSide,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True, params=[True, False], ids=["default-traps", "no-traps"])
def decimal_context(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep the default exponent range, exercising both raising and nonfinite results."""
    with localcontext(Context()) as context:
        if not request.param:
            context.clear_traps()
        yield


def entry(
    *,
    quantity: str = "0.01",
    notional: str = "1",
    snapshot: DeploymentSnapshot | None = None,
    state: FuturesBookState | None = None,
    evidence: FuturesVenueEvidence | None = None,
    definition: RiskPolicyDefinition | None = None,
) -> RiskVerdict:
    """Exercise the public entry boundary with validated policy and synthetic loaded facts."""
    snapshot = book() if snapshot is None else snapshot
    definition = policy() if definition is None else definition
    with futures_book_scope(_state(snapshot) if state is None else state):
        return evaluate_new_entry(
            RiskPolicyDefinition.model_validate(definition.model_dump()),
            mode=DeploymentMode.LIVE,
            proposed=ProposedEntry(
                product_id=_PERP,
                strategy_id=snapshot.deployment.strategy_id,
                quantity=Decimal(quantity),
                notional=Decimal(notional),
            ),
            snapshots=(snapshot,),
            observation=_observation(),
            futures_venue=venue() if evidence is None else evidence,
        )


def start(
    *,
    allocated: str = "1000",
    deployments: tuple[Deployment, ...] = (),
    initial: str = "100",
    evidence: FuturesVenueEvidence | None = None,
    definition: RiskPolicyDefinition | None = None,
) -> RiskVerdict:
    """Exercise public start admission without calling any live start service."""
    definition = policy() if definition is None else definition
    return evaluate_new_deployment(
        RiskPolicyDefinition.model_validate(definition.model_dump()),
        mode=DeploymentMode.LIVE,
        product_id=_PERP,
        strategy_id=None,
        paper_starting_cash=None,
        deployments=deployments,
        live_futures=LiveFuturesStart(
            Decimal(allocated),
            _CONTRACT,
            venue() if evidence is None else evidence,
            _NOW,
            Decimal(initial),
        ),
    )


@pytest.mark.parametrize(
    "quantity",
    ["1e999999", "1e-1000030", "0.0100000000000000000000000000001"],
    ids=["overflow", "underflow-to-zero", "fraction-rounded-to-integer"],
)
def test_order_contract_arithmetic_denies_unrepresentable_counts(quantity: str) -> None:
    """Overflow, underflow and inexact division cannot certify positive whole contracts."""
    assert entry(quantity=quantity).reason_code is RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED


@pytest.mark.parametrize("quantity", ["0.01", "0.0100000000000000000000000000000"])
def test_exact_one_contract_still_passes(quantity: str) -> None:
    """Discarding only trailing zeroes does not invalidate an exact boundary order."""
    assert entry(quantity=quantity).reason_code is RiskReasonCode.ALLOWED


@pytest.mark.parametrize("at_start", [False, True], ids=["entry", "start"])
def test_projected_reserve_haircut_overflow_denies(at_start: bool) -> None:
    """Accepted finite current margin cannot leak Overflow while applying the haircut."""
    evidence = replace(venue(), initial_margin_usd=Decimal("9e999999"))
    result = start(evidence=evidence) if at_start else entry(evidence=evidence)
    assert result.reason_code is RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT


def test_projected_margin_sum_overflow_denies() -> None:
    """Current plus proposed margin is checked before its haircut as well."""
    evidence = replace(
        venue(),
        initial_margin_usd=Decimal("9e999999"),
        buying_power_usd=Decimal("9e999999"),
    )
    assert start(initial="9e999999", evidence=evidence).reason_code is (
        RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT
    )


@pytest.mark.parametrize(
    ("notional", "rate"),
    [("9e999999", "2"), ("1", "1e1000000"), ("1", "sNaN"), ("1e-1000026", "0.2")],
    ids=["multiply-overflow", "outside-context-rate", "loaded-signaling-nan", "underflow"],
)
def test_initial_margin_estimate_arithmetic_denies(notional: str, rate: str) -> None:
    """The margin calculation before the check chain must not raise or authorize rounded zero."""
    snapshot = book()
    state = _state(snapshot)
    assert state.margin is not None
    state = replace(state, margin=replace(state.margin, long_rate=Decimal(rate)))
    assert entry(snapshot=snapshot, state=state, notional=notional).reason_code is (
        RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN
    )


@pytest.mark.parametrize("initial", ["800", "800.0000000000000000000000000000"])
def test_reserve_exact_equality_still_passes(initial: str) -> None:
    """An exact 1000 reserve covers 800 times the normal 1.25 haircut inclusively."""
    assert start(initial=initial).reason_code is RiskReasonCode.ALLOWED


def test_reserve_subnormal_rounding_cannot_authorize_start() -> None:
    """A tiny positive requirement rounded during the haircut is unknown, not free capacity."""
    assert start(initial="1e-1000026").reason_code is (
        RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT
    )


@pytest.mark.parametrize(
    ("occupied", "allocated"),
    [(("9e999999", "9e999999"), "1000"), (("9e999999",), "9e999999"), (("10000",), "1e-30")],
    ids=["occupied-sum-overflow", "new-allocation-sum-overflow", "rounded-away-allocation"],
)
def test_allocation_arithmetic_denies(occupied: tuple[str, ...], allocated: str) -> None:
    """Every addition in the envelope must preserve all occupied and proposed capital."""
    deployments = tuple(
        replace(book().deployment, product_id=product, allocated_capital=Decimal(value))
        for product, value in zip(("ETP-20DEC30-CDE", "SLP-20DEC30-CDE"), occupied, strict=False)
    )
    assert start(allocated=allocated, deployments=deployments).reason_code is (
        RiskReasonCode.FUTURES_LIVE_CAPITAL_EXCEEDED
    )


@pytest.mark.parametrize("allocated", ["1000", "1000.000000000000000000000000000"])
def test_allocation_exact_envelope_boundary_passes(allocated: str) -> None:
    """The new allocation plus occupied capital may exactly equal the live envelope."""
    occupied = replace(
        book().deployment, product_id="ETP-20DEC30-CDE", allocated_capital=Decimal(9000)
    )
    assert start(allocated=allocated, deployments=(occupied,)).reason_code is RiskReasonCode.ALLOWED


def held_book(quantities: tuple[str, ...], side: PositionSide) -> DeploymentSnapshot:
    """Model loaded positions without constructors repairing or rounding their quantities."""
    snapshot = book()
    prototype = _book(_PERP, quantity="0.01").position
    assert prototype is not None
    positions = tuple(
        replace(prototype, deployment_id=snapshot.deployment.id, quantity=Decimal(value), side=side)
        for value in quantities
    )
    return replace(snapshot, position=positions[0], positions=positions)


@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
@pytest.mark.parametrize(
    "quantities",
    [
        ("1e999999",),
        ("9e999999", "9e999999"),
        ("1e-1000030",),
        ("0.0100000000000000000000000000001",),
        ("sNaN",),
        ("Infinity",),
    ],
    ids=["division-overflow", "sum-overflow", "underflow", "inexact", "snan", "infinity"],
)
def test_loaded_position_arithmetic_is_unknown(
    quantities: tuple[str, ...],
    side: PositionSide,
) -> None:
    """Invalid signed sum/division must not be mistaken for a reconciled venue position."""
    snapshot = held_book(quantities, side)
    assert entry(snapshot=snapshot).reason_code is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN


@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_exact_held_contracts_reach_the_position_cap(side: PositionSide) -> None:
    """Both sides reconcile exactly and still deny a second contract under the default cap."""
    snapshot = held_book(("0.0100000000000000000000000000000",), side)
    evidence = replace(venue(), positions={_PERP: Decimal(-1 if side is PositionSide.SHORT else 1)})
    assert entry(snapshot=snapshot, evidence=evidence).reason_code is (
        RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED
    )


@pytest.mark.parametrize("at_start", [False, True], ids=["entry-sum", "start-product"])
def test_rounded_away_margin_cannot_authorize_reserve(at_start: bool) -> None:
    """Finite rounded margin must not turn an insufficient reserve into exact coverage."""
    result = (
        start(initial="800.0000000000000000000000000001")
        if at_start
        else entry(
            evidence=replace(
                venue(), initial_margin_usd=Decimal("799.8000000000000000000000000001")
            )
        )
    )
    assert result.reason_code is RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT


def test_inexact_initial_margin_estimate_is_unknown() -> None:
    """The pre-chain estimate cannot discard risk before collateral thresholds see it."""
    snapshot = book()
    state = _state(snapshot)
    assert state.margin is not None
    state = replace(
        state, margin=replace(state.margin, long_rate=Decimal("0.20000000000000000000000000001"))
    )
    assert entry(snapshot=snapshot, state=state, notional="5").reason_code is (
        RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN
    )


def test_contract_cap_sum_overflow_denies() -> None:
    """Individually representable ordered and held contracts may still overflow their sum."""
    snapshot = held_book(("9e999999",), PositionSide.LONG)
    state = _state(snapshot)
    assert state.binding is not None and state.margin is not None
    contract = InstrumentContract.model_validate({**_CONTRACT.model_dump(), "contract_size": "1"})
    state = replace(
        state,
        binding=replace(state.binding, contract=contract),
        margin=replace(state.margin, contract_size=Decimal(1)),
    )
    evidence = replace(
        venue(), positions={_PERP: Decimal("9e999999")}, contract_sizes={_PERP: Decimal(1)}
    )
    assert entry(
        snapshot=snapshot, state=state, evidence=evidence, quantity="9e999999"
    ).reason_code is (RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED)


@pytest.mark.parametrize("rate", ["NaN", "Infinity", "-Infinity", "0", "-1"])
def test_nonfinite_or_nonpositive_loaded_rate_denies(rate: str) -> None:
    """Unchecked loaded margin rates cannot supply initial-margin authority."""
    snapshot = book()
    state = _state(snapshot)
    assert state.margin is not None
    state = replace(state, margin=replace(state.margin, long_rate=Decimal(rate)))
    assert (
        entry(snapshot=snapshot, state=state).reason_code
        is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN
    )


def test_saturating_overflow_still_denies() -> None:
    """ROUND_DOWN may return a finite saturated result on overflow; that is not valid evidence."""
    with localcontext() as context:
        context.clear_traps()
        context.rounding = ROUND_DOWN
        snapshot = book()
        state = _state(snapshot)
        assert state.margin is not None
        state = replace(state, margin=replace(state.margin, long_rate=Decimal(2)))
        assert entry(snapshot=snapshot, state=state, notional="9e999999").reason_code is (
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN
        )


def test_arithmetic_guards_do_not_change_caller_context() -> None:
    """Scoped exactness neither leaks traps nor mistakes old caller flags for new failures."""
    context = getcontext()
    context.flags[Inexact] = True
    traps, flags = dict(context.traps), dict(context.flags)
    assert entry().reason_code is RiskReasonCode.ALLOWED
    assert entry(quantity="1e999999").reason_code is RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED
    assert dict(context.traps) == traps
    assert dict(context.flags) == flags
