"""The futures entry gate's policy caps and base-unit beta netting (ADR 0129 §5, §6, P1-5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from thytrader.evaluation.futures_spec import InstrumentContract
from thytrader.risk.beta import BetaEstimate, BetaEvidence
from thytrader.risk.beta_exposure import beta_verdict
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.futures_beta import FuturesLegs, futures_beta_verdict
from thytrader.risk.futures_entry import evaluate_futures_entry
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    RiskPolicyDefinition,
    RiskReasonCode,
    compiled_default_risk_policy,
    risk_policy_fingerprint,
)
from thytrader.trading.futures_book import (
    BoundFuturesContract,
    FuturesBookState,
    futures_book_scope,
)
from thytrader.trading.futures_sizing import FuturesMarginTerms
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Position,
    PositionSide,
    RuntimePhase,
)

_NOW = datetime(2026, 10, 12, 15, tzinfo=UTC)
_PERP = "BIP-20DEC30-CDE"
_STRATEGY = UUID("01978a3e-5f2c-7d10-b3a4-0000000000e1")
_CONTRACT = InstrumentContract(
    product_id=_PERP,
    kind="perpetual_future",
    underlying="BTC",
    contract_size="0.01",
    listed_expiry=date(2030, 12, 20),
    catalog_fingerprint="sha256:" + "c" * 64,
)
_MARGIN = FuturesMarginTerms(
    contract_size=Decimal("0.01"),
    long_rate=Decimal("0.2"),
    short_rate=Decimal("0.25"),
    maintenance_fraction=Decimal(1),
    min_buffer_fraction=Decimal("0.5"),
    max_leverage=Decimal(2),
    fee_per_contract=Decimal("0.15"),
)


def _policy(**futures: Any) -> RiskPolicyDefinition:
    """The compiled policy with a 10000 USD paper futures envelope plus ``futures`` fields."""
    return compiled_default_risk_policy().model_copy(
        update={"futures": FuturesRiskPolicy(paper_capital_usd="10000", **futures)}
    )


def _book(
    product_id: str,
    *,
    quantity: str = "0",
    price: str = "100",
    side: PositionSide = PositionSide.LONG,
    cash: str = "10000",
) -> DeploymentSnapshot:
    """One running paper book, optionally holding ``quantity`` base at ``price``."""
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=uuid4(),
        product_id=product_id,
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal(cash),
        phase=RuntimePhase.OPEN if Decimal(quantity) else RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal(10000),
        initial_equity=Decimal(10000),
    )
    if Decimal(quantity) == 0:
        return DeploymentSnapshot(deployment=deployment)
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal(quantity),
        entry_price=Decimal(price),
        stop_price=Decimal(1) if side is PositionSide.LONG else Decimal(10000),
        target_price=None,
        entered_bar=_NOW - timedelta(hours=2),
        updated_at=_NOW - timedelta(hours=2),
        side=side,
        product_id=product_id,
    )
    return DeploymentSnapshot(deployment=deployment, position=position, positions=(position,))


def _state(book: DeploymentSnapshot, **changes: Any) -> FuturesBookState:
    """A fully known long futures book state."""
    state = FuturesBookState(
        deployment_id=book.deployment.id,
        product_id=_PERP,
        side="long",
        binding=BoundFuturesContract(
            deployment_id=book.deployment.id,
            contract=_CONTRACT,
            fee_per_contract=Decimal("0.15"),
            bound_at=_NOW,
        ),
        margin=_MARGIN,
        latest_funding_rate=Decimal("0.00001"),
    )
    return replace(state, **changes)


def _observation(**marks: str) -> EntryObservation:
    """A bar observation with a 100 USD futures mark plus ``marks``."""
    return EntryObservation(
        as_of=_NOW,
        proposed_price=Decimal(100),
        reference_price=Decimal(100),
        marks={_PERP: Decimal(100), **{k.replace("_", "-"): Decimal(v) for k, v in marks.items()}},
    )


def _futures_entry(
    policy: RiskPolicyDefinition,
    *,
    notional: str = "1000",
    quantity: str = "10",
    others: tuple[DeploymentSnapshot, ...] = (),
    state_changes: dict[str, Any] | None = None,
) -> RiskReasonCode:
    """The verdict code of one paper futures entry by a flat book."""
    book = _book(_PERP)
    with futures_book_scope(_state(book, **(state_changes or {}))):
        verdict = evaluate_futures_entry(
            policy,
            mode=DeploymentMode.PAPER,
            proposed=ProposedEntry(
                product_id=_PERP,
                strategy_id=_STRATEGY,
                notional=Decimal(notional),
                quantity=Decimal(quantity),
            ),
            snapshots=(book, *others),
            observation=_observation(),
        )
    return verdict.reason_code


def test_existing_futures_blocks_keep_their_policy_fingerprint() -> None:
    """Every P1-5 field is excluded while unset."""
    block = FuturesRiskPolicy(live_spot_collateral_reserve_quote="500")
    dumped = json.loads(block.model_dump_json())
    assert dumped == {"live_spot_collateral_reserve_quote": "500", "peg_haircut": "1.25"}
    policy = compiled_default_risk_policy().model_copy(update={"futures": block})
    assert risk_policy_fingerprint(policy).startswith("sha256:")
    assert block.liquidation_buffer_fraction == Decimal("0.5")
    assert FuturesRiskPolicy(min_liquidation_buffer_fraction="0.3").liquidation_buffer_fraction == (
        Decimal("0.3")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_leverage", "0.5"),
        ("max_leverage", "21"),
        ("min_liquidation_buffer_fraction", "1"),
        ("max_exposure_fraction", "0"),
        ("max_hourly_funding_rate_abs", "0.02"),
        ("max_daily_loss_usd", "0"),
        ("max_btc_beta_exposure_fraction", "25"),
    ],
)
def test_out_of_range_futures_caps_are_refused(field: str, value: str) -> None:
    """Each cap validates its range."""
    with pytest.raises(ValueError, match="futures"):
        FuturesRiskPolicy.model_validate({field: value})


def test_unset_caps_admit_a_known_entry() -> None:
    """A 1000 USD entry by a known flat book passes every check."""
    assert _futures_entry(_policy()) is RiskReasonCode.ALLOWED


def test_contracts_per_order_cap() -> None:
    """Ten contracts of 0.01 BTC per order is 1000 contracts."""
    assert (
        _futures_entry(_policy(max_order_contracts=999))
        is RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED
    )
    assert _futures_entry(_policy(max_order_contracts=1000)) is RiskReasonCode.ALLOWED


def test_gross_exposure_cap_counts_every_paper_futures_book() -> None:
    """A 9500 USD short elsewhere plus a 1000 USD entry exceeds 1.0 x 10000 USD."""
    other = _book(_PERP, quantity="95", side=PositionSide.SHORT)
    assert (
        _futures_entry(_policy(max_exposure_fraction="1"), others=(other,))
        is RiskReasonCode.FUTURES_EXPOSURE_EXCEEDED
    )
    assert (
        _futures_entry(_policy(max_exposure_fraction="1.1"), others=(other,))
        is RiskReasonCode.ALLOWED
    )


def test_funding_rate_cap_denies_high_and_unknown_rates() -> None:
    """The latest settled rate must be known and within the cap."""
    capped = _policy(max_hourly_funding_rate_abs="0.0001")
    assert _futures_entry(capped) is RiskReasonCode.ALLOWED
    assert (
        _futures_entry(capped, state_changes={"latest_funding_rate": Decimal("-0.0002")})
        is RiskReasonCode.FUTURES_FUNDING_RATE_EXCEEDED
    )
    assert (
        _futures_entry(capped, state_changes={"latest_funding_rate": None})
        is RiskReasonCode.FUNDING_HISTORY_MISSING
    )


def test_policy_leverage_and_buffer_come_through_the_margin_terms() -> None:
    """The book's margin terms carry the lower leverage and the policy buffer."""
    tight = replace(_MARGIN, max_leverage=Decimal("0.05"))
    assert (
        _futures_entry(_policy(), state_changes={"margin": tight})
        is RiskReasonCode.FUTURES_LEVERAGE_EXCEEDED
    )
    buffered = replace(_MARGIN, min_buffer_fraction=Decimal("0.99"))
    assert (
        _futures_entry(_policy(), state_changes={"margin": buffered})
        is RiskReasonCode.FUTURES_LIQUIDATION_BUFFER
    )


