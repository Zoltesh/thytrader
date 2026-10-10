"""BTC-beta-weighted exposure cap (ADR 0125)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.risk.beta import (
    BetaEstimate,
    BetaEvidence,
    BetaResult,
    BetaUnavailable,
    BetaUnavailableReason,
)
from thytrader.risk.beta_exposure import beta_cap_applies, beta_products, beta_verdict
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    RiskVerdict,
    compiled_default_risk_policy,
)
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Position,
    PositionSide,
    RuntimePhase,
)

_NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
_CLOSE = datetime(2026, 10, 9, tzinfo=UTC)
_STRATEGY = UUID("01978a3e-5f2c-7d10-b3a4-0000000000d1")


def _policy(
    *, fraction: str | None = "0.6", quote: str | None = None, paper_capital: str = "1000"
) -> RiskPolicyDefinition:
    """Compiled envelope with the β cap fields set as given and a small paper book."""
    return RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="python"),
            "paper_capital_quote": paper_capital,
            "max_btc_beta_exposure_fraction": fraction,
            "max_btc_beta_exposure_quote": quote,
        }
    )


def _estimate(product_id: str, beta: str, *, last_close: datetime = _CLOSE) -> BetaEstimate:
    """A β estimate whose last daily bar closed at ``last_close``."""
    return BetaEstimate(
        product_id=product_id,
        reference_id="BTC-USDC",
        beta=Decimal(beta),
        returns=90,
        last_close=last_close,
    )


def _evidence(*results: BetaResult) -> BetaEvidence:
    """Evidence keyed by each result's product."""
    return BetaEvidence(results={item.product_id: item for item in results})


def _book(
    product_id: str,
    cost: str,
    *,
    side: PositionSide = PositionSide.LONG,
    mode: DeploymentMode = DeploymentMode.PAPER,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
) -> DeploymentSnapshot:
    """One book holding ``cost`` quote of ``product_id`` at price 1."""
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "d" * 64,
        strategy_id=_STRATEGY,
        product_id=product_id,
        mode=mode,
        status=status,
        cash=Decimal(1000),
        phase=RuntimePhase.OPEN,
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW - timedelta(days=1),
        paper_starting_cash=Decimal(1000),
    )
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal(cost),
        entry_price=Decimal(1),
        stop_price=Decimal("0.5") if side is PositionSide.LONG else Decimal(2),
        target_price=None,
        entered_bar=_NOW - timedelta(hours=2),
        updated_at=_NOW - timedelta(hours=2),
        side=side,
        product_id=product_id,
    )
    return DeploymentSnapshot(deployment=deployment, position=position, positions=(position,))


def _proposed(
    product_id: str = "SOL-USDC", notional: str = "100", *, in_kind: bool = False
) -> ProposedEntry:
    """One entry of ``notional`` quote."""
    return ProposedEntry(
        product_id=product_id,
        strategy_id=_STRATEGY,
        notional=Decimal(notional),
        funding="in_kind" if in_kind else "quote",
    )


def _verdict(
    policy: RiskPolicyDefinition,
    books: tuple[DeploymentSnapshot, ...],
    beta: BetaEvidence | None,
    *,
    proposed: ProposedEntry | None = None,
    mode: DeploymentMode = DeploymentMode.PAPER,
    live_quote_cash: Decimal | None = None,
    as_of: datetime | None = _NOW,
) -> RiskVerdict | None:
    """Run the β cap alone."""
    return beta_verdict(
        policy,
        mode=mode,
        proposed=proposed or _proposed(),
        occupied=books,
        live_quote_cash=live_quote_cash,
        beta=beta,
        as_of=as_of,
    )


def test_unset_cap_needs_no_evidence() -> None:
    """No β fields: no objection even with no evidence, no as_of, and a huge entry."""
    policy = _policy(fraction=None)

    assert beta_cap_applies(policy, DeploymentMode.PAPER) is False
    assert beta_cap_applies(policy, DeploymentMode.LIVE) is False
    assert _verdict(policy, (_book("SOL-USDC", "900"),), None, as_of=None) is None


