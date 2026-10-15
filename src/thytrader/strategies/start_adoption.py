"""Adopt the convergence warmup policy when a strategy saved before it is started (ADR 0142).

A start snapshots the strategy's current saved rules (ADR 0082). A strategy saved before the
convergence warmup policy (ADR 0140) is first re-saved with its unchanged document, which is
exactly what saving does for any user: the policy is adopted and its warmups raised. Nobody
has to re-save strategies after upgrading. Bots already running keep the snapshot they started.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.library import StrategyRevisionConflictError
from thytrader.strategies.warmup_policy import with_convergence_warmup

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.strategies.library import StrategyStore


async def adopt_warmup_policy_for_start(store: StrategyStore, strategy_id: UUID) -> bool:
    """Re-save a strategy saved before the policy so its start snapshot carries it.

    Returns whether it was re-saved. A strategy that already carries the policy, is invalid,
    or needs more warmup than a strategy can declare is left alone, as is one another writer
    changed meanwhile; its start then warns (or refuses, with the opt-in safeguard).
    """
    record = await store.get(strategy_id)
    definition = record.definition
    if definition is None or definition.data_requirements.warmup_policy == "convergence":
        return False
    if with_convergence_warmup(definition).data_requirements.warmup_policy != "convergence":
        return False
    try:
        await store.save(strategy_id, record.document, expected_revision=record.revision)
    except StrategyRevisionConflictError:
        return False
    return True
