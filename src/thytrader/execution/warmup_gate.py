"""Refuse paper and live starts and resumes of snapshots seeded below convergence (ADR 0140).

A strategy snapshot saved before the convergence warmup policy seeds its recursive indicators
on their minimum warmup, so its signals depend on when the book started. Starts and operator
resumes require the policy; books already running keep running, and discretionary books, which
evaluate no strategy rules, never pass through here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.warmup_policy import MAX_DECLARED_WARMUP_BARS, convergence_warmups
from thytrader.trading.models import ExecutionConflictError

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition

WARMUP_POLICY_REQUIRED = "WARMUP_POLICY_REQUIRED"
"""Reason-code prefix of a refused start or resume."""


def require_convergence_policy(definition: StrategyDefinition, *, action: str) -> None:
    """Raise unless the snapshot seeds every indicator under the convergence policy.

    ``action`` is the refused verb as the operator sees it ("started" or "resumed").
    """
    if definition.data_requirements.warmup_policy == "convergence":
        return
    raise ExecutionConflictError(convergence_policy_message(definition, action=action))


def convergence_policy_message(definition: StrategyDefinition, *, action: str) -> str:
    """Explain one refusal and name the way out."""
    subject = f"Strategy “{definition.name}” ({definition.strategy_id})"
    restart = " as a new bot after stopping this one" if action == "resumed" else ""
    decision, htf = convergence_warmups(definition)
    if max(decision, htf or 0) > MAX_DECLARED_WARMUP_BARS:
        return (
            f"{WARMUP_POLICY_REQUIRED}: {subject} needs {max(decision, htf or 0)} warmup bars "
            f"for its indicators to converge, more than the {MAX_DECLARED_WARMUP_BARS} a strategy "
            f"can declare (ADR 0140). Nothing was {action}. Shorten its slowest indicator (for "
            "example a KAMA slow constant), save it, re-check its backtests, then start it"
            f"{restart}."
        )
    remedy = (
        "Re-save the strategy unchanged to adopt the policy (`uv run thytrader-research "
        f"show-strategy --strategy-id {definition.strategy_id}`, then pass its `document`, "
        f"unchanged, to `save-strategy --strategy-id {definition.strategy_id} --file "
        "document.json --revision N --confirm` with its current revision), re-check its "
        f"backtests under the longer warmup, then start it{restart}"
    )
    return (
        f"{WARMUP_POLICY_REQUIRED}: {subject} was saved before the convergence warmup policy "
        "(ADR 0140), so its indicators would be seeded on their minimum warmup and its signals "
        f"would depend on when the bot started. Nothing was {action}. {remedy}."
    )
