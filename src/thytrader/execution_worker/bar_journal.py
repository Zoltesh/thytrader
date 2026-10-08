"""Closed-bar decision journaling for strategy books (ADR 0087).

Wraps one closed-bar call so the decision journal records what it decided, skips bars
already evaluated, and journals a raised call as an error before re-raising it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.decision_journal import observe_bar, record_bar_decision
from thytrader.execution.decision_scope import note_reference_gate

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from thytrader.execution.references import ReferenceGate
    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot


async def _journaled_bar(
    before: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product_id: str,
    candle: Candle,
    allow_new_entries: bool,
    advance: Callable[[], Awaitable[DeploymentSnapshot]],
    require_activity: bool = False,
    reference_gate: ReferenceGate | None = None,
) -> DeploymentSnapshot:
    """Run one closed-bar call and journal what it decided (ADR 0087).

    The call runs exactly as without a journal. A bar that was already evaluated
    (between-bar protection) is not journaled again. ``require_activity`` (flatten
    passes, priced on the latest closed bar) journals only when the call created
    intents or fills, even on an already evaluated bar. A raised call is journaled
    as an error and re-raised unchanged. ``reference_gate`` records why a stale or
    missing reference instrument blocked entries on this bar (ADR 0096).
    """
    if not require_activity and before.deployment.last_evaluated_bar == candle.starts_at:
        return await advance()
    with observe_bar() as observations:
        if reference_gate is not None:
            note_reference_gate(reference_gate)
        try:
            after = await advance()
        except Exception as error:
            await record_bar_decision(
                strategy=strategy,
                product_id=product_id,
                candle=candle,
                before=before,
                after=None,
                observations=observations,
                allow_new_entries=allow_new_entries,
                error=(
                    f"closed-bar processing raised {type(error).__name__}; "
                    "the cycle retries next interval"
                ),
            )
            raise
    if require_activity and not _bar_had_activity(before, after):
        return after
    await record_bar_decision(
        strategy=strategy,
        product_id=product_id,
        candle=candle,
        before=before,
        after=after,
        observations=observations,
        allow_new_entries=allow_new_entries,
    )
    return after


def _bar_had_activity(before: DeploymentSnapshot, after: DeploymentSnapshot) -> bool:
    """Whether a closed-bar call created intents or recorded fills."""
    return len(after.intents) != len(before.intents) or len(after.fills) != len(before.fills)
