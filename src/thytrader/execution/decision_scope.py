"""Per-bar observations the closed-bar loop reports for the decision journal.

The execution worker binds one ``DecisionObservations`` around each closed-bar call.
Loop helpers report facts that a before/after snapshot cannot show (the evaluated
rule values, risk and freshness verdicts, why an entry was not attempted). Every
``note_*`` function is a no-op when nothing is bound and only assigns plain fields,
so journaling can never change or block a trading decision.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from thytrader.execution.decisions import DecisionSkipReason
    from thytrader.execution.geometry import EntrySkipReason
    from thytrader.execution.signals import LatestEntryEvaluation, LatestExitEvaluation
    from thytrader.risk.models import RiskVerdict


@dataclass(slots=True)
class DecisionObservations:
    """Facts reported by the loop while one bar is processed for one product."""

    entry_gate: DecisionSkipReason | None = None
    evaluation: LatestEntryEvaluation | None = None
    exit_evaluation: LatestExitEvaluation | None = None
    evaluation_error: str | None = None
    risk_verdicts: list[RiskVerdict] = field(default_factory=list)
    freshness: RiskVerdict | None = None
    breaker: RiskVerdict | None = None
    entry_block_code: str | None = None
    entry_block_detail: str | None = None
    entry_skip: EntrySkipReason | None = None


_SCOPE: ContextVar[DecisionObservations | None] = ContextVar(
    "decision_observation_scope", default=None
)


@contextmanager
def decision_observation_scope(*, enabled: bool) -> Iterator[DecisionObservations | None]:
    """Bind fresh observations for one bar; yields None (and binds nothing) when disabled."""
    if not enabled:
        yield None
        return
    observations = DecisionObservations()
    token: Token[DecisionObservations | None] = _SCOPE.set(observations)
    try:
        yield observations
    finally:
        _SCOPE.reset(token)


def decision_observation_active() -> bool:
    """Return whether a journal is observing the current bar."""
    return _SCOPE.get() is not None


def note_entry_gate(reason: DecisionSkipReason | None) -> None:
    """Record why this bar did not attempt an entry (cooldown, max books, working entry)."""
    observations = _SCOPE.get()
    if observations is None or reason is None:
        return
    observations.entry_gate = reason


def note_evaluation(evaluation: LatestEntryEvaluation) -> None:
    """Record the evaluated entry rule and the exact values it read."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.evaluation = evaluation


def note_exit_evaluation(evaluation: LatestExitEvaluation) -> None:
    """Record the evaluated ``exits.signal_exit`` rule and the values it read (ADR 0093)."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.exit_evaluation = evaluation


def note_evaluation_error(message: str) -> None:
    """Record a fail-closed signal-evaluation error message (no secrets, bounded)."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.evaluation_error = message[:500]


def note_risk(verdict: RiskVerdict) -> None:
    """Record one entry/reprice risk-gate verdict, allow or deny."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.risk_verdicts.append(verdict)


def note_freshness(verdict: RiskVerdict) -> None:
    """Record a freshness/prerequisite verdict that refused a matched signal."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.freshness = verdict


def note_breaker(verdict: RiskVerdict) -> None:
    """Record a runtime circuit-breaker verdict that paused new entries on this bar."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.breaker = verdict


def note_entry_skip(reason: EntrySkipReason) -> None:
    """Record why sizing or stop/target geometry rested no entry for a matched signal."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.entry_skip = reason


def note_entry_block(code: str, detail: str) -> None:
    """Record why a matched signal produced no order (ATR, sizing, base, maker price)."""
    observations = _SCOPE.get()
    if observations is None:
        return
    observations.entry_block_code = code
    observations.entry_block_detail = detail[:500]
