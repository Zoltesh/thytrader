"""In-memory inventory adoption under an ``asyncio.Lock`` per (mode, base) (ADR 0124).

The same order as PostgreSQL: lock, read every live book's claims, read the venue,
prepare, then write. Every check that can refuse runs before the first write, and the
writes never wait on anything, so a refusal leaves the store unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.trading.adoption_write import (
    AdoptionCommit,
    flat_snapshot,
    prepare_adoption,
    read_venue_balances,
)
from thytrader.trading.fill_ledger import fill_projection_deployment
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    ExecutionConflictError,
    resolved_product_id,
)

if TYPE_CHECKING:
    from thytrader.trading.adoption_write import AdoptionWrite, BalanceReader, PreparedAdoption
    from thytrader.trading.memory import InMemoryExecutionStore

BALANCE_READ_TIMEOUT_SECONDS = 10.0


class InMemoryInventoryAdoptionStore:
    """Commit adoptions into an ``InMemoryExecutionStore``; implements the protocol."""

    def __init__(self, store: InMemoryExecutionStore) -> None:
        """Bind the execution store whose per-base locks entry intents also take."""
        self._store = store

    async def adopt_inventory(
        self, write: AdoptionWrite, *, read_balances: BalanceReader
    ) -> AdoptionCommit:
        """Lock, read claims, read the venue, prepare, then write everything."""
        store = self._store
        async with store.inventory_lock(DeploymentMode.LIVE, write.base):
            live_books = tuple(
                [
                    await store.get_deployment(deployment.id)
                    for deployment in tuple(store.deployments.values())
                    if deployment.mode is DeploymentMode.LIVE
                ]
            )
            balances = await read_venue_balances(
                read_balances, timeout_seconds=BALANCE_READ_TIMEOUT_SECONDS
            )
            book = await self._target_book(write)
            prepared = prepare_adoption(write, book=book, live_books=live_books, balances=balances)
            if write.idempotency_key is not None and (
                await store.get_intent_by_idempotency_key(write.idempotency_key)
            ):
                raise ExecutionConflictError("idempotency_key already used")
            if write.new_deployment is not None:
                await self._insert_book(write)
            snapshot = await self._write(book, prepared)
        return AdoptionCommit(
            snapshot=snapshot, records=prepared.records, availability=prepared.availability
        )

    async def _target_book(self, write: AdoptionWrite) -> DeploymentSnapshot:
        """The new book's empty snapshot, or the current existing book."""
        created = write.new_deployment
        if created is not None:
            if created.id != write.deployment_id or created.id in self._store.deployments:
                raise ExecutionConflictError("The new book is not the adoption's book.")
            return flat_snapshot(created)
        book = await self._store.get_deployment(write.deployment_id)
        if (
            write.expected_revision is not None
            and book.deployment.revision != write.expected_revision
        ):
            raise ExecutionConflictError("Deployment revision conflict.")
        return book

    async def _insert_book(self, write: AdoptionWrite) -> None:
        """Insert the new book, checking the fleet latch only when asked."""
        created = write.new_deployment
        if created is None:
            return
        if write.respect_entry_latch:
            await self._store.create_deployment(created)
        else:
            self._store.deployments[created.id] = created

    async def _write(
        self, book: DeploymentSnapshot, prepared: PreparedAdoption
    ) -> DeploymentSnapshot:
        """Write the records, position, runtime and next revision."""
        store = self._store
        records = prepared.records
        projected = prepared.projected
        deployment_id = book.deployment.id
        await store.save_intent(records.intent)
        await store.save_order(records.order)
        await store.save_fill(prepared.fill)
        product_id = resolved_product_id(records.order.product_id, book.deployment)
        await store.save_position(
            projected.position, deployment_id=deployment_id, product_id=product_id
        )
        for runtime in projected.instrument_runtimes:
            await store.save_instrument_runtime(runtime, deployment_id=deployment_id)
        await store.save_deployment(fill_projection_deployment(book, projected))
        return await store.get_deployment(deployment_id)
