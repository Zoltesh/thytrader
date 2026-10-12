"""Read-only CFM visibility from existing mirror/catalog ports, never new venue authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.futures_live import FuturesLiveAccount
from thytrader.execution.futures_sources import CatalogContractSizes, _nonnegative, _positions
from thytrader.execution.futures_venue_check import PositionComparison, compare_position
from thytrader.risk.futures_live import FuturesVenueEvidence
from thytrader.trading.fill_ledger import unprojected_inventory_products
from thytrader.trading.models import resolved_product_id, snapshot_positions

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from decimal import Decimal

    from thytrader.exchanges.futures_models import (
        FuturesAccountObservation,
        FuturesAccountSnapshotStore,
    )
    from thytrader.execution.futures_paper import FuturesObservationReader
    from thytrader.trading.models import DeploymentSnapshot


def visible_position_comparison(
    book: DeploymentSnapshot,
    account: FuturesLiveAccount | None,
    *,
    now: datetime,
) -> PositionComparison | None:
    """Compare only a complete, single-product projection; foreign holdings are unknown."""
    positions = snapshot_positions(book)
    if (
        len(positions) > 1
        or (positions and book.position != positions[0])
        or any(
            resolved_product_id(row.product_id, book.deployment) != book.deployment.product_id
            for row in positions
        )
        or unprojected_inventory_products(book)
    ):
        return None
    return compare_position(book, None if account is None else account.venue, now=now)


async def mirror_live_account(
    store: FuturesAccountSnapshotStore | None,
    observations: FuturesObservationReader | None,
    books: Sequence[DeploymentSnapshot],
) -> FuturesLiveAccount | None:
    """Reuse durable reads; missing order coverage remains unknown, never an empty listing.

    This function has no exchange client or mutation capability. Receipt timestamps are
    copied unchanged from the mirror. Catalog failure only invalidates contract comparison.
    """
    account, _ = await read_visibility_account(store, observations, books)
    return account


async def read_visibility_account(
    store: FuturesAccountSnapshotStore | None,
    observations: FuturesObservationReader | None,
    books: Sequence[DeploymentSnapshot],
) -> tuple[FuturesLiveAccount | None, FuturesAccountObservation | None]:
    """Load exactly one receipt so funding counters and account metrics share provenance."""
    if store is None:
        return None, None
    latest = await store.latest()
    if latest is None:
        return None, None
    sizes = {}
    if observations is not None:
        source = CatalogContractSizes(observations)
        for product in {book.deployment.product_id for book in books}:
            size = await source.contract_size(product)
            if size is not None:
                sizes[product] = size
    return observation_live_account(latest, sizes), latest


def observation_live_account(
    latest: FuturesAccountObservation,
    sizes: Mapping[str, Decimal],
    external_order_products: frozenset[str] | None = None,
) -> FuturesLiveAccount:
    """Project exactly one mirror receipt; callers supply separately proved order coverage."""
    positions, marks = _positions(latest.positions)
    balance = latest.balance
    window = latest.margin_window
    flags = (
        ()
        if window is None
        else (
            window.intraday_killswitch_enabled,
            window.enrollment_killswitch_enabled,
        )
    )
    killswitch = (
        True
        if any(flag is True for flag in flags)
        else (False if flags and all(flag is False for flag in flags) else None)
    )
    return FuturesLiveAccount(
        venue=FuturesVenueEvidence(
            observed_at=latest.observed_at,
            buying_power_usd=_nonnegative(
                None if balance is None else balance.futures_buying_power
            ),
            initial_margin_usd=_nonnegative(None if balance is None else balance.initial_margin),
            positions=positions,
            external_order_products=external_order_products,
            killswitch_enabled=killswitch,
            maintenance={},
            contract_sizes=sizes,
        ),
        marks=marks,
        available_margin_usd=None if balance is None else balance.available_margin,
        liquidation_threshold_usd=None if balance is None else balance.liquidation_threshold,
    )
