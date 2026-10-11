"""Bounded portfolio mark reads retain failure, absence and cancellation semantics."""

import asyncio
from decimal import Decimal
import threading
from typing import Any

import pytest
from requests import HTTPError, Response

from tests.exchanges.test_coinbase import StubResponse
from tests.portfolio.test_batch_pricing import BatchClient
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailureKind
from thytrader.portfolio.service import PortfolioService


class OmittedClient(BatchClient):
    """A listing omits one market without proving it does not exist."""

    def __init__(self, status: int | None) -> None:
        """Select the independent lookup's evidence."""
        super().__init__()
        self.status = status

    def get_products(self, **kwargs: Any) -> StubResponse:
        """Return no matches while retaining the request counter."""
        super().get_products(**kwargs)
        return StubResponse({"products": []})

    def get_product(self, product_id: str) -> StubResponse:
        """Only an explicit 404 can establish a missing market."""
        self.singles.append(product_id)
        if self.status is not None:
            response = Response()
            response.status_code = self.status
            raise HTTPError("synthetic upstream text", response=response)
        return StubResponse({"product_id": product_id, "price": "3.25"})


@pytest.mark.parametrize("status", [None, 404, 401, 403, 429, 500])
def test_batch_omission_requires_individual_evidence(status: int | None) -> None:
    """Missing listing rows are neither zero nor proof of a product 404."""
    client = OmittedClient(status)
    unsupported: set[str] = set()
    adapter = CoinbaseAccount(client, unsupported_products=unsupported)
    if status not in {None, 404}:
        with pytest.raises(ExchangeReadError) as caught:
            asyncio.run(adapter.get_usd_prices(("COIN", "COIN")))
        assert caught.value.failure.http_status == status
        assert "synthetic upstream text" not in str(caught.value)
        assert unsupported == set()
    else:
        expected = None if status == 404 else Decimal("3.25")
        assert asyncio.run(adapter.get_usd_prices(("COIN", "COIN"))) == {"COIN": expected}
        assert unsupported == ({"COIN-USD"} if status == 404 else set())
    assert client.singles == ["COIN-USD"]
    assert client.batches == [["COIN-USD"]]
    if status == 404:
        assert asyncio.run(adapter.get_usd_prices(("COIN",))) == {"COIN": None}
        assert client.batches == [["COIN-USD"]]
        assert client.singles == ["COIN-USD"]


@pytest.mark.parametrize(
    "products",
    [
        None,
        {},
        [None],
        [{}],
        [{"product_id": "COIN-USD", "price": value} for value in ("1", "2")],
        [{"product_id": "OTHER-USD", "price": "2"}],
        *[
            [{"product_id": "COIN-USD", "price": value}]
            for value in (None, 2.5, "", "NaN", "sNaN", "Infinity", "-Infinity", "0", "-1")
        ],
    ],
)
def test_malformed_batch_fails_instead_of_undervaluing(products: Any) -> None:
    """Dynamic JSON fixtures exercise the strict SDK boundary, not domain floats."""

    class MalformedClient(BatchClient):
        """Inject malformed untrusted SDK JSON."""

        def get_products(self, **kwargs: Any) -> StubResponse:
            """Return a bad listing without allowing silent single-read fallback."""
            super().get_products(**kwargs)
            return StubResponse({"products": products})

    client = MalformedClient()
    with pytest.raises(ExchangeReadError) as caught:
        asyncio.run(CoinbaseAccount(client).get_usd_prices(("COIN",)))
    assert caught.value.failure.kind is ExchangeReadFailureKind.INVALID_RESPONSE
    assert client.singles == []


def test_empty_currency_set_does_not_fetch_products() -> None:
    """Cash-only portfolios need no venue price call."""
    client = BatchClient()
    assert asyncio.run(CoinbaseAccount(client).get_usd_prices(())) == {}
    assert client.batches == client.singles == []


def test_cancelled_portfolio_does_not_start_remaining_batches() -> None:
    """One already-running SDK read can finish; cancellation schedules no more work."""
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingClient(BatchClient):
        """A real thread barrier proves no concurrent shared-session burst."""

        def get_products(self, **kwargs: Any) -> StubResponse:
            """Hold the first request while the event loop cancels the portfolio."""
            result = super().get_products(**kwargs)
            entered.set()
            try:
                assert release.wait(timeout=5)
                return result
            finally:
                finished.set()

    client = BlockingClient()

    async def exercise() -> None:
        """Bound readiness and cleanup so failed tests cannot strand a worker."""
        task = asyncio.create_task(PortfolioService(CoinbaseAccount(client)).get_portfolio())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert not task.done()
            assert len(client.batches) == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert await asyncio.to_thread(finished.wait, 5)

    asyncio.run(exercise())
    assert len(client.batches) == 1
    assert client.singles == []
