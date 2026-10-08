"""Reference datasets bind like every other clock, and studies carry them (ADR 0096).

Backtest starts auto-bind each reference instrument to the newest complete catalog
dataset (echoed as a ``reference`` row in ``bound_datasets``). Studies carry the
bindings to every child window, and a cross-market re-target keeps the reference
instrument fixed: BTC stays BTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from tests.strategies.reference_support import reference_strategy
from thytrader.backtest.submission import BacktestStartRequest
from thytrader.evaluation.models import ReferenceInstrumentDataset
from thytrader.market_data.datasets import DatasetManifest, DatasetStore
from thytrader.research.dataset_binding import (
    DatasetResolver,
    DatasetsMissingError,
    bind_backtest_datasets,
    bind_reference_datasets,
)
from thytrader.research.market_variants import MarketVariantError, derive_market_variant
from thytrader.research.studies import (
    MarketBinding,
    PlannedStudyWindow,
    StudyKind,
    WindowRole,
    plan_study,
    window_submission_request,
)
from thytrader.research.study_start import ResearchStudyStartRequest, bind_study_start
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.strategies.models import strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot


def _fingerprint(product_id: str, timeframe: str) -> str:
    """A distinct deterministic fingerprint per cataloged series."""
    return "sha256:" + sha256(f"demo|{product_id}|{timeframe}".encode()).hexdigest()


def _manifest(product_id: str, timeframe: str) -> DatasetManifest:
    """One complete demo catalog row."""
    return DatasetManifest(
        provider="demo",
        product_id=product_id,
        timeframe=timeframe,
        starts_at="2025-01-01T00:00:00Z",
        ends_at="2026-01-01T00:00:00Z",
        expected_candle_count=1,
        received_candle_count=1,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=_fingerprint(product_id, timeframe),
        files=(Path("unused.parquet"),),
        manifest_path=Path("unused.json"),
    )


class _Catalog:
    """A dataset store double that lists canned latest rows."""

    def __init__(self, *series: tuple[str, str]) -> None:
        """Catalog one complete dataset per (product, timeframe)."""
        self._manifests = tuple(_manifest(product, timeframe) for product, timeframe in series)

    def list_latest_verified(self) -> tuple[DatasetManifest, ...]:
        """Return the canned catalog."""
        return self._manifests


def _resolver(*series: tuple[str, str]) -> DatasetResolver:
    """Resolve against the demo provider over the given catalog."""
    return DatasetResolver(store=cast("DatasetStore", _Catalog(*series)), provider="demo")


def _start(**fields: object) -> BacktestStartRequest:
    """A backtest start for the fixture strategy with omitted datasets."""
    return BacktestStartRequest.model_validate(
        {
            "strategy_id": str(reference_strategy().strategy_id),
            "initial_quote_balance": "1000",
            "maker_fee_rate": "0.001",
            "taker_fee_rate": "0.002",
            "fixed_slippage_bps": "5",
            **fields,
        }
    )


def test_backtest_start_binds_the_reference_to_the_latest_catalog_dataset() -> None:
    """The decision clock and the BTC 1d reference both bind and are echoed."""
    resolver = _resolver(("ETH-USD", "1h"), ("BTC-USD", "1d"))
    bound = bind_backtest_datasets(_start(), reference_strategy(), resolver)
    assert bound.dataset_fingerprint == _fingerprint("ETH-USD", "1h")
    (reference,) = bound.reference_dataset_fingerprints
    assert (reference.reference_id, reference.product_id, reference.timeframe) == (
        "btc",
        "BTC-USD",
        "1d",
    )
    assert reference.dataset_fingerprint == _fingerprint("BTC-USD", "1d")
    rows = {row.role: row for row in resolver.bindings()}
    assert rows["reference"].reference_id == "btc"
    assert rows["reference"].source == "latest_catalog"
    assert "reference_id" not in rows["decision"].model_dump()


def test_a_pinned_reference_fingerprint_is_kept_and_echoed_as_request() -> None:
    """An explicit binding wins over the catalog."""
    pinned = "sha256:" + "7" * 64
    start = _start(
        reference_dataset_fingerprints=[
            {
                "reference_id": "btc",
                "product_id": "BTC-USD",
                "timeframe": "1d",
                "dataset_fingerprint": pinned,
            }
        ]
    )
    resolver = _resolver(("ETH-USD", "1h"), ("BTC-USD", "1d"))
    bound = bind_backtest_datasets(start, reference_strategy(), resolver)
    assert bound.reference_dataset_fingerprints[0].dataset_fingerprint == pinned
    reference_rows = [row for row in resolver.bindings() if row.role == "reference"]
    assert [row.source for row in reference_rows] == ["request"]


def test_a_missing_reference_dataset_names_the_watch_and_ingest_commands() -> None:
    """No cataloged BTC 1d dataset fails closed with the data-lane commands."""
    resolver = _resolver(("ETH-USD", "1h"))
    with pytest.raises(DatasetsMissingError) as caught:
        bind_backtest_datasets(_start(), reference_strategy(), resolver)
    message = str(caught.value)
    assert "BTC-USD 1d (reference instrument)" in message
    assert "thytrader-data watch-add --product-id BTC-USD --timeframe 1d" in message
    assert [need.reference_id for need in caught.value.missing] == ["btc"]


def test_an_inconsistent_explicit_reference_list_is_kept_for_validation_to_reject() -> None:
    """A binding naming the wrong product is not silently repaired."""
    wrong = ReferenceInstrumentDataset(
        reference_id="btc",
        product_id="SOL-USD",
        timeframe="1d",
        dataset_fingerprint="sha256:" + "8" * 64,
    )
    resolver = _resolver(("BTC-USD", "1d"))
    kept = bind_reference_datasets(reference_strategy(), resolver, explicit=(wrong,))
    assert kept == (wrong,)


def test_cross_market_variant_keeps_the_reference_instrument() -> None:
    """Re-targeting ETH at SOL (or at BTC itself) keeps reading BTC 1d."""
    base = reference_strategy()
    snapshot = StrategySnapshot(strategy_fingerprint=strategy_fingerprint(base), definition=base)
    for product_id in ("SOL-USD", "BTC-USD"):
        variant = derive_market_variant(snapshot, product_id)
        assert variant.instrument.product_id == product_id
        assert variant.data_requirements.reference_instruments == (
            base.data_requirements.reference_instruments
        )


def test_cross_market_variant_on_another_quote_currency_is_refused() -> None:
    """A USDC market cannot keep a BTC-USD reference: the variant fails closed."""
    base = reference_strategy()
    snapshot = StrategySnapshot(strategy_fingerprint=strategy_fingerprint(base), definition=base)
    with pytest.raises(MarketVariantError, match="quote currency"):
        derive_market_variant(snapshot, "SOL-USDC")


@pytest.mark.anyio
async def test_cross_market_study_binds_the_shared_reference_for_every_market() -> None:
    """Each market leg binds its own decision clock and the same BTC reference."""
    store = InMemoryStrategyStore()
    base = store.seed_definition(reference_strategy())
    resolver = _resolver(("ETH-USD", "1h"), ("SOL-USD", "1h"), ("BTC-USD", "1d"))
    start = ResearchStudyStartRequest.model_validate(
        {
            "kind": StudyKind.CROSS_MARKET.value,
            "strategy_id": str(base.definition.strategy_id),
            "markets": [{"product_id": "ETH-USD"}, {"product_id": "SOL-USD"}],
            "evaluation_start": "2025-06-01T00:00:00Z",
            "evaluation_end": "2025-07-01T00:00:00Z",
            "initial_quote_balance": "1000",
            "maker_fee_rate": "0.001",
            "taker_fee_rate": "0.002",
            "fixed_slippage_bps": "5",
        }
    )
    bound = await bind_study_start(
        start, strategies=store, publications=store, resolver=resolver, datasets=None
    )
    markets = bound.request.markets or ()
    assert [market.dataset_fingerprint for market in markets] == [
        _fingerprint("ETH-USD", "1h"),
        _fingerprint("SOL-USD", "1h"),
    ]
    for market in markets:
        (reference,) = market.reference_dataset_fingerprints
        assert reference.dataset_fingerprint == _fingerprint("BTC-USD", "1d")
    assert [row.role for row in bound.bound_datasets].count("reference") == 1
    publications = {
        market.strategy_fingerprint: await store.load(market.strategy_fingerprint)
        for market in markets
    }
    plan = plan_study(bound.request, publications=publications)
    for window in plan.windows:
        submission = window_submission_request(bound.request, window)
        assert submission.reference_dataset_fingerprints == window.reference_dataset_fingerprints
        assert submission.reference_dataset_fingerprints[0].product_id == "BTC-USD"


@pytest.mark.anyio
async def test_single_market_studies_carry_references_to_every_window() -> None:
    """Walk-forward children bind the same reference as the base strategy."""
    store = InMemoryStrategyStore()
    base = store.seed_definition(reference_strategy())
    resolver = _resolver(("ETH-USD", "1h"), ("BTC-USD", "1d"))
    start = ResearchStudyStartRequest.model_validate(
        {
            "kind": StudyKind.WALK_FORWARD.value,
            "strategy_id": str(base.definition.strategy_id),
            "evaluation_start": "2025-06-01T00:00:00Z",
            "evaluation_end": "2025-06-11T00:00:00Z",
            "in_sample_bars": 120,
            "out_of_sample_bars": 48,
            "step_bars": 48,
            "initial_quote_balance": "1000",
            "maker_fee_rate": "0.001",
            "taker_fee_rate": "0.002",
            "fixed_slippage_bps": "5",
        }
    )
    bound = await bind_study_start(
        start, strategies=store, publications=store, resolver=resolver, datasets=None
    )
    assert bound.request.reference_dataset_fingerprints[0].dataset_fingerprint == _fingerprint(
        "BTC-USD", "1d"
    )
    plan = plan_study(bound.request, publications={base.strategy_fingerprint: base})
    assert len(plan.windows) > 1
    assert {window.reference_dataset_fingerprints for window in plan.windows} == {
        bound.request.reference_dataset_fingerprints
    }


def test_reference_free_study_documents_keep_their_identity() -> None:
    """Empty reference bindings never appear in market bindings or planned windows."""
    fingerprint = "sha256:" + "1" * 64
    market = MarketBinding(strategy_fingerprint=fingerprint, dataset_fingerprint=fingerprint)
    assert "reference_dataset_fingerprints" not in market.model_dump(mode="json")
    window = PlannedStudyWindow(
        label="full",
        role=WindowRole.FULL_WINDOW,
        fold_index=0,
        product_id="ETH-USD",
        timeframe="1h",
        strategy_fingerprint=fingerprint,
        dataset_fingerprint=fingerprint,
        evaluation_start=datetime(2025, 6, 1, tzinfo=UTC),
        evaluation_end=datetime(2025, 7, 1, tzinfo=UTC),
    )
    assert "reference_dataset_fingerprints" not in window.model_dump(mode="json")
