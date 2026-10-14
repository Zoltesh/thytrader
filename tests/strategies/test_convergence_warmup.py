"""Convergence warmup (ADR 0140): formulas, materialization, validation, anchor independence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
import math
import random

from pydantic import ValidationError
import pytest

from tests.strategies.reference_support import reference_strategy
from thytrader.evaluation.indicator_math.moving_averages import _exponential_moving_average
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import evaluate_document, parse_document_text
from thytrader.strategies.models import (
    IndicatorDefinition,
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)
from thytrader.strategies.schema.document_queries import reference_data_requirements
from thytrader.strategies.schema.indicator_convergence import (
    RESIDUAL_SEED_WEIGHT,
    indicator_anchor_sensitive,
    indicator_convergence_warmup,
)
from thytrader.strategies.schema.indicator_warmup import indicator_min_warmup
from thytrader.strategies.warmup_policy import convergence_warmups, with_convergence_warmup


def _indicator(kind: str, **parameters: int | str) -> IndicatorDefinition:
    return IndicatorDefinition.model_validate(
        {"id": "x", "kind": kind, "input": "close", "parameters": parameters}
    )


def _updates(alpha: float) -> int:
    return math.ceil(math.log(RESIDUAL_SEED_WEIGHT) / math.log(1 - alpha))


@pytest.mark.parametrize(
    ("kind", "parameters", "expected"),
    [
        ("ema", {"period": 200}, 200 + _updates(2 / 201)),
        ("ema", {"period": 50}, 50 + _updates(2 / 51)),
        ("rsi", {"period": 14}, 15 + _updates(1 / 14)),
        ("sma", {"period": 200}, 200),
        ("cmo", {"period": 14}, 15),
    ],
)
def test_convergence_warmup_matches_the_exact_decay(
    kind: str, parameters: dict[str, int], expected: int
) -> None:
    """EMA uses alpha 2/(n+1), Wilder 1/n; exact windows converge at their first value."""
    indicator = _indicator(kind, **parameters)
    assert indicator_convergence_warmup(indicator) == expected
    assert indicator_convergence_warmup(indicator) >= indicator_min_warmup(indicator)


def test_ema200_needs_691_updates_after_its_seed() -> None:
    """Independent check of the headline number reviewed for ADR 0140."""
    assert indicator_convergence_warmup(_indicator("ema", period=200)) == 891


def test_kama_uses_the_squared_slow_constant() -> None:
    """KAMA's slowest coefficient is (2/(slow+1))**2, so it converges far slower than an EMA."""
    kama = IndicatorDefinition.model_validate(
        {
            "id": "k",
            "kind": "kama",
            "input": "close",
            "parameters": {"period": 10, "fast_period": 2, "slow_period": 30},
        }
    )
    assert indicator_convergence_warmup(kama) == 11 + _updates((2 / 31) ** 2)


def test_stateful_kinds_are_flagged_anchor_sensitive() -> None:
    """Supertrend direction and SAR trend latch state no warmup removes; EMA does not."""
    assert indicator_anchor_sensitive(
        IndicatorDefinition.model_validate(
            {
                "id": "s",
                "kind": "supertrend",
                "input": ["high", "low", "close"],
                "parameters": {"period": 10, "multiplier": "3"},
            }
        )
    )
    assert not indicator_anchor_sensitive(_indicator("ema", period=20))


def test_saving_adopts_the_policy_and_only_raises_warmups() -> None:
    """Unconverged documents gain the policy and enough warmup; references derive it too."""
    original = reference_strategy()
    assert original.data_requirements.warmup_policy is None
    converged = with_convergence_warmup(original)
    decision, _ = convergence_warmups(original)
    assert converged.data_requirements.warmup_policy == "convergence"
    assert converged.data_requirements.warmup_bars == max(
        original.data_requirements.warmup_bars, decision
    )
    for before, after in zip(
        reference_data_requirements(original), reference_data_requirements(converged), strict=True
    ):
        assert after.warmup_bars >= before.warmup_bars
    assert with_convergence_warmup(converged) == converged


def test_a_reference_ema_derives_its_convergence_warmup() -> None:
    """The BTC daily EMA(100) regime reference derives 100 + EMA decay bars, not 100."""
    definition = create_template_strategy(
        product_id="ETH-USDC", timeframe="4h", template="btc-regime-gate"
    )
    expected = 100 + _updates(2 / 101)
    assert [item.warmup_bars for item in reference_data_requirements(definition)] == [expected]


