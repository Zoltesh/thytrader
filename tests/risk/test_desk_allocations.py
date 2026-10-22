"""Live agent desks are admitted by their own policy reservation (ADR 0148)."""

from __future__ import annotations

from decimal import Decimal
import json
from uuid import UUID

import pytest

from tests.risk.test_start_safeguards import _REQUIRED
from thytrader.risk.gate import evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    CapitalAllocation,
    DeskAllocation,
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    RiskVerdict,
    canonical_risk_policy_bytes,
    compiled_default_risk_policy,
    risk_policy_fingerprint,
)
from thytrader.runtime_control.client import RuntimeControlError
from thytrader.runtime_control.configuration_handlers import _risk_policy_payload
from thytrader.runtime_control.parsers.root import _parser as build_parser
from thytrader.trading.models import DeploymentMode

_STRATEGY = UUID("01978a3e-5f2c-7d10-b3a4-0000000000b1")
_DESK = "01a1603f-0000-7000-8000-000000000001"


def _policy(*desks: DeskAllocation) -> RiskPolicyDefinition:
    """A published-style policy with one strategy allocation in force."""
    return RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="json"),
            "allocations": [CapitalAllocation(strategy_id=_STRATEGY, allocated_quote="100")],
            "desk_allocations": list(desks),
        }
    )


def test_a_policy_without_desk_allocations_keeps_its_bytes_and_fingerprint() -> None:
    """The field is omitted when empty, so existing policies keep their identity."""
    policy = compiled_default_risk_policy()
    assert "desk_allocations" not in json.loads(canonical_risk_policy_bytes(policy))
    listed = policy.model_copy(
        update={"desk_allocations": (DeskAllocation(desk_id=_DESK, allocated_quote="150"),)}
    )
    assert json.loads(canonical_risk_policy_bytes(listed))["desk_allocations"] == [
        {"desk_id": _DESK, "allocated_quote": "150"}
    ]
    assert risk_policy_fingerprint(listed) != risk_policy_fingerprint(policy)


def test_desk_ids_are_unique_and_count_against_paper_capital() -> None:
    """Duplicates are refused; desk reservations join the allocation total."""
    allocation = DeskAllocation(desk_id=_DESK, allocated_quote="150")
    with pytest.raises(ValueError, match="unique desk_id"):
        _policy(allocation, allocation)
    with pytest.raises(ValueError, match="cannot exceed paper_capital_quote"):
        _policy(DeskAllocation(desk_id=_DESK, allocated_quote="99999999"))


def test_a_listed_desk_trades_live_while_allocations_are_in_force() -> None:
    """Unlisted desks and other discretionary books stay denied, with a hint for the desk."""
    listed = _policy(DeskAllocation(desk_id=_DESK, allocated_quote="150"))

    def admit(policy: RiskPolicyDefinition, desk_id: str | None) -> RiskVerdict:
        return evaluate_new_deployment(
            policy,
            mode=DeploymentMode.LIVE,
            product_id="BTC-USD",
            strategy_id=None,
            paper_starting_cash=None,
            deployments=(),
            desk_id=desk_id,
        )

    allowed = admit(listed, _DESK)
    unlisted = admit(_policy(), _DESK)
    ticket = admit(listed, None)
    assert allowed.decision is RiskDecision.ALLOW
    assert unlisted.reason_code is RiskReasonCode.DISCRETIONARY_NOT_ALLOCATED
    assert "desk_allocations" in unlisted.detail
    assert ticket.reason_code is RiskReasonCode.DISCRETIONARY_NOT_ALLOCATED


def test_each_live_desk_order_is_bounded_by_its_allocation() -> None:
    """An order above the desk's reservation is denied; one within it is admitted."""
    policy = _policy(DeskAllocation(desk_id=_DESK, allocated_quote="150"))

    def entry(notional: str) -> RiskVerdict:
        return evaluate_new_entry(
            policy,
            mode=DeploymentMode.LIVE,
            proposed=ProposedEntry(
                product_id="BTC-USD",
                strategy_id=None,
                notional=Decimal(notional),
                desk_id=_DESK,
            ),
            snapshots=(),
            live_quote_cash=Decimal("10000"),
        )

    assert entry("50").decision is RiskDecision.ALLOW
    over = entry("151")
    assert over.reason_code is RiskReasonCode.ALLOCATION_EXCEEDED


def test_set_risk_policy_maps_desk_allocations() -> None:
    """--desk-allocation DESK_ID:QUOTE becomes desk_allocations; omitted, there are none."""
    parser = build_parser()
    plain = _risk_policy_payload(parser.parse_args(list(_REQUIRED)))
    listed = _risk_policy_payload(
        parser.parse_args([*_REQUIRED, "--desk-allocation", f"{_DESK}:150"])
    )
    assert plain["desk_allocations"] == ()
    assert listed["desk_allocations"] == ({"desk_id": _DESK, "allocated_quote": "150"},)
    with pytest.raises(RuntimeControlError, match="desk_id:allocated_quote"):
        _risk_policy_payload(parser.parse_args([*_REQUIRED, "--desk-allocation", "nope"]))


def test_a_desk_reservation_alone_keeps_other_live_orders_restricted() -> None:
    """With only desk_allocations, tickets and unlisted desks are still denied live."""
    policy = RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="json"),
            "desk_allocations": [DeskAllocation(desk_id=_DESK, allocated_quote="100")],
        }
    )

    def admit(desk_id: str | None) -> RiskVerdict:
        return evaluate_new_deployment(
            policy,
            mode=DeploymentMode.LIVE,
            product_id="BTC-USD",
            strategy_id=None,
            paper_starting_cash=None,
            deployments=(),
            desk_id=desk_id,
        )

    assert admit(_DESK).decision is RiskDecision.ALLOW
    assert admit(None).reason_code is RiskReasonCode.DISCRETIONARY_NOT_ALLOCATED
    assert admit("another-desk").reason_code is RiskReasonCode.DISCRETIONARY_NOT_ALLOCATED