def test_absolute_cap_binds_live_only() -> None:
    """A quote cap alone does not bind paper, so paper needs no β evidence."""
    policy = _policy(fraction=None, quote="100")

    assert beta_cap_applies(policy, DeploymentMode.PAPER) is False
    assert beta_cap_applies(policy, DeploymentMode.LIVE) is True
    assert _verdict(policy, (), None) is None
    denied = _verdict(
        policy,
        (),
        _evidence(_estimate("SOL-USDC", "1.5")),
        mode=DeploymentMode.LIVE,
        live_quote_cash=Decimal(1000),
    )
    assert denied is not None
    assert denied.reason_code is RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED
    assert "cap=100.00" in denied.detail


def test_cap_admits_up_to_the_weighted_limit() -> None:
    """Capital 1000 x 0.6 = 600: 300 ETH x 1.2 + 160 SOL x 1.5 = 600 is admitted."""
    books = (_book("ETH-USDC", "300"),)
    beta = _evidence(_estimate("ETH-USDC", "1.2"), _estimate("SOL-USDC", "1.5"))

    assert _verdict(_policy(), books, beta, proposed=_proposed(notional="160")) is None
    denied = _verdict(_policy(), books, beta, proposed=_proposed(notional="160.01"))
    assert denied is not None
    assert denied.reason_code is RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED


def test_exceeded_detail_names_every_term() -> None:
    """The denial carries existing, proposed x β, cap, capital, fraction and absolute."""
    books = (_book("ETH-USDC", "300"),)
    beta = _evidence(_estimate("ETH-USDC", "1.2"), _estimate("SOL-USDC", "1.5"))

    denied = _verdict(_policy(), books, beta, proposed=_proposed(notional="200"))

    assert denied is not None
    assert denied.detail == (
        "BTC-beta exposure exceeded (reference BTC-USDC): existing=360.00, "
        "proposed=200.00*β1.5=300.00, cap=600.00, capital=1000.00, fraction=0.6, "
        "absolute=None."
    )


def test_btc_itself_is_beta_one() -> None:
    """The reference needs no estimate: 600 of BTC fills a 0.6 cap exactly."""
    assert (
        _verdict(_policy(), (), BetaEvidence(results={}), proposed=_proposed("BTC-USDC", "600"))
        is None
    )
    assert (
        _verdict(_policy(), (), BetaEvidence(results={}), proposed=_proposed("BTC-USDC", "601"))
        is not None
    )


def test_shorts_count_gross() -> None:
    """A short never hedges a long: both add their β-weighted exposure."""
    books = (_book("ETH-USDC", "200"), _book("SOL-USDC", "200", side=PositionSide.SHORT))
    beta = _evidence(_estimate("ETH-USDC", "1"), _estimate("SOL-USDC", "1"))

    denied = _verdict(_policy(), books, beta, proposed=_proposed(notional="201"))

    assert denied is not None
    assert "existing=400.00" in denied.detail


def test_zero_beta_floor_admits_uncorrelated_exposure() -> None:
    """An estimate clamped at 0 adds nothing to the β-weighted sum."""
    beta = _evidence(_estimate("PAXG-USDC", "0"))

    assert _verdict(_policy(), (), beta, proposed=_proposed("PAXG-USDC", "5000")) is None


@pytest.mark.parametrize(
    ("result", "cause"),
    [
        (
            BetaUnavailable("SOL-USDC", "BTC-USDC", BetaUnavailableReason.INSUFFICIENT_HISTORY, 42),
            "insufficient_history n=42<60",
        ),
        (
            BetaUnavailable("SOL-USDC", "BTC-USDC", BetaUnavailableReason.FETCH_FAILED),
            "fetch_failed",
        ),
    ],
)
def test_unavailable_proposed_beta_denies(result: BetaResult, cause: str) -> None:
    """No β for the new product fails closed and names the cause."""
    denied = _verdict(_policy(), (), _evidence(result))

    assert denied is not None
    assert denied.reason_code is RiskReasonCode.BTC_BETA_UNAVAILABLE
    assert denied.detail == (
        f"BTC beta unavailable for SOL-USDC vs BTC-USDC: {cause}; new entries blocked."
    )


