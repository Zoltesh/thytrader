"""Fleet entry readiness checks on account capital and capacity (ADR 0130).

The venue quote balance, the live shared-collateral gate (ADR 0129), the policy's
open-position slots and the account exposure cap, each run with the gate's own rule for a
whole mode and quote scope (``risk.fleet_entry_health`` composes them). Futures scopes use
the futures gate's own envelope, so the spot capacity checks do not apply to them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.risk.breakers import _snapshot_quote
from thytrader.risk.entry_limits import (
    _capital_base,
    _exposure_verdict,
    open_position_slot_count,
)
from thytrader.risk.fleet_entry_models import (
    MAX_BLOCKING_BOOKS,
    FleetEntryCheck,
    blocked_check,
    blocking_book,
)
from thytrader.risk.futures_collateral import (
    COLLATERAL_LINKED_QUOTES,
    FuturesCollateralState,
    collateral_verdict,
)
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import DeploymentMode, DeploymentStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.risk.fleet_entry_models import FleetScopeEvidence
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot


def scope_live_quote_cash(
    snapshots: Sequence[DeploymentSnapshot], mode: DeploymentMode, scope: str
) -> Decimal | None:
    """The newest observed venue quote among the scope's occupied live books, else None."""
    observed = [
        item.deployment
        for item in snapshots
        if item.deployment.mode is mode
        and occupies_running_slot(item.deployment)
        and _snapshot_quote(item) == scope
        and item.deployment.venue_available_quote is not None
    ]
    if not observed:
        return None
    return max(observed, key=lambda deployment: deployment.updated_at).venue_available_quote


def venue_check(
    *,
    mode: DeploymentMode,
    futures: bool,
    occupied: Sequence[DeploymentSnapshot],
    snapshots: Sequence[DeploymentSnapshot],
    scope: str,
) -> tuple[FleetEntryCheck, Decimal | None]:
    """A live book with an unknown venue quote balance cannot enter (``live_capital_base``).

    Fleet-wide only when no occupied book of the scope has an observed balance.
    """
    if mode is not DeploymentMode.LIVE or futures:
        return FleetEntryCheck(name="venue_quote_balance", status="not_applicable"), None
    blind = tuple(
        item
        for item in occupied
        if item.deployment.status is DeploymentStatus.RUNNING
        and item.deployment.venue_available_quote is None
    )
    cash = scope_live_quote_cash(snapshots, mode, scope)
    if not blind:
        return FleetEntryCheck(name="venue_quote_balance", status="pass"), cash
    every = cash is None
    detail = (
        "No occupied book has an observed venue quote balance; every live entry is denied."
        if every
        else f"{len(blind)} running book(s) have no observed venue quote balance."
    )
    check = FleetEntryCheck(
        name="venue_quote_balance",
        status="blocked",
        detail=detail,
        reason_code=RiskReasonCode.VENUE_BALANCE_UNKNOWN.value,
        blocker_class="evidence",
        fleet_wide=every,
        books=tuple(
            blocking_book(item, "venue quote balance unknown")
            for item in blind[:MAX_BLOCKING_BOOKS]
        ),
    )
    return check, cash


def collateral_check(
    policy: RiskPolicyDefinition,
    *,
    evidence: FleetScopeEvidence,
    probe: ProposedEntry,
    cash: Decimal | None,
) -> tuple[FleetEntryCheck, Decimal | None]:
    """The live shared-collateral gate for USD and USDC spot scopes; returns spot cash."""
    if evidence.mode is not DeploymentMode.LIVE or evidence.scope not in COLLATERAL_LINKED_QUOTES:
        return FleetEntryCheck(name="futures_collateral", status="not_applicable"), cash
    if not evidence.collateral_loaded:
        return (
            FleetEntryCheck(
                name="futures_collateral",
                status="unknown",
                detail="The futures account mirror could not be classified for this report.",
            ),
            cash,
        )
    verdict, remaining = collateral_verdict(
        policy,
        mode=evidence.mode,
        proposed=probe,
        evidence=evidence.futures_collateral,
        live_quote_cash=cash,
    )
    if verdict is None:
        return FleetEntryCheck(name="futures_collateral", status="pass"), remaining
    state = evidence.futures_collateral
    unknown = (state is not None and state.state is FuturesCollateralState.UNKNOWN) or (
        verdict.reason_code is RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN
    )
    return blocked_check("futures_collateral", verdict, "evidence" if unknown else "policy"), cash


def open_slots_check(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    futures: bool,
    snapshots: Sequence[DeploymentSnapshot],
) -> FleetEntryCheck:
    """Every open-position slot of the mode in use denies every new (non-add) entry."""
    if futures:
        return FleetEntryCheck(name="open_position_slots", status="not_applicable")
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and occupies_running_slot(item.deployment)
    )
    used = sum(open_position_slot_count(item) for item in occupied)
    cap = policy.max_concurrent_open_positions
    if used < cap:
        return FleetEntryCheck(name="open_position_slots", status="pass")
    return FleetEntryCheck(
        name="open_position_slots",
        status="blocked",
        detail=(
            f"{used} open or pending positions use every one of the {cap} concurrent slots "
            f"for {mode.value}; new entries wait for an exit."
        ),
        reason_code=RiskReasonCode.MAX_OPEN_POSITIONS.value,
        blocker_class="capacity",
        fleet_wide=True,
    )


def exposure_cap_check(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    futures: bool,
    probe: ProposedEntry,
    quote_books: Sequence[DeploymentSnapshot],
    cash: Decimal | None,
) -> FleetEntryCheck:
    """Account exposure already over its cap, or no capital base, denies every entry.

    Only the account-wide cap is fleet-wide; a product or strategy allocation cap stops only
    its own entries, so those outcomes of the gate's exposure rule pass here.
    """
    if futures:
        return FleetEntryCheck(name="exposure_cap", status="not_applicable")
    verdict = _exposure_verdict(
        policy, mode=mode, proposed=probe, occupied=quote_books, live_quote_cash=cash
    )
    if verdict.reason_code is not RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED:
        return FleetEntryCheck(name="exposure_cap", status="pass")
    capital = _capital_base(policy, mode=mode, live_quote_cash=cash, occupied=quote_books)
    return blocked_check("exposure_cap", verdict, "evidence" if capital <= 0 else "capacity")
