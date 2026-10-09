"""PostgreSQL per-base advisory lock that serialises inventory adoption (ADR 0124).

An adoption holds ``thytrader:inventory:live:<BASE>`` exclusively for its transaction:
it reads every live book's claims, reads the venue balance, then writes. A live ENTRY
intent takes the same key shared, so no entry intent can be written between an
adoption's claim read and its commit, while entries on one base never block each other.
Both locks are transaction-scoped and released at commit or rollback. Take this lock
before the fleet latch row, so adoption and entry writers lock in the same order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, text

from thytrader.market_data.products import is_spot_product_id
from thytrader.persistence.schema import deployments
from thytrader.trading.geometry import base_currency

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.trading.models import OrderIntent

_KEY_PREFIX = "thytrader:inventory:live:"
_EXCLUSIVE = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_SHARED = text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))")


def inventory_lock_key(base: str) -> str:
    """The advisory lock key text for one live base currency."""
    return f"{_KEY_PREFIX}{base}"


async def lock_live_base(connection: AsyncConnection, base: str, *, shared: bool) -> None:
    """Take the transaction's exclusive (adoption) or shared (entry) lock on one base."""
    await connection.execute(_SHARED if shared else _EXCLUSIVE, {"key": inventory_lock_key(base)})


async def lock_entry_base(connection: AsyncConnection, intent: OrderIntent, *, mode: str) -> None:
    """Hold the live base lock shared while one live entry intent is written.

    Paper entries take no lock: paper books own no venue inventory.
    """
    if mode != "live":
        return
    product_id = intent.product_id or await connection.scalar(
        select(deployments.c.product_id).where(deployments.c.id == intent.deployment_id)
    )
    if isinstance(product_id, str) and is_spot_product_id(product_id):
        await lock_live_base(connection, base_currency(product_id), shared=True)
