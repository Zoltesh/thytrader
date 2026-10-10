"""Fleet entry readiness vocabulary (ADR 0130).

One :class:`FleetScopeHealth` per mode and quote/settlement scope says whether the entry
gate would admit any new entry there right now (``entries_admissible``), and which checks
block it, with the exact books and records responsible. ``risk.fleet_entry_health`` fills
these from the gate's own functions, so the report and the gate cannot disagree.

Blocker classes say what clears a block:

- ``evidence``: something the gate needs is missing or unreadable (marks, accounting, beta,
  the futures account, the venue balance). Nothing clears it but a repair. Always alerted.
- ``latch``: a daily-loss breaker is latched or tripped; an operator reset clears it.
- ``policy``: manual CFM futures hold the shared collateral without a covering reserve
  (ADR 0129); the operator decides.
- ``capacity``: the fleet is fully invested (every open-position slot used, account or
  BTC-beta-weighted exposure at its cap); an exit frees room. Reported, never alerted.
- ``transient``: the fleet entry clustering cap (ADR 0125); it clears by itself when the
  window slides. Reported, never alerted.
- ``operator``: a deliberate operator control, the fleet disarm (ADR 0117); a rearm clears
  it. Reported, never alerted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from thytrader.risk.models import RiskReasonCode

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.risk.beta import BetaEvidence
    from thytrader.risk.futures_beta import FuturesLegs
    from thytrader.risk.futures_collateral import FuturesCollateralEvidence
    from thytrader.risk.models import RiskVerdict
    from thytrader.trading.models import DeploymentMode, DeploymentSnapshot

type FleetAdmissibility = Literal["yes", "blocked", "unknown"]
type FleetCheckStatus = Literal["pass", "blocked", "unknown", "not_applicable"]
type FleetBlockerClass = Literal["evidence", "latch", "policy", "capacity", "transient", "operator"]
UNALERTED_CLASSES: frozenset[str] = frozenset({"capacity", "transient", "operator"})
"""Blocks that clear by themselves or were set on purpose; reported, never alerted."""
type FleetCheckName = Literal[
    "fleet_disarm",
    "accounting_inventory",
    "quote_scope",
    "venue_quote_balance",
    "futures_collateral",
    "open_position_slots",
    "exposure_cap",
    "btc_beta",
    "unresolved_accounting",
    "daily_loss",
    "drawdown_latch",
    "linked_futures_breaker",
    "entry_cluster",
]

FLEET_CHECKS: tuple[FleetCheckName, ...] = (
    "fleet_disarm",
    "accounting_inventory",
    "quote_scope",
    "venue_quote_balance",
    "futures_collateral",
    "open_position_slots",
    "exposure_cap",
    "btc_beta",
    "unresolved_accounting",
    "daily_loss",
    "drawdown_latch",
    "linked_futures_breaker",
    "entry_cluster",
)

EVIDENCE_REASON_CODES: frozenset[str] = frozenset(
    {
        RiskReasonCode.BREAKER_MARK_MISSING.value,
        RiskReasonCode.BTC_BETA_UNAVAILABLE.value,
        RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN.value,
        RiskReasonCode.VENUE_BALANCE_UNKNOWN.value,
        RiskReasonCode.FUTURES_MARGIN_UNKNOWN.value,
        RiskReasonCode.PORTFOLIO_LIMITS_UNAVAILABLE.value,
        RiskReasonCode.REFERENCE_PRICE_UNAVAILABLE.value,
    }
)
"""Gate denials that mean evidence is missing, not that a limit was reached.

