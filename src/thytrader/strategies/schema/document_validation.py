"""Cross-section validation of a whole strategy document.

``StrategyDefinition.validate_semantics`` runs these checks: covered instruments,
decision indicators and ATR stop references, reference instruments, the signal exit,
the HTF filter, and extra indicator timeframes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.schema.conditions import (
    _referenced_indicator_ids,
    _require_condition_series,
)
from thytrader.strategies.schema.document_queries import (
    MAX_STRATEGY_INSTRUMENTS,
    covered_product_ids,
    decision_clock_indicators,
    extra_indicator_timeframe_groups,
    strategy_indicator_operands,
)
from thytrader.strategies.schema.exits import (
    AtrTrailingStop,
    signal_exit_condition,
)
from thytrader.strategies.schema.indicator_definition import (
    IndicatorDefinition,
    _indicator_input_fields,
)
from thytrader.strategies.schema.indicator_parameters import (
    IndicatorKind,
)
from thytrader.strategies.schema.timeframes import (
    extra_indicator_required_fields,
    extra_indicator_timeframe_warmup,
    is_valid_htf_pair,
    is_valid_reference_pair,
    resolved_indicator_timeframe,
)

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition


def _validate_covered_instruments(definition: StrategyDefinition) -> None:
    """Reject duplicate products and concurrent-position caps that exceed coverage."""
    products = covered_product_ids(definition)
    if len(products) != len(set(products)):
        raise ValueError("additional_instruments must be unique and exclude instrument.product_id")
    if len(products) > MAX_STRATEGY_INSTRUMENTS:
        raise ValueError("a strategy document may cover at most 8 spot products")
    quotes = {
        instrument.quote_currency
        for instrument in (definition.instrument, *definition.additional_instruments)
    }
    if len(quotes) != 1:
        raise ValueError("all covered instruments must share one quote currency")
    if definition.portfolio_limits.max_concurrent_positions > len(products):
        raise ValueError("max_concurrent_positions cannot exceed the number of covered products")


def _validate_derivatives(definition: StrategyDefinition) -> None:
    """Futures documents are single-instrument, carry ``derivatives``, and never pyramid."""
    future = definition.instrument.is_future
    if any(instrument.is_future for instrument in definition.additional_instruments):
        raise ValueError("additional_instruments must be spot; futures are single-instrument")
    if not future:
        if definition.derivatives is not None:
            raise ValueError("derivatives is only allowed with instrument.kind future")
        return
    if definition.derivatives is None:
        raise ValueError("a futures strategy needs a derivatives block (max_leverage)")
    if definition.additional_instruments:
        raise ValueError("a futures strategy cannot cover additional instruments (P3)")
    if definition.data_requirements.reference_instruments:
        raise ValueError("a futures strategy cannot read reference instruments in P1")
    if definition.entry.pyramiding is not None:
        raise ValueError("a futures strategy cannot pyramid in P1 (one position per book)")


def _validate_decision_indicators(definition: StrategyDefinition) -> None:
    """Resolve LTF indicator identity, warmup, and ATR-stop references."""
    identifiers = [indicator.id for indicator in definition.indicators]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("indicator ids must be unique")
    known = set(identifiers)
    references = _referenced_indicator_ids(definition.entry.when)
    references.add(definition.exits.initial_stop.atr_indicator)
    trailing = definition.exits.trailing_stop
    if isinstance(trailing, AtrTrailingStop):
        references.add(trailing.atr_indicator)
    unknown = references - known
    if unknown:
        raise ValueError(f"unknown indicator references: {sorted(unknown)}")
    _require_condition_series(definition.entry.when, definition.indicators)
    _require_atr_indicator(
        definition.indicators,
        definition.exits.initial_stop.atr_indicator,
        role="initial stop",
        decision_timeframe=definition.timeframe,
    )
    if isinstance(trailing, AtrTrailingStop):
        _require_atr_indicator(
            definition.indicators,
            trailing.atr_indicator,
            role="trailing stop",
            decision_timeframe=definition.timeframe,
        )
    _validate_indicator_timeframes(definition)
    decision_indicators = decision_clock_indicators(definition)
    required_fields = {
        field for indicator in decision_indicators for field in _indicator_input_fields(indicator)
    }
    if not required_fields.issubset(definition.data_requirements.required_fields):
        raise ValueError("required_fields must include every indicator input")
    required_warmup = extra_indicator_timeframe_warmup(
        decision_indicators, operands=strategy_indicator_operands(definition)
    )
    if definition.data_requirements.warmup_bars < required_warmup:
        raise ValueError("warmup_bars must cover the longest indicator period")


def _validate_reference_instruments(definition: StrategyDefinition) -> None:
    """Resolve indicator sources against declared references (ADR 0096).

    Every ``source`` must name a declared reference, every reference must be read by at
    least one indicator, share the traded instrument's quote currency, and use the
    decision timeframe or a coarser integer multiple of it (the HTF alignment rule, with
    the decision clock itself allowed). Warmup per reference is derived from its
    indicators, so it can never be under-declared.
    """
    references = definition.data_requirements.reference_instruments
    declared = {reference.id for reference in references}
    sources = {
        indicator.source for indicator in definition.indicators if indicator.source is not None
    }
    unknown = sorted(sources - declared)
    if unknown:
        raise ValueError(
            "indicator source must name a declared data_requirements.reference_instruments "
            f"id: {unknown}"
        )
    quote = definition.instrument.quote_currency
    for reference in references:
        if reference.quote_currency != quote:
            raise ValueError(
                f"reference instrument {reference.id} ({reference.product_id}) must use the "
                f"strategy quote currency {quote}"
            )
        if not is_valid_reference_pair(definition.timeframe, reference.timeframe):
            raise ValueError(
                f"reference instrument {reference.id} timeframe {reference.timeframe} must "
                f"equal the strategy timeframe {definition.timeframe} or be a coarser integer "
                "multiple of it"
            )
    unused = sorted(declared - sources)
    if unused:
        raise ValueError(
            f"every reference instrument must be read by at least one indicator source: {unused}"
        )


def _validate_signal_exit(definition: StrategyDefinition) -> None:
    """Resolve the optional exit-rule tree with the entry operand rules (ADR 0093).

    The tree may reference any decision-list indicator (including per-indicator extra
    timeframes, like ``entry.when``) but never an HTF-filter indicator: the HTF filter
    only gates entries. Multi-series operands must name a declared series.
    """
    condition = signal_exit_condition(definition.exits)
    if condition is None:
        return
    references = _referenced_indicator_ids(condition)
    known = {indicator.id for indicator in definition.indicators}
    htf_filter = definition.htf_filter
    htf_ids = set() if htf_filter is None else {indicator.id for indicator in htf_filter.indicators}
    filter_only = sorted((references - known) & htf_ids)
    if filter_only:
        raise ValueError(f"exits.signal_exit cannot reference HTF filter indicators: {filter_only}")
    unknown = sorted(references - known)
    if unknown:
        raise ValueError(f"unknown exits.signal_exit indicator references: {unknown}")
    _require_condition_series(condition, definition.indicators)


def _require_atr_indicator(
    indicators: tuple[IndicatorDefinition, ...],
    indicator_id: str,
    *,
    role: str,
    decision_timeframe: str,
) -> None:
    """Reject a stop reference that is missing, not an ATR, or not on the decision clock."""
    atr = next((item for item in indicators if item.id == indicator_id), None)
    if atr is None or atr.kind is not IndicatorKind.ATR:
        raise ValueError(f"{role} indicator must reference an ATR")
    if atr.source is not None:
        raise ValueError(f"{role} ATR must read the traded instrument, not a reference instrument")
    if resolved_indicator_timeframe(atr, decision_timeframe) != decision_timeframe:
        raise ValueError(f"{role} ATR must use the strategy decision timeframe")


def _validate_htf_filter(definition: StrategyDefinition) -> None:
    """Reject HTF clocks that are not strictly coarser, or that reuse LTF indicator ids."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return
    if not is_valid_htf_pair(definition.timeframe, htf_filter.timeframe):
        raise ValueError(
            "htf_filter.timeframe must be strictly coarser than the strategy decision "
            "timeframe and an integer multiple of it"
        )
    overlap = {indicator.id for indicator in definition.indicators}.intersection(
        {indicator.id for indicator in htf_filter.indicators}
    )
    if overlap:
        raise ValueError(f"HTF indicator ids must not reuse decision indicators: {sorted(overlap)}")


