"""Per-market variants of one strategy for cross-market studies (ADR 0089).

A cross-market study may name one base ``strategy_id`` plus ``markets[].product_id``
instead of one cloned strategy per market. The server re-targets the base
definition at each product. Like sweep candidates, variants keep the base
``strategy_id`` (so their snapshots belong to, and are deleted with, the strategy
they came from), carry a derived name and tag, and are recorded as ordinary
content-addressed snapshots, so every child backtest names an exact definition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.market_data.products import parse_spot_product_id
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyMetadata
    from thytrader.strategies.snapshots import StrategySnapshot

MARKET_VARIANT_TAG = "research-market-variant"
_MAX_NAME_CHARS = 120
_MAX_DESCRIPTION_CHARS = 500
_MAX_TAGS = 20


class MarketVariantError(ValueError):
    """The base strategy cannot be re-targeted at the requested product."""


def derive_market_variant(base: StrategySnapshot, product_id: str) -> StrategyDefinition:
    """Return ``base`` re-targeted at ``product_id``, or ``base`` itself for its own product.

    Only the instrument, name, description, and tags change; rules, sizing, exits,
    and execution preferences are copied exactly.

    Raises:
        MarketVariantError: When the base covers several products or the product id
            is not a supported spot market, or the re-targeted document is invalid.
    """
    definition = base.definition
    if definition.additional_instruments:
        message = (
            "markets[].product_id derives single-instrument variants, but this strategy covers "
            f"{1 + len(definition.additional_instruments)} products; clone it per market and "
            "name each clone with markets[].strategy_id instead."
        )
        raise MarketVariantError(message)
    try:
        base_currency, quote_currency = parse_spot_product_id(product_id)
    except ValueError as error:
        raise MarketVariantError(str(error)) from error
    target = f"{base_currency}-{quote_currency}"
    if target == definition.instrument.product_id:
        return definition
    payload = definition.model_dump(mode="python")
    payload["instrument"] = {
        "product_id": target,
        "base_currency": base_currency,
        "quote_currency": quote_currency,
    }
    payload["name"] = _variant_name(definition.name, target)
    payload["description"] = (
        f"Derived cross-market variant of {base.strategy_fingerprint} for {target}. "
        "Not a human-authored edit."
    )[:_MAX_DESCRIPTION_CHARS]
    payload["metadata"] = _variant_metadata(definition.metadata)
    try:
        return StrategyDefinition.model_validate(payload)
    except ValidationError as error:
        message = f"The strategy cannot be re-targeted at {target}: {error.errors()[0]['msg']}"
        raise MarketVariantError(message) from error


def _variant_name(base_name: str, product_id: str) -> str:
    """Name the variant after its market while staying inside the schema limit."""
    suffix = f" [{product_id}]"
    return f"{base_name[: _MAX_NAME_CHARS - len(suffix)]}{suffix}"


def _variant_metadata(metadata: StrategyMetadata) -> dict[str, object]:
    """Tag the variant without dropping the base document's annotations."""
    tags = list(metadata.tags)
    if MARKET_VARIANT_TAG not in tags and len(tags) < _MAX_TAGS:
        tags.append(MARKET_VARIANT_TAG)
    return {"tags": tuple(tags), "notes": metadata.notes}
