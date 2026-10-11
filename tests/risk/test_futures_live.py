"""Synthetic admission evidence for the unreachable live futures gate (ADR 0134)."""

from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal

import pytest

from tests.risk.test_futures_gate_caps import _CONTRACT, _NOW, _PERP, _book, _state
from thytrader.market_data.instruments import MaintenanceWindow
from thytrader.risk.futures_live import (
    FuturesVenueEvidence,
    LiveFuturesStart,
    live_futures_deployment_verdict,
    live_futures_entry_verdict,
)
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskPolicyDefinition, RiskReasonCode, compiled_default_risk_policy
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot


def policy() -> RiskPolicyDefinition:
    """A synthetic opted-in policy with a separate USD envelope."""
    return compiled_default_risk_policy().model_copy(
        update={
            "futures": FuturesRiskPolicy(
                live_enabled=True,
                live_capital_usd="10000",
                product_allowlist=(_PERP,),
                live_spot_collateral_reserve_quote="1000",
            )
        }
    )


def venue() -> FuturesVenueEvidence:
    """A complete flat CFM read with fresh catalog facts."""
    return FuturesVenueEvidence(
        observed_at=_NOW,
        buying_power_usd=Decimal(1000),
        initial_margin_usd=Decimal(0),
        positions={},
        external_order_products=frozenset(),
        killswitch_enabled=False,
        maintenance={},
        contract_sizes={_PERP: Decimal("0.01")},
    )


def book() -> DeploymentSnapshot:
    """A live book starts with zero ledger cash and an explicit allocation."""
    paper = _book(_PERP, cash="0")
    return replace(
        paper,
        deployment=replace(
            paper.deployment,
            mode=DeploymentMode.LIVE,
            allocated_capital=Decimal(1000),
            paper_starting_cash=None,
            initial_equity=Decimal(0),
        ),
    )


@pytest.mark.parametrize("quantity", ["0", "-0.01", "0.005", "0.015", "NaN", "Infinity"])
def test_invalid_contract_counts_are_denied(quantity: str) -> None:
    """An order must contain a positive whole number of contracts, not merely fit a cap."""
    snapshot = book()
    verdict = live_futures_entry_verdict(
        policy(),
        proposed=ProposedEntry(
            product_id=_PERP,
            strategy_id=snapshot.deployment.strategy_id,
            quantity=Decimal(quantity),
            notional=Decimal(1),
        ),
        book=snapshot,
        state=_state(snapshot),
        snapshots=(snapshot,),
        venue=venue(),
        as_of=_NOW,
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1"])
@pytest.mark.parametrize("field", ["buying_power_usd", "initial_margin_usd"])
def test_invalid_venue_amounts_are_rejected(field: str, amount: str) -> None:
    """Malformed numeric evidence cannot become admission authority."""
    with pytest.raises(ValueError):
        replace(venue(), **{field: Decimal(amount)})


@pytest.mark.parametrize(
    "observed", [_NOW.replace(tzinfo=None), _NOW.astimezone(timezone(timedelta(hours=1)))]
)
def test_venue_evidence_requires_utc(observed: object) -> None:
    """Naive or non-UTC evidence is refused at construction."""
    with pytest.raises(ValueError):
        replace(venue(), observed_at=observed)


def test_start_checks_killswitch() -> None:
    """A valid allocation does not override the venue killswitch at deployment admission."""
    start = LiveFuturesStart(
        Decimal(1000), _CONTRACT, replace(venue(), killswitch_enabled=True), _NOW
    )
    verdict = live_futures_deployment_verdict(
        policy(), covered=(_PERP,), deployments=(), start=start
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_VENUE_KILLSWITCH


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"contract_sizes": {_PERP: Decimal("0.02")}}, RiskReasonCode.FUTURES_CONTRACT_DRIFT),
        ({"buying_power_usd": Decimal(0)}, RiskReasonCode.FUTURES_BUYING_POWER_SHORT),
        ({"initial_margin_usd": Decimal(1000)}, RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT),
    ],
)
def test_start_checks_catalog_and_margin(changes: dict[str, object], code: RiskReasonCode) -> None:
    """Start admission checks the catalog and projected margin, not only entry admission."""
    start = LiveFuturesStart(
        Decimal(1000),
        _CONTRACT,
        replace(venue(), **changes),
        _NOW,
        proposed_initial_margin_usd=Decimal(100),
    )
    verdict = live_futures_deployment_verdict(
        policy(), covered=(_PERP,), deployments=(), start=start
    )
    assert verdict is not None
    assert verdict.reason_code is code


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity"])
def test_start_invalid_allocation_is_denied(amount: str) -> None:
    """Invalid capital never makes an envelope pass or raises Decimal comparison errors."""
    start = LiveFuturesStart(Decimal(amount), _CONTRACT, venue(), _NOW)
    verdict = live_futures_deployment_verdict(
        policy(), covered=(_PERP,), deployments=(), start=start
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_LIVE_CAPITAL_EXCEEDED


@pytest.mark.parametrize(
    "changes",
    [
        {"killswitch_enabled": 0},
        {"killswitch_enabled": "false"},
        {"buying_power_usd": 1000.0},
        {"positions": {_PERP: Decimal("NaN")}},
        {"positions": {_PERP: Decimal("0.5")}},
        {"contract_sizes": {_PERP: Decimal(0)}},
        {"maintenance": {_PERP: MaintenanceWindow(_NOW, _NOW - timedelta(seconds=1))}},
        {"maintenance": {_PERP: MaintenanceWindow(_NOW.replace(tzinfo=None), _NOW)}},
    ],
)
def test_malformed_venue_facts_are_rejected(changes: dict[str, object]) -> None:
    """Runtime annotations are not validation, including windows and killswitch booleans."""
    with pytest.raises(ValueError):
        replace(venue(), **changes)


def test_venue_evidence_defensively_copies_external_orders() -> None:
    """A caller cannot clear an already-observed external order to authorize admission."""
    products = {_PERP}
    evidence = replace(venue(), external_order_products=products)
    products.clear()
    assert evidence.external_order_products == frozenset({_PERP})


def test_usdt_reserve_cannot_authorize_usd_futures_margin() -> None:
    """USDT is not in the declared USD/USDC collateral group (ADR 0129)."""
    definition = policy().model_copy(update={"quote_currency": "USDT"})
    start = LiveFuturesStart(Decimal(1000), _CONTRACT, venue(), _NOW, Decimal(100))
    verdict = live_futures_deployment_verdict(
        definition, covered=(_PERP,), deployments=(), start=start
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN


def test_start_rejects_a_different_bound_product() -> None:
    """A same-sized contract of another product is not a binding for the proposed product."""
    contract = _CONTRACT.model_copy(update={"product_id": "ETP-20DEC30-CDE"})
    start = LiveFuturesStart(Decimal(1000), contract, venue(), _NOW, Decimal(100))
    verdict = live_futures_deployment_verdict(
        policy(), covered=(_PERP,), deployments=(), start=start
    )
    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FUTURES_CONTRACT_DRIFT
