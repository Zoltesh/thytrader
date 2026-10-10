"""Fleet entry readiness agrees with the entry gate and names what blocks it (ADR 0130)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tests.fleet_incident_support import (
    EXPECTED_GAP,
    INCIDENT_AT,
    LEGACY_DEPLOYMENT_ID,
    NEAR_DEPLOYMENT_ID,
    incident,
    incident_policy,
)
from thytrader.risk.beta import BetaEvidence, BetaUnavailable, BetaUnavailableReason
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.fleet_entry_health import evaluate_scope, fleet_entry_scopes
from thytrader.risk.fleet_entry_models import BlockingBook, FleetScopeEvidence, FleetScopeHealth
from thytrader.risk.futures_collateral import FuturesCollateralEvidence, FuturesCollateralState
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskDecision, RiskPolicyDefinition, RiskReasonCode
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderIntent,
    OrderKind,
    OrderSide,
    Position,
    PositionSide,
    RuntimePhase,
)

_ABSENT = FuturesCollateralEvidence(
    state=FuturesCollateralState.ABSENT,
    observed_at=None,
    initial_margin_usd=None,
    open_orders_hold_usd=None,
    position_count=None,
    cause="No futures mirror is configured.",
)


def _observation(marks: dict[str, Decimal] | None = None) -> EntryObservation:
    """The evaluation time of the incident, with no proposed price."""
    return EntryObservation(
        as_of=INCIDENT_AT, proposed_price=None, reference_price=None, marks=marks or {}
    )


def _live_usdc(
    snapshots: tuple[DeploymentSnapshot, ...],
    *,
    policy: RiskPolicyDefinition | None = None,
    collateral: FuturesCollateralEvidence | None = _ABSENT,
    beta: BetaEvidence | None = None,
    marks: dict[str, Decimal] | None = None,
    unreadable: tuple[BlockingBook, ...] = (),
) -> FleetScopeHealth:
    """Evaluate the live USDC scope the way the worker does."""
    return evaluate_scope(
        policy or incident_policy(),
        snapshots=snapshots,
        evidence=FleetScopeEvidence(
            mode=DeploymentMode.LIVE, scope="USDC", beta=beta, futures_collateral=collateral
        ),
        observation=_observation(marks),
        unreadable=unreadable,
    )


def test_incident_blocks_live_usdc_and_names_the_legacy_order() -> None:
    """The incident failure is a fleet-wide evidence block naming order and sums."""
    books = incident()
    scope = _live_usdc(books.snapshots)
    assert scope.entries_admissible == "blocked"
    assert scope.reason_codes == ("BREAKER_MARK_MISSING",)
    assert scope.blocking_deployment_ids == (LEGACY_DEPLOYMENT_ID,)
    daily = next(check for check in scope.checks if check.name == "daily_loss")
    assert daily.blocker_class == "evidence"
    assert daily.fleet_wide is True
    (book,) = daily.books
    assert book.status == "stopped"
    assert EXPECTED_GAP in book.detail
    assert scope.running_deployments == 2
    assert [check.name for check in scope.alertable] == ["daily_loss"]


def test_the_gate_denies_a_running_book_for_the_same_reason() -> None:
    """The report cannot disagree with the gate: NEAR's entry is denied the same way."""
    books = incident()
    verdict = evaluate_new_entry(
        incident_policy(),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry(product_id="NEAR-USDC", strategy_id=uuid4(), notional=Decimal("10")),
        snapshots=books.snapshots,
        live_quote_cash=Decimal("300"),
        observation=EntryObservation(
            as_of=INCIDENT_AT,
            proposed_price=Decimal("3"),
            reference_price=Decimal("3"),
            marks={"NEAR-USDC": Decimal("3")},
        ),
        futures_collateral=_ABSENT,
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert str(LEGACY_DEPLOYMENT_ID) in verdict.detail


def test_repaired_fleet_admits_entries_and_the_gate_agrees() -> None:
    """After the guarded repair both the report and the gate admit."""
    books = incident(repaired=True)
    scope = _live_usdc(books.snapshots)
    assert scope.entries_admissible == "yes"
    assert scope.blockers == ()
    verdict = evaluate_new_entry(
        incident_policy(),
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry(product_id="NEAR-USDC", strategy_id=uuid4(), notional=Decimal("10")),
        snapshots=books.snapshots,
        live_quote_cash=Decimal("300"),
        observation=EntryObservation(
            as_of=INCIDENT_AT,
            proposed_price=Decimal("3"),
            reference_price=Decimal("3"),
            marks={"NEAR-USDC": Decimal("3")},
        ),
        futures_collateral=_ABSENT,
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_scopes_cover_occupied_books_only() -> None:
    """A stopped book alone does not open a scope; running and paused books do."""
    books = incident()
    scopes = fleet_entry_scopes(tuple(item.deployment for item in books.snapshots))
    assert scopes == ((DeploymentMode.LIVE, "USDC"),)


def test_latched_daily_loss_is_a_latch_block() -> None:
    """An operator reset clears a latched daily-loss breaker; the report says so."""
    books = incident(repaired=True)
    latched = replace(books.legacy.deployment, daily_loss_latched=True)
    scope = _live_usdc((replace(books.legacy, deployment=latched), *books.running))
    daily = next(check for check in scope.checks if check.name == "daily_loss")
    assert scope.entries_admissible == "blocked"
    assert daily.reason_code == "DAILY_LOSS_LIMIT"
    assert daily.blocker_class == "latch"
    assert [book.deployment_id for book in daily.books] == [LEGACY_DEPLOYMENT_ID]


def test_unknown_futures_collateral_blocks_live_usdc() -> None:
    """ADR 0129 L1: an unknown CFM account pauses every live USDC entry."""
    books = incident(repaired=True)
    unknown = replace(
        _ABSENT, state=FuturesCollateralState.UNKNOWN, cause="The futures snapshot is stale."
    )
    scope = _live_usdc(books.snapshots, collateral=unknown)
    check = next(check for check in scope.checks if check.name == "futures_collateral")
    assert scope.reason_codes == ("FUTURES_COLLATERAL_UNKNOWN",)
    assert check.blocker_class == "evidence"


def test_collateral_in_use_is_a_policy_block() -> None:
    """Manual futures in use without a reserve deny by policy, not missing evidence."""
    books = incident(repaired=True)
    in_use = replace(
        _ABSENT,
        state=FuturesCollateralState.IN_USE,
        initial_margin_usd=Decimal("50"),
        cause="CFM futures hold 1 position(s).",
    )
    scope = _live_usdc(books.snapshots, collateral=in_use)
    check = next(check for check in scope.checks if check.name == "futures_collateral")
    assert check.reason_code == "FUTURES_COLLATERAL_IN_USE"
    assert check.blocker_class == "policy"


def test_unloaded_collateral_is_unknown_not_admissible() -> None:
    """A failed classification read is unknown, never a pass."""
    books = incident(repaired=True)
    scope = evaluate_scope(
        incident_policy(),
        snapshots=books.snapshots,
        evidence=FleetScopeEvidence(
            mode=DeploymentMode.LIVE, scope="USDC", collateral_loaded=False
        ),
        observation=_observation(),
    )
    assert scope.entries_admissible == "unknown"


def _holding(snapshot: DeploymentSnapshot, product_id: str) -> DeploymentSnapshot:
    """The same running book holding one flat-priced position."""
    position = Position(
        deployment_id=snapshot.deployment.id,
        side=PositionSide.LONG,
        quantity=Decimal("10"),
        entry_price=Decimal("3"),
        stop_price=Decimal("2"),
        target_price=None,
        entered_bar=INCIDENT_AT - timedelta(hours=2),
        updated_at=INCIDENT_AT - timedelta(hours=1),
        product_id=product_id,
    )
    deployment = replace(snapshot.deployment, phase=RuntimePhase.OPEN)
    return replace(snapshot, deployment=deployment, position=position, positions=(position,))


def test_missing_beta_for_a_held_product_blocks_the_scope() -> None:
    """ADR 0125: a held product's unavailable β denies every entry; the holder is named."""
    books = incident(repaired=True)
    held = _holding(books.running[0], "NEAR-USDC")
    policy = incident_policy().model_copy(update={"max_btc_beta_exposure_fraction": "0.9"})
    beta = BetaEvidence(
        results={
            "NEAR-USDC": BetaUnavailable(
                product_id="NEAR-USDC",
                reference_id="BTC-USDC",
                reason=BetaUnavailableReason.FETCH_FAILED,
            )
        }
    )
    scope = _live_usdc(
        (books.legacy, held, books.running[1]),
        policy=policy,
        beta=beta,
        marks={"NEAR-USDC": Decimal("3")},
    )
    check = next(check for check in scope.checks if check.name == "btc_beta")
    assert check.reason_code == "BTC_BETA_UNAVAILABLE"
    assert check.blocker_class == "evidence"
    assert [book.deployment_id for book in check.books] == [NEAR_DEPLOYMENT_ID]


def test_open_inventory_without_a_mark_names_the_product() -> None:
    """Missing last-close marks are named, not summed as zero."""
    books = incident(repaired=True)
    held = _holding(books.running[0], "NEAR-USDC")
    scope = _live_usdc((books.legacy, held, books.running[1]))
    daily = next(check for check in scope.checks if check.name == "daily_loss")
    assert daily.reason_code == "BREAKER_MARK_MISSING"
    assert "no last-close mark for open inventory in NEAR-USDC" in daily.books[0].detail


def test_unknown_venue_balance_on_every_book_blocks_live() -> None:
    """With no observed venue quote anywhere, every live entry is denied."""
    books = incident(repaired=True)
    blind = tuple(
        replace(item, deployment=replace(item.deployment, venue_available_quote=None))
        for item in books.running
    )
    scope = _live_usdc((books.legacy, *blind))
    check = next(check for check in scope.checks if check.name == "venue_quote_balance")
    assert check.fleet_wide is True
    assert "VENUE_BALANCE_UNKNOWN" in scope.reason_codes


def test_one_unknown_venue_balance_blocks_only_that_book() -> None:
    """A single book without a venue balance does not block the scope."""
    books = incident(repaired=True)
    blind = replace(
        books.running[0],
        deployment=replace(books.running[0].deployment, venue_available_quote=None),
    )
    scope = _live_usdc((books.legacy, blind, books.running[1]))
    check = next(check for check in scope.checks if check.name == "venue_quote_balance")
    assert check.status == "blocked"
    assert check.fleet_wide is False
    assert scope.entries_admissible == "yes"


def test_unreadable_book_blocks_every_scope() -> None:
    """Admission reloads every book first, so one unreadable book denies all entries."""
    books = incident(repaired=True)
    unreadable = (
        BlockingBook(
            deployment_id=UUID(int=7),
            status="running",
            product_id="SOL-USDC",
            detail="accounting snapshot unavailable",
        ),
    )
    scope = _live_usdc(books.snapshots, unreadable=unreadable)
    check = next(check for check in scope.checks if check.name == "accounting_inventory")
    assert check.fleet_wide is True
    assert scope.reason_codes == ("BREAKER_MARK_MISSING",)


def test_cluster_saturation_is_transient_and_not_alertable() -> None:
    """The clustering window frees itself; it blocks but never raises the alert."""
    books = incident(repaired=True)
    policy = incident_policy().model_copy(
        update={"max_fleet_entries_per_window": 1, "fleet_entry_window_minutes": 120}
    )
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=NEAR_DEPLOYMENT_ID,
        client_order_id="near-entry",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("3"),
        created_at=INCIDENT_AT - timedelta(minutes=5),
        candle_starts_at=INCIDENT_AT - timedelta(hours=2),
        product_id="NEAR-USDC",
    )
    entered = replace(books.running[0], intents=(intent,))
    scope = _live_usdc((books.legacy, entered, books.running[1]), policy=policy)
    check = next(check for check in scope.checks if check.name == "entry_cluster")
    assert scope.entries_admissible == "blocked"
    assert check.blocker_class == "transient"
    assert scope.alertable == ()


def test_drawdown_latch_blocks_only_its_strategy() -> None:
    """A strategy's drawdown latch is reported but does not block the fleet."""
    books = incident(repaired=True)
    latched = replace(
        books.running[0], deployment=replace(books.running[0].deployment, drawdown_latched=True)
    )
    scope = _live_usdc((books.legacy, latched, books.running[1]))
    check = next(check for check in scope.checks if check.name == "drawdown_latch")
    assert check.status == "blocked"
    assert check.fleet_wide is False
    assert scope.entries_admissible == "yes"


@pytest.mark.parametrize("mode", [DeploymentMode.PAPER])
def test_paper_scope_skips_live_only_checks(mode: DeploymentMode) -> None:
    """Venue balance and shared collateral apply to live entries only."""
    books = incident(repaired=True)
    paper = tuple(
        replace(item, deployment=replace(item.deployment, mode=mode)) for item in books.snapshots
    )
    scope = evaluate_scope(
        incident_policy(),
        snapshots=paper,
        evidence=FleetScopeEvidence(mode=mode, scope="USDC"),
        observation=_observation(),
    )
    statuses = {check.name: check.status for check in scope.checks}
    assert statuses["venue_quote_balance"] == "not_applicable"
    assert statuses["futures_collateral"] == "not_applicable"


def test_fleet_disarm_is_reported_but_not_alerted() -> None:
    """A deliberate disarm blocks entries; an unreadable latch is missing evidence."""
    books = incident(repaired=True)
    for inhibited, expected in ((True, "operator"), (None, "evidence")):
        scope = evaluate_scope(
            incident_policy(),
            snapshots=books.snapshots,
            evidence=FleetScopeEvidence(
                mode=DeploymentMode.LIVE,
                scope="USDC",
                futures_collateral=_ABSENT,
                entries_inhibited=inhibited,
            ),
            observation=_observation(),
        )
        check = next(check for check in scope.checks if check.name == "fleet_disarm")
        assert check.reason_code == "ENTRIES_DISABLED"
        assert check.blocker_class == expected
        assert (check in scope.alertable) is (expected == "evidence")


def test_full_open_position_slots_block_every_new_entry() -> None:
    """Every concurrent open-position slot in use denies every new entry by policy."""
    books = incident(repaired=True)
    held = _holding(books.running[0], "NEAR-USDC")
    policy = incident_policy().model_copy(update={"max_concurrent_open_positions": 1})
    scope = _live_usdc(
        (books.legacy, held, books.running[1]), policy=policy, marks={"NEAR-USDC": Decimal("3")}
    )
    check = next(check for check in scope.checks if check.name == "open_position_slots")
    assert check.reason_code == "MAX_OPEN_POSITIONS"
    assert check.blocker_class == "capacity"
    assert scope.entries_admissible == "blocked"
    assert check not in scope.alertable


def test_exposure_over_the_account_cap_blocks_every_entry() -> None:
    """Existing exposure above the absolute cap leaves no room for any entry."""
    books = incident(repaired=True)
    held = _holding(books.running[0], "NEAR-USDC")
    policy = incident_policy().model_copy(update={"max_portfolio_exposure_quote": "20"})
    scope = _live_usdc(
        (books.legacy, held, books.running[1]), policy=policy, marks={"NEAR-USDC": Decimal("3")}
    )
    check = next(check for check in scope.checks if check.name == "exposure_cap")
    assert check.reason_code == "PORTFOLIO_EXPOSURE_EXCEEDED"
    assert check.blocker_class == "capacity"
