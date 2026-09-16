"""Tests for immutable canonical research-run specifications."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.research.models import (
    AdditionalInstrumentDataset,
    BarExecutionAssumptions,
    BrokerAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
    canonical_research_run_bytes,
    research_run_fingerprint,
    warmup_starts_at,
)

_STRATEGY_FINGERPRINT = "sha256:" + "1" * 64
_DATASET_FINGERPRINT = "sha256:" + "2" * 64


def _reference_run() -> ResearchRunSpecification:
    """Return one deterministic valid research-run specification."""
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000065"),
        created_at=datetime(2026, 7, 29, 20, 0, tzinfo=UTC),
        strategy_fingerprint=_STRATEGY_FINGERPRINT,
        dataset_fingerprint=_DATASET_FINGERPRINT,
        evaluation=EvaluationWindow(
            starts_at=datetime(2026, 7, 10, 0, 0, tzinfo=UTC),
            ends_at=datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
        ),
        warmup=WarmupWindow(
            bars=50,
            starts_at=datetime(2026, 7, 7, 22, 0, tzinfo=UTC),
        ),
        capital=CapitalAssumptions(
            quote_currency="USD",
            initial_quote_balance="10000.00",
        ),
        costs=CostAssumptions(
            maker_fee_rate="0.0040",
            taker_fee_rate="0.0060",
            fixed_slippage_bps="2.50",
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="next_candle_open",
        ),
        engine_contract_version="thytrader-bar-v1",
        random_seed=42,
    )


def test_reference_run_has_stable_canonical_identity() -> None:
    """The complete run request must match a literal durable golden vector."""
    run = _reference_run()
    expected = Path("tests/research/golden/reference_run_spec_v1.json").read_bytes().rstrip(b"\n")

    assert canonical_research_run_bytes(run) == expected
    assert (
        research_run_fingerprint(run)
        == "sha256:897c3b058475c508c43d2e8f08f5abcf5e60633036b55cd6543e3cc0cff8d543"
    )
    assert run.capital.initial_quote_balance == "10000"
    assert run.costs.maker_fee_rate == "0.004"
    assert run.costs.taker_fee_rate == "0.006"
    assert run.costs.fixed_slippage_bps == "2.5"


def test_run_spec_is_strict_frozen_and_rejects_float_financial_values() -> None:
    """Unknown fields, mutation, and binary floating-point inputs must fail closed."""
    run = _reference_run()

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchRunSpecification.model_validate(
            {**run.model_dump(mode="python"), "unexpected": True}
        )
    with pytest.raises(ValidationError, match="string_type"):
        ResearchRunSpecification.model_validate(
            {
                **run.model_dump(mode="python"),
                "strategy_fingerprint": cast("str", _STRATEGY_FINGERPRINT.encode()),
            }
        )
    with pytest.raises(ValidationError, match="string_type"):
        ResearchRunSpecification.model_validate(
            {
                **run.model_dump(mode="python"),
                "dataset_fingerprint": cast("str", _DATASET_FINGERPRINT.encode()),
            }
        )
    with pytest.raises(ValidationError, match="is_instance_of"):
        ResearchRunSpecification.model_validate(
            {
                **run.model_dump(mode="python"),
                "run_id": cast("UUID", run.run_id.bytes),
            }
        )
    with pytest.raises(ValidationError, match="string_type"):
        CostAssumptions.model_validate(
            {
                "maker_fee_rate": 0.004,
                "taker_fee_rate": "0.006",
                "fixed_slippage_bps": "2.5",
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        run.__setattr__("random_seed", 43)


def test_run_spec_requires_uuid7_canonical_utc_and_hour_boundaries() -> None:
    """Identity and every simulation boundary must be deterministic and hourly UTC."""
    run = _reference_run()

    with pytest.raises(ValidationError, match="UUIDv7"):
        ResearchRunSpecification.model_validate(
            {
                **run.model_dump(mode="python"),
                "run_id": UUID("550e8400-e29b-41d4-a716-446655440000"),
            }
        )
    with pytest.raises(ValidationError, match="created_at"):
        ResearchRunSpecification.model_validate(
            {
                **run.model_dump(mode="python"),
                "run_id": UUID("01985cf0-7b60-7000-8000-000000000101"),
            }
        )
    with pytest.raises(ValidationError, match="UTC"):
        EvaluationWindow(
            starts_at=datetime.combine(date(2026, 7, 10), time.min),
            ends_at=datetime.combine(date(2026, 7, 20), time.min),
        )
    with pytest.raises(ValidationError, match="candle boundary"):
        EvaluationWindow(
            starts_at=datetime(2026, 7, 10, 0, 0, 1, tzinfo=UTC),
            ends_at=datetime(2026, 7, 20, 0, 0, tzinfo=UTC),
        )


def test_run_spec_accepts_five_minute_warmup_spacing() -> None:
    """5m strategies space warmup bars by five minutes, not by hours."""
    starts_at = datetime(2026, 7, 10, 0, 5, tzinfo=UTC)
    run = ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000065"),
        created_at=datetime(2026, 7, 29, 20, 0, tzinfo=UTC),
        strategy_fingerprint=_STRATEGY_FINGERPRINT,
        dataset_fingerprint=_DATASET_FINGERPRINT,
        evaluation=EvaluationWindow(
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=10),
        ),
        warmup=WarmupWindow(bars=2, starts_at=starts_at - timedelta(minutes=10)),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.004",
            taker_fee_rate="0.006",
            fixed_slippage_bps="2.5",
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="next_candle_open",
        ),
        engine_contract_version="thytrader-bar-v1",
        random_seed=42,
    )
    assert run.warmup.starts_at == datetime(2026, 7, 9, 23, 55, tzinfo=UTC)


def _venue_clock_run(
    *,
    starts_at: datetime,
    bar: timedelta,
    run_id: UUID,
) -> ResearchRunSpecification:
    """Build one valid research-run spec for a single-bar ingested venue clock."""
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=run_id,
        created_at=datetime(2026, 7, 29, 20, 0, tzinfo=UTC),
        strategy_fingerprint=_STRATEGY_FINGERPRINT,
        dataset_fingerprint=_DATASET_FINGERPRINT,
        evaluation=EvaluationWindow(
            starts_at=starts_at,
            ends_at=starts_at + bar,
        ),
        warmup=WarmupWindow(bars=1, starts_at=starts_at - bar),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.004",
            taker_fee_rate="0.006",
            fixed_slippage_bps="2.5",
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="next_candle_open",
        ),
        engine_contract_version="thytrader-bar-v1",
        random_seed=42,
    )


def test_run_spec_accepts_ingested_venue_warmup_spacing() -> None:
    """Complete-only venue clocks may space research warmup by their own duration."""
    cases = (
        (datetime(2026, 7, 10, 0, 1, tzinfo=UTC), timedelta(minutes=1), "1m"),
        (datetime(2026, 7, 10, 0, 15, tzinfo=UTC), timedelta(minutes=15), "15m"),
        (datetime(2026, 7, 10, 0, 30, tzinfo=UTC), timedelta(minutes=30), "30m"),
        (datetime(2026, 7, 10, 2, 0, tzinfo=UTC), timedelta(hours=2), "2h"),
        (datetime(2026, 7, 10, 4, 0, tzinfo=UTC), timedelta(hours=4), "4h"),
        (datetime(2026, 7, 10, 6, 0, tzinfo=UTC), timedelta(hours=6), "6h"),
        (datetime(2026, 7, 10, 0, 0, tzinfo=UTC), timedelta(days=1), "1d"),
    )
    for starts_at, bar, timeframe in cases:
        run = _venue_clock_run(
            starts_at=starts_at,
            bar=bar,
            run_id=UUID("019faf76-6600-7000-8000-000000000066"),
        )
        assert run.warmup.starts_at == starts_at - bar
        assert warmup_starts_at(starts_at, 1, timeframe) == starts_at - bar


def test_run_spec_rejects_hourly_warmup_on_one_minute_boundary() -> None:
    """An inferred 1h clock must not accept a :01 bound even when spacing is one hour."""
    starts_at = datetime(2026, 7, 10, 0, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="align"):
        ResearchRunSpecification(
            schema_version="1.0",
            run_id=UUID("019faf76-6600-7000-8000-000000000066"),
            created_at=datetime(2026, 7, 29, 20, 0, tzinfo=UTC),
            strategy_fingerprint=_STRATEGY_FINGERPRINT,
            dataset_fingerprint=_DATASET_FINGERPRINT,
            evaluation=EvaluationWindow(
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
            ),
            warmup=WarmupWindow(bars=1, starts_at=starts_at - timedelta(hours=1)),
            capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
            costs=CostAssumptions(
                maker_fee_rate="0.004",
                taker_fee_rate="0.006",
                fixed_slippage_bps="2.5",
            ),
            bar_execution=BarExecutionAssumptions(
                signal_timing="completed_candle_close",
                fill_timing="next_candle_open",
            ),
            engine_contract_version="thytrader-bar-v1",
            random_seed=42,
        )


def test_warmup_starts_at_rejects_unknown_strategy_clock() -> None:
    """Deriving warmup from a non-venue timeframe must fail closed."""
    with pytest.raises(ValueError, match="Unsupported candle interval"):
        warmup_starts_at(datetime(2026, 7, 10, tzinfo=UTC), 1, "3h")


def test_run_spec_requires_exact_derived_warmup_range() -> None:
    """Warmup start must equal evaluation start minus the declared bars."""
    run = _reference_run()

    with pytest.raises(ValidationError, match="warmup"):
        ResearchRunSpecification.model_validate(
            {
                **run.model_dump(mode="python"),
                "warmup": {
                    "bars": 50,
                    "starts_at": datetime(2026, 7, 7, 21, 0, tzinfo=UTC),
                },
            }
        )
    with pytest.raises(ValidationError, match="evaluation"):
        EvaluationWindow(
            starts_at=datetime(2026, 7, 10, tzinfo=UTC),
            ends_at=datetime(2026, 7, 10, tzinfo=UTC),
        )


def test_run_spec_rejects_unrepresentable_derived_warmup_range() -> None:
    """Minimum-date evaluation bounds must produce a controlled validation error."""
    run = _reference_run()
    minimum = datetime.min.replace(tzinfo=UTC)

    with pytest.raises(ValidationError, match="warmup"):
        ResearchRunSpecification.model_validate(
            {
                **run.model_dump(mode="python"),
                "evaluation": EvaluationWindow(
                    starts_at=minimum,
                    ends_at=minimum + timedelta(hours=1),
                ),
                "warmup": WarmupWindow(bars=1, starts_at=minimum),
            }
        )


def test_run_spec_rejects_malformed_fingerprints_and_unbounded_assumptions() -> None:
    """Artifact identities, capital, costs, and seeds have explicit safe bounds."""
    run = _reference_run()

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ResearchRunSpecification.model_validate(
            {**run.model_dump(mode="python"), "dataset_fingerprint": "sha256:not-a-digest"}
        )
    with pytest.raises(ValidationError, match="initial_quote_balance"):
        CapitalAssumptions(quote_currency="USD", initial_quote_balance="0")
    with pytest.raises(ValidationError, match="maker_fee_rate"):
        CostAssumptions(
            maker_fee_rate="0.02",
            taker_fee_rate="0.01",
            fixed_slippage_bps="0",
        )
    with pytest.raises(ValidationError):
        ResearchRunSpecification.model_validate(
            {**run.model_dump(mode="python"), "random_seed": 2**63}
        )


def test_long_decimal_identity_is_context_independent() -> None:
    """Digits beyond Decimal's ambient precision must not collapse during canonicalization."""
    first = _reference_run().model_copy(
        update={
            "capital": CapitalAssumptions(
                quote_currency="USD",
                initial_quote_balance="10000.12345678901234567890123456789",
            )
        }
    )
    equivalent = first.model_copy(
        update={
            "capital": CapitalAssumptions(
                quote_currency="USD",
                initial_quote_balance="10000.123456789012345678901234567890",
            )
        }
    )
    distinct = first.model_copy(
        update={
            "capital": CapitalAssumptions(
                quote_currency="USD",
                initial_quote_balance="10000.12345678901234567890123456788",
            )
        }
    )

    assert canonical_research_run_bytes(first) == canonical_research_run_bytes(equivalent)
    assert research_run_fingerprint(first) == research_run_fingerprint(equivalent)
    assert canonical_research_run_bytes(first) != canonical_research_run_bytes(distinct)
    assert research_run_fingerprint(first) != research_run_fingerprint(distinct)


