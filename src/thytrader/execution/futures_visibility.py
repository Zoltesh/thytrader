"""Read-only CFM visibility from existing mirror/catalog ports, never new venue authority."""

from __future__ import annotations

from decimal import DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.execution.futures_cancel import _owned_order
from thytrader.execution.futures_live import FuturesLiveAccount, managed_live_futures
from thytrader.execution.futures_sources import CatalogContractSizes, _nonnegative, _positions
from thytrader.execution.futures_venue_check import PositionComparison, compare_position
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.futures_live import FuturesVenueEvidence
from thytrader.trading.fill_ledger import unprojected_inventory_products
from thytrader.trading.futures_repair import managed_signed_base
from thytrader.trading.models import DeploymentMode, resolved_product_id, snapshot_positions

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
    books: Sequence[DeploymentSnapshot] | None = None,
    complete: bool = True,
) -> PositionComparison | None:
    """Prove a unique owner, full signed ledger and fresh post-fill comparison.

    Callers with portfolio inventory must supply it; the default is a single-book proof.
    This read-only proof never changes I9 counters, duties, orders or lifecycle state.
    """
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            portfolio = (book,) if books is None else books
            owners = [
                item
                for item in portfolio
                if managed_live_futures(item)
                and item.deployment.product_id == book.deployment.product_id
            ]
            if (
                not complete
                or any(not item.accounting_complete for item in portfolio)
                or len(owners) != 1
                or owners[0] != book
                or account is None
                or account.venue.external_order_products is None
                or book.deployment.product_id in account.venue.external_order_products
            ):
                return None
            return _proven_comparison(book, account, now=now)
    except DecimalException:
        return None


def visibility_scope(snapshot: DeploymentSnapshot) -> bool | None:
    """Read-only managed scope: unknown accounting can never retire an obligation.

    Do not change the execution predicate or grant mutation authority. Callers retain
    None in their inventory and deny complete evidence until exact scope is established.
    """
    if snapshot.deployment.mode is not DeploymentMode.LIVE or not is_futures_product_id(
        snapshot.deployment.product_id
    ):
        return False
    if not snapshot.accounting_complete:
        return None
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            return managed_live_futures(snapshot)
    except DecimalException:
        return None


def _proven_comparison(
    book: DeploymentSnapshot, account: FuturesLiveAccount, *, now: datetime
) -> PositionComparison | None:
    """Keep all ledger arithmetic within the exact, fail-closed proof context."""
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
    if (
        len({order.id for order in book.orders}) != len(book.orders)
        or len({order.client_order_id for order in book.orders}) != len(book.orders)
        or len({intent.id for intent in book.intents}) != len(book.intents)
        or not all(_owned_order(book, order) for order in book.orders)
    ):
        return None
    intents = {intent.id: intent for intent in book.intents}
    if any(order.side != intents[order.intent_id].side for order in book.orders):
        return None
    sample = compare_position(book, account.venue, now=now)
    managed = managed_signed_base(book)
    size = account.venue.contract_sizes.get(book.deployment.product_id)
    if sample is None or managed is None or size is None or managed / size != sample.book_contracts:
        return None
    after = book.deployment.futures_flat_confirmation_after
    if after is not None and account.venue.observed_at <= after:
        return None
    return sample


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