def _validate_indicator_timeframes(definition: StrategyDefinition) -> None:
    """Reject extra indicator clocks that are not coarser integer multiples of LTF."""
    for indicator in definition.indicators:
        clock = resolved_indicator_timeframe(indicator, definition.timeframe)
        if clock == definition.timeframe:
            continue
        if not is_valid_htf_pair(definition.timeframe, clock):
            raise ValueError(
                "indicator timeframe must be strictly coarser than the strategy decision "
                "timeframe and an integer multiple of it"
            )
    _require_htf_coverage_for_shared_indicator_clock(definition)


def _require_htf_coverage_for_shared_indicator_clock(definition: StrategyDefinition) -> None:
    """When extra indicators share the HTF clock, the HTF dataset must cover them."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return
    groups = dict(extra_indicator_timeframe_groups(definition))
    shared = groups.get(htf_filter.timeframe)
    if shared is None:
        return
    needed_warmup = extra_indicator_timeframe_warmup(
        shared, operands=strategy_indicator_operands(definition)
    )
    if htf_filter.data_requirements.warmup_bars < needed_warmup:
        raise ValueError("HTF warmup_bars must cover extra indicators on the HTF timeframe")
    needed_fields = extra_indicator_required_fields(shared)
    if not set(needed_fields).issubset(htf_filter.data_requirements.required_fields):
        raise ValueError("HTF required_fields must include extra indicators on the HTF timeframe")
