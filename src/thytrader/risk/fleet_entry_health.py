"""Fleet entry readiness: would the gate admit any new entry in each scope? (ADR 0130).

For every mode and quote/settlement scope that has an occupied (running or paused) book,
this runs the fleet-wide checks the entry gate (``risk.gate``) applies to every entry,
whether or not any bot currently has a signal. It calls the gate's own functions with a
neutral probe entry (zero notional, the scope's BTC reference product, a strategy id no book
has), so the report cannot disagree with the gate, then attributes each denial to the exact
books, and for missing daily-loss evidence to the exact records (``risk.opening_diagnosis``).

Pure: the caller loads the snapshots, marks and per-scope evidence the way entry admission
loads them (``execution.fleet_entry_evidence``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.beta import BetaUnavailable, fresh_beta
from thytrader.risk.beta_exposure import _held_products, beta_cap_applies, beta_verdict
from thytrader.risk.breakers import (
    FUTURES_SCOPE,
    _daily_pnl,
    _open_inventory_missing_mark,
    _product_quote,
    _snapshot_quote,
    evaluate_circuit_breakers,
    quote_scoped_snapshots,
    unresolved_accounting_verdict,
)
from thytrader.risk.entry_clustering import cluster_verdict
from thytrader.risk.entry_limits import _capital_base
from thytrader.risk.fleet_entry_capital import (
    collateral_check,
    exposure_cap_check,
    open_slots_check,
    venue_check,
)
from thytrader.risk.fleet_entry_models import (
    MAX_BLOCKING_BOOKS,
    BlockingBook,
    FleetEntryCheck,
    FleetScopeEvidence,
    FleetScopeHealth,
    blocked_check,
    blocking_book,
)
from thytrader.risk.futures_entry import futures_capital, linked_breaker_verdict
from thytrader.risk.gate_common import ProposedEntry, _book_products
from thytrader.risk.models import RiskReasonCode
from thytrader.risk.opening_diagnosis import accounting_gaps, daily_evidence_gaps
from thytrader.trading.exposure import daily_loss_snapshots, risk_bearing_snapshots
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import DeploymentMode, DeploymentStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.risk.breakers import EntryObservation
    from thytrader.risk.fleet_entry_models import FleetAdmissibility
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import Deployment, DeploymentSnapshot

NO_STRATEGY = UUID(int=0)
"""Probe strategy id no book carries: strategy-scoped drawdown never matches the probe."""


def fleet_entry_scopes(deployments: Sequence[Deployment]) -> tuple[tuple[DeploymentMode, str], ...]:
    """Mode and scope pairs with at least one occupied book, live first.

    A book whose product has no supported scope adds none; the quote-scope check of every
    scope in its mode reports it instead.
    """
    scopes: set[tuple[DeploymentMode, str]] = set()
    for deployment in deployments:
        scope = _product_quote(deployment.product_id)
        if scope is not None and occupies_running_slot(deployment):
            scopes.add((deployment.mode, scope))
    order = {DeploymentMode.LIVE: 0, DeploymentMode.PAPER: 1}
    return tuple(sorted(scopes, key=lambda pair: (order[pair[0]], pair[1])))


def evaluate_fleet_entry_health(
    policy: RiskPolicyDefinition,
    *,
    snapshots: Sequence[DeploymentSnapshot],
    evidence: Sequence[FleetScopeEvidence],
    observation: EntryObservation,
    unreadable: Sequence[BlockingBook] = (),
) -> tuple[FleetScopeHealth, ...]:
    """Evaluate every scope ``evidence`` names against the same snapshots.

    ``unreadable`` names books whose fresh accounting snapshot could not be read. Entry
    admission reloads every book before each entry and denies all of them when one fails,
    so any unreadable book blocks every scope.
    """
    return tuple(
        evaluate_scope(
            policy,
            snapshots=snapshots,
            evidence=item,
            observation=observation,
            unreadable=unreadable,
        )
        for item in evidence
    )


@dataclass(frozen=True, slots=True)
class _Scope:
    """One scope's probe and the book sets the gate would read for it."""

    policy: RiskPolicyDefinition
    evidence: FleetScopeEvidence
    probe: ProposedEntry
    snapshots: Sequence[DeploymentSnapshot]
    observation: EntryObservation

    @property
    def mode(self) -> DeploymentMode:
        """The scope's deployment mode."""
        return self.evidence.mode

    @property
    def futures(self) -> bool:
        """True for the CFM futures settlement scope."""
        return self.evidence.scope == FUTURES_SCOPE

    def books(self) -> tuple[DeploymentSnapshot, ...]:
        """Daily-loss books of this mode and scope (stopped flat books included)."""
        return tuple(
            item
            for item in daily_loss_snapshots(self.snapshots, self.mode)
            if _snapshot_quote(item) == self.evidence.scope
        )

    def occupied(self) -> tuple[DeploymentSnapshot, ...]:
        """Running and paused books of this mode and scope."""
        return tuple(item for item in self.books() if occupies_running_slot(item.deployment))


