"""Unit tests for per-market strategy variants used by cross-market studies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from thytrader.research.market_variants import (
    MARKET_VARIANT_TAG,
    MarketVariantError,
    derive_market_variant,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot

_REFERENCE = Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"


def _snapshot(payload: dict[str, object] | None = None) -> StrategySnapshot:
    """Snapshot the golden BTC-USD reference (optionally edited)."""
    document = payload or cast("dict[str, object]", json.loads(_REFERENCE.read_text("utf-8")))
    definition = StrategyDefinition.model_validate(document)
    return StrategySnapshot(
        strategy_fingerprint=strategy_fingerprint(definition), definition=definition
    )


def test_variant_retargets_only_the_instrument_and_keeps_the_base_identity() -> None:
    """Rules are copied exactly; the variant keeps strategy_id and is tagged as derived."""
    base = _snapshot()
    variant = derive_market_variant(base, "ETH-USDC")
    assert variant.instrument.product_id == "ETH-USDC"
    assert variant.instrument.base_currency == "ETH"
    assert variant.instrument.quote_currency == "USDC"
    assert variant.strategy_id == base.definition.strategy_id
    assert variant.indicators == base.definition.indicators
    assert variant.entry == base.definition.entry
    assert variant.exits == base.definition.exits
    assert variant.name.endswith(" [ETH-USDC]")
    assert MARKET_VARIANT_TAG in variant.metadata.tags
    assert base.strategy_fingerprint in (variant.description or "")
    assert derive_market_variant(base, "ETH-USDC") == variant


def test_the_base_product_returns_the_base_definition() -> None:
    """Naming the base strategy's own product reuses its exact snapshot."""
    base = _snapshot()
    assert derive_market_variant(base, "BTC-USD") is base.definition


def test_multi_instrument_strategies_are_refused() -> None:
    """Variants are single-instrument; lockstep documents must be cloned per market."""
    payload = cast("dict[str, object]", json.loads(_REFERENCE.read_text("utf-8")))
    payload["additional_instruments"] = [
        {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
    ]
    with pytest.raises(MarketVariantError, match="clone it per market"):
        derive_market_variant(_snapshot(payload), "SOL-USD")


def test_unsupported_product_ids_are_refused() -> None:
    """Only BASE-USD, BASE-USDC, and BASE-USDT spot products can be targeted."""
    with pytest.raises(MarketVariantError, match="spot identifier"):
        derive_market_variant(_snapshot(), "BTC-EUR")
