"""Descriptive registry of every implemented indicator kind.

The strategy schema (:mod:`thytrader.strategies.models`) is the authority on what a
document may declare; this registry describes those kinds for people and agents:
category, label, one-line help, input policy, parameter bounds with builder
defaults, output series, and the warmup formula. The operator ``indicators`` report
and the browser builder catalog (``web/src/lib/indicator-catalog.json``) are both
rendered from it, and ``tests/strategies/test_indicator_catalog.py`` proves every
bound, default, output, and warmup here agrees with the schema validators.

This module assembles the catalog in its fixed order from the per-category entry modules
(``indicator_specs_*``) and answers lookups and builder defaults. The spec types live in
``indicator_spec_model``; ``IndicatorKindSpec`` is re-exported here as the lookup's
return type.
"""

from __future__ import annotations

from thytrader.strategies.indicator_spec_model import IndicatorKindSpec
from thytrader.strategies.indicator_specs_momentum import _MOMENTUM_SPECS
from thytrader.strategies.indicator_specs_original import _EXISTING_SPECS
from thytrader.strategies.indicator_specs_trend import _TREND_SPECS
from thytrader.strategies.indicator_specs_volatility_volume import (
    _STATISTICAL_SPECS,
    _VOLATILITY_SPECS,
    _VOLUME_SPECS,
)
from thytrader.strategies.models import (
    IndicatorDefinition,
    IndicatorKind,
    indicator_min_warmup,
)

__all__ = [
    "INDICATOR_KIND_SPECS",
    "IndicatorKindSpec",
    "default_indicator_definition",
    "default_parameters",
    "default_warmup_bars",
    "indicator_kind_spec",
    "integer_parameter_bounds",
]

INDICATOR_KIND_SPECS: tuple[IndicatorKindSpec, ...] = (
    *_EXISTING_SPECS,
    *_TREND_SPECS,
    *_MOMENTUM_SPECS,
    *_VOLATILITY_SPECS,
    *_VOLUME_SPECS,
    *_STATISTICAL_SPECS,
)
"""Every implemented kind: the historical 21 first, then later slices by category."""

_SPECS_BY_KIND: dict[IndicatorKind, IndicatorKindSpec] = {
    spec.kind: spec for spec in INDICATOR_KIND_SPECS
}


def indicator_kind_spec(kind: IndicatorKind) -> IndicatorKindSpec:
    """Return the descriptive spec for one implemented kind."""
    return _SPECS_BY_KIND[kind]


def default_parameters(spec: IndicatorKindSpec) -> dict[str, int | str]:
    """Return the builder default parameter object (optional parameters omitted)."""
    return {
        parameter.name: parameter.default
        for parameter in spec.parameters
        if parameter.default is not None
    }


def default_indicator_definition(
    spec: IndicatorKindSpec,
    indicator_id: str = "indicator",
    *,
    offset: int | None = None,
) -> IndicatorDefinition:
    """Validate one declaration of ``spec`` with its default input and parameters."""
    payload: dict[str, object] = {
        "id": indicator_id,
        "kind": spec.kind.value,
        "parameters": default_parameters(spec),
    }
    if spec.default_input is not None:
        payload["input"] = spec.default_input
    if offset is not None:
        payload["offset"] = offset
    return IndicatorDefinition.model_validate(payload)


def default_warmup_bars(spec: IndicatorKindSpec) -> int:
    """Return the schema warmup of one default declaration (no offset)."""
    return indicator_min_warmup(default_indicator_definition(spec))


def integer_parameter_bounds(spec: IndicatorKindSpec) -> tuple[int | None, int | None]:
    """Return the smallest integer minimum and largest integer maximum, or ``None``s.

    This reproduces the historical ``period_min`` / ``period_max`` report fields: the
    bounds of the kind's required integer (period-like) parameters, ignoring decimal ones.
    """
    minimums = [
        parameter.minimum
        for parameter in spec.parameters
        if parameter.value_type == "integer"
        and not parameter.optional
        and isinstance(parameter.minimum, int)
    ]
    maximums = [
        parameter.maximum
        for parameter in spec.parameters
        if parameter.value_type == "integer"
        and not parameter.optional
        and isinstance(parameter.maximum, int)
    ]
    return (min(minimums, default=None), max(maximums, default=None))
