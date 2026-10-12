"""Readiness futures section: the CFM account beside the spot preflight (ADR 0127, P0-6).

Advisory and read-only. It discloses the newest CFM mirror snapshot and adds findings when
futures state matters for spot capacity:

- ``FUTURES_COLLATERAL_SHARED`` (info): futures are enabled and the account's USDC spot
  balance is CFM collateral, so futures margin and USDC (or USD) books draw on one pool.
- ``FUTURES_POSITIONS_EXTERNAL`` (advisory): open CFM positions are unmanaged exposure that
  consumes that shared collateral; no cap in the published policy accounts for them.
- ``FUTURES_ACCOUNT_UNKNOWN`` (unknown): the mirror is stale, the balance or position read
  failed; nothing is guessed.

USD amounts are never added to USDC amounts here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.exchanges.futures_models import (
    SHARED_COLLATERAL_NOTE,
    FuturesAccountStoreUnavailableError,
    FuturesEnablement,
    margin_ratio,
)
from thytrader.execution.futures_visibility import (
    observation_live_account,
    visibility_scope,
    visible_position_comparison,
)
from thytrader.operator.readiness_models import (
    FuturesCollateralGroup,
    ReadinessFinding,
    ReadinessFuturesDuty,
    ReadinessFuturesPosition,
    ReadinessFuturesSection,
    ReadinessSeverity,
)
from thytrader.risk.futures_margin_evidence import margin_state
from thytrader.risk.futures_reserve import reserve_coverage

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.exchanges.futures_models import (
        FuturesAccountObservation,
        FuturesAccountSnapshotStore,
    )
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot

_STALE_AFTER = timedelta(seconds=180)


async def futures_section(
    store: FuturesAccountSnapshotStore | None,
    findings: list[ReadinessFinding],
    *,
    now: datetime | None = None,
    snapshots: Sequence[DeploymentSnapshot] = (),
    policy: RiskPolicyDefinition | None = None,
    managed_complete: bool = True,
) -> ReadinessFuturesSection | None:
    """Return the futures section, or ``None`` when no mirror snapshot exists (not set up)."""
    books = tuple(book for book in snapshots if visibility_scope(book) is not False)
    managed_complete = managed_complete and all(
        visibility_scope(book) is not None for book in snapshots
    )
    _live_findings(books, findings)
    if store is None:
        if books:
            findings.append(
                _finding(
                    "FUTURES_ACCOUNT_UNKNOWN",
                    ReadinessSeverity.UNKNOWN,
                    "Managed live futures have no account mirror; comparison is unknown.",
                )
            )
        return None
    try:
        latest = await store.latest()
    except FuturesAccountStoreUnavailableError:
        findings.append(
            _finding(
                "FUTURES_ACCOUNT_UNKNOWN",
                ReadinessSeverity.UNKNOWN,
                "Futures account storage could not be read; futures collateral use is unknown.",
            )
        )
        return None
    if latest is None:
        return None
    moment = now or datetime.now(UTC)
    stale = latest.observed_at > moment or moment - latest.observed_at > _STALE_AFTER
    _ownership_findings(books, latest, findings, complete=managed_complete, now=moment)
    section = _section(latest, stale=stale)
    _findings(
        latest,
        section,
        findings,
        managed_products=frozenset(book.deployment.product_id for book in books),
        managed_complete=managed_complete,
    )
    collateral = _collateral(latest, policy, stale=stale)
    balance = None if stale else latest.balance
    threshold = (
        None
        if policy is None or policy.futures is None
        else policy.futures.live_derisk_margin_ratio
    )
    state = margin_state(
        None if balance is None else balance.available_margin,
        None if balance is None else balance.liquidation_threshold,
        threshold,
    )
    if books and state is None:
        findings.append(
            _finding(
                "FUTURES_MARGIN_UNKNOWN",
                ReadinessSeverity.UNKNOWN,
                "Exact margin safety evidence is unavailable; display rounding is not proof.",
            )
        )
    if books and state in {"critical", "warning"}:
        findings.append(
            _finding(
                "FUTURES_MARGIN_LOW",
                ReadinessSeverity.VIOLATION if state == "critical" else ReadinessSeverity.ADVISORY,
                "Venue margin is below the configured live warning boundary.",
            )
        )
    if books and collateral.covers_current_margin is False:
        findings.append(
            _finding(
                "FUTURES_COLLATERAL_RESERVE_SHORT",
                ReadinessSeverity.VIOLATION,
                "The declared reserve does not cover current margin under the peg haircut.",
            )
        )
    return section.model_copy(
        update={
            "live_books": tuple(
                ReadinessFuturesDuty(
                    deployment_id=book.deployment.id,
                    product_id=book.deployment.product_id,
                    derisk_pending=book.deployment.futures_derisk_pending,
                    flat_confirmation_after=book.deployment.futures_flat_confirmation_after,
                )
                for book in books
            ),
            "collateral_group": collateral,
        }
    )


def _ownership_findings(
    books: Sequence[DeploymentSnapshot],
    latest: FuturesAccountObservation,
    findings: list[ReadinessFinding],
    *,
    complete: bool,
    now: datetime,
) -> None:
    """Readiness has mirror facts, not a complete order/catalog ownership proof.

    Apply the shared proof instead of treating a matching product id as recovered.
    Missing order coverage/catalog size remains unknown; no extra venue call is made.
    """
    account = observation_live_account(latest, {})
    findings.extend(
        ReadinessFinding(
            reason_code="POSITION_COMPARISON_UNPROVEN",
            severity=ReadinessSeverity.UNKNOWN,
            deployment_id=book.deployment.id,
            detail="Managed ownership/comparison requires a complete ledger and fresh exclusive "
            "venue order and position evidence; the mirror alone cannot prove recovery.",
        )
        for book in books
        if visible_position_comparison(book, account, books=books, complete=complete, now=now)
        is None
    )


def _section(latest: FuturesAccountObservation, *, stale: bool) -> ReadinessFuturesSection:
    """Project the snapshot onto exact USD strings."""
    balance = latest.balance
    return ReadinessFuturesSection(
        observed_at=latest.observed_at,
        stale=stale,
        enablement=latest.enablement.value,
        futures_buying_power=_text(None if balance is None else balance.futures_buying_power),
        cbi_usd_balance=_text(None if balance is None else balance.cbi_usd_balance),
        cfm_usd_balance=_text(None if balance is None else balance.cfm_usd_balance),
        available_margin=_text(None if balance is None else balance.available_margin),
        liquidation_threshold=_text(None if balance is None else balance.liquidation_threshold),
        liquidation_buffer_amount=_text(
            None if balance is None else balance.liquidation_buffer_amount
        ),
        liquidation_buffer_percentage=_text(
            None if balance is None else balance.liquidation_buffer_percentage
        ),
        margin_ratio=_text(None if balance is None else margin_ratio(balance)),
        unrealized_pnl=_text(None if balance is None else balance.unrealized_pnl),
        funding_pnl=_text(None if balance is None else balance.funding_pnl),
        positions=(
            None
            if latest.positions is None
            else tuple(
                ReadinessFuturesPosition(
                    product_id=position.product_id,
                    side=position.side.value,
                    number_of_contracts=format(position.number_of_contracts, "f"),
                )
                for position in latest.positions
            )
        ),
        collateral_note=SHARED_COLLATERAL_NOTE,
    )


def _findings(
    latest: FuturesAccountObservation,
    section: ReadinessFuturesSection,
    findings: list[ReadinessFinding],
    *,
    managed_products: frozenset[str] = frozenset(),
    managed_complete: bool = True,
) -> None:
    """Add the futures findings for one snapshot."""
    if section.stale or latest.enablement is FuturesEnablement.UNKNOWN or section.positions is None:
        findings.append(
            _finding(
                "FUTURES_ACCOUNT_UNKNOWN",
                ReadinessSeverity.UNKNOWN,
                "The futures account snapshot is stale or a read failed; futures positions and "
                "use of the shared USDC collateral are unknown, not zero.",
            )
        )
    if latest.enablement is FuturesEnablement.ENABLED:
        findings.append(
            _finding(
                "FUTURES_COLLATERAL_SHARED",
                ReadinessSeverity.INFO,
                "Futures are enabled and Coinbase counts the USDC spot balance as futures "
                "collateral; futures margin and USDC/USD books draw on one pool.",
            )
        )
    external = tuple(
        row for row in section.positions or () if row.product_id not in managed_products
    )
    if external and managed_complete and not section.stale:
        findings.append(
            _finding(
                "FUTURES_POSITIONS_EXTERNAL",
                ReadinessSeverity.ADVISORY,
                f"{len(external)} open CFM futures position(s) are unmanaged exposure "
                "consuming the shared collateral; no bot or policy cap accounts for them.",
            )
        )


def _live_findings(books: Sequence[DeploymentSnapshot], findings: list[ReadinessFinding]) -> None:
    """Name managed books and persisted duties even without a mirror observation."""
    if books:
        findings.append(
            _finding(
                "FUTURES_LIVE_BOOKS",
                ReadinessSeverity.INFO,
                f"{len(books)} managed live futures book(s); read-only visibility, "
                "live starts remain unsupported.",
            )
        )
    for book in books:
        duties = (
            (book.deployment.futures_derisk_pending, "FUTURES_DERISK_PENDING"),
            (
                book.deployment.futures_flat_confirmation_after is not None,
                "FUTURES_FLAT_CONFIRMATION_PENDING",
            ),
            (
                (book.deployment.mismatch_detail or "").startswith("FUTURES_POSITION_MISMATCH"),
                "FUTURES_MANAGED_POSITION_MISMATCH",
            ),
        )
        for active, code in duties:
            if active:
                findings.append(
                    ReadinessFinding(
                        reason_code=code,
                        severity=ReadinessSeverity.VIOLATION,
                        deployment_id=book.deployment.id,
                        detail=(
                            "A persisted futures safety obligation remains outstanding; unknown "
                            "evidence cannot clear it."
                        ),
                    )
                )


def _collateral(
    latest: FuturesAccountObservation, policy: RiskPolicyDefinition | None, *, stale: bool
) -> FuturesCollateralGroup:
    """Compare current USD margin to one explicitly denominated reserve under a peg."""
    initial = None if latest.balance is None or stale else latest.balance.initial_margin
    coverage = reserve_coverage(policy, initial)
    return FuturesCollateralGroup(
        currency=coverage.currency,
        reserve_quote=coverage.reserve_quote,
        required_margin_usd=_text(coverage.required_usd),
        headroom_quote=_text(coverage.headroom_quote),
        covers_current_margin=coverage.covers,
    )


def _finding(code: str, severity: ReadinessSeverity, detail: str) -> ReadinessFinding:
    """One fleet-level futures finding."""
    return ReadinessFinding(reason_code=code, severity=severity, detail=detail)


def _text(value: Decimal | None) -> str | None:
    """Exact decimal string, or ``None`` when unknown."""
    return None if value is None else format(value, "f")