def test_held_product_without_beta_denies_every_entry() -> None:
    """A held same-quote product with unknown β blocks even a BTC entry."""
    books = (_book("NEW-USDC", "10"),)

    denied = _verdict(
        _policy(), books, BetaEvidence(results={}), proposed=_proposed("BTC-USDC", "1")
    )

    assert denied is not None
    assert denied.reason_code is RiskReasonCode.BTC_BETA_UNAVAILABLE
    assert "NEW-USDC vs BTC-USDC: not_loaded" in denied.detail


def test_stale_beta_denies_after_forty_eight_hours() -> None:
    """An estimate whose last bar closed over 48 h before as_of is unavailable."""
    old = _estimate("SOL-USDC", "1.5", last_close=_NOW - timedelta(hours=48, seconds=1))

    denied = _verdict(_policy(), (), _evidence(old))

    assert denied is not None
    assert denied.reason_code is RiskReasonCode.BTC_BETA_UNAVAILABLE
    assert "stale last_close=" in denied.detail
    fresh = _estimate("SOL-USDC", "1.5", last_close=_NOW - timedelta(hours=48))
    assert _verdict(_policy(), (), _evidence(fresh)) is None


def test_missing_evidence_or_time_denies_when_set() -> None:
    """With a cap set, unloaded evidence or no observation time is unknown, not zero."""
    beta = _evidence(_estimate("SOL-USDC", "1.5"))

    for verdict in (_verdict(_policy(), (), None), _verdict(_policy(), (), beta, as_of=None)):
        assert verdict is not None
        assert verdict.reason_code is RiskReasonCode.BTC_BETA_UNAVAILABLE
        assert "evidence was not loaded" in verdict.detail


def test_in_kind_adoption_is_capped_on_capital_including_its_notional() -> None:
    """An adoption adds its notional to live capital and its β exposure to the sum."""
    beta = _evidence(_estimate("SOL-USDC", "1.5"))
    adoption = _proposed(notional="400", in_kind=True)

    denied = _verdict(
        _policy(),
        (),
        beta,
        proposed=adoption,
        mode=DeploymentMode.LIVE,
        live_quote_cash=Decimal(500),
    )

    assert denied is not None
    assert "proposed=400.00*β1.5=600.00, cap=540.00, capital=900.00" in denied.detail


def test_beta_products_lists_proposed_then_held_same_quote_products() -> None:
    """The loader reads what the gate sums: proposed first, held same-quote products sorted."""
    books = (
        _book("SOL-USDC", "10"),
        _book("ETH-USDC", "10"),
        _book("ADA-USD", "10"),
        _book("DOGE-USDC", "10", mode=DeploymentMode.LIVE),
        _book("AVAX-USDC", "10", status=DeploymentStatus.STOPPED),
    )

    products = beta_products(books, mode=DeploymentMode.PAPER, product_id="ETH-USDC")

    assert products == ("ETH-USDC", "AVAX-USDC", "SOL-USDC")


def _observation() -> EntryObservation:
    """A collar-clean observation for SOL-USDC."""
    return EntryObservation(
        as_of=_NOW,
        proposed_price=Decimal(100),
        reference_price=Decimal(100),
        marks={"SOL-USDC": Decimal(100)},
    )


def test_gate_denies_when_the_cap_is_set_and_no_evidence_was_passed() -> None:
    """``evaluate_new_entry`` without ``beta`` denies once a β cap is published."""
    verdict = evaluate_new_entry(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(),
        observation=_observation(),
    )

    assert verdict.reason_code is RiskReasonCode.BTC_BETA_UNAVAILABLE


def test_gate_is_unchanged_without_the_cap() -> None:
    """With no β fields the same call is admitted without evidence."""
    verdict = evaluate_new_entry(
        _policy(fraction=None),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(),
        observation=_observation(),
    )

    assert verdict.decision is RiskDecision.ALLOW


def test_gate_admits_with_fresh_evidence_under_the_cap() -> None:
    """Fresh evidence and headroom pass the β cap and the rest of the gate."""
    verdict = evaluate_new_entry(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(),
        observation=_observation(),
        beta=_evidence(_estimate("SOL-USDC", "1.5")),
    )

    assert verdict.decision is RiskDecision.ALLOW