def test_old_documents_keep_their_canonical_bytes() -> None:
    """Without the policy the field is omitted, so existing fingerprints never change."""
    original = reference_strategy()
    assert b"warmup_policy" not in canonical_strategy_bytes(original)
    assert b'"warmup_policy":"convergence"' in canonical_strategy_bytes(
        with_convergence_warmup(original)
    )


def test_a_converged_document_cannot_declare_too_little_warmup() -> None:
    """The policy is enforced by validation, naming the bars required."""
    document = json.loads(canonical_strategy_bytes(with_convergence_warmup(reference_strategy())))
    document["data_requirements"]["warmup_bars"] = 1
    with pytest.raises(ValidationError, match="convergence warmup needs"):
        StrategyDefinition.model_validate(document)


def test_saving_through_the_library_materializes_the_policy() -> None:
    """``evaluate_document`` is the create/save path; it stores the converged document."""
    original = reference_strategy()
    evaluated = evaluate_document(
        parse_document_text(canonical_strategy_bytes(original).decode("utf-8")),
        strategy_id=original.strategy_id,
        created_at=original.created_at,
    )
    assert evaluated.definition is not None
    assert evaluated.definition.data_requirements.warmup_policy == "convergence"
    assert evaluated.fingerprint == strategy_fingerprint(evaluated.definition)
    assert evaluated.fingerprint != strategy_fingerprint(original)


def test_templates_emit_converged_documents() -> None:
    """Template drafts are valid under the policy without any edit."""
    definition = create_template_strategy(
        product_id="BTC-USDC", timeframe="1h", template="ema-trend"
    )
    assert definition.data_requirements.warmup_policy == "convergence"
    assert with_convergence_warmup(definition) == definition


def test_an_unachievable_convergence_warmup_is_left_unconverged() -> None:
    """A warmup above the declarable cap is not silently clipped; the document keeps no policy."""
    document = json.loads(canonical_strategy_bytes(reference_strategy()))
    document["indicators"].append(
        {
            "id": "slow_kama",
            "kind": "kama",
            "input": "close",
            "parameters": {"period": 10, "fast_period": 2, "slow_period": 500},
        }
    )
    document["data_requirements"]["warmup_bars"] = 11
    definition = StrategyDefinition.model_validate(document)
    assert with_convergence_warmup(definition).data_requirements.warmup_policy is None


def _random_walk(count: int, seed: int) -> list[Decimal]:
    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not security.
    price = 100.0
    out = []
    for _ in range(count):
        price *= math.exp(rng.gauss(0.0005, 0.02))
        out.append(Decimal(str(round(price, 6))))
    return out


@pytest.mark.parametrize("period", [50, 200])
def test_converged_ema_no_longer_depends_on_where_the_series_starts(period: int) -> None:
    """Two seeds 500 bars apart agree to the residual weight once converged; not at minimum."""
    prices = _random_walk(3_000, seed=period)
    target = 2_500
    converged = indicator_convergence_warmup(_indicator("ema", period=period))

    def ema_at(start: int) -> Decimal:
        value = _exponential_moving_average(prices[start : target + 1], period)[-1]
        assert value is not None
        return value

    early, late_converged = ema_at(target - converged - 500), ema_at(target - converged)
    late_minimum = ema_at(target - period)
    scale = max(prices[target - converged : target + 1]) - min(
        prices[target - converged : target + 1]
    )
    assert abs(early - late_converged) <= Decimal(str(RESIDUAL_SEED_WEIGHT)) * scale
    assert abs(early - late_minimum) > Decimal(str(RESIDUAL_SEED_WEIGHT)) * scale


def test_reference_fixture_is_dated_utc() -> None:
    """Guard the fixture assumption used above (UTC creation instant)."""
    assert reference_strategy().created_at.tzinfo == UTC
    assert reference_strategy().created_at < datetime.now(UTC)


def _with_htf_ema(document: dict, *, period: int, warmup: int) -> dict:
    """Attach a 4h HTF filter reading EMA(period) with the given declared warmup."""
    document["htf_filter"] = {
        "timeframe": "4h",
        "data_requirements": {"warmup_bars": warmup, "required_fields": ["close"]},
        "indicators": [
            {"id": "htf_ema", "kind": "ema", "input": "close", "parameters": {"period": period}}
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_ema"},
                    "operator": "greater_than",
                    "right": {"literal": "0"},
                }
            ]
        },
    }
    return document