def test_meaningful_assumption_changes_produce_distinct_identity() -> None:
    """Changing the seed or evaluation interval must create a distinct run fingerprint."""
    run = _reference_run()
    changed_seed = run.model_copy(update={"random_seed": 43})
    changed_end = run.model_copy(
        update={
            "evaluation": EvaluationWindow(
                starts_at=run.evaluation.starts_at,
                ends_at=run.evaluation.ends_at + timedelta(hours=1),
            )
        }
    )

    assert research_run_fingerprint(run) != research_run_fingerprint(changed_seed)
    assert research_run_fingerprint(run) != research_run_fingerprint(changed_end)


def test_run_spec_rejects_undefined_engine_contract_versions() -> None:
    """A future-looking engine label must not imply semantics that are not implemented."""
    run = _reference_run()

    with pytest.raises(ValidationError, match="literal_error"):
        ResearchRunSpecification.model_validate(
            {**run.model_dump(mode="python"), "engine_contract_version": "thytrader-bar-v2"}
        )


def test_executable_engine_contracts_are_explicit_and_identity_bearing() -> None:
    """Signal and backtest semantics require identities distinct from request-only V1."""
    request_only = _reference_run()

    signal = ResearchRunSpecification.model_validate(
        {
            **request_only.model_dump(mode="python"),
            "engine_contract_version": "thytrader-bar-signal-v1",
        }
    )
    backtest = ResearchRunSpecification.model_validate(
        {
            **request_only.model_dump(mode="python"),
            "engine_contract_version": "thytrader-bar-backtest-v1",
        }
    )

    assert signal.engine_contract_version == "thytrader-bar-signal-v1"
    assert backtest.engine_contract_version == "thytrader-bar-backtest-v1"
    assert research_run_fingerprint(signal) != research_run_fingerprint(request_only)
    assert research_run_fingerprint(backtest) != research_run_fingerprint(request_only)
    assert research_run_fingerprint(backtest) != research_run_fingerprint(signal)


