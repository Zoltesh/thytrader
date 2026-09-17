"""Default evaluation windows shrink to common LTF+HTF coverage when dates are omitted."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import cast

import pytest

from thytrader.backtest.submission import (
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    _with_evaluation_window,
)
from thytrader.market_data.datasets import DatasetManifest, DatasetStore, DatasetStoreError
from thytrader.research.multi_timeframe import (
    earliest_evaluation_start_for_closed_bar,
    htf_required_coverage,
    latest_evaluation_end_for_closed_bar,
)
from thytrader.research.publication import dataset_evaluation_bounds
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy

_LTF_FINGERPRINT = "sha256:" + "b" * 64
_HTF_FINGERPRINT = "sha256:" + "c" * 64


def _store(manifests: dict[str, DatasetManifest]) -> DatasetStore:
    """Return a load_manifest double as DatasetStore.

    Window fill only reads manifests. Casting is required because DatasetStore is a
    concrete class, not a protocol.
    """
    return cast("DatasetStore", _ManifestStore(manifests))


class _ManifestStore:
    """Return canned manifests without reading Parquet."""

    def __init__(self, manifests: dict[str, DatasetManifest]) -> None:
        """Index manifests by content fingerprint."""
        self._manifests = manifests

    def load_manifest(self, content_fingerprint: str) -> DatasetManifest:
        """Return one canned manifest or fail as a missing artifact."""
        try:
            return self._manifests[content_fingerprint]
        except KeyError as error:
            raise DatasetStoreError("missing dataset") from error


def _htf_strategy(*, decision_timeframe: str, htf_timeframe: str) -> StrategyDefinition:
    """Return a long-only document with an HTF SMA filter on one extra clock."""
    payload = cast(
        "dict[str, object]",
        json.loads(Path("tests/strategies/golden/reference_strategy_v1.json").read_text()),
    )
    payload["timeframe"] = decision_timeframe
    payload["data_requirements"] = {
        "warmup_bars": 2,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    payload["indicators"] = [
        {"id": "sma", "kind": "sma", "input": "close", "parameters": {"period": 2}},
        {
            "id": "atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
        },
    ]
    payload["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"literal": "1"},
                    "operator": "greater_than_or_equal",
                    "right": {"literal": "0"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    payload["exits"] = {
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "2"},
        "take_profit": {"kind": "reward_risk", "multiple": "2"},
        "trailing_stop": {"enabled": False},
        "time_exit": {"max_bars_held": 96},
    }
    payload["htf_filter"] = {
        "timeframe": htf_timeframe,
        "data_requirements": {
            "warmup_bars": 2,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {"id": "htf_sma", "kind": "sma", "input": "close", "parameters": {"period": 2}},
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_sma"},
                    "operator": "greater_than",
                    "right": {"literal": "20"},
                }
            ]
        },
    }
    return StrategyDefinition.model_validate(payload)


def _published(strategy: StrategyDefinition) -> PublishedStrategy:
    """Wrap one validated definition as a publication."""
    return PublishedStrategy(
        strategy_fingerprint=strategy_fingerprint(strategy),
        definition=strategy,
    )


def _manifest(
    *,
    timeframe: str,
    fingerprint: str,
    starts_at: str,
    ends_at: str,
) -> DatasetManifest:
    """Return verified-manifest facts for one BTC-USD clock."""
    return DatasetManifest(
        provider="coinbase",
        product_id="BTC-USD",
        timeframe=timeframe,
        starts_at=starts_at,
        ends_at=ends_at,
        expected_candle_count=4,
        received_candle_count=4,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=fingerprint,
        files=(Path("unused.parquet"),),
        manifest_path=Path("unused.json"),
    )


def _omitted_request() -> BacktestSubmissionRequest:
    """Build one HTF submission that leaves evaluation dates to the server."""
    return BacktestSubmissionRequest.model_validate(
        {
            "strategy_fingerprint": "sha256:" + "a" * 64,
            "dataset_fingerprint": _LTF_FINGERPRINT,
            "htf_dataset_fingerprint": _HTF_FINGERPRINT,
            "initial_quote_balance": "10000",
            "maker_fee_rate": "0.001",
            "taker_fee_rate": "0.002",
            "fixed_slippage_bps": "10",
        }
    )


def test_closed_bar_default_bounds_match_known_htf_coverage() -> None:
    """5m/1h last-completed mapping is the inverse of the eligibility coverage check."""
    start = earliest_evaluation_start_for_closed_bar(
        dataset_starts_at=datetime(2026, 7, 10, 8, tzinfo=UTC),
        timeframe="1h",
        warmup_bars=2,
        decision_timeframe="5m",
    )
    end = latest_evaluation_end_for_closed_bar(
        dataset_ends_at=datetime(2026, 7, 10, 12, tzinfo=UTC),
        timeframe="1h",
        decision_timeframe="5m",
    )
    assert start == datetime(2026, 7, 10, 10, tzinfo=UTC)
    assert end == datetime(2026, 7, 10, 12, 55, tzinfo=UTC)
    strategy = _htf_strategy(decision_timeframe="5m", htf_timeframe="1h")
    assert strategy.htf_filter is not None
    required_start, required_end = htf_required_coverage(
        evaluation_starts_at=start,
        evaluation_ends_at=end,
        htf_filter=strategy.htf_filter,
    )
    assert required_start == datetime(2026, 7, 10, 8, tzinfo=UTC)
    assert required_end == datetime(2026, 7, 10, 12, tzinfo=UTC)


def test_omitted_bounds_use_common_ltf_htf_intersection() -> None:
    """A shorter HTF island must shrink the LTF-only default instead of 422."""
    strategy = _htf_strategy(decision_timeframe="5m", htf_timeframe="1h")
    published = _published(strategy)
    ltf = _manifest(
        timeframe="5m",
        fingerprint=_LTF_FINGERPRINT,
        starts_at="2026-07-01T00:00:00Z",
        ends_at="2026-09-01T00:00:00Z",
    )
    htf = _manifest(
        timeframe="1h",
        fingerprint=_HTF_FINGERPRINT,
        starts_at="2026-07-10T08:00:00Z",
        ends_at="2026-07-10T12:00:00Z",
    )
    ltf_start, ltf_end = dataset_evaluation_bounds(
        dataset_starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        dataset_ends_at=datetime(2026, 9, 1, tzinfo=UTC),
        warmup_bars=2,
        timeframe="5m",
    )
    filled = _with_evaluation_window(
        _omitted_request(),
        published,
        _store({_LTF_FINGERPRINT: ltf, _HTF_FINGERPRINT: htf}),
    )
    assert filled.evaluation_start == datetime(2026, 7, 10, 10, tzinfo=UTC)
    assert filled.evaluation_end == datetime(2026, 7, 10, 12, 55, tzinfo=UTC)
    assert filled.evaluation_start != ltf_start
    assert filled.evaluation_end != ltf_end


def test_omitted_bounds_keep_ltf_window_when_htf_fully_covers() -> None:
    """HTF coverage that contains the LTF usable window must not shrink it."""
    strategy = _htf_strategy(decision_timeframe="5m", htf_timeframe="1h")
    published = _published(strategy)
    ltf = _manifest(
        timeframe="5m",
        fingerprint=_LTF_FINGERPRINT,
        starts_at="2026-07-10T10:40:00Z",
        ends_at="2026-07-10T12:10:00Z",
    )
    htf = _manifest(
        timeframe="1h",
        fingerprint=_HTF_FINGERPRINT,
        starts_at="2026-07-01T00:00:00Z",
        ends_at="2026-08-01T00:00:00Z",
    )
    expected_start, expected_end = dataset_evaluation_bounds(
        dataset_starts_at=datetime(2026, 7, 10, 10, 40, tzinfo=UTC),
        dataset_ends_at=datetime(2026, 7, 10, 12, 10, tzinfo=UTC),
        warmup_bars=2,
        timeframe="5m",
    )
    filled = _with_evaluation_window(
        _omitted_request(),
        published,
        _store({_LTF_FINGERPRINT: ltf, _HTF_FINGERPRINT: htf}),
    )
    assert filled.evaluation_start == expected_start
    assert filled.evaluation_end == expected_end


def test_omitted_two_hour_six_hour_intersection() -> None:
    """2h UNI-style LTF with a later 6h HTF island defaults to the overlap."""
    strategy = _htf_strategy(decision_timeframe="2h", htf_timeframe="6h")
    published = _published(strategy)
    ltf = _manifest(
        timeframe="2h",
        fingerprint=_LTF_FINGERPRINT,
        starts_at="2026-01-01T00:00:00Z",
        ends_at="2026-09-17T04:00:00Z",
    )
    htf = _manifest(
        timeframe="6h",
        fingerprint=_HTF_FINGERPRINT,
        starts_at="2026-07-02T00:00:00Z",
        ends_at="2026-09-17T00:00:00Z",
    )
    filled = _with_evaluation_window(
        _omitted_request(),
        published,
        _store({_LTF_FINGERPRINT: ltf, _HTF_FINGERPRINT: htf}),
    )
    assert filled.evaluation_start == datetime(2026, 7, 2, 12, tzinfo=UTC)
    assert filled.evaluation_end == datetime(2026, 9, 17, 2, tzinfo=UTC)
    assert filled.evaluation_start is not None
    assert filled.evaluation_end is not None
    assert strategy.htf_filter is not None
    required_start, required_end = htf_required_coverage(
        evaluation_starts_at=filled.evaluation_start,
        evaluation_ends_at=filled.evaluation_end,
        htf_filter=strategy.htf_filter,
    )
    assert required_start == datetime(2026, 7, 2, tzinfo=UTC)
    assert required_end == datetime(2026, 9, 17, tzinfo=UTC)


def test_omitted_disjoint_htf_coverage_rejects() -> None:
    """No overlapping last-completed window is still a caller rejection."""
    strategy = _htf_strategy(decision_timeframe="5m", htf_timeframe="1h")
    published = _published(strategy)
    ltf = _manifest(
        timeframe="5m",
        fingerprint=_LTF_FINGERPRINT,
        starts_at="2026-07-01T00:00:00Z",
        ends_at="2026-09-01T00:00:00Z",
    )
    htf = _manifest(
        timeframe="1h",
        fingerprint=_HTF_FINGERPRINT,
        starts_at="2026-01-01T00:00:00Z",
        ends_at="2026-02-01T00:00:00Z",
    )
    with pytest.raises(BacktestSubmissionRejectedError, match="no common evaluation window"):
        _with_evaluation_window(
            _omitted_request(),
            published,
            _store({_LTF_FINGERPRINT: ltf, _HTF_FINGERPRINT: htf}),
        )


def test_supplied_bounds_beyond_htf_still_reject() -> None:
    """Explicit dates that HTF cannot cover stay fail-closed."""
    strategy = _htf_strategy(decision_timeframe="5m", htf_timeframe="1h")
    published = _published(strategy)
    ltf = _manifest(
        timeframe="5m",
        fingerprint=_LTF_FINGERPRINT,
        starts_at="2026-07-01T00:00:00Z",
        ends_at="2026-09-01T00:00:00Z",
    )
    htf = _manifest(
        timeframe="1h",
        fingerprint=_HTF_FINGERPRINT,
        starts_at="2026-07-10T08:00:00Z",
        ends_at="2026-07-10T12:00:00Z",
    )
    request = _omitted_request().model_copy(
        update={
            "evaluation_start": datetime(2026, 7, 1, 1, tzinfo=UTC),
            "evaluation_end": datetime(2026, 8, 1, tzinfo=UTC),
        }
    )
    with pytest.raises(
        BacktestSubmissionRejectedError,
        match="not fully covered by the HTF dataset",
    ):
        _with_evaluation_window(
            request,
            published,
            _store({_LTF_FINGERPRINT: ltf, _HTF_FINGERPRINT: htf}),
        )