def test_the_futures_daily_loss_ceiling_binds_paper() -> None:
    """A 300 USD loss today trips a 200 USD ceiling but not the 5% fraction (500 USD)."""
    losing = _book(_PERP, cash="9700")
    assert (
        _futures_entry(_policy(daily_loss_limit_fraction="0.05"), others=(losing,))
        is RiskReasonCode.ALLOWED
    )
    assert (
        _futures_entry(
            _policy(daily_loss_limit_fraction="0.05", max_daily_loss_usd="200"),
            others=(losing,),
        )
        is RiskReasonCode.DAILY_LOSS_LIMIT
    )


def _estimate(product_id: str, beta: str) -> BetaEstimate:
    """One fresh β estimate against BTC-USDC."""
    return BetaEstimate(
        product_id=product_id,
        reference_id="BTC-USDC",
        beta=Decimal(beta),
        returns=90,
        last_close=_NOW.replace(hour=0),
    )


def test_the_futures_scope_beta_cap() -> None:
    """Σ |futures notional| x β(<underlying>-USDC) against fraction x futures capital."""
    policy = _policy(max_btc_beta_exposure_fraction="0.5")
    held = _book(_PERP, quantity="30", side=PositionSide.SHORT)
    legs = FuturesLegs(underlyings={held.deployment.id: "BTC"})
    evidence = BetaEvidence(results={})

    def run(notional: str, legs_value: FuturesLegs | None = legs) -> RiskReasonCode | None:
        verdict = futures_beta_verdict(
            policy,
            mode=DeploymentMode.PAPER,
            proposed=ProposedEntry(
                product_id=_PERP, strategy_id=_STRATEGY, notional=Decimal(notional)
            ),
            proposing_underlying="BTC",
            snapshots=(held,),
            legs=legs_value,
            beta=evidence,
            capital=Decimal(10000),
            as_of=_NOW,
        )
        return None if verdict is None else verdict.reason_code

    assert run("2000") is None
    assert run("2001") is RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED
    assert run("100", FuturesLegs()) is RiskReasonCode.BTC_BETA_UNAVAILABLE
    assert run("100", None) is RiskReasonCode.BTC_BETA_UNAVAILABLE


