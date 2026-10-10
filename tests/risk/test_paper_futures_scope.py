"""Paper futures books in the risk gate: their own scope, envelope and linked breakers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.risk.breakers import EntryObservation
from thytrader.risk.futures_entry import linked_breaker_verdict
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.gate import evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    compiled_default_risk_policy,
)
from thytrader.risk.opening_accounting import reconstruct_day_open
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    FundingCashFlow,
    RuntimePhase,
)

_NOW = datetime(2026, 10, 12, 15, tzinfo=UTC)
_PERP = "BIP-20DEC30-CDE"
_STRATEGY = UUID("01978a3e-5f2c-7d10-b3a4-0000000000c1")


def _policy(*, envelope: str | None = "100000") -> RiskPolicyDefinition:
    """The compiled policy, optionally with a paper futures envelope."""
    futures = None if envelope is None else FuturesRiskPolicy(paper_capital_usd=envelope)
    return compiled_default_risk_policy().model_copy(update={"futures": futures})


def _book(
    product_id: str,
    *,
    cash: str = "10000",
    latched: bool = False,
    mode: DeploymentMode = DeploymentMode.PAPER,
    created_at: datetime = _NOW,
) -> Deployment:
    """One running flat book."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=uuid4(),
        product_id=product_id,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=Decimal(cash),
        phase=RuntimePhase.FLAT,
        created_at=created_at,
        updated_at=created_at,
        paper_starting_cash=Decimal(cash),
        initial_equity=Decimal(cash),
        daily_loss_latched=latched,
    )


def _start(policy: RiskPolicyDefinition, cash: str, *existing: Deployment) -> RiskReasonCode:
    """The verdict code of one paper futures start."""
    return evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id=_PERP,
        strategy_id=_STRATEGY,
        paper_starting_cash=Decimal(cash),
        deployments=existing,
    ).reason_code


def test_paper_futures_starts_draw_from_their_own_usd_envelope() -> None:
    """Unset envelope refuses; futures books count against it; spot books never do."""
    assert _start(_policy(envelope=None), "1000") is RiskReasonCode.FUTURES_POLICY_UNSET
    futures_book = _book(_PERP, cash="60000")
    spot_book = _book("BTC-USDC", cash="90000")
    assert _start(_policy(), "50000", futures_book) is RiskReasonCode.FUTURES_PAPER_CAPITAL_EXCEEDED
    assert _start(_policy(), "40000", futures_book, spot_book) is RiskReasonCode.ALLOWED
    live = evaluate_new_deployment(
        _policy(),
        mode=DeploymentMode.LIVE,
        product_id=_PERP,
        strategy_id=_STRATEGY,
        paper_starting_cash=None,
        deployments=(),
    )
    assert live.reason_code is RiskReasonCode.FUTURES_LIVE_UNSUPPORTED


def test_a_futures_book_never_blocks_or_consumes_spot_paper_capital() -> None:
    """A spot paper start beside a futures book uses paper_capital_quote alone."""
    verdict = evaluate_new_deployment(
        _policy(),
        mode=DeploymentMode.PAPER,
        product_id="BTC-USDC",
        strategy_id=_STRATEGY,
        paper_starting_cash=Decimal("90000"),
        deployments=(_book(_PERP, cash="60000"),),
    )
    assert verdict.decision is RiskDecision.ALLOW


def _spot_entry(product_id: str, snapshots: tuple[DeploymentSnapshot, ...]) -> RiskReasonCode:
    """The verdict code of one spot paper entry beside the given books."""
    verdict = evaluate_new_entry(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(product_id=product_id, strategy_id=_STRATEGY, notional=Decimal(100)),
        snapshots=snapshots,
        observation=EntryObservation(
            as_of=_NOW,
            proposed_price=Decimal(100),
            reference_price=Decimal(100),
            marks={product_id: Decimal(100), _PERP: Decimal(100)},
        ),
    )
    return verdict.reason_code


