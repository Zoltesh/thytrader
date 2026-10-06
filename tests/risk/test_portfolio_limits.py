"""Portfolio limits in the entry gate and live allocation membership (ADR 0091)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Position,
    RuntimePhase,
)
from thytrader.risk.gate import (
    PortfolioRiskBook,
    ProposedEntry,
    evaluate_new_deployment,
    evaluate_new_entry,
    portfolio_exposure,
)
from thytrader.risk.models import (
    CapitalAllocation,
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    compiled_default_risk_policy,
)
from thytrader.risk.portfolio_scope import portfolio_risk_for, portfolio_risk_scope

_PORTFOLIO = UUID("01978a3e-5f2c-7d10-b3a4-00000000f001")
_OTHER_PORTFOLIO = UUID("01978a3e-5f2c-7d10-b3a4-00000000f002")
_SLEEVE_A = UUID("01978a3e-5f2c-7d10-b3a4-0000000000a1")
_SLEEVE_B = UUID("01978a3e-5f2c-7d10-b3a4-0000000000a2")
_LISTED = UUID("01978a3e-5f2c-7d10-b3a4-0000000000a9")
_NOW = datetime(2026, 10, 2, tzinfo=UTC)


def _book(
    *,
    total: str = "1",
    per_asset: str = "1",
    live: bool = False,
    breaker: RiskReasonCode | None = None,
) -> PortfolioRiskBook:
    """A 1,000-quote portfolio with the given caps."""
    return PortfolioRiskBook(
        portfolio_id=_PORTFOLIO,
        capital=Decimal("1000"),
        max_total_exposure_fraction=Decimal(total),
        max_per_asset_fraction=Decimal(per_asset),
        live=live,
        breaker_reason=breaker,
    )


def _sleeve(
    strategy_id: UUID,
    *,
    product_id: str = "BTC-USD",
    held: str | None = None,
    portfolio_id: UUID | None = _PORTFOLIO,
    mode: DeploymentMode = DeploymentMode.PAPER,
) -> DeploymentSnapshot:
    """One running sleeve book, optionally holding ``held`` quote of its product."""
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "b" * 64,
        strategy_id=strategy_id,
        product_id=product_id,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("500"),
        phase=RuntimePhase.OPEN if held is not None else RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal("500") if mode is DeploymentMode.PAPER else None,
        allocated_capital=Decimal("500"),
        portfolio_id=portfolio_id,
    )
    if held is None:
        return DeploymentSnapshot(deployment=deployment)
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal(held) / Decimal("100"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_NOW,
        updated_at=_NOW,
        product_id=product_id,
    )
    return DeploymentSnapshot(deployment=deployment, position=position, positions=(position,))


def _entry(strategy_id: UUID, notional: str, *, product_id: str = "BTC-USD") -> ProposedEntry:
    """A proposed sleeve entry."""
    return ProposedEntry(product_id=product_id, strategy_id=strategy_id, notional=Decimal(notional))


def _verdict(
    book: PortfolioRiskBook | None,
    proposed: ProposedEntry,
    snapshots: tuple[DeploymentSnapshot, ...],
    *,
    policy: RiskPolicyDefinition | None = None,
    mode: DeploymentMode = DeploymentMode.PAPER,
) -> tuple[RiskDecision, RiskReasonCode]:
    """Gate one entry and return its decision and reason."""
    verdict = evaluate_new_entry(
        policy or compiled_default_risk_policy(),
        mode=mode,
        proposed=proposed,
        snapshots=snapshots,
        live_quote_cash=Decimal("500") if mode is DeploymentMode.LIVE else None,
        portfolio=book,
    )
    return verdict.decision, verdict.reason_code


def test_total_exposure_cap_counts_every_sleeve_of_the_portfolio() -> None:
    """Held 300 + 200 across two sleeves plus 150 breaks a 60% cap on 1,000 capital."""
    held = (_sleeve(_SLEEVE_A, held="300"), _sleeve(_SLEEVE_B, product_id="ETH-USD", held="200"))
    assert _verdict(_book(total="0.6"), _entry(_SLEEVE_A, "150"), held) == (
        RiskDecision.DENY,
        RiskReasonCode.PORTFOLIO_TOTAL_EXPOSURE_LIMIT,
    )
    assert _verdict(_book(total="0.65"), _entry(_SLEEVE_A, "150"), held) == (
        RiskDecision.ALLOW,
        RiskReasonCode.ALLOWED,
    )


def test_other_portfolios_and_standalone_books_do_not_count() -> None:
    """Exposure of books outside this portfolio never consumes its caps."""
    outside = (
        _sleeve(_SLEEVE_B, held="900", portfolio_id=_OTHER_PORTFOLIO),
        _sleeve(_LISTED, held="900", portfolio_id=None),
    )
    assert _verdict(_book(total="0.2"), _entry(_SLEEVE_A, "150"), outside)[0] is (
        RiskDecision.ALLOW
    )


def test_per_asset_cap_sums_every_product_of_the_base_asset() -> None:
    """A shared portfolio cannot compare USD inventory to a USDC capital cap without FX."""
    held = (_sleeve(_SLEEVE_B, held="250"),)
    proposed = _entry(_SLEEVE_A, "100", product_id="BTC-USDC")
    assert _verdict(_book(per_asset="0.3"), proposed, held) == (
        RiskDecision.DENY,
        RiskReasonCode.PORTFOLIO_LIMITS_UNAVAILABLE,
    )
    other_asset = _entry(_SLEEVE_A, "100", product_id="ETH-USD")
    assert _verdict(_book(per_asset="0.3"), other_asset, held)[0] is RiskDecision.ALLOW


def test_a_latched_portfolio_breaker_blocks_every_new_entry() -> None:
    """While latched, entries are refused with the latch reason until an operator reset."""
    book = _book(breaker=RiskReasonCode.PORTFOLIO_DRAWDOWN_STOP)
    decision, reason = _verdict(book, _entry(_SLEEVE_A, "1"), ())
    assert (decision, reason) == (RiskDecision.DENY, RiskReasonCode.PORTFOLIO_BREAKER_LATCHED)


def test_an_unavailable_book_fails_closed() -> None:
    """A sleeve whose portfolio limits could not be loaded cannot enter."""
    decision, reason = _verdict(
        PortfolioRiskBook.unavailable(_PORTFOLIO), _entry(_SLEEVE_A, "1"), ()
    )
    assert (decision, reason) == (RiskDecision.DENY, RiskReasonCode.PORTFOLIO_LIMITS_UNAVAILABLE)


def test_account_policy_still_binds_after_portfolio_limits_pass() -> None:
    """The strictest limit wins: the account-wide exposure cap still denies."""
    policy = compiled_default_risk_policy().model_copy(
        update={"max_portfolio_exposure_fraction": "0.001"}
    )
    decision, reason = _verdict(_book(), _entry(_SLEEVE_A, "150"), (), policy=policy)
    assert (decision, reason) == (RiskDecision.DENY, RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED)


def _allocated_policy() -> RiskPolicyDefinition:
    """A policy whose allocations list only one other strategy (live is an allowlist)."""
    return compiled_default_risk_policy().model_copy(
        update={
            "allocations": (CapitalAllocation(strategy_id=_LISTED, allocated_quote="1000"),),
        }
    )


def test_live_sleeve_allocation_counts_as_membership_but_standalone_books_do_not() -> None:
    """Approach (a): a live portfolio's sleeves are members; standalone semantics stay."""
    policy = _allocated_policy()
    sleeve = _sleeve(_SLEEVE_A, mode=DeploymentMode.LIVE)
    standalone = _verdict(
        None, _entry(_SLEEVE_A, "10"), (sleeve,), policy=policy, mode=DeploymentMode.LIVE
    )
    assert standalone == (RiskDecision.DENY, RiskReasonCode.STRATEGY_NOT_ALLOCATED)
    member = _verdict(
        _book(live=True),
        _entry(_SLEEVE_A, "10"),
        (sleeve,),
        policy=policy,
        mode=DeploymentMode.LIVE,
    )
    assert member == (RiskDecision.ALLOW, RiskReasonCode.ALLOWED)
    paper_book = _verdict(
        _book(live=False),
        _entry(_SLEEVE_A, "10"),
        (sleeve,),
        policy=policy,
        mode=DeploymentMode.LIVE,
    )
    assert paper_book == (RiskDecision.DENY, RiskReasonCode.STRATEGY_NOT_ALLOCATED)


