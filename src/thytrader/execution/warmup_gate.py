"""Warn about, or refuse, starts and resumes of snapshots seeded below convergence.

A strategy snapshot saved before the convergence warmup policy (ADR 0140) seeds its recursive
indicators on their minimum warmup, so its signals depend on when the book started. A paper or
live start or an operator resume of such a snapshot warns; the risk policy's opt-in
``start_safeguards.require_convergence_warmup`` refuses it instead (ADR 0142). Books already
running keep running, and discretionary books, which evaluate no strategy rules, never pass
through here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.warmup_policy import MAX_DECLARED_WARMUP_BARS, convergence_warmups
from thytrader.trading.models import ExecutionConflictError

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition

WARMUP_POLICY_REQUIRED = "WARMUP_POLICY_REQUIRED"
"""Reason-code prefix of a start or resume refused by the opt-in safeguard."""

WARMUP_POLICY_MISSING = "WARMUP_POLICY_MISSING"
"""Reason-code prefix of the warning a start or resume of such a snapshot carries."""


def convergence_policy_check(
    definition: StrategyDefinition, *, action: str, enforce: bool
) -> str | None:
    """Return a warning for a snapshot without the policy, or raise when ``enforce`` is set.

    ``action`` is the verb as the operator sees it ("started" or "resumed"). ``None`` means
    the snapshot carries the policy and there is nothing to say.
    """
    if definition.data_requirements.warmup_policy == "convergence":
        return None
    if enforce:
        raise ExecutionConflictError(convergence_policy_message(definition, action=action))
    return (
        f"{WARMUP_POLICY_MISSING}: Strategy “{definition.name}” ({definition.strategy_id}) "
        "predates the convergence warmup policy (ADR 0140), so its signals depend on when the "
        f"bot started. It {action} anyway; re-save the strategy to adopt the policy, or set "
        "the risk policy's require_convergence_warmup to refuse this."
    )


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
