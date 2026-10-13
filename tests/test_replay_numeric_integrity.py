"""Financial evidence uses one bounded envelope before canonical publication."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, Overflow, getcontext, localcontext
import json
from pathlib import Path

import pytest

from thytrader.decimal_text import canonical_decimal
from thytrader.replay.codec import wire_value
from thytrader.replay.models import ReplayPackage, SourceIdentity, canonical_bytes
from thytrader.replay.numeric import financial_arithmetic, financial_text
from thytrader.replay.packages import read_input
from thytrader.replay.runner import run
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint


@pytest.mark.parametrize(
    "field", ["price", "quantity", "fee", "starting_cash", "observed_ending_cash"]
)
def test_ledger_strings_cannot_bypass_decimal64(field: str) -> None:
    """The independent over64 reproducer applies to every ledger financial string."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    assert package.ledger is not None
    ledger = package.ledger
    value = "1" + "0" * 63 + "1"
    if field in {"starting_cash", "observed_ending_cash"}:
        ledger = ledger.model_copy(update={field: value})
    else:
        event = ledger.fills[0].model_copy(update={field: value})
        ledger = ledger.model_copy(update={"fills": (event, *ledger.fills[1:])})
    with pytest.raises(ValueError):
        canonical_bytes(package.model_copy(update={"ledger": ledger}))


def test_nonfinancial_numeric_prefix_label_is_not_parsed_as_amount() -> None:
    """Bounding numeric parsing must not impose amount limits on unrelated build metadata."""
    package = read_input(Path("tests/replay/fixtures/no-signal.input.json"))
    label = "1-synthetic-label-" * 20
    package = package.model_copy(
        update={"source": SourceIdentity(execution_build=label, research_build=None)}
    )
    assert (
        ReplayPackage.model_validate_json(canonical_bytes(package)).source.execution_build == label
    )


def test_nonrepresentable_wire_exponent_is_controlled() -> None:
    """Decimal's own exponent conversion failure must be a domain refusal, not ArithmeticError."""
    package = read_input(Path("tests/replay/fixtures/no-signal.input.json"))
    payload = package.model_dump(mode="json")
    payload["steps"][0]["marks"] = [["BTC-USD", "1E" + "9" * 80]]
    with pytest.raises(ValueError):
        ReplayPackage.model_validate_json(json.dumps(payload))


def test_lexical_limit_and_smallest_64_digit_coefficient() -> None:
    """Lexical work and normalized magnitude have inclusive, independently asserted boundaries."""
    assert financial_text("0" * 195 + "1") == Decimal(1)
    with pytest.raises(ValueError):
        financial_text("0" * 196 + "1")
    assert wire_value(Decimal("1." + "1" * 63 + "E-128")) == "0." + "0" * 127 + "1" * 64


def test_disabled_traps_cannot_certify_finite_saturation() -> None:
    """The guard checks arithmetic flags even if a nested caller disables an unsafe trap."""
    with pytest.raises(ValueError, match="arithmetic"), financial_arithmetic(precision=64):
        context = getcontext()
        context.traps[Overflow] = False
        context.rounding = ROUND_DOWN
        saturated = Decimal("9E128") * Decimal(10)
        assert saturated.is_finite()


def test_ordinary_64_digit_quantity_retains_last_unit() -> None:
    """Precision guard admits the existing usable 64-digit boundary without rounding identity."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    assert package.ledger is not None
    value = "1" + "0" * 62 + "1"
    event = package.ledger.fills[0].model_copy(update={"quantity": value})
    ledger = package.ledger.model_copy(update={"fills": (event,)})
    assert value.encode() in canonical_bytes(package.model_copy(update={"ledger": ledger}))
    assert Decimal(value).as_tuple().digits[-1] == 1


@pytest.mark.parametrize(
    "text", ["1E129", "1E-129", "0E1000100", "0E-1000100", "1E500000", "1E-1000101"]
)
def test_typed_decimals_require_usable_magnitude(text: str) -> None:
    """Finite small-coefficient Decimals cannot allocate huge canonical output or vanish."""
    with pytest.raises(ValueError):
        wire_value(Decimal(text))


@pytest.mark.parametrize(
    "text", ["1" + "0" * 129, "0." + "0" * 128 + "1", "0." + "0" * 1000100 + "1"]
)
@pytest.mark.parametrize("path", ["ledger", "domain"])
def test_raw_financial_strings_are_bounded_before_model_parsing(text: str, path: str) -> None:
    """Both ledger and nested domain Decimal inputs fail at the model's raw boundary."""
    package = read_input(Path("tests/replay/fixtures/no-signal.input.json"))
    if path == "ledger":
        ledger = read_input(Path("tests/replay/fixtures/partial-loss.input.json")).ledger
        assert ledger is not None
        package = package.model_copy(
            update={"ledger": ledger.model_copy(update={"starting_cash": text})}
        )
    else:
        step = package.steps[0]
        package = package.model_copy(
            update={"steps": (step.model_copy(update={"marks": (("BTC-USD", Decimal(text)),)}),)}
        )
    # JSON mode intentionally supplies text to the raw model boundary, not the codec guard.
    with pytest.raises(ValueError):
        ReplayPackage.model_validate_json(package.model_dump_json())


