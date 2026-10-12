"""Bounded read-only evidence loading for the live CFM fleet admission check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.futures_live import (
    current_live_futures_runtime,
    prepare_live_futures_book,
)
from thytrader.execution.futures_paper import current_futures_runtime
from thytrader.execution.futures_visibility import mirror_live_account, visibility_scope
from thytrader.execution.futures_visibility_probe import visibility_probe

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.execution.futures_paper import FuturesRuntime
    from thytrader.risk.futures_entry_probe import LiveFuturesProbe
    from thytrader.risk.futures_live import FuturesVenueEvidence
    from thytrader.trading.models import DeploymentSnapshot


async def load_live_futures_probes(
    snapshots: Sequence[DeploymentSnapshot],
    *,
    store: FuturesAccountSnapshotStore | None,
    runtime: FuturesRuntime | None,
    now: datetime,
) -> tuple[FuturesVenueEvidence | None, dict[UUID, LiveFuturesProbe]]:
    """Reuse cycle account evidence in the worker, durable mirror evidence in reports."""
    books = tuple(book for book in snapshots if visibility_scope(book) is not False)
    if not books or any(visibility_scope(book) is None for book in snapshots):
        return None, {}
    runtime = runtime or current_futures_runtime()
    try:
        account = (
            await prepare_live_futures_book(books[0], portfolio=snapshots)
            if current_live_futures_runtime() is not None
            else await mirror_live_account(
                store, None if runtime is None else runtime.observations, books
            )
        )
    except Exception:  # noqa: BLE001 - failed bounded evidence reads deny, never pass.
        account = None
    probes: dict[UUID, LiveFuturesProbe] = {}
    if runtime is not None:
        for book in books:
            try:
                binding = await runtime.contracts.load_contract(book.deployment.id)
                observation = await runtime.observations.latest_instrument(
                    book.deployment.product_id
                )
                price = (
                    None
                    if account is None or not account.venue.is_fresh(now)
                    else account.marks.get(book.deployment.product_id)
                )
                probes[book.deployment.id] = visibility_probe(
                    book,
                    binding=binding,
                    observation=None if observation is None else observation[0],
                    price=price,
                    now=now,
                )
            except Exception:  # noqa: BLE001 - one unreadable binding leaves that probe unknown.
                probes.pop(book.deployment.id, None)
    return None if account is None else account.venue, probes
