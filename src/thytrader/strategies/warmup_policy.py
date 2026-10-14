"""Materialize the convergence warmup policy into a strategy on save (ADR 0140).

A saved strategy gets ``data_requirements.warmup_policy = "convergence"`` and its declared
decision-clock and HTF warmups raised to what convergence requires, so backtests and live
books seed every recursive indicator long enough before their first decision. Derived clocks
(extra indicator timeframes, reference instruments) follow the policy automatically. Loading
never calls this: existing snapshots and stored rows keep their exact meaning.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from thytrader.strategies.models import StrategyDefinition, canonical_strategy_bytes
from thytrader.strategies.schema.conditions import condition_indicator_operands
from thytrader.strategies.schema.document_queries import (
    decision_clock_indicators,
    extra_indicator_timeframe_groups,
    strategy_indicator_operands,
)
from thytrader.strategies.schema.timeframes import extra_indicator_timeframe_warmup

if TYPE_CHECKING:
    from pydantic import JsonValue

MAX_DECLARED_WARMUP_BARS = 10_000
"""Upper bound of ``DataRequirements.warmup_bars``."""


def convergence_warmups(definition: StrategyDefinition) -> tuple[int, int | None]:
    """Return the decision-clock and HTF warmups convergence requires (HTF ``None`` if absent)."""
    operands = strategy_indicator_operands(definition)
    decision = extra_indicator_timeframe_warmup(
        decision_clock_indicators(definition), operands=operands, policy="convergence"
    )
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return decision, None
    htf = extra_indicator_timeframe_warmup(
        htf_filter.indicators,
        operands=condition_indicator_operands(htf_filter.when),
        policy="convergence",
    )
    shared = dict(extra_indicator_timeframe_groups(definition)).get(htf_filter.timeframe)
    if shared:
        htf = max(
            htf,
            extra_indicator_timeframe_warmup(shared, operands=operands, policy="convergence"),
        )
    return decision, htf


def with_convergence_warmup(definition: StrategyDefinition) -> StrategyDefinition:
    """Return the definition under the convergence policy, raising declared warmups as needed.

    Declared warmups are only ever raised, never lowered. A definition whose convergence
    warmup cannot be declared (above ``MAX_DECLARED_WARMUP_BARS``, e.g. KAMA with a very slow
    constant) is returned unchanged under the historical policy; starts refuse it later.
    """
    if definition.data_requirements.warmup_policy == "convergence":
        return definition
    decision, htf = convergence_warmups(definition)
    if decision > MAX_DECLARED_WARMUP_BARS or (htf or 0) > MAX_DECLARED_WARMUP_BARS:
        return definition
    document: dict[str, JsonValue] = json.loads(canonical_strategy_bytes(definition))
    _raise(document, decision)
    htf_document = document.get("htf_filter")
    if htf is not None and isinstance(htf_document, dict):
        _raise(htf_document, htf)
    return StrategyDefinition.model_validate(document)


def _raise(container: dict[str, JsonValue], required: int) -> None:
    """Set the policy on one ``data_requirements`` block and lift its warmup to ``required``."""
    requirements = container["data_requirements"]
    if not isinstance(requirements, dict):  # pragma: no cover - schema guarantees a mapping.
        raise TypeError("data_requirements must be a mapping")
    current = requirements["warmup_bars"]
    declared = current if isinstance(current, int) else 0
    requirements["warmup_bars"] = max(declared, required)
    requirements["warmup_policy"] = "convergence"
