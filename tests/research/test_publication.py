"""Tests for fail-closed research-run input eligibility."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from thytrader.market_data.datasets import DatasetManifest
from thytrader.research.models import (
    BarExecutionAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.research.publication import (
    ResearchRunPublicationError,
    verify_research_run_eligibility,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy

_DATASET_FINGERPRINT = "sha256:" + "2" * 64


def _published_strategy() -> PublishedStrategy:
    """Load the immutable reference strategy used by publication tests."""
    definition = StrategyDefinition.model_validate_json(
        Path("tests/strategies/golden/reference_strategy_v1.json").read_text(encoding="utf-8")
    )
    return PublishedStrategy(
        strategy_fingerprint=strategy_fingerprint(definition),
        definition=definition,
    )


def _run(
    published: PublishedStrategy,
    *,
    evaluation: EvaluationWindow | None = None,
) -> ResearchRunSpecification:
    """Return a run request compatible with the reference strategy."""
    selected_evaluation = evaluation or EvaluationWindow(
        starts_at=datetime(2026, 7, 10, tzinfo=UTC),
        ends_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000066"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint=published.strategy_fingerprint,
        dataset_fingerprint=_DATASET_FINGERPRINT,
        evaluation=selected_evaluation,
        warmup=WarmupWindow(
            bars=50,
            starts_at=selected_evaluation.starts_at - timedelta(hours=50),
        ),
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


def _manifest() -> DatasetManifest:
    """Return verified-manifest facts covering warmup, evaluation, and next-open fill data."""
    return DatasetManifest(
        provider="coinbase",
        product_id="BTC-USD",
        timeframe="1h",
        starts_at="2026-07-07T22:00:00Z",
        ends_at="2026-07-20T01:00:00Z",
        expected_candle_count=291,
        received_candle_count=291,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=_DATASET_FINGERPRINT,
        files=(Path("unused.parquet"),),
        manifest_path=Path("unused.json"),
    )


def test_eligibility_accepts_exact_verified_artifact_contract() -> None:
    """A compatible strategy, dataset, range, warmup, and fill lookahead are eligible."""
    published = _published_strategy()

    verify_research_run_eligibility(_run(published), published, _manifest())


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("provider", "other"),
        ("product_id", "ETH-USD"),
        ("timeframe", "5m"),
        ("content_fingerprint", "sha256:" + "3" * 64),
    ],
)
def test_eligibility_rejects_mismatched_dataset_identity(
    changed_field: str,
    changed_value: str,
) -> None:
    """Every immutable dataset identity dimension must match independently."""
    published = _published_strategy()
    manifest = _manifest()
    mismatched = replace(manifest, **{changed_field: changed_value})

    with pytest.raises(ResearchRunPublicationError, match="dataset identity"):
        verify_research_run_eligibility(_run(published), published, mismatched)


def test_eligibility_rejects_strategy_identity_or_warmup_mismatch() -> None:
    """The request must resolve the exact strategy and its declared warmup bars."""
    published = _published_strategy()
    run = _run(published)
    wrong_identity = run.model_copy(update={"strategy_fingerprint": "sha256:" + "4" * 64})
    wrong_warmup = run.model_copy(
        update={
            "warmup": WarmupWindow(
                bars=249,
                starts_at=run.evaluation.starts_at - timedelta(hours=249),
            )
        }
    )

    with pytest.raises(ResearchRunPublicationError, match="strategy identity"):
        verify_research_run_eligibility(wrong_identity, published, _manifest())
    with pytest.raises(ResearchRunPublicationError, match="warmup"):
        verify_research_run_eligibility(wrong_warmup, published, _manifest())


def test_eligibility_rejects_insufficient_warmup_or_next_open_coverage() -> None:
    """Dataset coverage must include all warmup bars and one post-evaluation fill candle."""
    published = _published_strategy()
    run = _run(published)
    manifest = _manifest()
    late_start = replace(manifest, starts_at="2026-07-07T23:00:00Z")
    missing_next_open = replace(manifest, ends_at="2026-07-20T00:00:00Z")

    with pytest.raises(ResearchRunPublicationError, match="warmup coverage"):
        verify_research_run_eligibility(run, published, late_start)
    with pytest.raises(ResearchRunPublicationError, match="next-candle-open"):
        verify_research_run_eligibility(run, published, missing_next_open)


def test_eligibility_rejects_unrepresentable_next_open_boundary() -> None:
    """Maximum-date evaluation windows fail as controlled publication errors."""
    published = _published_strategy()
    extreme_evaluation = EvaluationWindow(
        starts_at=datetime(9999, 12, 31, 22, tzinfo=UTC),
        ends_at=datetime(9999, 12, 31, 23, tzinfo=UTC),
    )
    extreme_run = _run(published, evaluation=extreme_evaluation)
    extreme_manifest = replace(
        _manifest(),
        starts_at="9999-12-29T20:00:00Z",
        ends_at="9999-12-31T23:00:00Z",
    )

    with pytest.raises(ResearchRunPublicationError, match="next-candle-open"):
        verify_research_run_eligibility(extreme_run, published, extreme_manifest)


def test_eligibility_rejects_incomplete_manifest_even_when_forged() -> None:
    """Only a verified complete immutable dataset can make a run request eligible."""
    published = _published_strategy()

    with pytest.raises(ResearchRunPublicationError, match="complete"):
        verify_research_run_eligibility(
            _run(published),
            published,
            replace(_manifest(), complete=False),
        )
