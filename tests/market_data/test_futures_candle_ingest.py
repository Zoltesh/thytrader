"""Futures candles in the data lane (ADR 0126, P0-4): reads, manifests, expiry, admission."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.config import Settings
from thytrader.data_control.models import DataControlError
from thytrader.data_control.service import add_watch_target
from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.exchanges.coinbase_market_data import CoinbaseMarketData, CoinbaseMarketDataError
from thytrader.market_data.datasets import DatasetStore, DatasetStoreError
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService
from thytrader.market_data.watchlist import (
    InMemoryMarketDataWatchlistStore,
    MarketDataWatchTarget,
)
from thytrader.market_data_worker.targets import _cycle_targets

if TYPE_CHECKING:
    from thytrader.market_data.instruments import FuturesProduct

_FIXTURE = Path(__file__).parents[1] / "exchanges" / "fixtures" / "coinbase_futures_listing.json"
_START = datetime(2026, 10, 9, 0, tzinfo=UTC)
_NOW = datetime(2026, 10, 10, 1, tzinfo=UTC)


def _rows() -> list[dict[str, Any]]:
    """Return the verbatim public-listing rows."""
    payload: dict[str, Any] = json.loads(_FIXTURE.read_text())
    rows: list[dict[str, Any]] = payload["products"]
    return rows


class _Futures:
    """Futures provider double over the fixture."""

    async def list_futures_products(self) -> tuple[FuturesProduct, ...]:
        """Return the eight FCM rows."""
        parsed = (parse_futures_row(row) for row in _rows())
        return tuple(product for product in parsed if product is not None)


class _Response:
    """SDK response double."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Hold one payload."""
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        """Return the payload."""
        return self._payload


class _FuturesCandleClient:
    """Serve the BIP listing row and three hourly candles; volume is in contracts."""

    def get_products(
        self,
        limit: int | None = None,
        offset: int | None = None,
        product_type: str | None = None,
        product_ids: list[str] | None = None,
        contract_expiry_type: str | None = None,
        expiring_contract_status: str | None = None,
        get_tradability_status: bool | None = False,
        get_all_products: bool | None = False,
    ) -> _Response:
        """Unused by range reads."""
        del contract_expiry_type, expiring_contract_status, get_tradability_status
        del get_all_products
        raise AssertionError((limit, offset, product_type, product_ids))

    def get_product(self, product_id: str) -> _Response:
        """Return the verbatim futures row for the requested id."""
        return _Response(next(row for row in _rows() if row["product_id"] == product_id))

    def get_candles(
        self, product_id: str, start: str, end: str, granularity: str, limit: int | None = None
    ) -> _Response:
        """Return the three hourly candles of the fixture window."""
        del product_id, end, granularity, limit
        first = int(start)
        candles = [
            {
                "start": str(first + 3600 * hour),
                "open": "82000",
                "high": "82500",
                "low": "81900",
                "close": "82400",
                "volume": "1250",
            }
            for hour in range(3)
        ]
        return _Response({"candles": candles})


def test_futures_range_is_verified_with_the_futures_parser() -> None:
    """A futures id reads candles; its product row is checked by the FCM parser."""
    report = asyncio.run(
        CoinbaseMarketData(_FuturesCandleClient()).get_historical_range(
            "BIP-20DEC30-CDE",
            CandleInterval.ONE_HOUR,
            _START,
            _START + timedelta(hours=3),
            now=_NOW,
        )
    )
    assert report.complete is True
    assert report.quality.candles[0].volume == Decimal(1250)


def test_spot_id_with_a_futures_row_still_fails_the_spot_parser() -> None:
    """The spot parser is never loosened: a futures row behind a spot id fails closed."""

    class _Mislabelled(_FuturesCandleClient):
        def get_product(self, product_id: str) -> _Response:
            """Return the BIP row under any id."""
            del product_id
            return super().get_product("BIP-20DEC30-CDE")

    with pytest.raises(CoinbaseMarketDataError):
        asyncio.run(
            CoinbaseMarketData(_Mislabelled()).get_historical_range(
                "BTC-USD", CandleInterval.ONE_HOUR, _START, _START + timedelta(hours=3), now=_NOW
            )
        )


def _report() -> Any:
    """Three complete hourly candles."""
    candles = tuple(
        Candle(
            starts_at=_START + timedelta(hours=hour),
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(90),
            close=Decimal(105),
            volume=Decimal(7),
        )
        for hour in range(3)
    )
    return analyze_range(
        candles, CandleInterval.ONE_HOUR, _START, _START + timedelta(hours=3), now=_NOW
    )


