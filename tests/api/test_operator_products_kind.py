"""Operator ``products --kind`` (ADR 0126): spot bytes unchanged, futures read-only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.exchanges.coinbase_futures_catalog import (
    CoinbaseFuturesCatalogError,
    parse_futures_row,
)
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService

if TYPE_CHECKING:
    from thytrader.market_data.instruments import FuturesProduct

_GOLDEN = (
    Path(__file__).parents[1] / "operator_diagnostics" / "golden" / "products_spot_payload.json"
)
_FIXTURE = Path(__file__).parents[1] / "exchanges" / "fixtures" / "coinbase_futures_listing.json"


class _Futures:
    """Serve the verbatim fixture listing, or fail like the adapter's fail-closed error."""

    def __init__(self, *, fail: bool = False) -> None:
        """Choose success or failure."""
        self.fail = fail

    async def list_futures_products(self) -> tuple[FuturesProduct, ...]:
        """Return the eight FCM fixture rows."""
        if self.fail:
            raise CoinbaseFuturesCatalogError("listing unavailable")
        payload: dict[str, Any] = json.loads(_FIXTURE.read_text())
        parsed = (parse_futures_row(row) for row in payload["products"])
        return tuple(product for product in parsed if product is not None)


def _get(path: str, futures: _Futures | None = None) -> dict[str, Any]:
    """GET one operator route on a demo app, optionally with a futures listing."""
    service = MarketDataService(DemoMarketData(), futures_provider=futures)
    app = create_app(Settings(_env_file=None), market_data_service=service)
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _stable(payload: dict[str, Any]) -> str:
    """Serialize a payload compactly with the observation instant masked."""
    masked = dict(payload)
    masked["catalog_observed_at"] = "<observed_at>"
    return json.dumps(masked, separators=(",", ":")) + "\n"


def test_default_and_explicit_spot_payloads_are_byte_identical_to_before() -> None:
    """The golden was captured from main before ``--kind`` existed."""
    golden = _GOLDEN.read_text()
    for path in ("/api/v1/operator/products", "/api/v1/operator/products?kind=spot"):
        body = _get(path, _Futures())
        assert _stable(body["payload"]) == golden
        assert [c["name"] for c in body["components"]] == ["products"]


def test_future_kind_lists_contracts_read_only_without_spot_rows() -> None:
    """Futures rows carry contract facts, are never orderable, and spot is not listed."""
    body = _get("/api/v1/operator/products?kind=future", _Futures())
    payload = body["payload"]
    assert body["overall_status"] == "healthy"
    assert payload["kind"] == "future"
    assert payload["products"] == []
    by_id = {row["product_id"]: row for row in payload["futures"]}
    assert len(by_id) == 8
    bip = by_id["BIP-20DEC30-CDE"]
    assert bip["orderable"] is False
    assert (bip["kind"], bip["underlying"], bip["contract_size"]) == (
        "perpetual_future",
        "BTC",
        "0.01",
    )
    assert bip["expires_at"] is None
    assert bip["listed_expiry"] == "2030-12-20"
    assert bip["funding_rate"] == "0.000009"
    assert by_id["BIT-30OCT26-CDE"]["expires_at"] == "2026-10-30T16:00:00Z"
    assert by_id["US5-19DEC30-CDE"]["twenty_four_by_seven"] is False
    assert payload["futures_catalog_fingerprint"].startswith("sha256:")


def test_all_kind_keeps_the_spot_rows_and_adds_futures() -> None:
    """``all`` is the unchanged spot catalog plus the futures listing."""
    body = _get("/api/v1/operator/products?kind=all", _Futures())
    payload = body["payload"]
    assert len(payload["products"]) == 4
    assert len(payload["futures"]) == 8
    assert [c["name"] for c in body["components"]] == ["products", "futures_products"]


def test_unconfigured_or_failing_futures_listing_is_never_an_empty_success() -> None:
    """Demo mode is DEGRADED; a failing listing is FAILED; neither claims zero contracts."""
    demo = _get("/api/v1/operator/products?kind=future")
    assert demo["overall_status"] == "degraded"
    assert demo["components"][0]["reason_code"] == "FUTURES_CATALOG_UNCONFIGURED"
    failing = _get("/api/v1/operator/products?kind=future", _Futures(fail=True))
    assert failing["overall_status"] == "failed"
    assert failing["components"][0]["reason_code"] == "FUTURES_CATALOG_UNAVAILABLE"
    assert failing["payload"]["futures"] == []