@pytest.mark.parametrize("text", ["1E128", "1E-128", "-1E128", "-1E-128", "1" + "0" * 62 + "1"])
def test_financial_magnitude_boundaries_round_trip(text: str) -> None:
    """The closed boundaries preserve signed cash and context-independent numeric identity."""
    amount = Decimal(text)
    assert wire_value(amount) == canonical_decimal(amount)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("price", "quantity", "starting", "fee"),
    [
        ("1" + "0" * 128, "10", "0", "0"),
        ("0." + "0" * 127 + "1", "0.1", "0", "0"),
        ("9" + "0" * 128, "1", "-9" + "0" * 128, "0"),
        ("1", "1" + "0" * 128, "0", "0." + "0" * 127 + "1"),
    ],
    ids=[
        "notional-overflow",
        "positive-notional-underflow",
        "cash-addition-overflow",
        "fee-allocation-underflow",
    ],
)
async def test_derived_financial_arithmetic_refuses_outside_envelope(
    price: str, quantity: str, starting: str, fee: str
) -> None:
    """Valid operands do not imply a usable derived product, cash or fee allocation."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    assert package.ledger is not None
    event = package.ledger.fills[0].model_copy(
        update={"price": price, "quantity": quantity, "fee": fee}
    )
    # Two buys exercise the kernel's average-cost/fee arithmetic as well as direct cash folding.
    other = event.model_copy(update={"fill_id": "synthetic-second"})
    if fee != "0":
        other = other.model_copy(update={"side": "sell", "quantity": "1", "fee": "0"})
    ledger = package.ledger.model_copy(
        update={"starting_cash": starting, "observed_ending_cash": "0", "fills": (event, other)}
    )
    checked = ReplayPackage.model_validate_json(
        canonical_bytes(package.model_copy(update={"ledger": ledger}))
    )
    with localcontext() as context:
        for signal in context.traps:
            context.traps[signal] = False
        with pytest.raises(ValueError, match=r"arithmetic|envelope"):
            await run(checked)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("price", "quantity", "cash"),
    [
        ("1" + "0" * 127, "10", "-1" + "0" * 128),
        ("0." + "0" * 126 + "1", "0.1", "-0." + "0" * 127 + "1"),
    ],
)
async def test_derived_product_inclusive_boundaries(price: str, quantity: str, cash: str) -> None:
    """Notional products exactly on each usable magnitude boundary remain computable."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    assert package.ledger is not None
    event = package.ledger.fills[0].model_copy(
        update={"price": price, "quantity": quantity, "fee": "0"}
    )
    ledger = package.ledger.model_copy(
        update={"starting_cash": "0", "observed_ending_cash": cash, "fills": (event,)}
    )
    report = await run(package.model_copy(update={"ledger": ledger}))
    assert report.ledger is not None
    assert report.ledger.status == "match"
    assert report.ledger.ending_cash == cash
    assert report.ledger.ending_quantity == quantity


@pytest.mark.anyio
async def test_valid_decimal64_arithmetic_still_rounds_normally() -> None:
    """Ordinary finite precision rounding is not an integrity failure or an Inexact trap."""
    package = read_input(Path("tests/replay/fixtures/partial-loss.input.json"))
    assert package.ledger is not None
    a = package.ledger.fills[0].model_copy(update={"price": "1", "quantity": "1", "fee": "0"})
    b = a.model_copy(update={"fill_id": "synthetic-second", "price": "2", "quantity": "2"})
    ledger = package.ledger.model_copy(
        update={"fills": (a, b), "starting_cash": "-1", "observed_ending_cash": "-6"}
    )
    report = await run(package.model_copy(update={"ledger": ledger}))
    assert report.ledger is not None
    assert report.ledger.ending_cash == "-6"
    assert report.ledger.ending_quantity == "3"
    assert report.ledger.status == "match"


@pytest.mark.anyio
async def test_derived_indicator_values_share_output_envelope() -> None:
    """The real force-index evaluator can multiply valid inputs beyond the usable envelope."""
    package = read_input(Path("tests/replay/fixtures/no-signal.input.json"))
    assert package.strategy is not None
    definition = package.strategy.model_dump(mode="json", by_alias=True)
    definition["indicators"].append(
        {
            "id": "force",
            "kind": "force_index",
            "input": ["close", "volume"],
            "parameters": {"period": 2},
        }
    )
    strategy = StrategyDefinition.model_validate_json(json.dumps(definition))
    step = package.steps[0]
    large = Decimal("9E128")
    candles = tuple(
        replace(
            candle,
            open=Decimal(10) if index % 2 else Decimal(1),
            close=Decimal(10) if index % 2 else Decimal(1),
            high=Decimal(10),
            low=Decimal(1),
            volume=large,
        )
        for index, candle in enumerate(step.candles)
    )
    before = replace(
        step.before,
        deployment=replace(
            step.before.deployment, strategy_fingerprint=strategy_fingerprint(strategy)
        ),
    )
    step = step.model_copy(
        update={"before": before, "candles": candles, "allow_new_entries": False}
    )
    package = package.model_copy(update={"strategy": strategy, "steps": (step,)})
    assert canonical_bytes(package)
    with pytest.raises(ValueError, match="envelope"):
        await run(package)
