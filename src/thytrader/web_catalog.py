"""Generated catalogs the browser builder imports instead of re-declaring them in TypeScript.

``scripts/export_indicator_catalog.py`` writes these files under
``web/src/lib/generated/``; ``tests/strategies/test_indicator_catalog.py`` fails when a
checked-in file differs from :func:`render_web_catalog_files`. Python stays the single
source for indicator kinds, parameter bounds, defaults, help text, output series, and
the research template list.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from thytrader.operator.indicator_report import (
    WebCatalogDocument,
    render_json_document,
    web_indicator_catalog_document,
    web_indicator_warmup_examples_document,
)
from thytrader.strategies.template_ids import template_catalog

WEB_GENERATED_DIRECTORY = ("web", "src", "lib", "generated")
"""Repository-relative directory (as path parts) holding the generated JSON files."""


class WebStrategyTemplate(BaseModel):
    """One New-strategy template option."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    description: str


class WebStrategyTemplates(WebCatalogDocument):
    """The New-strategy template picker list."""

    templates: tuple[WebStrategyTemplate, ...]


def web_strategy_templates_document() -> WebStrategyTemplates:
    """Return the template picker list (ids, names, one-line descriptions)."""
    return WebStrategyTemplates(
        templates=tuple(WebStrategyTemplate.model_validate(item) for item in template_catalog())
    )


def render_web_catalog_files() -> dict[str, str]:
    """Return generated file name -> exact file text for every builder catalog."""
    return {
        "indicator-catalog.json": render_json_document(web_indicator_catalog_document()),
        "indicator-warmup-examples.json": render_json_document(
            web_indicator_warmup_examples_document()
        ),
        "strategy-templates.json": render_json_document(web_strategy_templates_document()),
    }
