"""Transient account GET recovery is bounded and never returns a partial balance book."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from requests import HTTPError, JSONDecodeError, Response, Timeout

from tests.exchanges.test_coinbase import StubCoinbaseClient, StubResponse
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.read_errors import ExchangeReadError

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def instant_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove wall-clock backoff from deterministic transport-failure tests."""

    async def no_wait(seconds: float) -> None:
        """Yield no network or clock dependency while preserving async call shape."""
        del seconds

    monkeypatch.setattr(asyncio, "sleep", no_wait)


class _ScriptedClient(StubCoinbaseClient):
    """Fail selected balance pages while retaining every attempted cursor."""

    def __init__(self, outcomes: Iterator[Exception | None]) -> None:
        """Bind a finite scripted transport sequence."""
        super().__init__()
        self.outcomes = outcomes
        self.attempted_cursors: list[str | None] = []

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> StubResponse:
        """Raise the scripted error or return the normal validated page."""
        self.attempted_cursors.append(cursor)
        error = next(self.outcomes)
        if error is not None:
            raise error
        return StubResponse(super().get_accounts(limit=limit, cursor=cursor).to_dict())


def test_failed_page_recovers_without_duplicate_balances() -> None:
    """A second-page timeout retries that page rather than restarting or duplicating cash."""
    client = _ScriptedClient(iter([None, Timeout("secret-token"), None]))
    balances = asyncio.run(CoinbaseAccount(client).list_balances())
    assert client.attempted_cursors == [None, "next-page", "next-page"]
    assert len(balances) == 2
    assert len({balance.currency for balance in balances}) == 2


def test_exhausted_page_failure_never_returns_partial_balances() -> None:
    """A failed retry leaves the whole account unavailable, despite a successful first page."""
    client = _ScriptedClient(iter([None, Timeout("secret-token"), Timeout("secret-token")]))
    with pytest.raises(ExchangeReadError) as raised:
        asyncio.run(CoinbaseAccount(client).list_balances())
    assert raised.value.failure.attempts == 2
    assert client.attempted_cursors == [None, "next-page", "next-page"]
    assert "secret-token" not in str(raised.value)


def test_invalid_provider_json_is_not_retried_as_a_network_failure() -> None:
    """The SDK's JSONDecodeError is also a RequestException, but remains a response error."""
    client = _ScriptedClient(iter([JSONDecodeError("secret-token", "secret-body", 0)]))
    with pytest.raises(ExchangeReadError) as raised:
        asyncio.run(CoinbaseAccount(client).list_balances())
    assert raised.value.failure.kind == "invalid_response"
    assert raised.value.failure.attempts == 1
    assert client.attempted_cursors == [None]
    assert "secret-body" not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "attempts"),
    [(401, 1), (403, 1), (404, 1), (429, 1), (500, 1), (502, 2), (503, 2), (504, 2)],
)
def test_http_retry_does_not_repeat_authentication_or_rate_limits(
    status: int, attempts: int
) -> None:
    """Only gateway/service transients repeat; definitive responses fail immediately."""
    response = Response()
    response.status_code = status
    error = HTTPError("secret-token", response=response)
    client = _ScriptedClient(iter([error, error]))
    with pytest.raises(ExchangeReadError) as raised:
        asyncio.run(CoinbaseAccount(client).list_balances())
    assert len(client.attempted_cursors) == attempts
    assert raised.value.failure.attempts == attempts
    assert raised.value.failure.http_status == status
