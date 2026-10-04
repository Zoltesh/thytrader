"""Read failures cross the Coinbase boundary as safe operation-specific evidence."""

import asyncio
from dataclasses import replace

import pytest
from requests import ConnectionError as RequestConnectionError, HTTPError, Response, Timeout

from tests.exchanges.test_coinbase import StubCoinbaseClient, StubResponse
from tests.operator_diagnostics.test_service import _diagnostics
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailureKind
from thytrader.operator.models import ReportStatus
from thytrader.portfolio.service import PortfolioService


class _FailedAccountClient(StubCoinbaseClient):
    """Raise one scripted transport failure instead of opening a network connection."""

    def __init__(self, error: Exception) -> None:
        """Select the read failure under test."""
        super().__init__()
        self.error = error

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> StubResponse:
        """Fail the first read without echoing the request's secret-rich error."""
        del limit, cursor
        raise self.error


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (Timeout("secret-token"), ExchangeReadFailureKind.TIMEOUT),
        (RequestConnectionError("secret-token"), ExchangeReadFailureKind.NETWORK),
        (ValueError("secret-token"), ExchangeReadFailureKind.INVALID_RESPONSE),
    ],
)
def test_account_failure_is_typed_and_redacted(
    error: Exception, kind: ExchangeReadFailureKind
) -> None:
    """Timeout, connection and response failures retain their safe category."""
    adapter = CoinbaseAccount(_FailedAccountClient(error))
    with pytest.raises(ExchangeReadError) as raised:
        asyncio.run(adapter.list_balances())
    assert raised.value.failure.operation == "balances"
    assert raised.value.failure.kind is kind
    assert "secret-token" not in str(raised.value)


def test_exchange_and_health_reports_show_http_operation_without_error_body() -> None:
    """Both shipped reports show status and operation while raw transport data stays private."""
    response = Response()
    response.status_code = 503
    response._content = b'{"error":"secret-token"}'
    error = HTTPError("Authorization=secret-token", response=response)
    portfolio = PortfolioService(CoinbaseAccount(_FailedAccountClient(error)))
    diagnostics = replace(_diagnostics(), portfolio=portfolio)
    report = asyncio.run(diagnostics.exchange())
    assert report.overall_status is ReportStatus.FAILED
    assert report.payload.failure is not None
    assert report.payload.failure.http_status == 503
    assert report.payload.failure.operation == "balances"
    assert "secret-token" not in report.model_dump_json()
    health = asyncio.run(diagnostics.health())
    component = next(item for item in health.components if item.name == "exchange")
    assert "balances" in component.detail
    assert "HTTP 503" in component.detail
