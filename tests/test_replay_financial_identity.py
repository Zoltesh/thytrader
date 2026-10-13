"""Currency units and mark identities remain unambiguous financial inputs."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from thytrader.replay.models import canonical_bytes
from thytrader.replay.packages import read_input
from thytrader.replay.runner import run


@pytest.mark.parametrize("field", ["base_currency", "quote_currency"])
def test_primary_product_currencies_match_strategy(field: str) -> None:
    """A correct product id cannot launder conflicting base/settlement units."""
    package = read_input(Path("tests/replay/fixtures/usdc-lifecycle.input.json"))
    step = package.steps[0]
    product = replace(step.product, **{field: "BTC" if field == "base_currency" else "USD"})
    forged = step.model_copy(update={"product": product})
    with pytest.raises(ValueError):
        canonical_bytes(package.model_copy(update={"steps": (forged,)}))


@pytest.mark.parametrize("fixture", ["no-signal", "usdc-lifecycle"])
def test_valid_usd_and_usdc_product_units(fixture: str) -> None:
    """Both independently valid quote currencies remain supported, without changing risk policy."""
    package = read_input(Path(f"tests/replay/fixtures/{fixture}.input.json"))
    assert package.strategy is not None
    assert package.steps[0].product.quote_currency == package.strategy.instrument.quote_currency
    assert canonical_bytes(package)


@pytest.mark.parametrize("other", ["999", "1"])
def test_all_duplicate_mark_keys_fail_closed(other: str) -> None:
    """Conflicting and equal duplicates cannot acquire canonical identity before dict conversion."""
    package = read_input(Path("tests/replay/fixtures/usdc-lifecycle.input.json"))
    step = package.steps[0].model_copy(
        update={"marks": (("ETH-USDC", Decimal("1")), ("ETH-USDC", Decimal(other)))}
    )
    with pytest.raises(ValueError):
        canonical_bytes(package.model_copy(update={"steps": (step,)}))


@pytest.mark.anyio
async def test_valid_marked_portfolio_preserves_trailing_outcome() -> None:
    """An occupied synthetic book with a unique mark still runs the actual engine unchanged."""
    package = read_input(Path("tests/replay/fixtures/usdc-lifecycle.input.json"))
    step = package.steps[2]
    assert step.before.position is not None
    step = step.model_copy(
        update={"portfolio": (step.before,), "marks": (("ETH-USDC", Decimal("10")),)}
    )
    package = package.model_copy(update={"steps": (step,), "ledger": None})
    assert canonical_bytes(package)
    report = await run(package)
    assert report.execution.status == "match"
    assert report.execution.steps[0].actual.positions[0].stop_price == Decimal("9.5")