def evaluate_scope(
    policy: RiskPolicyDefinition,
    *,
    snapshots: Sequence[DeploymentSnapshot],
    evidence: FleetScopeEvidence,
    observation: EntryObservation,
    unreadable: Sequence[BlockingBook] = (),
) -> FleetScopeHealth:
    """Run every fleet-wide check for one scope, in the gate's order."""
    probe = ProposedEntry(
        product_id=_probe_product(evidence, snapshots),
        strategy_id=NO_STRATEGY,
        notional=Decimal(0),
    )
    scope = _Scope(policy, evidence, probe, snapshots, observation)
    quote_check, quote_books = _quote_scope_check(scope)
    occupied = scope.occupied()
    venue, cash = venue_check(
        mode=scope.mode,
        futures=scope.futures,
        occupied=occupied,
        snapshots=snapshots,
        scope=evidence.scope,
    )
    collateral, cash = collateral_check(policy, evidence=evidence, probe=probe, cash=cash)
    checks = (
        _disarm_check(evidence),
        _inventory_check(unreadable),
        quote_check,
        venue,
        collateral,
        open_slots_check(policy, mode=scope.mode, futures=scope.futures, snapshots=snapshots),
        exposure_cap_check(
            policy,
            mode=scope.mode,
            futures=scope.futures,
            probe=probe,
            quote_books=quote_books,
            cash=cash,
        ),
        _beta_check(scope, quote_books, cash),
        _unresolved_check(scope),
        _daily_loss_check(scope, quote_books, cash),
        _drawdown_latch_check(scope),
        _linked_breaker_check(scope),
        _cluster_check(scope),
    )
    return FleetScopeHealth(
        mode=evidence.mode,
        scope=evidence.scope,
        entries_admissible=_admissibility(checks),
        running_deployments=sum(
            1 for item in occupied if item.deployment.status is DeploymentStatus.RUNNING
        ),
        occupied_deployments=len(occupied),
        checks=checks,
    )


def _admissibility(checks: Sequence[FleetEntryCheck]) -> FleetAdmissibility:
    """Blocked by any fleet-wide block, else unknown by any unknown check, else yes."""
    if any(check.status == "blocked" and check.fleet_wide for check in checks):
        return "blocked"
    if any(check.status == "unknown" for check in checks):
        return "unknown"
    return "yes"


def _probe_product(evidence: FleetScopeEvidence, snapshots: Sequence[DeploymentSnapshot]) -> str:
    """The BTC reference of a spot quote (β 1), or a futures book's own contract."""
    if evidence.scope != FUTURES_SCOPE:
        return f"BTC-{evidence.scope}"
    if evidence.probe_product is not None:
        return evidence.probe_product
    for item in snapshots:
        deployment = item.deployment
        if deployment.mode is evidence.mode and is_futures_product_id(deployment.product_id):
            return deployment.product_id
    message = "A futures scope needs a futures probe product."
    raise ValueError(message)


def _disarm_check(evidence: FleetScopeEvidence) -> FleetEntryCheck:
    """A fleet disarm of the mode refuses every new entry until a rearm (ADR 0117)."""
    if evidence.entries_inhibited is False:
        return FleetEntryCheck(name="fleet_disarm", status="pass")
    unreadable = evidence.entries_inhibited is None
    return FleetEntryCheck(
        name="fleet_disarm",
        status="blocked",
        detail=(
            "The fleet entry-inhibition latch could not be read; entries fail closed."
            if unreadable
            else f"New {evidence.mode.value} entries are disarmed until an explicit fleet rearm."
        ),
        reason_code=RiskReasonCode.ENTRIES_DISABLED.value,
        blocker_class="evidence" if unreadable else "operator",
        fleet_wide=True,
    )


