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
    RiskPolicySource,
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
    discretionary = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="BTC-USD",
        strategy_id=None,
        paper_starting_cash=Decimal("1000"),
        deployments=(),
    )
    assert discretionary.reason_code is RiskReasonCode.DISCRETIONARY_NOT_ALLOCATED


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


def test_pyramid_add_denied_without_policy_flag() -> None:
    """Paper/live same-side adds require allow_intra_strategy_pyramiding."""
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="BTC-USD",
            strategy_id=_STRATEGY_A,
            notional=Decimal("100"),
            is_pyramid_add=True,
        ),
        snapshots=(),
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.PYRAMIDING_NOT_ALLOWED


def test_pyramid_add_skips_open_position_slot_cap() -> None:
    """Adds occupy an existing product book and must not consume a new concurrent slot."""
    policy = compiled_default_risk_policy().model_copy(
        update={
            "max_concurrent_open_positions": 1,
            "allow_intra_strategy_pyramiding": True,
        }
    )
    open_peer = DeploymentSnapshot(
        deployment=_deployment(strategy_id=_STRATEGY_A, phase=RuntimePhase.OPEN),
        orders=(),
        fills=(),
        position=None,
    )
    fresh = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="ETH-USD",
            strategy_id=_STRATEGY_B,
            notional=Decimal("100"),
        ),
        snapshots=(open_peer,),
    )
    add = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="BTC-USD",
            strategy_id=_STRATEGY_A,
            notional=Decimal("100"),
            is_pyramid_add=True,
        ),
        snapshots=(open_peer,),
    )
    assert fresh.reason_code is RiskReasonCode.MAX_OPEN_POSITIONS
    assert add.decision is RiskDecision.ALLOW
    assert add.reason_code is RiskReasonCode.ALLOWED


def test_live_entry_uses_remaining_quote_plus_marked_exposure() -> None:
    """Live entries fail closed without quote cash and admit when remaining cash covers them."""
    proposed = ProposedEntry(
        product_id="BTC-USD",
        strategy_id=_STRATEGY_A,
        notional=Decimal("100"),
    )
    missing = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        proposed=proposed,
        snapshots=(),
        live_quote_cash=None,
    )
    funded = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        proposed=proposed,
        snapshots=(),
        live_quote_cash=Decimal("10000"),
    )
    assert missing.decision is RiskDecision.DENY
    assert missing.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    assert funded.decision is RiskDecision.ALLOW


def test_per_product_exposure_cap_is_independent_of_portfolio_cap() -> None:
    """A product cap can deny even when the book still has unused portfolio room."""
    policy = compiled_default_risk_policy().model_copy(
        update={"per_product_max_exposure_fraction": "0.01"}
    )
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="BTC-USD",
            strategy_id=_STRATEGY_A,
            notional=Decimal("2000"),
        ),
        snapshots=(),
    )
    assert verdict.reason_code is RiskReasonCode.PRODUCT_EXPOSURE_EXCEEDED


def test_absolute_portfolio_exposure_cap_binds_tighter_than_the_fraction() -> None:
    """An absolute quote ceiling can deny even when the fractional cap has room (F25)."""
    policy = compiled_default_risk_policy().model_copy(
        update={"max_portfolio_exposure_quote": "500"}
    )
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="BTC-USD",
            strategy_id=_STRATEGY_A,
            notional=Decimal("600"),
        ),
        snapshots=(),
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    allowed = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=ProposedEntry(
            product_id="BTC-USD",
            strategy_id=_STRATEGY_A,
            notional=Decimal("400"),
        ),
        snapshots=(),
    )
    assert allowed.decision is RiskDecision.ALLOW


def test_live_deployment_requires_a_published_policy_not_the_compiled_default() -> None:
    """A fresh install's compiled fallback must not silently arm live orders (F25)."""
    denied = evaluate_new_deployment(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        product_id="BTC-USD",
        strategy_id=None,
        paper_starting_cash=None,
        deployments=(),
        policy_source=RiskPolicySource.COMPILED_DEFAULT,
    )
    assert denied.decision is RiskDecision.DENY
    assert denied.reason_code is RiskReasonCode.LIVE_REQUIRES_PUBLISHED_POLICY
    allowed = evaluate_new_deployment(
        compiled_default_risk_policy(),
        mode=DeploymentMode.LIVE,
        product_id="BTC-USD",
        strategy_id=None,
        paper_starting_cash=None,
        deployments=(),
        policy_source=RiskPolicySource.PUBLISHED,
    )
    assert allowed.decision is RiskDecision.ALLOW


def test_paper_deployment_is_unaffected_by_the_published_policy_requirement() -> None:
    """The live-only publication gate must not block paper starts under any source."""
    verdict = evaluate_new_deployment(
        compiled_default_risk_policy(),
        mode=DeploymentMode.PAPER,
        product_id="BTC-USD",
        strategy_id=None,
        paper_starting_cash=Decimal("10000"),
        deployments=(),
        policy_source=RiskPolicySource.COMPILED_DEFAULT,
    )
    assert verdict.decision is RiskDecision.ALLOW
