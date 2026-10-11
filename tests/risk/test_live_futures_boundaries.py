"""Boundary cases for P2-3 admission, without starting a live runtime."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.risk.test_futures_gate_caps import _CONTRACT, _NOW, _PERP, _book, _state
from tests.risk.test_futures_live import book, policy, venue
from tests.risk.test_live_futures_admission import entry_code
from thytrader.risk.futures_live import (
    LiveFuturesStart,
    live_futures_deployment_verdict,
    live_futures_entry_verdict,
)
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskReasonCode, pauses_risk_increasing
from thytrader.trading.models import DeploymentMode, DeploymentStatus, PositionSide


@pytest.mark.parametrize("side", list(PositionSide))
def test_live_cap_applies_to_resulting_position(side: PositionSide) -> None:
    """One new contract fits the order cap but cannot add to an already full live position."""
    held = _book(_PERP, quantity="0.01", side=side)
    snapshot = replace(book(), position=held.position, positions=held.positions)
    count = Decimal(-1) if side is PositionSide.SHORT else Decimal(1)
    verdict = live_futures_entry_verdict(
        policy(),
        proposed=ProposedEntry(
            product_id=_PERP,
            strategy_id=snapshot.deployment.strategy_id,
            quantity=Decimal("0.01"),
            notional=Decimal(1),
        ),
        book=snapshot,
        state=_state(snapshot),
        snapshots=(snapshot,),
        venue=replace(venue(), positions={_PERP: count}),
        as_of=_NOW,
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED


def test_live_contract_cap_default_and_explicit_override() -> None:
    """Two whole contracts exceed the unset live cap, but fit an explicitly published cap."""
    assert entry_code(quantity="0.02") is RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED
    assert entry_code(quantity="0.02", policy_changes={"max_order_contracts": 2}) is (
        RiskReasonCode.ALLOWED
    )


@pytest.mark.parametrize("status", [DeploymentStatus.RUNNING, DeploymentStatus.PAUSED])
def test_entry_refuses_another_occupied_live_book(status: DeploymentStatus) -> None:
    """Paused books retain product exclusivity, as do running books."""
    snapshot = book()
    other = replace(snapshot, deployment=replace(snapshot.deployment, id=uuid4(), status=status))
    verdict = live_futures_entry_verdict(
        policy(),
        proposed=ProposedEntry(
            product_id=_PERP,
            strategy_id=snapshot.deployment.strategy_id,
            quantity=Decimal("0.01"),
            notional=Decimal(1),
        ),
        book=snapshot,
        state=_state(snapshot),
        snapshots=(snapshot, other),
        venue=venue(),
        as_of=_NOW,
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_PRODUCT_OCCUPIED


def test_dated_live_start_is_refused() -> None:
    """A known dated contract is still unsupported even with fresh complete admission evidence."""
    contract = _CONTRACT.model_copy(
        update={"kind": "dated_future", "expires_at": _NOW + timedelta(days=10)}
    )
    verdict = live_futures_deployment_verdict(
        policy(),
        covered=(_PERP,),
        deployments=(),
        start=LiveFuturesStart(Decimal(1000), contract, venue(), _NOW, Decimal(100)),
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_LIVE_DATED_UNSUPPORTED


@pytest.mark.parametrize("margin", [None, Decimal(0), Decimal(-1), Decimal("NaN")])
def test_start_requires_positive_known_proposed_margin(margin: Decimal | None) -> None:
    """A start may not certify collateral without accounting for its proposed margin."""
    verdict = live_futures_deployment_verdict(
        policy(),
        covered=(_PERP,),
        deployments=(),
        start=LiveFuturesStart(Decimal(1000), _CONTRACT, venue(), _NOW, margin),
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN


def test_paper_allocations_do_not_consume_live_envelope() -> None:
    """Identical paper products and capital do not occupy a live product or USD envelope."""
    paper = replace(book().deployment, mode=DeploymentMode.PAPER, allocated_capital=Decimal(99999))
    assert (
        live_futures_deployment_verdict(
            policy(),
            covered=(_PERP,),
            deployments=(paper,),
            start=LiveFuturesStart(Decimal(1000), _CONTRACT, venue(), _NOW, Decimal(100)),
        )
        is None
    )


def test_live_futures_admission_denials_do_not_pause_or_gate_exits() -> None:
    """Every futures admission reason remains outside the pause-on-breaker reason set."""
    codes = [code for code in RiskReasonCode if code.value.startswith("FUTURES_")]
    assert codes
    assert all(not pauses_risk_increasing(code) for code in codes)