def test_spot_entries_ignore_an_unlatched_futures_book() -> None:
    """The futures scope is separate: its book is neither summed nor unreadable for spot."""
    spot = DeploymentSnapshot(deployment=_book("BTC-USDC"))
    futures = DeploymentSnapshot(deployment=_book(_PERP))
    assert _spot_entry("BTC-USDC", (spot, futures)) is RiskReasonCode.ALLOWED


@pytest.mark.parametrize(
    ("product_id", "code"),
    [
        ("BTC-USDC", RiskReasonCode.SHARED_COLLATERAL_BREAKER),
        ("ETH-USD", RiskReasonCode.SHARED_COLLATERAL_BREAKER),
        ("BTC-USDT", RiskReasonCode.ALLOWED),
    ],
)
def test_a_latched_futures_breaker_denies_linked_spot_entries(
    product_id: str, code: RiskReasonCode
) -> None:
    """USD and USDC share collateral with CFM futures; USDT does not."""
    futures = DeploymentSnapshot(deployment=_book(_PERP, latched=True))
    spot = DeploymentSnapshot(deployment=_book(product_id))
    assert _spot_entry(product_id, (spot, futures)) is code


def test_a_latched_spot_breaker_denies_futures_entries_but_not_live() -> None:
    """The link runs both ways in paper; live futures are unmanaged and never linked."""
    latched_spot = DeploymentSnapshot(deployment=_book("BTC-USDC", latched=True))
    paper = linked_breaker_verdict(
        mode=DeploymentMode.PAPER, product_id=_PERP, snapshots=(latched_spot,)
    )
    assert paper is not None
    assert paper.reason_code is RiskReasonCode.SHARED_COLLATERAL_BREAKER
    assert "spot USDC" in paper.detail
    live_spot = DeploymentSnapshot(
        deployment=_book("BTC-USDC", latched=True, mode=DeploymentMode.LIVE)
    )
    assert (
        linked_breaker_verdict(mode=DeploymentMode.LIVE, product_id=_PERP, snapshots=(live_spot,))
        is None
    )


def test_futures_entries_without_a_loaded_book_are_denied() -> None:
    """No bound cycle state means the contract binding is unknown."""
    futures = DeploymentSnapshot(deployment=_book(_PERP))
    verdict = evaluate_new_entry(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(product_id=_PERP, strategy_id=_STRATEGY, notional=Decimal(100)),
        snapshots=(futures,),
    )
    assert verdict.reason_code is RiskReasonCode.FUTURES_CONTRACT_UNBOUND
    unset = evaluate_new_entry(
        _policy(envelope=None),
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(product_id=_PERP, strategy_id=_STRATEGY, notional=Decimal(100)),
        snapshots=(futures,),
    )
    assert unset.reason_code is RiskReasonCode.FUTURES_POLICY_UNSET


def test_opening_evidence_reverses_todays_funding() -> None:
    """Yesterday's book with funding today opens at cash minus today's funding."""
    yesterday = _NOW - timedelta(days=1)
    book = _book(_PERP, created_at=yesterday)

    def flow(hours: int, amount: str) -> FundingCashFlow:
        return FundingCashFlow(
            deployment_id=book.id,
            product_id=_PERP,
            funding_time=_NOW.replace(hour=0) + timedelta(hours=hours),
            signed_quantity=Decimal("0"),
            mark_price=Decimal(100),
            rate=Decimal("0.0001"),
            amount=Decimal(amount),
            applied_at=_NOW,
        )

    funding = (flow(-2, "-1.5"), flow(3, "-2"), flow(4, "0.5"))
    snapshot = DeploymentSnapshot(
        deployment=replace(book, cash=Decimal(10000) - Decimal("3")),
        funding=funding,
    )
    evidence = reconstruct_day_open(snapshot, as_of=_NOW)
    assert evidence is not None
    assert evidence.source == "per_product_applied_fills_and_funding_v1"
    assert evidence.equity == Decimal("9998.5")
    tampered = DeploymentSnapshot(deployment=snapshot.deployment, funding=funding[:2])
    assert reconstruct_day_open(tampered, as_of=_NOW) is None
