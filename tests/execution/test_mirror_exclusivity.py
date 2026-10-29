"""Reports prove product exclusivity from the mirror's own order listing (ADR 0151).

Without it every API and operator read of a live CFM book saw exclusivity as unknown, so
``fleet-health`` reported the live CFM scope blocked while the worker, which reads the
venue listing itself, admitted entries.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from tests.alerts.test_futures_recovery_proof import proven_book
from tests.execution.test_paper_futures_books import _observation
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesEnablement,
    FuturesOpenOrderRef,
)
from thytrader.execution import futures_visibility_fleet
from thytrader.execution.futures_live import managed_order_ids
from thytrader.execution.futures_sources import external_order_products
from thytrader.execution.futures_visibility import read_visibility_account, visibility_scope
from thytrader.trading.models import DeploymentStatus, OrderStatus

if TYPE_CHECKING:
    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.execution.futures_paper import FuturesObservationReader
    from thytrader.trading.models import DeploymentSnapshot

pytestmark = pytest.mark.anyio


class _Mirror:
    """One stored receipt."""

    def __init__(self, receipt: FuturesAccountObservation) -> None:
        """Serve ``receipt`` as the newest row."""
        self.receipt = receipt

    async def latest(self) -> FuturesAccountObservation:
        """Return the stored receipt."""
        return self.receipt


async def test_mirror_exclusivity_matches_exact_managed_client_ids() -> None:
    """Managed orders are not external; a foreign order is; no listing stays unknown."""
    book, _account, now = await proven_book()
    product = book.deployment.product_id
    order = book.orders[0]
    receipt = FuturesAccountObservation(
        observed_at=now - timedelta(seconds=5),
        enablement=FuturesEnablement.ENABLED,
        balance=None,
        positions=(),
        intraday_margin_setting=None,
        margin_window=None,
        read_failures=(),
    )

    async def external(orders: tuple[FuturesOpenOrderRef, ...] | None) -> frozenset[str] | None:
        mirror = cast("FuturesAccountSnapshotStore", _Mirror(replace(receipt, open_orders=orders)))
        account, _ = await read_visibility_account(mirror, None, (book,))
        assert account is not None
        return account.venue.external_order_products

    assert await external(None) is None
    assert await external(()) == frozenset()
    managed = FuturesOpenOrderRef(order.product_id or product, order.client_order_id)
    assert await external((managed,)) == frozenset()
    assert await external((managed, FuturesOpenOrderRef(product, "someone-else"))) == {product}
    assert await external((FuturesOpenOrderRef(product, None),)) == {product}


async def test_an_order_placed_after_the_books_were_read_is_owned_after_a_reload() -> None:
    """Ownership is re-read once after the receipt, so a fresh managed order is not external."""
    book, _account, now = await proven_book()
    order = book.orders[0]
    product = book.deployment.product_id
    receipt = FuturesAccountObservation(
        observed_at=now - timedelta(seconds=5),
        enablement=FuturesEnablement.ENABLED,
        balance=None,
        positions=(),
        intraday_margin_setting=None,
        margin_window=None,
        read_failures=(),
        open_orders=(FuturesOpenOrderRef(order.product_id or product, order.client_order_id),),
    )
    earlier = replace(book, orders=())
    mirror = cast("FuturesAccountSnapshotStore", _Mirror(receipt))

    async def reload() -> tuple[DeploymentSnapshot, ...]:
        return (book,)

    stale, _ = await read_visibility_account(mirror, None, (earlier,))
    assert stale is not None
    assert stale.venue.external_order_products == {product}
    fresh, _ = await read_visibility_account(mirror, None, (earlier,), reload)
    assert fresh is not None
    assert fresh.venue.external_order_products == frozenset()


async def test_the_fleet_loader_matches_ownership_against_retired_books_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped book's order is still ours; the loader must not drop it before matching."""
    book, _account, now = await proven_book()
    seen: list[tuple[DeploymentSnapshot, ...]] = []

    async def mirror(store: object, observations: object, books: tuple, reload: object) -> None:
        del store, observations, reload
        seen.append(tuple(books))

    retired = replace(
        book,
        position=None,
        positions=(),
        deployment=replace(book.deployment, status=DeploymentStatus.STOPPED, id=uuid4()),
        intents=(),
        fills=(),
        orders=tuple(
            replace(order, id=uuid4(), status=OrderStatus.CANCELED, filled_quantity=Decimal(0))
            for order in book.orders
        ),
    )
    assert visibility_scope(retired) is False
    monkeypatch.setattr(futures_visibility_fleet, "mirror_live_account", mirror)
    await futures_visibility_fleet.load_live_futures_probes(
        (book, retired), store=None, runtime=None, now=now
    )
    assert seen
    assert retired in seen[0]


async def test_listed_product_ids_compare_canonically() -> None:
    """A lower-case venue product id still matches its managed upper-case pair."""
    managed = frozenset({("BIP-20DEC30-CDE", "ours")})
    listing = (FuturesOpenOrderRef("bip-20dec30-cde", "ours"),)
    assert external_order_products(listing, managed) == frozenset()
    book, _account, _now = await proven_book()
    order = book.orders[0]
    lower = replace(order, product_id=(order.product_id or book.deployment.product_id).lower())
    ids = managed_order_ids((replace(book, orders=(lower,)),))
    assert ((order.product_id or book.deployment.product_id).upper(), order.client_order_id) in ids


async def test_the_mirror_projection_carries_catalog_maintenance_windows() -> None:
    """A recorded maintenance window reaches mirror evidence, as it reaches the worker's."""
    book, _account, now = await proven_book()
    product = book.deployment.product_id
    window = replace(
        _observation(),
        product_id=product,
        maintenance_starts_at=now - timedelta(minutes=5),
        maintenance_ends_at=now + timedelta(minutes=55),
    )

    class _Catalog:
        async def latest_instrument(self, product_id: str) -> tuple[object, object] | None:
            return (window, now) if product_id == product else None

    receipt = FuturesAccountObservation(
        observed_at=now - timedelta(seconds=5),
        enablement=FuturesEnablement.ENABLED,
        balance=None,
        positions=(),
        intraday_margin_setting=None,
        margin_window=None,
        read_failures=(),
        open_orders=(),
    )
    mirror = cast("FuturesAccountSnapshotStore", _Mirror(receipt))
    account, _ = await read_visibility_account(
        mirror, cast("FuturesObservationReader", _Catalog()), (book,)
    )
    assert account is not None
    assert product in account.venue.maintenance