def test_backtest_v2_requires_immutable_broker_assumptions() -> None:
    """Spread-aware execution only exists when every broker choice is identity-bearing."""
    v1 = ResearchRunSpecification.model_validate(
        {
            **_reference_run().model_dump(mode="python"),
            "engine_contract_version": "thytrader-bar-backtest-v1",
        }
    )
    broker = BrokerAssumptions(
        price_model="constant_spread_bps",
        spread_bps="10.00",
        fill_policy="full",
        trigger_evaluation="bid_side",
        equity_marking="bid_close",
    )
    v2 = ResearchRunSpecification.model_validate(
        {
            **v1.model_dump(mode="python"),
            "broker": broker,
            "engine_contract_version": "thytrader-bar-backtest-v2",
        }
    )

    assert v2.broker is not None
    assert v2.broker.spread_bps == "10"
    assert research_run_fingerprint(v1) != research_run_fingerprint(v2)
    with pytest.raises(ValidationError, match="requires broker"):
        ResearchRunSpecification.model_validate(
            {**v1.model_dump(mode="python"), "engine_contract_version": "thytrader-bar-backtest-v2"}
        )
    with pytest.raises(ValidationError, match="require the backtest V2 contract"):
        ResearchRunSpecification.model_validate({**v1.model_dump(mode="python"), "broker": broker})


