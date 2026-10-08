"""Shared entry-gate vocabulary: the proposed entry, verdict constructors, and book exposure.

Every gate module builds verdicts with ``_allow`` and the fail-closed ``_deny``. A book's
products are its primary product plus every product it holds, works, or runs, and its
marked exposure is position cost plus every working entry remainder on those products.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict
from thytrader.trading.exposure import product_exposure
from thytrader.trading.models import DeploymentSnapshot, resolved_product_id, snapshot_positions

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProposedEntry:
    """One sized entry the runtime wants to rest after a matched closed bar."""

    product_id: str
    strategy_id: UUID | None
    notional: Decimal
    is_pyramid_add: bool = False
    quantity: Decimal | None = None


def _book_products(snapshot: DeploymentSnapshot) -> set[str]:
    """Every product a book holds, works, or runs (its primary product included)."""
    products = {snapshot.deployment.product_id}
    products.update(
        resolved_product_id(position.product_id, snapshot.deployment)
        for position in snapshot_positions(snapshot)
    )
    products.update(runtime.product_id for runtime in snapshot.instrument_runtimes)
    products.update(
        resolved_product_id(order.product_id, snapshot.deployment) for order in snapshot.orders
    )
    return {product for product in products if product}


def _marked_exposure(snapshot: DeploymentSnapshot) -> Decimal:
    """Position cost plus every working entry remainder, even with a stale FLAT overlay."""
    return sum(
        (product_exposure(snapshot, product) for product in _book_products(snapshot)), Decimal("0")
    )


def _allow() -> RiskVerdict:
    """Return a successful gate decision."""
    return RiskVerdict(
        decision=RiskDecision.ALLOW,
        reason_code=RiskReasonCode.ALLOWED,
        detail="Risk policy allows this action.",
    )


def _deny(reason_code: RiskReasonCode, detail: str) -> RiskVerdict:
    """Return a fail-closed gate decision."""
    return RiskVerdict(decision=RiskDecision.DENY, reason_code=reason_code, detail=detail)