def _spot_beta(
    policy: RiskPolicyDefinition,
    *,
    hedge: DeploymentSnapshot | None,
    legs: FuturesLegs | None,
    mode: DeploymentMode = DeploymentMode.PAPER,
    marks: dict[str, Decimal] | None = None,
) -> RiskReasonCode | None:
    """The spot β cap for a 400 USD BTC-USDC long beside a 500 USD BTC-USDC long."""
    spot = _book("BTC-USDC", quantity="5")
    books = (spot,) if hedge is None else (spot, hedge)
    verdict = beta_verdict(
        policy,
        mode=mode,
        proposed=ProposedEntry(
            product_id="BTC-USDC",
            strategy_id=_STRATEGY,
            notional=Decimal(400),
            quantity=Decimal(4),
            side="long",
        ),
        occupied=(spot,),
        live_quote_cash=None,
        beta=BetaEvidence(results={}),
        as_of=_NOW,
        snapshots=books,
        legs=legs,
        marks=marks if marks is not None else {"BTC-USDC": Decimal(100), _PERP: Decimal(100)},
    )
    return None if verdict is None else verdict.reason_code


def _netting_policy(netting: str | None = "net_by_underlying") -> RiskPolicyDefinition:
    """A 600 USDC spot β cap (0.6 x 1000) with opt-in netting."""
    return RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="python"),
            "paper_capital_quote": "1000",
            "max_btc_beta_exposure_fraction": "0.6",
            "futures": {"paper_capital_usd": "10000", "beta_netting": netting},
        }
    )


def test_a_managed_futures_short_nets_spot_btc_in_base_units() -> None:
    """900 USDC gross BTC long exceeds 600; a 6 BTC futures short nets it to 300."""
    hedge = _book(_PERP, quantity="6", side=PositionSide.SHORT)
    legs = FuturesLegs(
        underlyings={hedge.deployment.id: "BTC"}, funding_current=frozenset({hedge.deployment.id})
    )
    assert _spot_beta(_netting_policy(), hedge=None, legs=legs) is (
        RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED
    )
    assert _spot_beta(_netting_policy(), hedge=hedge, legs=legs) is None


@pytest.mark.parametrize(
    "case",
    ["gross_policy", "funding_overdue", "unbound", "futures_mark_missing", "live", "same_side"],
)
def test_netting_falls_back_to_gross_whenever_a_leg_is_unknown(case: str) -> None:
    """Each missing condition keeps the gross figure, so the entry is denied."""
    side = PositionSide.LONG if case == "same_side" else PositionSide.SHORT
    hedge = _book(_PERP, quantity="6", side=side)
    legs = FuturesLegs(
        underlyings={} if case == "unbound" else {hedge.deployment.id: "BTC"},
        funding_current=frozenset()
        if case == "funding_overdue"
        else frozenset({hedge.deployment.id}),
    )
    marks = {"BTC-USDC": Decimal(100)}
    if case != "futures_mark_missing":
        marks[_PERP] = Decimal(100)
    verdict = _spot_beta(
        _netting_policy("gross" if case == "gross_policy" else "net_by_underlying"),
        hedge=hedge,
        legs=legs,
        mode=DeploymentMode.LIVE if case == "live" else DeploymentMode.PAPER,
        marks=marks,
    )
    assert verdict is RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED
