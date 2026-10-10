"""Fleet entry clustering cap through the entry gate (ADR 0125)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.risk.breakers import EntryObservation
from thytrader.risk.entry_clustering import cluster_verdict
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    compiled_default_risk_policy,
)
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    OrderIntent,
    OrderKind,
    OrderSide,
    RuntimePhase,
)

_NOW = datetime(2026, 10, 9, 0, 1, tzinfo=UTC)
_STRATEGY = UUID("01978a3e-5f2c-7d10-b3a4-0000000000c1")


def _policy(*, cap: int | None = 2, minutes: int | None = 120) -> RiskPolicyDefinition:
    """Compiled envelope with the clustering cap set (or unset)."""
    return RiskPolicyDefinition.model_validate(
        {
            **compiled_default_risk_policy().model_dump(mode="python"),
            "max_fleet_entries_per_window": cap,
            "fleet_entry_window_minutes": minutes,
        }
    )


def _observation(as_of: datetime = _NOW) -> EntryObservation:
    """Observation that passes the compiled collar for BTC-USDC."""
    return EntryObservation(
        as_of=as_of,
        proposed_price=Decimal(100),
        reference_price=Decimal(100),
        marks={"BTC-USDC": Decimal(100)},
    )


def _book(
    *,
    entries: tuple[datetime, ...] = (),
    purpose: IntentPurpose = IntentPurpose.ENTRY,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    mode: DeploymentMode = DeploymentMode.PAPER,
    product_id: str = "BTC-USDC",
    intent_product: str = "",
) -> DeploymentSnapshot:
    """One flat book whose intents were created at ``entries``."""
    deployment = Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "c" * 64,
        strategy_id=_STRATEGY,
        product_id=product_id,
        mode=mode,
        status=status,
        cash=Decimal(1000),
        phase=RuntimePhase.FLAT,
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW - timedelta(days=1),
        paper_starting_cash=Decimal(1000),
    )
    intents = tuple(
        OrderIntent(
            id=uuid4(),
            deployment_id=deployment.id,
            client_order_id=f"client-{index}",
            purpose=purpose,
            side=OrderSide.BUY,
            kind=OrderKind.POST_ONLY_LIMIT,
            quantity=Decimal("0.01"),
            created_at=created_at,
            candle_starts_at=created_at,
            price=Decimal(100),
            product_id=intent_product,
        )
        for index, created_at in enumerate(entries)
    )
    return DeploymentSnapshot(deployment=deployment, intents=intents)


def _proposed(
    *, pyramid: bool = False, readmits: bool = False, in_kind: bool = False
) -> ProposedEntry:
    """A small BTC-USDC entry; flags select a pyramid add, a reprice, or an adoption."""
    return ProposedEntry(
        product_id="BTC-USDC",
        strategy_id=_STRATEGY,
        notional=Decimal(10),
        is_pyramid_add=pyramid,
        funding="in_kind" if in_kind else "quote",
        readmits_working_entry=readmits,
    )


def _verdict(
    policy: RiskPolicyDefinition,
    snapshots: tuple[DeploymentSnapshot, ...],
    *,
    proposed: ProposedEntry | None = None,
    mode: DeploymentMode = DeploymentMode.PAPER,
    observation: EntryObservation | None = None,
) -> RiskDecision:
    """Run the cluster check alone and return its decision."""
    verdict = cluster_verdict(
        policy,
        mode=mode,
        proposed=proposed or _proposed(),
        snapshots=snapshots,
        observation=observation or _observation(),
    )
    return RiskDecision.ALLOW if verdict is None else verdict.decision


def test_unset_cap_allows_even_without_an_observation() -> None:
    """No fields means today's behaviour: nothing is counted and nothing denies."""
    busy = tuple(_book(entries=(_NOW,)) for _ in range(5))
    policy = _policy(cap=None, minutes=None)

    assert (
        cluster_verdict(
            policy,
            mode=DeploymentMode.PAPER,
            proposed=_proposed(),
            snapshots=busy,
            observation=None,
        )
        is None
    )


def test_cap_denies_once_distinct_entries_reach_it() -> None:
    """Two books entered in the window, cap 2: the third is denied with a full detail."""
    first = _NOW - timedelta(minutes=30)
    second = _NOW - timedelta(minutes=5)
    books = (_book(entries=(first,)), _book(entries=(second,)), _book())

    verdict = cluster_verdict(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=books,
        observation=_observation(),
    )

    assert verdict is not None
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.FLEET_ENTRY_CLUSTER_LIMIT
    assert verdict.detail == (
        "Fleet entry cluster limit: 2 new entries in the last 120 minutes, cap=2; "
        f"oldest at {first.isoformat()}, a slot frees at "
        f"{(first + timedelta(minutes=120)).isoformat()}."
    )


def test_below_the_cap_allows() -> None:
    """One counted entry under a cap of 2 admits the next."""
    assert _verdict(_policy(), (_book(entries=(_NOW,)), _book())) is RiskDecision.ALLOW


def test_one_book_product_counts_once() -> None:
    """Several entry intents of one book on one product are one entry."""
    many = _book(entries=(_NOW - timedelta(minutes=50), _NOW - timedelta(minutes=10), _NOW))

    assert _verdict(_policy(), (many,)) is RiskDecision.ALLOW


def test_one_book_on_two_products_counts_twice() -> None:
    """A multi-product book's entries on distinct products are distinct entries."""
    btc = _book(entries=(_NOW,), intent_product="BTC-USDC")
    eth = _book(entries=(_NOW,), intent_product="ETH-USDC")
    merged = DeploymentSnapshot(
        deployment=btc.deployment,
        intents=(*btc.intents, replace(eth.intents[0], deployment_id=btc.deployment.id)),
    )

    assert _verdict(_policy(), (merged,)) is RiskDecision.DENY


def test_window_edges_are_inclusive_at_the_start() -> None:
    """An entry exactly ``window`` old still counts; one second older does not."""
    edge = _NOW - timedelta(minutes=120)
    at_edge = (_book(entries=(edge,)), _book(entries=(_NOW,)))
    aged_out = (_book(entries=(edge - timedelta(seconds=1),)), _book(entries=(_NOW,)))

    assert _verdict(_policy(), at_edge) is RiskDecision.DENY
    assert _verdict(_policy(), aged_out) is RiskDecision.ALLOW


def test_a_slot_frees_when_the_pairs_newest_entry_ages_out() -> None:
    """The detail reports when enough pairs leave the window, using each pair's newest entry."""
    old_then_new = _book(entries=(_NOW - timedelta(minutes=100), _NOW - timedelta(minutes=20)))
    recent = _book(entries=(_NOW - timedelta(minutes=10),))

    verdict = cluster_verdict(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(old_then_new, recent),
        observation=_observation(),
    )

    assert verdict is not None
    oldest = _NOW - timedelta(minutes=20)
    assert f"oldest at {oldest.isoformat()}" in verdict.detail
    assert f"frees at {(oldest + timedelta(minutes=120)).isoformat()}" in verdict.detail


def test_adoption_intents_are_not_entries() -> None:
    """ADR 0124 adoptions never count toward the cluster."""
    adopted = tuple(_book(entries=(_NOW,), purpose=IntentPurpose.ADOPTION) for _ in range(3))

    assert _verdict(_policy(), adopted) is RiskDecision.ALLOW


def test_protective_intents_are_not_entries() -> None:
    """Stops, targets and exits never count."""
    exits = tuple(
        _book(entries=(_NOW,), purpose=purpose)
        for purpose in (IntentPurpose.STOP, IntentPurpose.TAKE_PROFIT, IntentPurpose.SIGNAL_EXIT)
    )

    assert _verdict(_policy(), exits) is RiskDecision.ALLOW


def test_stopped_books_still_count() -> None:
    """A bot that entered and was then stopped still entered in the window."""
    books = (
        _book(entries=(_NOW,), status=DeploymentStatus.STOPPED),
        _book(entries=(_NOW,), status=DeploymentStatus.PAUSED),
    )

    assert _verdict(_policy(), books) is RiskDecision.DENY


def test_other_mode_books_do_not_count() -> None:
    """Paper entries never consume the live cluster, and vice versa."""
    paper = (_book(entries=(_NOW,)), _book(entries=(_NOW,)))

    assert _verdict(_policy(), paper, mode=DeploymentMode.LIVE) is RiskDecision.ALLOW


def test_pyramid_adds_are_gated() -> None:
    """A same-side add is a new risk-increasing entry and is denied at the cap."""
    books = (_book(entries=(_NOW,)), _book(entries=(_NOW,)))

    assert _verdict(_policy(), books, proposed=_proposed(pyramid=True)) is RiskDecision.DENY


def test_reprice_of_an_admitted_working_entry_is_not_gated() -> None:
    """A reprice re-admits an entry already counted; the cap does not deny it."""
    books = (_book(entries=(_NOW,)), _book(entries=(_NOW,)))

    assert _verdict(_policy(), books, proposed=_proposed(readmits=True)) is RiskDecision.ALLOW


def test_in_kind_adoption_is_not_gated() -> None:
    """An adoption sends nothing to the venue and skips the cluster cap."""
    books = (_book(entries=(_NOW,)), _book(entries=(_NOW,)))

    assert _verdict(_policy(), books, proposed=_proposed(in_kind=True)) is RiskDecision.ALLOW


def test_missing_observation_denies_when_the_cap_is_set() -> None:
    """Without an as_of the window has no anchor, so the set cap fails closed."""
    verdict = cluster_verdict(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(),
        observation=None,
    )

    assert verdict is not None
    assert verdict.reason_code is RiskReasonCode.FLEET_ENTRY_CLUSTER_LIMIT


@pytest.mark.parametrize("observed", [True, False])
def test_gate_applies_the_cluster_cap(*, observed: bool) -> None:
    """``evaluate_new_entry`` denies at the cap with or without an observation."""
    books = (_book(entries=(_NOW,)), _book(entries=(_NOW,)))

    verdict = evaluate_new_entry(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=books,
        observation=_observation() if observed else None,
    )

    assert verdict.reason_code is RiskReasonCode.FLEET_ENTRY_CLUSTER_LIMIT


def test_gate_without_the_cap_is_unchanged() -> None:
    """The same busy fleet is admitted when the policy leaves the cap unset."""
    books = (_book(entries=(_NOW,)), _book(entries=(_NOW,)))

    verdict = evaluate_new_entry(
        _policy(cap=None, minutes=None),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=books,
        observation=_observation(),
    )

    assert verdict.decision is RiskDecision.ALLOW


def test_gate_reprice_skips_the_cap() -> None:
    """A reprice reaches the gate with ``readmits_working_entry`` and is admitted."""
    books = (_book(entries=(_NOW,)), _book(entries=(_NOW,)))

    verdict = evaluate_new_entry(
        _policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(readmits=True),
        snapshots=books,
        observation=_observation(),
    )

    assert verdict.decision is RiskDecision.ALLOW