@pytest.mark.parametrize(
    ("sleeve", "expected"), [(False, RiskDecision.DENY), (True, RiskDecision.ALLOW)]
)
def test_live_start_membership_follows_the_portfolio_sleeve_flag(
    sleeve: bool, expected: RiskDecision
) -> None:
    """A live portfolio start admits unlisted sleeves; a standalone live start does not."""
    verdict = evaluate_new_deployment(
        _allocated_policy(),
        mode=DeploymentMode.LIVE,
        product_id="BTC-USD",
        strategy_id=_SLEEVE_A,
        paper_starting_cash=None,
        deployments=(),
        portfolio_sleeve=sleeve,
    )
    assert verdict.decision is expected


def test_portfolio_exposure_reports_total_and_assets() -> None:
    """The gate's exposure view of one portfolio."""
    exposure = portfolio_exposure(
        _PORTFOLIO,
        (
            _sleeve(_SLEEVE_A, held="300"),
            _sleeve(_SLEEVE_B, product_id="ETH-USD", held="200"),
            _sleeve(_LISTED, held="999", portfolio_id=None),
        ),
    )
    assert exposure.total == Decimal("500")
    assert dict(exposure.assets) == {"BTC": Decimal("300"), "ETH": Decimal("200")}


def test_scope_lookup_fails_closed_for_unbound_sleeves() -> None:
    """A tagged book with no (or another portfolio's) bound book gets an unavailable one."""
    tagged = _sleeve(_SLEEVE_A).deployment
    standalone = _sleeve(_SLEEVE_A, portfolio_id=None).deployment
    assert portfolio_risk_for(standalone) is None
    unbound = portfolio_risk_for(tagged)
    assert unbound is not None
    assert unbound.available is False
    with portfolio_risk_scope(_book()):
        bound = portfolio_risk_for(tagged)
    assert bound == _book()
    other = PortfolioRiskBook.unavailable(_OTHER_PORTFOLIO)
    with portfolio_risk_scope(other):
        mismatched = portfolio_risk_for(tagged)
    assert mismatched is not None
    assert mismatched.available is False
