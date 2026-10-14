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

from pydantic import ValidationError

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

    from thytrader.strategies.schema.market import WarmupPolicy

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

    Declared warmups are only ever raised, never lowered, and the policy is set on the decision
    clock and the HTF filter together, so the result is idempotent. A definition whose convergence
    warmup cannot be declared (above ``MAX_DECLARED_WARMUP_BARS``, e.g. KAMA with a very slow
    constant) is returned unchanged under the historical policy; starts refuse it later.
    """
    decision, htf = convergence_warmups(definition)
    if decision > MAX_DECLARED_WARMUP_BARS or (htf or 0) > MAX_DECLARED_WARMUP_BARS:
        return definition
    document: dict[str, JsonValue] = json.loads(canonical_strategy_bytes(definition))
    _raise(document, decision)
    htf_document = document.get("htf_filter")
    if htf is not None and isinstance(htf_document, dict):
        _raise(htf_document, htf)
    return StrategyDefinition.model_validate(document)


def converge_draft(document: dict[str, JsonValue]) -> StrategyDefinition:
    """Validate a saved draft and adopt the convergence policy (ADR 0140).

    The draft is validated with any policy stripped, under the historical minimum rules. If only
    its declared warmups are too short (an edit lengthened an indicator), they are recomputed
    rather than rejected: the larger of what was declared and what convergence requires, or the
    historical minimum without a policy when convergence cannot be declared. Any other problem
    raises the original ``ValidationError``.
    """
    stripped = without_warmup_policy(document)
    try:
        return with_convergence_warmup(StrategyDefinition.model_validate(stripped))
    except ValidationError as error:
        if not _declared_warmups_well_formed(stripped):
            raise
        probe_document = _with_declared_warmups(stripped, MAX_DECLARED_WARMUP_BARS)
        try:
            probe = StrategyDefinition.model_validate(probe_document)
        except ValidationError:
            raise error from None
    decision, htf = convergence_warmups(probe)
    policy: WarmupPolicy | None = "convergence"
    if decision > MAX_DECLARED_WARMUP_BARS or (htf or 0) > MAX_DECLARED_WARMUP_BARS:
        decision, htf = _minimum_warmups(probe)
        policy = None
    final = _with_declared_warmups(stripped, None, decision=decision, htf=htf, policy=policy)
    return StrategyDefinition.model_validate(final)


def _declared_warmup(value: JsonValue) -> int | None:
    """A declared warmup as pydantic reads it (integers, integral floats), else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _declared_warmups_well_formed(document: dict[str, JsonValue]) -> bool:
    """Whether every declared warmup is an integer within the schema bounds.

    Recovery only repairs warmups that are too short; a malformed or out-of-range value keeps
    its own validation error.
    """
    blocks = [document.get("data_requirements")]
    htf = document.get("htf_filter")
    if isinstance(htf, dict):
        blocks.append(htf.get("data_requirements"))
    for block in blocks:
        if not isinstance(block, dict):
            return False
        declared = _declared_warmup(block.get("warmup_bars"))
        if declared is None or not 1 <= declared <= MAX_DECLARED_WARMUP_BARS:
            return False
    return True


def _minimum_warmups(definition: StrategyDefinition) -> tuple[int, int | None]:
    """Decision-clock and HTF warmups under the historical minimum rules."""
    operands = strategy_indicator_operands(definition)
    decision = extra_indicator_timeframe_warmup(
        decision_clock_indicators(definition), operands=operands
    )
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return decision, None
    htf = extra_indicator_timeframe_warmup(
        htf_filter.indicators, operands=condition_indicator_operands(htf_filter.when)
    )
    shared = dict(extra_indicator_timeframe_groups(definition)).get(htf_filter.timeframe)
    if shared:
        htf = max(htf, extra_indicator_timeframe_warmup(shared, operands=operands))
    return decision, htf


def _with_declared_warmups(
    document: dict[str, JsonValue],
    fixed: int | None,
    *,
    decision: int | None = None,
    htf: int | None = None,
    policy: WarmupPolicy | None = None,
) -> dict[str, JsonValue]:
    """Copy a document with declared warmups set to ``fixed`` or raised to the given values."""
    copied: dict[str, JsonValue] = json.loads(json.dumps(document))
    _set_warmup(copied.get("data_requirements"), fixed, decision, policy)
    htf_document = copied.get("htf_filter")
    if isinstance(htf_document, dict):
        _set_warmup(htf_document.get("data_requirements"), fixed, htf, policy)
    return copied


def _set_warmup(
    block: JsonValue, fixed: int | None, required: int | None, policy: WarmupPolicy | None
) -> None:
    """Set one block's warmup to ``fixed``, or to max(declared, required) with the policy."""
    if not isinstance(block, dict):
        return
    if fixed is not None:
        block["warmup_bars"] = fixed
        return
    current = _declared_warmup(block.get("warmup_bars")) or 0
    block["warmup_bars"] = max(current, required or 0)
    if policy is not None:
        block["warmup_policy"] = policy


def without_warmup_policy(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return a document with any ``warmup_policy`` removed (decision clock and HTF filter).

    Saving validates a draft under the historical minimum rules first and then materializes the
    policy, so an edit that lengthens an indicator raises warmup instead of failing validation.
    """
    stripped = dict(document)
    if "data_requirements" in stripped:
        stripped["data_requirements"] = _drop_policy(stripped["data_requirements"])
    htf = stripped.get("htf_filter")
    if isinstance(htf, dict):
        stripped["htf_filter"] = {
            **htf,
            "data_requirements": _drop_policy(htf.get("data_requirements")),
        }
    return stripped


def _drop_policy(block: JsonValue) -> JsonValue:
    """Copy one ``data_requirements`` mapping without its policy; leave other values alone."""
    if not isinstance(block, dict):
        return block
    return {name: value for name, value in block.items() if name != "warmup_policy"}


def _raise(container: dict[str, JsonValue], required: int) -> None:
    """Set the policy on one ``data_requirements`` block and lift its warmup to ``required``."""
    requirements = container["data_requirements"]
    if not isinstance(requirements, dict):  # pragma: no cover - schema guarantees a mapping.
        raise TypeError("data_requirements must be a mapping")
    current = requirements["warmup_bars"]
    declared = current if isinstance(current, int) else 0
    requirements["warmup_bars"] = max(declared, required)
    requirements["warmup_policy"] = "convergence"
