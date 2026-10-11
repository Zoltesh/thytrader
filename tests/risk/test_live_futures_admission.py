"""Live futures gate integration and every admission invariant, with synthetic facts."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from tests.risk.test_futures_gate_caps import _CONTRACT, _NOW, _PERP, _observation, _state
from tests.risk.test_futures_live import book, policy, venue
from thytrader.market_data.instruments import MaintenanceWindow
from thytrader.risk.futures_entry import futures_capital, linked_breaker_verdict
from thytrader.risk.futures_live import LiveFuturesStart, live_futures_deployment_verdict
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.gate import evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.futures_book import futures_book_equity, futures_book_scope
from thytrader.trading.models import DeploymentMode


def entry_code(
    *,
    venue_changes: dict[str, Any] | None = None,
    policy_changes: dict[str, Any] | None = None,
    notional: str = "1",
    quantity: str = "0.01",
) -> RiskReasonCode:
    """Exercise the public entry gate with a loaded live book; dictionaries vary test inputs."""
    snapshot = book()
    definition = policy()
    assert definition.futures is not None
    if policy_changes:
        block = definition.futures.model_dump() | policy_changes
        definition = definition.model_copy(
            update={"futures": FuturesRiskPolicy.model_validate(block)}
        )
    with futures_book_scope(_state(snapshot)):
        return evaluate_new_entry(
            definition,
            mode=DeploymentMode.LIVE,
            proposed=ProposedEntry(
                product_id=_PERP,
                strategy_id=snapshot.deployment.strategy_id,
                quantity=Decimal(quantity),
                notional=Decimal(notional),
            ),
            snapshots=(snapshot,),
            observation=_observation(),
            futures_venue=replace(venue(), **(venue_changes or {})),
        ).reason_code


def test_live_entry_uses_allocation_and_separate_capital() -> None:
    """Zero live ledger cash is not zero equity; the live envelope is not the paper envelope."""
    assert futures_book_equity(book(), {}) == Decimal(1000)
    assert futures_capital(policy(), DeploymentMode.LIVE) == Decimal(10000)
    assert futures_capital(policy(), DeploymentMode.PAPER) == Decimal(0)
    assert entry_code() is RiskReasonCode.ALLOWED
    snapshot = book()
    missing = replace(snapshot, deployment=replace(snapshot.deployment, allocated_capital=None))
    assert futures_book_equity(missing, {}) is None
    loss = replace(snapshot, deployment=replace(snapshot.deployment, cash=Decimal(-10)))
    assert futures_book_equity(loss, {}) == Decimal(990)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"live_enabled": False}, RiskReasonCode.FUTURES_LIVE_DISABLED),
        ({"live_capital_usd": None}, RiskReasonCode.FUTURES_LIVE_CAPITAL_UNSET),
        ({"product_allowlist": None}, RiskReasonCode.PRODUCT_NOT_ALLOWLISTED),
        (
            {"live_spot_collateral_reserve_quote": None},
            RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT,
        ),
    ],
)
def test_live_policy_denials(changes: dict[str, Any], code: RiskReasonCode) -> None:
    """Opt-in, capital, allowlist and reserve are independent requirements."""
    assert entry_code(policy_changes=changes) is code


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"observed_at": _NOW - timedelta(seconds=181)}, RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN),
        ({"observed_at": _NOW + timedelta(seconds=1)}, RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN),
        ({"positions": None}, RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN),
        ({"external_order_products": None}, RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN),
        ({"positions": {_PERP: Decimal(1)}}, RiskReasonCode.FUTURES_EXTERNAL_POSITION_ON_PRODUCT),
        (
            {"external_order_products": frozenset({_PERP})},
            RiskReasonCode.FUTURES_EXTERNAL_POSITION_ON_PRODUCT,
        ),
        ({"killswitch_enabled": None}, RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN),
        ({"killswitch_enabled": True}, RiskReasonCode.FUTURES_VENUE_KILLSWITCH),
        ({"buying_power_usd": None}, RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN),
        ({"buying_power_usd": Decimal(0)}, RiskReasonCode.FUTURES_BUYING_POWER_SHORT),
        ({"initial_margin_usd": None}, RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN),
        ({"contract_sizes": {}}, RiskReasonCode.FUTURES_CONTRACT_DRIFT),
        ({"contract_sizes": {_PERP: Decimal("0.02")}}, RiskReasonCode.FUTURES_CONTRACT_DRIFT),
        (
            {
                "maintenance": {
                    _PERP: MaintenanceWindow(
                        starts_at=_NOW + timedelta(hours=1), ends_at=_NOW + timedelta(hours=2)
                    )
                }
            },
            RiskReasonCode.FUTURES_VENUE_MAINTENANCE,
        ),
    ],
)
def test_live_venue_denials(changes: dict[str, Any], code: RiskReasonCode) -> None:
    """Unknown, stale, externally occupied, drifting or unavailable venue facts deny entries."""
    assert entry_code(venue_changes=changes) is code


def test_projected_reserve_includes_current_and_proposed_margin() -> None:
    """A 1000 USDC reserve covers 700 USD x 1.25, but not (700 + 200) USD x 1.25."""
    assert entry_code(venue_changes={"initial_margin_usd": Decimal(700)}, notional="1000") is (
        RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT
    )
    assert (
        entry_code(venue_changes={"observed_at": _NOW - timedelta(seconds=180)})
        is RiskReasonCode.ALLOWED
    )


@pytest.mark.parametrize("notional", ["0", "-1", "NaN", "sNaN", "Infinity"])
def test_invalid_notional_denies_without_arithmetic_errors(notional: str) -> None:
    """A malformed proposed margin fails closed before arithmetic."""
    assert entry_code(notional=notional) is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN


def test_live_start_separate_allowlist_envelope_and_exclusivity() -> None:
    """Spot-only allowlists do not veto futures; occupied live futures allocations do."""
    definition = policy().model_copy(update={"product_allowlist": ("BTC-USDC",)})
    start = LiveFuturesStart(Decimal(1000), _CONTRACT, venue(), _NOW, Decimal(100))
    assert (
        evaluate_new_deployment(
            definition,
            mode=DeploymentMode.LIVE,
            product_id=_PERP,
            strategy_id=None,
            paper_starting_cash=None,
            deployments=(),
            live_futures=start,
        ).reason_code
        is RiskReasonCode.ALLOWED
    )
    occupied = book().deployment
    verdict = live_futures_deployment_verdict(
        definition, covered=(_PERP,), deployments=(occupied,), start=start
    )
    assert verdict is not None and verdict.reason_code is RiskReasonCode.FUTURES_PRODUCT_OCCUPIED
    other = replace(occupied, product_id="ETP-20DEC30-CDE", allocated_capital=Decimal(9500))
    verdict = live_futures_deployment_verdict(
        definition, covered=(_PERP,), deployments=(other,), start=start
    )
    assert (
        verdict is not None and verdict.reason_code is RiskReasonCode.FUTURES_LIVE_CAPITAL_EXCEEDED
    )


@pytest.mark.parametrize("quote", ["USD", "USDC", "USDT"])
def test_live_linked_breakers_are_scoped_and_never_sum(quote: str) -> None:
    """Only same-mode managed CFM and USD/USDC scopes link; spot balances never enter the sum."""
    futures = book()
    spot = replace(
        futures,
        deployment=replace(
            futures.deployment,
            product_id=f"BTC-{quote}",
            daily_loss_latched=True,
            cash=Decimal("999999999"),
        ),
    )
    verdict = linked_breaker_verdict(
        mode=DeploymentMode.LIVE, product_id=_PERP, snapshots=(futures, spot)
    )
    assert (verdict is not None) is (quote != "USDT")
    latched = replace(futures, deployment=replace(futures.deployment, daily_loss_latched=True))
    verdict = linked_breaker_verdict(
        mode=DeploymentMode.LIVE, product_id=f"BTC-{quote}", snapshots=(latched,)
    )
    assert (verdict is not None) is (quote != "USDT")
    assert (
        linked_breaker_verdict(
            mode=DeploymentMode.PAPER, product_id=f"BTC-{quote}", snapshots=(latched,)
        )
        is None
    )
    assert futures_capital(policy(), DeploymentMode.LIVE) == Decimal(10000)
