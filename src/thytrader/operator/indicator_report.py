"""Operator ``indicators`` report rows and the browser builder catalog documents.

Both render :data:`thytrader.strategies.indicator_catalog.INDICATOR_KIND_SPECS`, so an
agent reading ``thytrader-operator indicators`` and a person using the builder see the
same kinds, parameters, bounds, defaults, outputs, and warmup formulas.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from thytrader.operator.models import IndicatorCatalogEntry, IndicatorParameterEntry
from thytrader.strategies.indicator_catalog import (
    INDICATOR_KIND_SPECS,
    default_indicator_definition,
    default_parameters,
    default_warmup_bars,
    integer_parameter_bounds,
)
from thytrader.strategies.models import IndicatorDefinition, IndicatorKind, indicator_min_warmup

if TYPE_CHECKING:
    from thytrader.strategies.indicator_catalog import IndicatorKindSpec

WebCatalogSchema = Literal["thytrader-indicator-catalog-v1"]
WEB_CATALOG_SCHEMA: WebCatalogSchema = "thytrader-indicator-catalog-v1"
_EXAMPLE_OFFSET = 3


class WebCatalogDocument(BaseModel):
    """Generated browser document; ``schema`` names the catalog format version."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    catalog_schema: WebCatalogSchema = Field(default=WEB_CATALOG_SCHEMA, alias="schema")


class WebIndicatorCatalog(WebCatalogDocument):
    """Builder indicator catalog: exactly the operator report rows, in catalog order."""

    indicators: tuple[IndicatorCatalogEntry, ...]


class IndicatorWarmupExample(BaseModel):
    """One schema-computed warmup for a concrete parameter object and bar lag."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters: dict[str, int | str]
    offset: int = Field(ge=0)
    warmup_bars: int = Field(ge=1)


class WebIndicatorWarmupExamples(WebCatalogDocument):
    """Warmups the browser unit test checks its local formulas against."""

    warmup_examples: dict[str, tuple[IndicatorWarmupExample, ...]]


def indicator_catalog_entries() -> tuple[IndicatorCatalogEntry, ...]:
    """Describe every implemented indicator kind, in catalog order."""
    return tuple(_catalog_entry(spec) for spec in INDICATOR_KIND_SPECS)


def _catalog_entry(spec: IndicatorKindSpec) -> IndicatorCatalogEntry:
    """Render one registry spec as an operator report row."""
    period_min, period_max = integer_parameter_bounds(spec)
    accepts_clock = spec.kind is not IndicatorKind.CONSTANT
    return IndicatorCatalogEntry(
        kind=spec.kind.value,
        label=spec.label,
        category=spec.category.value,
        summary=spec.summary,
        inputs=spec.inputs,
        input_mode=spec.input_mode,
        default_input=spec.default_input,
        parameter_kind=spec.parameter_kind,
        period_min=period_min,
        period_max=period_max,
        parameters=tuple(
            IndicatorParameterEntry(
                name=parameter.name,
                label=parameter.label,
                value_type=parameter.value_type,
                minimum=parameter.minimum,
                maximum=parameter.maximum,
                exclusive_minimum=parameter.exclusive_minimum,
                default=parameter.default,
                optional=parameter.optional,
                help=parameter.help,
            )
            for parameter in spec.parameters
        ),
        constraints=spec.constraints,
        outputs=spec.outputs,
        warmup=spec.warmup,
        default_warmup_bars=default_warmup_bars(spec),
        supports_timeframe=accepts_clock,
        supports_offset=accepts_clock,
        supports_source=accepts_clock,
    )


def web_indicator_catalog_document() -> WebIndicatorCatalog:
    """Return the builder catalog document."""
    return WebIndicatorCatalog(indicators=indicator_catalog_entries())


def web_indicator_warmup_examples_document() -> WebIndicatorWarmupExamples:
    """Return schema-computed warmups the browser test checks its formulas against.

    The browser computes warmup locally for instant feedback; these examples (defaults,
    every integer bumped by one, and defaults with a 3-bar offset) come from
    :func:`thytrader.strategies.models.indicator_min_warmup`, so the unit test proves
    the TypeScript formulas agree with the schema without a server.
    """
    return WebIndicatorWarmupExamples(
        warmup_examples={spec.kind.value: _warmup_examples(spec) for spec in INDICATOR_KIND_SPECS}
    )


def render_json_document(document: BaseModel) -> str:
    """Serialize one generated web document with stable two-space JSON and a final newline."""
    payload = document.model_dump(mode="json", by_alias=True)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _warmup_examples(spec: IndicatorKindSpec) -> tuple[IndicatorWarmupExample, ...]:
    """Return defaults, defaults with a lag, and every integer bumped by one (when valid)."""
    defaults = default_parameters(spec)
    bumped = {
        name: value + 1 if isinstance(value, int) else value for name, value in defaults.items()
    }
    candidates: list[tuple[dict[str, int | str], int | None]] = [
        (defaults, None),
        (bumped, None),
    ]
    if spec.kind is not IndicatorKind.CONSTANT:
        candidates.append((defaults, _EXAMPLE_OFFSET))
    examples: list[IndicatorWarmupExample] = []
    for parameters, offset in candidates:
        definition = _example_definition(spec, parameters, offset)
        if definition is None:
            continue
        examples.append(
            IndicatorWarmupExample(
                parameters=parameters,
                offset=offset or 0,
                warmup_bars=indicator_min_warmup(definition),
            )
        )
    return tuple(examples)


def _example_definition(
    spec: IndicatorKindSpec,
    parameters: dict[str, int | str],
    offset: int | None,
) -> IndicatorDefinition | None:
    """Validate one example declaration, or skip it when the bumped values break a rule."""
    base = default_indicator_definition(spec, offset=offset)
    try:
        return IndicatorDefinition.model_validate(
            {**base.model_dump(mode="json"), "parameters": parameters}
        )
    except ValidationError:
        return None