INVENTORY_UNAVAILABLE_DETAIL = (
    "Fresh complete risk accounting evidence is unavailable; entries are disabled."
)
"""The denial entry admission returns when any book's accounting snapshot cannot be read."""


def _inventory_check(unreadable: Sequence[BlockingBook]) -> FleetEntryCheck:
    """Every entry reloads every book first; one unreadable book denies them all."""
    if not unreadable:
        return FleetEntryCheck(name="accounting_inventory", status="pass")
    return FleetEntryCheck(
        name="accounting_inventory",
        status="blocked",
        detail=INVENTORY_UNAVAILABLE_DETAIL,
        reason_code=RiskReasonCode.BREAKER_MARK_MISSING.value,
        blocker_class="evidence",
        fleet_wide=True,
        books=tuple(unreadable[:MAX_BLOCKING_BOOKS]),
    )


def _quote_scope_check(
    scope: _Scope,
) -> tuple[FleetEntryCheck, tuple[DeploymentSnapshot, ...]]:
    """A mixed or unsupported quote on any book of the mode denies every scope in it."""
    books, incomplete = quote_scoped_snapshots(
        risk_bearing_snapshots(scope.snapshots, scope.mode), scope.probe.product_id
    )
    if incomplete is None:
        return FleetEntryCheck(name="quote_scope", status="pass"), books
    offenders = tuple(
        blocking_book(item, "unsupported or mixed quote currency")
        for item in daily_loss_snapshots(scope.snapshots, scope.mode)
        if _snapshot_quote(item) is None
    )
    return blocked_check("quote_scope", incomplete, "evidence", books=offenders), ()


def _beta_check(
    scope: _Scope, quote_books: Sequence[DeploymentSnapshot], cash: Decimal | None
) -> FleetEntryCheck:
    """A held product's missing β denies every entry in the scope (ADR 0125)."""
    if scope.futures or not beta_cap_applies(scope.policy, scope.mode):
        return FleetEntryCheck(name="btc_beta", status="not_applicable")
    if not scope.evidence.beta_loaded:
        return FleetEntryCheck(
            name="btc_beta",
            status="unknown",
            detail="BTC-beta evidence could not be loaded for this report.",
        )
    verdict = beta_verdict(
        scope.policy,
        mode=scope.mode,
        proposed=scope.probe,
        occupied=quote_books,
        live_quote_cash=cash,
        beta=scope.evidence.beta,
        as_of=scope.observation.as_of,
        snapshots=scope.snapshots,
        legs=scope.evidence.futures_legs,
        marks=scope.observation.marks,
    )
    if verdict is None:
        return FleetEntryCheck(name="btc_beta", status="pass")
    if verdict.reason_code is not RiskReasonCode.BTC_BETA_UNAVAILABLE:
        return blocked_check("btc_beta", verdict, "capacity")
    return blocked_check("btc_beta", verdict, "evidence", books=_beta_books(scope, quote_books))


def _beta_books(
    scope: _Scope, quote_books: Sequence[DeploymentSnapshot]
) -> tuple[BlockingBook, ...]:
    """Books holding a product whose β is unavailable or stale."""
    beta = scope.evidence.beta
    if beta is None:
        return ()
    missing: dict[str, str] = {}
    for product_id in _held_products(quote_books):
        result = fresh_beta(beta.result_for(product_id), as_of=scope.observation.as_of)
        if isinstance(result, BetaUnavailable):
            missing[product_id] = result.describe()
    found = [
        blocking_book(item, f"holds {product_id}: BTC beta {cause}")
        for item in quote_books
        for product_id, cause in missing.items()
        if product_id in _book_products(item)
    ]
    return tuple(found[:MAX_BLOCKING_BOOKS])


