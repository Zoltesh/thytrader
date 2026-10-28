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
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.execution.futures_paper import FuturesRuntime
    from thytrader.market_data.futures_observations import FuturesInstrumentObservation
    from thytrader.risk.futures_entry_probe import LiveFuturesProbe
    from thytrader.risk.futures_live import FuturesVenueEvidence
    from thytrader.trading.futures_book import BoundFuturesContract
    from thytrader.trading.models import DeploymentSnapshot


async def load_live_futures_probes(
    snapshots: Sequence[DeploymentSnapshot],
    *,
    store: FuturesAccountSnapshotStore | None,
    runtime: FuturesRuntime | None,
    now: datetime,
    clock: Callable[[], datetime] | None = None,
) -> tuple[FuturesVenueEvidence | None, dict[UUID, LiveFuturesProbe], datetime]:
    """Reuse cycle account evidence in the worker, durable mirror evidence in reports.

    Every read comes first; freshness is judged after them (ADR 0115), at ``clock()`` when a
    clock is given and at ``now`` otherwise. A receipt this pass read itself is then never
    future-dated against a time taken before the read. Returns the venue evidence, the probes
    and the time they were judged at, which the verdicts must use too.
    """
    books = tuple(book for book in snapshots if visibility_scope(book) is not False)
    if not books or any(visibility_scope(book) is None for book in snapshots):
        return None, {}, now
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
    reads = [] if runtime is None else [await _probe_inputs(runtime, book) for book in books]
    judged = now if clock is None else clock()
    probes: dict[UUID, LiveFuturesProbe] = {}
    for read in reads:
        if read is None:
            continue
        book, binding, observation = read
        try:
            price = (
                None
                if account is None or not account.venue.is_fresh(judged)
                else account.marks.get(book.deployment.product_id)
            )
            probes[book.deployment.id] = visibility_probe(
                book, binding=binding, observation=observation, price=price, now=judged
            )
        except Exception:  # noqa: BLE001 - one unreadable binding leaves that probe unknown.
            probes.pop(book.deployment.id, None)
    return None if account is None else account.venue, probes, judged


async def _probe_inputs(
    runtime: FuturesRuntime, book: DeploymentSnapshot
) -> (
    tuple[DeploymentSnapshot, BoundFuturesContract | None, FuturesInstrumentObservation | None]
    | None
):
    """Read one book's binding and newest instrument facts; ``None`` leaves its probe unknown."""
    try:
        binding = await runtime.contracts.load_contract(book.deployment.id)
        observation = await runtime.observations.latest_instrument(book.deployment.product_id)
    except Exception:  # noqa: BLE001 - one unreadable binding leaves that probe unknown.
        return None
    return book, binding, None if observation is None else observation[0]