def test_an_htf_filter_cannot_opt_out_of_the_document_policy() -> None:
    """A converged document whose HTF filter lacks the policy is invalid (review finding)."""
    base = json.loads(canonical_strategy_bytes(with_convergence_warmup(reference_strategy())))
    document = _with_htf_ema(base, period=200, warmup=200)
    with pytest.raises(ValidationError, match="HTF warmup_policy must match"):
        StrategyDefinition.model_validate(document)


def test_saving_converges_an_htf_filter_too() -> None:
    """Materialization raises an HTF EMA(200) to its convergence warmup and marks the policy."""
    document = _with_htf_ema(
        json.loads(canonical_strategy_bytes(reference_strategy())), period=200, warmup=200
    )
    converged = with_convergence_warmup(StrategyDefinition.model_validate(document))
    assert converged.htf_filter is not None
    assert converged.htf_filter.data_requirements.warmup_policy == "convergence"
    assert converged.htf_filter.data_requirements.warmup_bars == 891


def test_lengthening_an_indicator_in_a_saved_document_raises_its_warmup() -> None:
    """Editing a converged EMA(50) to EMA(200) saves valid with 891 bars, not as invalid."""
    saved = create_template_strategy(product_id="BTC-USDC", timeframe="1h", template="ema-trend")
    document = json.loads(canonical_strategy_bytes(saved))
    slow = next(item for item in document["indicators"] if item["id"] == "slow")
    slow["parameters"]["period"] = 200
    evaluated = evaluate_document(
        document, strategy_id=saved.strategy_id, created_at=saved.created_at
    )
    assert evaluated.definition is not None, evaluated.validation
    assert evaluated.definition.data_requirements.warmup_bars == 891
    assert evaluated.definition.data_requirements.warmup_policy == "convergence"


def test_an_edit_past_the_declared_minimum_still_saves_converged() -> None:
    """EMA(50) -> EMA(500) with 223 declared bars saves valid at 2,227 bars (review finding)."""
    saved = create_template_strategy(product_id="BTC-USDC", timeframe="1h", template="ema-trend")
    document = json.loads(canonical_strategy_bytes(saved))
    next(item for item in document["indicators"] if item["id"] == "slow")["parameters"][
        "period"
    ] = 500
    evaluated = evaluate_document(
        document, strategy_id=saved.strategy_id, created_at=saved.created_at
    )
    assert evaluated.definition is not None, evaluated.validation
    assert evaluated.definition.data_requirements.warmup_bars == indicator_convergence_warmup(
        _indicator("ema", period=500)
    )


def test_other_validation_errors_still_surface_on_save() -> None:
    """Warmup recovery never hides an unrelated problem."""
    saved = create_template_strategy(product_id="BTC-USDC", timeframe="1h", template="ema-trend")
    document = json.loads(canonical_strategy_bytes(saved))
    document["data_requirements"]["required_fields"] = ["volume"]
    evaluated = evaluate_document(
        document, strategy_id=saved.strategy_id, created_at=saved.created_at
    )
    assert evaluated.definition is None
    assert evaluated.validation.issues


@pytest.mark.parametrize("bad", ["invalid", None, 10001.0, 0])
def test_malformed_declared_warmups_keep_their_validation_error(bad: object) -> None:
    """Recovery repairs short warmups only; malformed values are reported (review finding)."""
    saved = create_template_strategy(product_id="BTC-USDC", timeframe="1h", template="ema-trend")
    document = json.loads(canonical_strategy_bytes(saved))
    document["data_requirements"]["warmup_bars"] = bad
    evaluated = evaluate_document(
        document, strategy_id=saved.strategy_id, created_at=saved.created_at
    )
    assert evaluated.definition is None


def test_recovery_never_lowers_a_float_declared_warmup() -> None:
    """A declared 9000.0 is read as 9,000 and kept while another clock is repaired."""
    saved = create_template_strategy(product_id="BTC-USDC", timeframe="1h", template="ema-trend")
    document = _with_htf_ema(json.loads(canonical_strategy_bytes(saved)), period=200, warmup=200)
    document["htf_filter"]["data_requirements"]["warmup_policy"] = "convergence"
    document["data_requirements"]["warmup_bars"] = 9000.0
    evaluated = evaluate_document(
        document, strategy_id=saved.strategy_id, created_at=saved.created_at
    )
    assert evaluated.definition is not None, evaluated.validation
    assert evaluated.definition.data_requirements.warmup_bars == 9000
    assert evaluated.definition.htf_filter is not None
    assert evaluated.definition.htf_filter.data_requirements.warmup_bars == 891
