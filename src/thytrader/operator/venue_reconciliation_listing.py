"""Venue side of venue reconciliation: balance and open-order listing reads.

Reads the whole venue balance listing and the nonterminal spot order listing, failing
closed with explicit listing evidence whenever a read is unavailable or incomplete.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.exchanges.read_errors import ExchangeReadError
from thytrader.operator.venue_reconciliation_managed import _ZERO
from thytrader.operator.venue_reconciliation_models import (
    ForeignOpenOrderRow,
    VenueFinding,
    VenueListingEvidence,
    VenueSeverity,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.portfolio.service import PortfolioService


class _BalancesRead:
    """Outcome of the venue balance listing."""

    def __init__(
        self,
        evidence: VenueListingEvidence,
        rows: dict[str, tuple[Decimal, Decimal, Decimal, int]] | None,
        demo: bool,
    ) -> None:
        """Store listing evidence plus summed rows per currency (or None when unknown)."""
        self.evidence = evidence
        self.rows = rows
        self.demo = demo


async def _read_balances(
    portfolio: PortfolioService, findings: list[VenueFinding]
) -> tuple[_BalancesRead, dict[str, tuple[Decimal, Decimal, Decimal, int]]]:
    """Read the whole venue balance listing, failing closed on any incompleteness."""
    try:
        observed = await portfolio.get_portfolio()
    except ExchangeReadError as error:
        evidence = VenueListingEvidence(
            status="unavailable", failure=error.failure, observed_at=None
        )
        findings.append(
            VenueFinding(
                reason_code="VENUE_BALANCES_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue balance listing is incomplete "
                    f"({error.failure.summary()}); inventory comparisons are unknown, "
                    "never guessed."
                ),
            )
        )
        return _BalancesRead(evidence, None, False), {}
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        evidence = VenueListingEvidence(status="unavailable", observed_at=None)
        findings.append(
            VenueFinding(
                reason_code="VENUE_BALANCES_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue balance listing is incomplete; inventory comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
        return _BalancesRead(evidence, None, False), {}
    rows: dict[str, tuple[Decimal, Decimal, Decimal, int]] = {}
    for asset in observed.assets:
        available, hold, total, count = rows.get(asset.currency, (_ZERO, _ZERO, _ZERO, 0))
        rows[asset.currency] = (
            available + asset.available,
            hold + asset.hold,
            total + asset.total,
            count + 1,
        )
    evidence = VenueListingEvidence(
        status="complete",
        scope="venue_balances",
        demo=observed.demo,
        observed_at=observed.as_of,
        rows=len(observed.assets),
    )
    return _BalancesRead(evidence, rows, observed.demo), rows


async def _read_open_orders(
    portfolio: PortfolioService, findings: list[VenueFinding]
) -> tuple[tuple[ForeignOpenOrderRow, ...] | None, VenueListingEvidence]:
    """Read all nonterminal spot orders; None means unknown, never an empty listing."""
    try:
        orders = await portfolio.list_open_orders()
    except ExchangeReadError as error:
        evidence = VenueListingEvidence(
            status="unavailable", failure=error.failure, observed_at=None
        )
        findings.append(
            VenueFinding(
                reason_code="VENUE_ORDERS_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue open-order listing is incomplete "
                    f"({error.failure.summary()}); order comparisons are unknown, never "
                    "guessed."
                ),
            )
        )
        return None, evidence
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        evidence = VenueListingEvidence(status="unavailable", observed_at=None)
        findings.append(
            VenueFinding(
                reason_code="VENUE_ORDERS_LISTING_INCOMPLETE",
                severity=VenueSeverity.UNKNOWN,
                detail=(
                    "The venue open-order listing is incomplete; order comparisons are "
                    "unknown, never guessed."
                ),
            )
        )
        return None, evidence
    evidence = VenueListingEvidence(
        status="complete",
        scope="spot_order_history_nonterminal",
        demo=portfolio.demo,
        observed_at=datetime.now(UTC),
        rows=len(orders),
    )
    return (
        tuple(
            ForeignOpenOrderRow(
                venue_order_id=order.venue_order_id,
                client_order_id=order.client_order_id,
                product_id=order.product_id,
                side=order.side,
                status=order.status,
            )
            for order in orders
        ),
        evidence,
    )