def test_backtest_v3_requires_resting_maker_assumptions() -> None:
    """Maker-limit fills are a new contract; v1/v2 must not accept those literals."""
    v1 = ResearchRunSpecification.model_validate(
        {
            **_reference_run().model_dump(mode="python"),
            "engine_contract_version": "thytrader-bar-backtest-v1",
        }
    )
    v3_broker = BrokerAssumptions(
        price_model="post_only_limit",
        spread_bps="0",
        fill_policy="resting_limit",
        trigger_evaluation="bar_extreme",
        equity_marking="last_close",
    )
    v3 = ResearchRunSpecification.model_validate(
        {
            **v1.model_dump(mode="python"),
            "broker": v3_broker,
            "bar_execution": BarExecutionAssumptions(
                signal_timing="completed_candle_close",
                fill_timing="resting_maker_limit",
                limit_at="completed_close",
            ),
            "engine_contract_version": "thytrader-bar-backtest-v3",
        }
    )

    assert v3.bar_execution.fill_timing == "resting_maker_limit"
    assert v3.bar_execution.limit_at == "completed_close"
    assert v3.broker is not None
    assert v3.broker.fill_policy == "resting_limit"
    assert research_run_fingerprint(v1) != research_run_fingerprint(v3)
    with pytest.raises(ValidationError, match="requires broker"):
        ResearchRunSpecification.model_validate(
            {
                **v1.model_dump(mode="python"),
                "bar_execution": v3.bar_execution,
                "engine_contract_version": "thytrader-bar-backtest-v3",
            }
        )
    with pytest.raises(ValidationError, match="require the backtest V3 or V4 contract"):
        ResearchRunSpecification.model_validate(
            {
                **v1.model_dump(mode="python"),
                "bar_execution": v3.bar_execution,
            }
        )