def test_futures_manifest_says_contracts_and_spot_manifest_bytes_do_not_change(
    tmp_path: Path,
) -> None:
    """Only a futures manifest carries ``volume_unit``; both round-trip through verification."""
    store = DatasetStore(tmp_path)
    future = store.write("coinbase", "BIP-20DEC30-CDE", _report())
    spot = store.write("coinbase", "BTC-USD", _report())
    future_payload = json.loads(future.manifest_path.read_text())
    spot_payload = json.loads(spot.manifest_path.read_text())
    assert future_payload["volume_unit"] == "contracts"
    assert sorted(spot_payload) == sorted(
        [
            "schema_version",
            "provider",
            "product_id",
            "timeframe",
            "starts_at",
            "ends_at",
            "expected_candle_count",
            "received_candle_count",
            "gap_count",
            "missing_intervals",
            "complete",
            "content_fingerprint",
            "files",
        ]
    )
    # Same rows, so the content fingerprint formula ignores the unit and the product binds it.
    assert future.content_fingerprint != spot.content_fingerprint
    assert len(DatasetStore(tmp_path).load_candles(future.content_fingerprint)) == 3
    assert len(DatasetStore(tmp_path).load_candles(spot.content_fingerprint)) == 3


def test_a_futures_manifest_without_its_unit_fails_verification(tmp_path: Path) -> None:
    """Removing the unit from a futures manifest makes the dataset unusable."""
    manifest = DatasetStore(tmp_path).write("coinbase", "BIP-20DEC30-CDE", _report())
    payload = json.loads(manifest.manifest_path.read_text())
    del payload["volume_unit"]
    manifest.manifest_path.write_text(json.dumps(payload))
    with pytest.raises(DatasetStoreError, match="volume unit"):
        DatasetStore(tmp_path).load_candles(manifest.content_fingerprint)


def _target(product_id: str) -> MarketDataWatchTarget:
    """One enabled hourly watch."""
    return MarketDataWatchTarget(
        provider="coinbase",
        product_id=product_id,
        timeframe=CandleInterval.ONE_HOUR,
        lookback_hours=24,
        enabled=True,
        updated_at=_NOW,
    )


def test_expired_futures_watch_is_retired_and_skipped() -> None:
    """After its listed day a dated contract's watch is disabled and not ingested."""

    async def exercise() -> None:
        """Plan one cycle after BIT-30OCT26 expired."""
        watchlist = InMemoryMarketDataWatchlistStore()
        for product_id in ("BIT-30OCT26-CDE", "BIP-20DEC30-CDE", "BTC-USD"):
            await watchlist.upsert(_target(product_id))
        after = datetime(2026, 10, 31, 0, 1, tzinfo=UTC)
        due = await _cycle_targets(
            watchlist,
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            lookback_hours=24,
            now=after,
        )
        assert {target.product_id for target in due} == {"BIP-20DEC30-CDE", "BTC-USD"}
        stored = {t.product_id: t.enabled for t in await watchlist.list_all()}
        assert stored == {"BIT-30OCT26-CDE": False, "BIP-20DEC30-CDE": True, "BTC-USD": True}
        before = await _cycle_targets(
            watchlist,
            provider="coinbase",
            product_id="BTC-USD",
            timeframe=CandleInterval.ONE_HOUR,
            lookback_hours=24,
            now=datetime(2026, 10, 30, 23, tzinfo=UTC),
        )
        assert "BIT-30OCT26-CDE" not in {t.product_id for t in before}

    asyncio.run(exercise())


def _watch(product_id: str, service: MarketDataService, now: datetime = _NOW) -> None:
    """Run the data-lane watch admission once."""
    asyncio.run(
        add_watch_target(
            store=InMemoryMarketDataWatchlistStore(),
            market_data=service,
            audit=InMemoryAuditEventStore(),
            settings=Settings(_env_file=None),
            product_id=product_id,
            timeframe="1h",
            lookback_hours=24,
            enabled=True,
            now=now,
        )
    )


def test_watch_admission_accepts_24_7_futures_and_refuses_sessions_and_expiry() -> None:
    """24/7 contracts are watchable; session-limited and expired ones are refused."""
    service = MarketDataService(DemoMarketData(), futures_provider=_Futures())
    _watch("BIP-20DEC30-CDE", service)
    _watch("GOL-25NOV26-CDE", service)
    with pytest.raises(DataControlError, match="INSTRUMENT_SESSIONS_UNSUPPORTED"):
        _watch("US5-19DEC30-CDE", service)
    with pytest.raises(DataControlError, match="expired"):
        _watch("BIT-30OCT26-CDE", service, now=datetime(2026, 11, 1, tzinfo=UTC))
    with pytest.raises(DataControlError, match="not an enabled Coinbase futures contract"):
        _watch("BIP-20DEC30-CDE", MarketDataService(DemoMarketData()))
