"""Backtest submission and publication checks for reference datasets (ADR 0096).

The reference dataset must be the declared product and timeframe, complete, and cover
the reference warmup before the first mapped bar through the last reference bar that
closes by evaluation end. Omitted bounds shrink to that coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from tests.strategies.reference_support import (
    DECISION_DATASET,
    REFERENCE_DATASET,
    reference_binding,
    reference_run,
    reference_strategy,
)
from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    resolve_backtest_window,
)
from thytrader.evaluation.models import ReferenceInstrumentDataset
from thytrader.evaluation.publication import (
    ResearchRunPublicationError,
    verify_research_run_eligibility,
)
from thytrader.market_data.datasets import DatasetManifest, DatasetStore, DatasetStoreError
from thytrader.persistence.postgres_strategy_snapshots import _verify_compatible_dataset
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import StrategyDatasetMismatchError, StrategySnapshot

_SOL_DATASET = "sha256:" + "5" * 64


def _manifest(
    fingerprint: str, product_id: str, timeframe: str, starts_at: str, ends_at: str
) -> DatasetManifest:
    """One complete coinbase manifest."""
    return DatasetManifest(
        provider="coinbase",
        product_id=product_id,
        timeframe=timeframe,
        starts_at=starts_at,
        ends_at=ends_at,
        expected_candle_count=1,
        received_candle_count=1,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=fingerprint,
        files=(Path("unused.parquet"),),
        manifest_path=Path("unused.json"),
    )


_MANIFESTS = {
    DECISION_DATASET: _manifest(
        DECISION_DATASET, "ETH-USD", "1h", "2025-01-01T00:00:00Z", "2025-03-01T00:00:00Z"
    ),
    REFERENCE_DATASET: _manifest(
        REFERENCE_DATASET, "BTC-USD", "1d", "2025-01-10T00:00:00Z", "2025-02-15T00:00:00Z"
    ),
    _SOL_DATASET: _manifest(
        _SOL_DATASET, "SOL-USD", "1d", "2025-01-01T00:00:00Z", "2025-03-01T00:00:00Z"
    ),
}


class _Manifests:
    """A dataset store double that only loads the canned manifests."""

    def load_manifest(self, content_fingerprint: str) -> DatasetManifest:
        """Return one manifest or fail like a missing artifact."""
        manifest = _MANIFESTS.get(content_fingerprint)
        if manifest is None:
            raise DatasetStoreError("missing")
        return manifest


def _store() -> DatasetStore:
    """The manifest double typed as the store the submission reads."""
    return cast("DatasetStore", _Manifests())


def _snapshot(definition: StrategyDefinition | None = None) -> StrategySnapshot:
    """Snapshot the reference fixture."""
    strategy = definition or reference_strategy()
    return StrategySnapshot(
        strategy_fingerprint=strategy_fingerprint(strategy), definition=strategy
    )


def _request(
    references: tuple[ReferenceInstrumentDataset, ...] | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> BacktestSubmissionRequest:
    """A submission for the fixture with the BTC reference bound (unless overridden)."""
    snapshot = _snapshot()
    bound = (reference_binding(snapshot.definition),) if references is None else references
    return BacktestSubmissionRequest(
        strategy_fingerprint=snapshot.strategy_fingerprint,
        dataset_fingerprint=DECISION_DATASET,
        reference_dataset_fingerprints=bound,
        evaluation_start=start,
        evaluation_end=end,
        initial_quote_balance="1000",
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps="5",
    )


def test_omitted_bounds_shrink_to_reference_closed_bar_coverage() -> None:
    """Warmup (2 daily bars) and the last closed daily bar clip the decision window."""
    start, end = resolve_backtest_window(_request(), _snapshot(), _store())
    assert start == datetime(2025, 1, 12, tzinfo=UTC)
    assert end == datetime(2025, 2, 15, 23, tzinfo=UTC)


def test_explicit_bounds_beyond_reference_coverage_are_rejected() -> None:
    """A window ending after the reference dataset's last closed bar names the reference."""
    request = _request(
        start=datetime(2025, 1, 20, tzinfo=UTC), end=datetime(2025, 2, 25, tzinfo=UTC)
    )
    with pytest.raises(BacktestSubmissionRejectedError, match=r"btc \(BTC-USD 1d\)"):
        resolve_backtest_window(request, _snapshot(), _store())


@pytest.mark.parametrize(
    "references",
    [
        (),
        (
            ReferenceInstrumentDataset(
                reference_id="btc",
                product_id="SOL-USD",
                timeframe="1d",
                dataset_fingerprint=_SOL_DATASET,
            ),
        ),
    ],
)
def test_reference_bindings_must_match_the_declared_references(
    references: tuple[ReferenceInstrumentDataset, ...],
) -> None:
    """Missing or mismatched bindings fail with the expected list."""
    with pytest.raises(BacktestSubmissionRejectedError, match="btc=BTC-USD 1d"):
        resolve_backtest_window(_request(references), _snapshot(), _store())


def test_a_binding_whose_dataset_is_another_product_is_rejected() -> None:
    """The bound fingerprint's manifest must be the declared reference series."""
    forged = ReferenceInstrumentDataset(
        reference_id="btc", product_id="BTC-USD", timeframe="1d", dataset_fingerprint=_SOL_DATASET
    )
    with pytest.raises(BacktestSubmissionRejectedError, match="must match the declared reference"):
        resolve_backtest_window(_request((forged,)), _snapshot(), _store())


def test_strategy_binding_accepts_the_reference_series_only() -> None:
    """A reference dataset may be bound to the strategy; an unrelated series may not."""
    snapshot = _snapshot()
    _verify_compatible_dataset(snapshot, REFERENCE_DATASET, _store())
    _verify_compatible_dataset(snapshot, DECISION_DATASET, _store())
    with pytest.raises(StrategyDatasetMismatchError, match="reference BTC-USD 1d"):
        _verify_compatible_dataset(snapshot, _SOL_DATASET, _store())


def test_publication_requires_covering_reference_manifests() -> None:
    """Eligibility verifies reference identity and closed-bar coverage."""
    snapshot = _snapshot()
    run = reference_run(snapshot.definition, starts_at=datetime(2025, 1, 20, tzinfo=UTC), hours=24)
    decision = _MANIFESTS[DECISION_DATASET]
    verify_research_run_eligibility(
        run, snapshot, decision, reference_manifests={"btc": _MANIFESTS[REFERENCE_DATASET]}
    )
    with pytest.raises(ResearchRunPublicationError, match="identity does not match"):
        verify_research_run_eligibility(run, snapshot, decision, reference_manifests={})
    late = reference_run(snapshot.definition, starts_at=datetime(2025, 2, 20, tzinfo=UTC), hours=24)
    with pytest.raises(ResearchRunPublicationError, match="required closed-bar coverage"):
        verify_research_run_eligibility(
            late, snapshot, decision, reference_manifests={"btc": _MANIFESTS[REFERENCE_DATASET]}
        )
    unbound = reference_run(
        snapshot.definition, starts_at=datetime(2025, 1, 20, tzinfo=UTC), hours=24, bound=False
    )
    with pytest.raises(ResearchRunPublicationError, match="do not match the published strategy"):
        verify_research_run_eligibility(unbound, snapshot, decision)