def test_run_spec_rejects_boolean_integers_and_numeric_timestamps() -> None:
    """Pydantic coercion must not turn JSON booleans or epochs into canonical run facts."""
    run = _reference_run()

    with pytest.raises(ValidationError, match="int_type"):
        ResearchRunSpecification.model_validate(
            {**run.model_dump(mode="python"), "random_seed": True}
        )
    with pytest.raises(ValidationError, match="int_type"):
        WarmupWindow.model_validate({"bars": True, "starts_at": run.warmup.starts_at})
    with pytest.raises(ValidationError, match="datetime_type"):
        EvaluationWindow.model_validate(
            {
                "starts_at": 1_789_000_000,
                "ends_at": run.evaluation.ends_at,
            }
        )


def test_run_identity_helpers_revalidate_copied_models() -> None:
    """Canonical run identities reject instances forged by unchecked model copies."""
    forged = _reference_run().model_copy(update={"random_seed": -1})

    with pytest.raises(ValidationError):
        canonical_research_run_bytes(forged)
    with pytest.raises(ValidationError):
        research_run_fingerprint(forged)


def test_omitted_additional_instrument_datasets_preserve_reference_identity() -> None:
    """Empty extra-product bindings must not appear in existing run fingerprints."""
    run = _reference_run()
    assert run.additional_instrument_datasets == ()
    canonical = canonical_research_run_bytes(run)
    assert b"additional_instrument_datasets" not in canonical
    expected = Path("tests/research/golden/reference_run_spec_v1.json").read_bytes().rstrip(b"\n")
    assert canonical == expected


def test_additional_instrument_datasets_must_be_lexicographic_and_unique() -> None:
    """Extra product bindings are identity-bearing and ordered by product_id."""
    payload = _reference_run().model_dump(mode="python")
    eth = AdditionalInstrumentDataset(
        product_id="ETH-USD",
        dataset_fingerprint="sha256:" + "3" * 64,
    )
    payload["additional_instrument_datasets"] = (eth,)
    spec = ResearchRunSpecification.model_validate(payload)
    canonical = canonical_research_run_bytes(spec)
    assert b"additional_instrument_datasets" in canonical
    assert canonical != canonical_research_run_bytes(_reference_run())
    payload["additional_instrument_datasets"] = (
        AdditionalInstrumentDataset(
            product_id="SOL-USD",
            dataset_fingerprint="sha256:" + "4" * 64,
        ),
        eth,
    )
    with pytest.raises(ValidationError, match="ordered by product_id"):
        ResearchRunSpecification.model_validate(payload)
    payload["additional_instrument_datasets"] = (
        eth,
        AdditionalInstrumentDataset(
            product_id="ETH-USD",
            dataset_fingerprint="sha256:" + "5" * 64,
        ),
    )
    with pytest.raises(ValidationError, match="unique"):
        ResearchRunSpecification.model_validate(payload)
