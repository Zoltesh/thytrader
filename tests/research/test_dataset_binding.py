"""Unit tests for binding omitted research datasets to the newest catalog revision."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import cast

import pytest

from thytrader.backtest.submission import BacktestStartRequest
from thytrader.market_data.datasets import DatasetManifest, DatasetStore
from thytrader.research.dataset_binding import (
    DatasetNeed,
    DatasetResolver,
    DatasetsMissingError,
    bind_backtest_datasets,
)
from thytrader.strategies.models import StrategyDefinition

_REFERENCE = Path(__file__).parents[1] / "strategies" / "golden" / "reference_strategy_v1.json"
_STRATEGY_ID = "01985cf0-7b60-7000-8000-000000000001"


def _fingerprint(product_id: str, timeframe: str, provider: str = "demo") -> str:
    """Return a distinct deterministic fingerprint for one cataloged clock."""
    return "sha256:" + sha256(f"{provider}|{product_id}|{timeframe}".encode()).hexdigest()


def _manifest(product_id: str, timeframe: str, provider: str = "demo") -> DatasetManifest:
    """Return one complete catalog row."""
    return DatasetManifest(
        provider=provider,
        product_id=product_id,
        timeframe=timeframe,
        starts_at="2025-01-01T00:00:00Z",
        ends_at="2026-01-01T00:00:00Z",
        expected_candle_count=1,
        received_candle_count=1,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=_fingerprint(product_id, timeframe, provider),
        files=(Path("unused.parquet"),),
        manifest_path=Path("unused.json"),
    )


class _Catalog:
    """A dataset store double that only lists the latest catalog rows."""

    def __init__(self, manifests: tuple[DatasetManifest, ...]) -> None:
        """Remember the rows and count listings."""
        self._manifests = manifests
        self.listings = 0

    def list_latest_verified(self) -> tuple[DatasetManifest, ...]:
        """Return the canned catalog."""
        self.listings += 1
        return self._manifests


def _resolver(catalog: _Catalog) -> DatasetResolver:
    """Resolve against the demo provider, as a credential-free install does."""
    return DatasetResolver(store=cast("DatasetStore", catalog), provider="demo")


def _rich_strategy() -> StrategyDefinition:
    """Return BTC-USD 1h with a 1d HTF filter, a 4h extra clock, and ETH-USD lockstep."""
    payload = cast("dict[str, object]", json.loads(_REFERENCE.read_text(encoding="utf-8")))
    indicators = cast("list[dict[str, object]]", payload["indicators"])
    indicators.append(
        {
            "id": "ema_4h",
            "kind": "ema",
            "input": "close",
            "parameters": {"period": 5},
            "timeframe": "4h",
        }
    )
    payload["htf_filter"] = {
        "timeframe": "1d",
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
                    "right": {"literal": "1"},
                }
            ]
        },
    }
    payload["additional_instruments"] = [
        {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
    ]
    return StrategyDefinition.model_validate(payload)


def _start(**fields: object) -> BacktestStartRequest:
    """Return a backtest start with costs and any explicit dataset fields."""
    return BacktestStartRequest.model_validate(
        {
            "strategy_id": _STRATEGY_ID,
            "initial_quote_balance": "10000",
            "maker_fee_rate": "0.004",
            "taker_fee_rate": "0.006",
            "fixed_slippage_bps": "5",
            **fields,
        }
    )


def _full_catalog(*extra: DatasetManifest) -> _Catalog:
    """Catalog every clock the rich strategy needs, plus any extra rows."""
    rows = tuple(
        _manifest(product, timeframe)
        for product in ("BTC-USD", "ETH-USD")
        for timeframe in ("1h", "4h", "1d")
    )
    return _Catalog((*rows, *extra))


def test_bind_backtest_datasets_fills_every_omitted_clock() -> None:
    """Primary, HTF, extra clock, and the additional instrument all bind from the catalog."""
    catalog = _full_catalog(_manifest("BTC-USD", "1h", provider="coinbase"))
    resolver = _resolver(catalog)

    bound = bind_backtest_datasets(_start(), _rich_strategy(), resolver)

    assert bound.dataset_fingerprint == _fingerprint("BTC-USD", "1h")
    assert bound.htf_dataset_fingerprint == _fingerprint("BTC-USD", "1d")
    assert [
        (item.timeframe, item.dataset_fingerprint) for item in bound.indicator_dataset_fingerprints
    ] == [("4h", _fingerprint("BTC-USD", "4h"))]
    [eth] = bound.additional_instrument_datasets
    assert eth.product_id == "ETH-USD"
    assert eth.dataset_fingerprint == _fingerprint("ETH-USD", "1h")
    assert eth.htf_dataset_fingerprint == _fingerprint("ETH-USD", "1d")
    assert [item.timeframe for item in eth.indicator_dataset_fingerprints] == ["4h"]
    assert {(item.product_id, item.timeframe, item.role) for item in resolver.bindings()} == {
        ("BTC-USD", "1h", "decision"),
        ("BTC-USD", "1d", "filter"),
        ("BTC-USD", "4h", "indicator"),
        ("ETH-USD", "1h", "decision"),
        ("ETH-USD", "1d", "filter"),
        ("ETH-USD", "4h", "indicator"),
    }
    assert {item.source for item in resolver.bindings()} == {"latest_catalog"}
    assert catalog.listings == 1


def test_bind_backtest_datasets_keeps_explicit_fingerprints_and_fills_the_rest() -> None:
    """An explicit primary dataset is used as sent; only the omitted clocks are bound."""
    explicit = "sha256:" + "e" * 64
    resolver = _resolver(_full_catalog())

    bound = bind_backtest_datasets(_start(dataset_fingerprint=explicit), _rich_strategy(), resolver)

    assert bound.dataset_fingerprint == explicit
    assert bound.htf_dataset_fingerprint == _fingerprint("BTC-USD", "1d")
    sources = {(item.timeframe, item.product_id): item.source for item in resolver.bindings()}
    assert sources[("1h", "BTC-USD")] == "request"
    assert sources[("1d", "BTC-USD")] == "latest_catalog"


def test_bind_backtest_datasets_lists_every_missing_clock_with_the_commands() -> None:
    """Missing clocks fail closed together, naming the watch-add and ingest commands."""
    catalog = _Catalog(
        tuple(
            row
            for row in _full_catalog().list_latest_verified()
            if (row.product_id, row.timeframe) not in {("ETH-USD", "1d"), ("BTC-USD", "4h")}
        )
    )

    with pytest.raises(DatasetsMissingError) as raised:
        bind_backtest_datasets(_start(), _rich_strategy(), _resolver(catalog))

    assert raised.value.missing == (
        DatasetNeed("BTC-USD", "4h", "indicator"),
        DatasetNeed("ETH-USD", "1d", "filter"),
    )
    message = str(raised.value)
    assert "No complete demo dataset is cataloged for BTC-USD 4h (indicator clock)" in message
    assert (
        "`uv run thytrader-data watch-add --product-id ETH-USD --timeframe 1d --confirm`" in message
    )
    assert "`uv run thytrader-data ingest --product-id BTC-USD --timeframe 4h --confirm`" in message


def test_explicit_fingerprints_never_list_the_catalog() -> None:
    """A fully explicit start keeps working without touching the dataset catalog."""
    catalog = _Catalog(())
    resolver = _resolver(catalog)
    reference = StrategyDefinition.model_validate_json(_REFERENCE.read_text(encoding="utf-8"))
    explicit = "sha256:" + "a" * 64

    bound = bind_backtest_datasets(_start(dataset_fingerprint=explicit), reference, resolver)

    assert bound.dataset_fingerprint == explicit
    assert catalog.listings == 0
    assert [item.source for item in resolver.bindings()] == ["request"]


def test_submission_still_requires_a_bound_primary_dataset() -> None:
    """The exact internal submission cannot be built from an unbound start."""
    with pytest.raises(ValueError, match="dataset_fingerprint"):
        _start().submission("sha256:" + "f" * 64)
