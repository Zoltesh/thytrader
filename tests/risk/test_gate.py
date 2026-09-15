"""Pre-trade gate verdicts for deployments and long entries."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.risk.gate import ProposedEntry, evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.models import (
    CapitalAllocation,
    RiskDecision,
    RiskReasonCode,
    compiled_default_risk_policy,
)

_STRATEGY_A = UUID("01978a3e-5f2c-7d10-b3a4-0000000000b1")
_STRATEGY_B = UUID("01978a3e-5f2c-7d10-b3a4-0000000000b2")


def _deployment(
    *,
    strategy_id: UUID = _STRATEGY_A,
    product_id: str = "BTC-USD",
    mode: DeploymentMode = DeploymentMode.PAPER,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    phase: RuntimePhase = RuntimePhase.FLAT,
    paper_starting_cash: Decimal | None = Decimal("10000"),
) -> Deployment:
    """Build one occupied or stopped deployment for gate tests."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=strategy_id,
        product_id=product_id,
        mode=mode,
        status=status,
        cash=paper_starting_cash or Decimal("0"),
        phase=phase,
        created_at=now,
        updated_at=now,
        paper_starting_cash=paper_starting_cash,
    )


def test_default_policy_allows_a_new_paper_deployment() -> None:
    """Empty allowlist and allocations must admit a normal paper start."""
    verdict = evaluate_new_deployment(
        compiled_default_risk_policy(),
        mode=DeploymentMode.PAPER,
        product_id="ETH-USD",
        strategy_id=_STRATEGY_B,
        paper_starting_cash=Decimal("10000"),
        deployments=(),
    )
    assert verdict.decision is RiskDecision.ALLOW
    assert verdict.reason_code is RiskReasonCode.ALLOWED


def test_paused_deployments_occupy_running_slots() -> None:
    """Running and paused both consume max_concurrent_running_deployments."""
    policy = compiled_default_risk_policy().model_copy(
        update={"max_concurrent_running_deployments": 1}
    )
    paused = _deployment(status=DeploymentStatus.PAUSED)
    verdict = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="ETH-USD",
        strategy_id=_STRATEGY_B,
        paper_starting_cash=Decimal("10000"),
        deployments=(paused,),
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.MAX_RUNNING_DEPLOYMENTS


def test_paper_capital_and_allowlist_and_allocation_denials() -> None:
    """Deny over-book paper cash, products off a nonempty allowlist, and unlisted strategies."""
    policy = compiled_default_risk_policy().model_copy(
        update={
            "product_allowlist": ("BTC-USD",),
            "paper_capital_quote": "15000",
            "allocations": (CapitalAllocation(strategy_id=_STRATEGY_A, allocated_quote="12000"),),
        }
    )
    occupied = (_deployment(paper_starting_cash=Decimal("10000")),)
    capital = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="BTC-USD",
        strategy_id=_STRATEGY_A,
        paper_starting_cash=Decimal("6000"),
        deployments=occupied,
    )
    allowlist = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="ETH-USD",
        strategy_id=_STRATEGY_A,
        paper_starting_cash=Decimal("1000"),
        deployments=(),
    )
    unlisted = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="BTC-USD",
        strategy_id=_STRATEGY_B,
        paper_starting_cash=Decimal("1000"),
        deployments=(),
    )
    assert capital.reason_code is RiskReasonCode.PAPER_CAPITAL_EXCEEDED
    assert allowlist.reason_code is RiskReasonCode.PRODUCT_NOT_ALLOWLISTED
    assert unlisted.reason_code is RiskReasonCode.STRATEGY_NOT_ALLOCATED


def test_entry_open_slot_and_exposure_caps() -> None:
    """Open/pending phases occupy an open slot; exposure uses the paper book."""
    tight_slots = compiled_default_risk_policy().model_copy(
        update={"max_concurrent_open_positions": 1}
    )
    open_peer = DeploymentSnapshot(
        deployment=_deployment(strategy_id=_STRATEGY_A, phase=RuntimePhase.OPEN),
        orders=(),
        fills=(),
        position=None,
    )
    slot = evaluate_new_entry(
        tight_slots,
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="ETH-USD",
            strategy_id=_STRATEGY_B,
            notional=Decimal("100"),
        ),
        snapshots=(open_peer,),
    )
    tight_exposure = compiled_default_risk_policy().model_copy(
        update={"max_portfolio_exposure_fraction": "0.01"}
    )
    exposure = evaluate_new_entry(
        tight_exposure,
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="BTC-USD",
            strategy_id=_STRATEGY_A,
            notional=Decimal("2000"),
        ),
        snapshots=(),
    )
    assert slot.reason_code is RiskReasonCode.MAX_OPEN_POSITIONS
    assert exposure.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