A decision row with one of these codes is a systemic blocker in the fleet decision log.
"""
MAX_BLOCKING_BOOKS = 20
"""Books named per check, so one report stays bounded."""


@dataclass(frozen=True, slots=True)
class BlockingBook:
    """One book that causes a check to block, and exactly why."""

    deployment_id: UUID
    status: str
    product_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class FleetEntryCheck:
    """One gate check evaluated for a whole scope.

    ``fleet_wide`` is true when the denial applies to every entry in the scope (it then
    blocks ``entries_admissible``). A non-fleet-wide block, such as one strategy's drawdown
    latch or one book's unknown venue balance, names only the books it stops.
    """

    name: FleetCheckName
    status: FleetCheckStatus
    detail: str = ""
    reason_code: str | None = None
    blocker_class: FleetBlockerClass | None = None
    fleet_wide: bool = False
    books: tuple[BlockingBook, ...] = ()


@dataclass(frozen=True, slots=True)
class FleetScopeEvidence:
    """Evidence the caller loaded for one scope the way entry admission loads it.

    ``beta_loaded`` is false when the β loader itself failed (the check is then unknown);
    a loaded ``None`` is what the gate would see and denies when a cap binds.
    ``collateral_loaded`` is false when the futures account could not be classified.
    ``probe_product`` names the contract a futures scope probes with (any of its books).
    ``entries_inhibited`` is the durable fleet disarm latch of the mode; ``None`` means it
    could not be read, which the gate treats as disarmed.
    """

    mode: DeploymentMode
    scope: str
    probe_product: str | None = None
    entries_inhibited: bool | None = False
    beta: BetaEvidence | None = None
    beta_loaded: bool = True
    futures_collateral: FuturesCollateralEvidence | None = None
    collateral_loaded: bool = True
    futures_legs: FuturesLegs | None = None


@dataclass(frozen=True, slots=True)
class FleetScopeHealth:
    """Whether the gate admits any new entry in one mode and scope, and what blocks it."""

    mode: DeploymentMode
    scope: str
    entries_admissible: FleetAdmissibility
    running_deployments: int
    occupied_deployments: int
    checks: tuple[FleetEntryCheck, ...]

    @property
    def blockers(self) -> tuple[FleetEntryCheck, ...]:
        """Every check that blocks some or all entries in this scope."""
        return tuple(check for check in self.checks if check.status == "blocked")

    @property
    def fleet_blockers(self) -> tuple[FleetEntryCheck, ...]:
        """Checks that block every entry in this scope."""
        return tuple(check for check in self.blockers if check.fleet_wide)

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Distinct fleet-wide blocking reason codes, in check order."""
        codes = (check.reason_code for check in self.fleet_blockers)
        return tuple(dict.fromkeys(code for code in codes if code is not None))

    @property
    def alertable(self) -> tuple[FleetEntryCheck, ...]:
        """Fleet-wide blocks that need a repair, a reset or a decision to clear."""
        return tuple(
            check for check in self.fleet_blockers if check.blocker_class not in UNALERTED_CLASSES
        )

    @property
    def blocking_deployment_ids(self) -> tuple[UUID, ...]:
        """Books named by fleet-wide blockers, without repeats."""
        ids = (book.deployment_id for check in self.fleet_blockers for book in check.books)
        return tuple(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class FleetEntryHealth:
    """One evaluation of every scope; ``complete`` is false when the fleet was unreadable."""

    evaluated_at: datetime
    complete: bool
    scopes: tuple[FleetScopeHealth, ...]
    detail: str = ""


def scope_subject(mode: DeploymentMode, scope: str) -> str:
    """The alert subject naming one scope, for example ``fleet:live:USDC``."""
    return f"fleet:{mode.value}:{scope}"


def blocked_check(
    name: FleetCheckName,
    verdict: RiskVerdict,
    blocker_class: FleetBlockerClass,
    *,
    books: tuple[BlockingBook, ...] = (),
) -> FleetEntryCheck:
    """A fleet-wide block carrying the gate's own reason code and detail."""
    return FleetEntryCheck(
        name=name,
        status="blocked",
        detail=verdict.detail,
        reason_code=verdict.reason_code.value,
        blocker_class=blocker_class,
        fleet_wide=True,
        books=books[:MAX_BLOCKING_BOOKS],
    )


def blocking_book(item: DeploymentSnapshot, detail: str) -> BlockingBook:
    """Name one book and why it blocks."""
    deployment = item.deployment
    return BlockingBook(
        deployment_id=deployment.id,
        status=deployment.status.value,
        product_id=deployment.product_id,
        detail=detail,
    )
