"""Venue REST calls are counted and timed per bound scope at the SDK HTTP session (ADR 0131)."""

from __future__ import annotations

import asyncio

from coinbase.rest import RESTClient
import pytest
import requests
from requests.adapters import HTTPAdapter
from requests.models import PreparedRequest, Response

from thytrader.exchanges.request_timing import (
    TimedHTTPAdapter,
    endpoint_shape,
    instrument_rest_client,
)
from thytrader.observability.venue_calls import (
    VenueCallLedger,
    record_venue_call,
    venue_call_scope,
)

pytestmark = pytest.mark.anyio


def _prepared(method: str, url: str) -> PreparedRequest:
    """Build one prepared request without sending it."""
    return requests.Request(method, url).prepare()


def _response(status: int) -> Response:
    """Return a bare response with only a status code."""
    response = Response()
    response.status_code = status
    return response


def test_endpoint_shape_redacts_identifiers_and_query() -> None:
    """Product, order and account ids become ``{id}``; query strings are dropped."""
    base = "https://api.coinbase.com/api/v3/brokerage"
    assert endpoint_shape(f"{base}/products/BTC-USDC/candles?start=1&end=2") == (
        "/products/{id}/candles"
    )
    assert endpoint_shape(f"{base}/products/BTC-USDC") == "/products/{id}"
    assert endpoint_shape(f"{base}/orders/historical/0f1e2d3c-aaaa-bbbb-cccc-1234567890ab") == (
        "/orders/historical/{id}"
    )
    assert endpoint_shape(f"{base}/orders/historical/batch?product_ids=X") == (
        "/orders/historical/batch"
    )
    assert endpoint_shape(f"{base}/orders/batch_cancel") == "/orders/batch_cancel"
    assert endpoint_shape(None) == "/"


def test_ledger_summarizes_costliest_endpoints_first() -> None:
    """Totals, errors and per-endpoint maxima are kept; the slowest endpoint leads."""
    ledger = VenueCallLedger()
    ledger.record("GET", "/products/{id}", 0.1, ok=True)
    ledger.record("GET", "/products/{id}/candles", 0.4, ok=True)
    ledger.record("GET", "/products/{id}/candles", 0.2, ok=False)
    summary = ledger.summary()
    assert summary.requests == 3
    assert summary.errors == 1
    assert summary.seconds == pytest.approx(0.7)
    assert summary.max_seconds == pytest.approx(0.4)
    assert summary.endpoints[0].endpoint == "/products/{id}/candles"
    assert summary.endpoints[0].requests == 2
    assert summary.endpoints[0].errors == 1
    assert ledger.totals().since(ledger.totals()).requests == 0


def test_recording_without_a_bound_ledger_is_a_no_op() -> None:
    """Calls outside a cycle scope are not attributed anywhere."""
    ledger = VenueCallLedger()
    record_venue_call("GET", "/products", 0.1, ok=True)
    with venue_call_scope(ledger):
        record_venue_call("GET", "/products", 0.1, ok=True)
    record_venue_call("GET", "/products", 0.1, ok=True)
    assert ledger.totals().requests == 1


async def test_calls_on_worker_threads_land_in_the_issuing_scope() -> None:
    """``asyncio.to_thread`` copies the context, so SDK threads record into the cycle."""
    ledger = VenueCallLedger()

    def blocking_call() -> None:
        record_venue_call("GET", "/accounts", 0.05, ok=True)

    with venue_call_scope(ledger):
        await asyncio.to_thread(blocking_call)
    await asyncio.to_thread(blocking_call)
    assert ledger.totals().requests == 1


def test_timed_adapter_records_success_http_error_and_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every request is recorded once, failed when it raises or returns 4xx/5xx."""
    outcomes: list[Response | OSError] = [
        _response(200),
        _response(429),
        requests.ConnectionError("reset"),
    ]

    def fake_send(self: HTTPAdapter, request: PreparedRequest, **kwargs: object) -> Response:
        del self, request, kwargs
        outcome = outcomes.pop(0)
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    adapter = TimedHTTPAdapter()
    ledger = VenueCallLedger()
    url = "https://api.coinbase.com/api/v3/brokerage/products/ETH-USDC"
    with venue_call_scope(ledger):
        assert adapter.send(_prepared("GET", url)).status_code == 200
        assert adapter.send(_prepared("GET", url)).status_code == 429
        with pytest.raises(requests.ConnectionError):
            adapter.send(_prepared("POST", url))
    summary = ledger.summary()
    assert summary.requests == 3
    assert summary.errors == 2
    assert {(row.method, row.endpoint) for row in summary.endpoints} == {
        ("GET", "/products/{id}"),
        ("POST", "/products/{id}"),
    }


def test_instrument_rest_client_mounts_the_timing_adapter_on_https() -> None:
    """The official SDK client's session routes Coinbase HTTPS through the timing adapter."""
    client = RESTClient(timeout=1)
    instrument_rest_client(client)
    adapter = client.session.get_adapter("https://api.coinbase.com/api/v3/brokerage/products")
    assert isinstance(adapter, TimedHTTPAdapter)
    instrument_rest_client(object())
