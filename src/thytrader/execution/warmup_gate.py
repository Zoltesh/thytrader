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
    """Explain one refusal and name the way out, short enough for a 500-character record."""
    subject = f"Strategy “{definition.name}” ({definition.strategy_id})"
    then = "stop this bot, then start the strategy anew" if action == "resumed" else "start it"
    decision, htf = convergence_warmups(definition)
    required = max(decision, htf or 0)
    if required > MAX_DECLARED_WARMUP_BARS:
        return (
            f"{WARMUP_POLICY_REQUIRED}: {subject} needs {required} bars to converge, over the "
            f"{MAX_DECLARED_WARMUP_BARS} it can declare (ADR 0140). Nothing was {action}. Shorten "
            f"its slowest indicator, save it, re-check its backtests, {then}."
        )
    return (
        f"{WARMUP_POLICY_REQUIRED}: {subject} predates the convergence warmup policy "
        f"(ADR 0140), so its signals depend on when it started. Nothing was {action}. "
        "Re-save its unchanged `document` from `thytrader-research show-strategy` with "
        f"`save-strategy --revision N --confirm`, re-check its backtests, {then}."
    )
