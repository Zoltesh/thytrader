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
from thytrader.operator.readiness_models import (
    ReadinessFinding,
    ReadinessFuturesPosition,
    ReadinessFuturesSection,
    ReadinessSeverity,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.futures_models import (
        FuturesAccountObservation,
        FuturesAccountSnapshotStore,
    )

_STALE_AFTER = timedelta(seconds=180)


async def futures_section(
    store: FuturesAccountSnapshotStore | None,
    findings: list[ReadinessFinding],
    *,
    now: datetime | None = None,
) -> ReadinessFuturesSection | None:
    """Return the futures section, or ``None`` when no mirror snapshot exists (not set up)."""
    if store is None:
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
    stale = (now or datetime.now(UTC)) - latest.observed_at > _STALE_AFTER
    section = _section(latest, stale=stale)
    _findings(latest, section, findings)
    return section


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
    if section.positions:
        findings.append(
            _finding(
                "FUTURES_POSITIONS_EXTERNAL",
                ReadinessSeverity.ADVISORY,
                f"{len(section.positions)} open CFM futures position(s) are unmanaged exposure "
                "consuming the shared collateral; no bot or policy cap accounts for them.",
            )
        )


def _finding(code: str, severity: ReadinessSeverity, detail: str) -> ReadinessFinding:
    """One fleet-level futures finding."""
    return ReadinessFinding(reason_code=code, severity=severity, detail=detail)


def _text(value: Decimal | None) -> str | None:
    """Exact decimal string, or ``None`` when unknown."""
    return None if value is None else format(value, "f")