def _unresolved_check(scope: _Scope) -> FleetEntryCheck:
    """Any same-scope book with unresolved economics denies every entry."""
    verdict = unresolved_accounting_verdict(
        mode=scope.mode, product_id=scope.probe.product_id, snapshots=scope.snapshots
    )
    if verdict is None:
        return FleetEntryCheck(name="unresolved_accounting", status="pass")
    offenders = tuple(
        blocking_book(item, "; ".join(accounting_gaps(item)) or verdict.detail)
        for item in scope.books()
        if unresolved_accounting_verdict(
            mode=scope.mode, product_id=scope.probe.product_id, snapshots=(item,)
        )
        is not None
    )
    return blocked_check("unresolved_accounting", verdict, "evidence", books=offenders)


def _daily_loss_check(
    scope: _Scope, quote_books: Sequence[DeploymentSnapshot], cash: Decimal | None
) -> FleetEntryCheck:
    """The account daily-loss breaker and its latch, over every same-scope book."""
    capital = (
        futures_capital(scope.policy, scope.mode)
        if scope.futures
        else _capital_base(
            scope.policy, mode=scope.mode, live_quote_cash=cash, occupied=quote_books
        )
    )
    verdict = evaluate_circuit_breakers(
        scope.policy,
        mode=scope.mode,
        proposed_product_id=scope.probe.product_id,
        proposed_strategy_id=NO_STRATEGY,
        snapshots=scope.snapshots,
        observation=scope.observation,
        capital=capital,
    )
    if verdict is None:
        return FleetEntryCheck(name="daily_loss", status="pass")
    if verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT:
        latched = tuple(
            blocking_book(item, "daily-loss breaker latched until an operator reset")
            for item in scope.books()
            if item.deployment.daily_loss_latched
        )
        return blocked_check("daily_loss", verdict, "latch", books=latched)
    return blocked_check("daily_loss", verdict, "evidence", books=_evidence_books(scope))


def _evidence_books(scope: _Scope) -> tuple[BlockingBook, ...]:
    """Books whose UTC-day loss the breaker cannot compute, with the failed rules."""
    observation = scope.observation
    found: list[BlockingBook] = []
    for item in scope.books():
        missing = _open_inventory_missing_mark(item, observation.marks) or (
            _daily_pnl(item, marks=observation.marks, as_of=observation.as_of) is None
        )
        if not missing:
            continue
        gaps = daily_evidence_gaps(item, as_of=observation.as_of, marks=observation.marks)
        found.append(blocking_book(item, "; ".join(gaps) or "daily-loss evidence unavailable"))
    return tuple(found[:MAX_BLOCKING_BOOKS])


def _drawdown_latch_check(scope: _Scope) -> FleetEntryCheck:
    """A latched drawdown breaker blocks only its own strategy's entries."""
    latched = tuple(item for item in scope.books() if item.deployment.drawdown_latched)
    if not latched:
        return FleetEntryCheck(name="drawdown_latch", status="pass")
    strategies = {item.deployment.strategy_id for item in latched}
    return FleetEntryCheck(
        name="drawdown_latch",
        status="blocked",
        detail=(
            f"Drawdown breaker latched for {len(strategies)} strategy(ies); only their "
            "entries are blocked until an operator reset."
        ),
        reason_code=RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT.value,
        blocker_class="latch",
        fleet_wide=False,
        books=tuple(
            blocking_book(item, f"strategy {item.deployment.strategy_id} drawdown latched")
            for item in latched[:MAX_BLOCKING_BOOKS]
        ),
    )


def _linked_breaker_check(scope: _Scope) -> FleetEntryCheck:
    """A latched daily-loss breaker in a collateral-linked paper scope (ADR 0129 §7)."""
    verdict = linked_breaker_verdict(
        mode=scope.mode, product_id=scope.probe.product_id, snapshots=scope.snapshots
    )
    if verdict is None:
        return FleetEntryCheck(name="linked_futures_breaker", status="pass")
    return blocked_check("linked_futures_breaker", verdict, "latch")


def _cluster_check(scope: _Scope) -> FleetEntryCheck:
    """The fleet entry clustering cap (ADR 0125); it frees by itself as the window slides."""
    verdict = cluster_verdict(
        scope.policy,
        mode=scope.mode,
        proposed=scope.probe,
        snapshots=scope.snapshots,
        observation=scope.observation,
    )
    if verdict is None:
        return FleetEntryCheck(name="entry_cluster", status="pass")
    return blocked_check("entry_cluster", verdict, "transient")
