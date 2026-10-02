"""CLI tests for the read-only operator command."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from tests.http_fakes import (
    json_urlopen_response,
    matching_ready_payload,
    stale_ready_payload,
    urlopen_ready_then,
)
from thytrader import __version__
from thytrader.operator.cli import main
from thytrader.operator.models import (
    SCHEMA_VERSION,
    STANDARD_REDACTION,
    HealthPayload,
    HealthReport,
    ReportStatus,
    current_ops_contract,
)
from thytrader.ops_contract import EXPECTED_SCHEMA_REVISION, OPS_CONTRACT_ID


def test_operator_help_describes_read_only_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """Operators can discover commands without a database."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "cancel orders" in output
    assert "health" in output
    assert "support-bundle" in output
    assert "runtime" in output
    assert "schema-check" in output
    assert "data-catalog" in output
    assert "products" in output
    assert "indicators" in output
    assert "monitor" in output
    assert "studies" in output
    assert "chat-status" in output
    assert "trade-reasons" in output
    assert "portfolio" in output
    assert "fees" in output
    assert "loopback HTTP" in output or "--local" in output


_FAKE_KEY_NAME = "organizations/test-org/apiKeys/test-key-name"
_FAKE_PRIVATE_KEY = (
    "-----BEGIN EC PRIVATE KEY-----\\nhermetic-test-only\\n-----END EC PRIVATE KEY-----"
)


class _StubResponse:
    """Minimal SDK response wrapper for the stubbed Coinbase REST client."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Keep one scripted payload."""
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        """Return the scripted payload like the SDK's response objects."""
        return self._payload


class _StubCoinbaseRestClient:
    """Hermetic stand-in for ``coinbase.rest.RESTClient``; it never opens a socket."""

    instances: ClassVar[list[_StubCoinbaseRestClient]] = []

    def __init__(self, *, api_key: str, api_secret: str, timeout: int) -> None:
        """Record construction so the test proves the credentialed path ran."""
        del api_secret, timeout
        self.api_key = api_key
        self.calls: list[str] = []
        _StubCoinbaseRestClient.instances.append(self)

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> _StubResponse:
        """Return one page with a USD cash balance and a BTC holding."""
        del limit, cursor
        self.calls.append("get_accounts")
        return _StubResponse(
            {
                "accounts": [
                    {
                        "currency": "USD",
                        "active": True,
                        "available_balance": {"value": "1234.5", "currency": "USD"},
                        "hold": {"value": "0", "currency": "USD"},
                    },
                    {
                        "currency": "BTC",
                        "active": True,
                        "available_balance": {"value": "0.5", "currency": "BTC"},
                        "hold": {"value": "0", "currency": "BTC"},
                    },
                ],
                "has_next": False,
            }
        )

    def get_api_key_permissions(self) -> _StubResponse:
        """Report view and trade permissions."""
        self.calls.append("get_api_key_permissions")
        return _StubResponse({"can_view": True, "can_trade": True, "can_transfer": False})

    def get_product(self, product_id: str) -> _StubResponse:
        """Return a fixed last price for any product."""
        self.calls.append(f"get_product:{product_id}")
        return _StubResponse({"product_id": product_id, "price": "60000"})

    def get_transaction_summary(self, **kwargs: object) -> _StubResponse:
        """Return one fee tier and 30-day volume."""
        del kwargs
        self.calls.append("get_transaction_summary")
        return _StubResponse(
            {
                "fee_tier": {
                    "pricing_tier": "Advanced 1",
                    "maker_fee_rate": "0.004",
                    "taker_fee_rate": "0.006",
                },
                "total_volume": "1500",
            }
        )


@pytest.fixture
def stub_coinbase(monkeypatch: pytest.MonkeyPatch) -> type[_StubCoinbaseRestClient]:
    """Configure fake credentials and route the operator session to the stubbed client."""
    _StubCoinbaseRestClient.instances = []
    monkeypatch.setenv("THYTRADER_COINBASE_API_KEY_NAME", _FAKE_KEY_NAME)
    monkeypatch.setenv("THYTRADER_COINBASE_API_PRIVATE_KEY", _FAKE_PRIVATE_KEY)
    monkeypatch.setattr("thytrader.operator.session.RESTClient", _StubCoinbaseRestClient)
    return _StubCoinbaseRestClient


def test_operator_local_portfolio_includes_balances_without_secrets(
    capsys: pytest.CaptureFixture[str],
    stub_coinbase: type[_StubCoinbaseRestClient],
) -> None:
    """Portfolio reports stubbed Coinbase balances and never echoes credential material."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "portfolio"])
    assert raised.value.code in {0, 1, 2}
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["report_kind"] == "portfolio"
    assert payload["redaction"]["secrets_redacted"] is True
    assert payload["redaction"]["balances_omitted"] is False
    total = payload["payload"]["total_value"]
    assert "amount" in total
    assert total["currency"] in {"USD", "USDC", "USDT"}
    assert stub_coinbase.instances
    assert "get_accounts" in stub_coinbase.instances[-1].calls
    assert "1234.5" in output
    assert _FAKE_KEY_NAME not in output
    assert "hermetic-test-only" not in output


def test_operator_local_fees_include_suggested_research_rates(
    capsys: pytest.CaptureFixture[str],
    stub_coinbase: type[_StubCoinbaseRestClient],
) -> None:
    """Fees is a versioned report matching the HTTP fee-profile contract."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "fees"])
    assert raised.value.code in {0, 1, 2}
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["report_kind"] == "fees"
    fees = payload["payload"]
    assert "maker_fee_rate" in fees
    assert "taker_fee_rate" in fees
    assert _FAKE_KEY_NAME not in output
    assert stub_coinbase.instances


def test_operator_local_indicators_and_products_are_healthy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Indicators and the demo product catalog do not require PostgreSQL."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "indicators"])
    assert raised.value.code == 0
    indicators = json.loads(capsys.readouterr().out)
    assert indicators["report_kind"] == "indicators"
    kinds = {item["kind"] for item in indicators["payload"]["indicators"]}
    assert kinds == {
        "ema",
        "sma",
        "rsi",
        "atr",
        "volume_sma",
        "highest",
        "lowest",
        "stdev",
        "stdev_sample",
        "roc",
        "williams_r",
        "cci",
        "wma",
        "momentum",
        "mfi",
        "macd",
        "bollinger",
        "stochastic",
        "adx",
        "identity",
        "constant",
        "dema",
        "tema",
        "hma",
        "kama",
        "vwma",
        "supertrend",
        "parabolic_sar",
        "aroon",
        "ichimoku",
        "vortex",
        "linear_regression",
        "trix",
        "stochastic_rsi",
        "ppo",
        "ultimate_oscillator",
        "awesome_oscillator",
        "cmo",
        "tsi",
        "keltner",
        "donchian",
        "bollinger_percent_b",
        "bollinger_bandwidth",
        "natr",
        "choppiness",
        "historical_volatility",
        "obv",
        "cmf",
        "accumulation_distribution",
        "vwap",
        "force_index",
        "zscore",
        "percent_rank",
    }
    by_kind = {item["kind"]: item for item in indicators["payload"]["indicators"]}
    assert by_kind["highest"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["lowest"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["stdev"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["stdev_sample"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["highest"]["period_max"] == 500
    assert by_kind["stdev"]["period_min"] == 2
    assert by_kind["roc"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["roc"]["period_max"] == 500
    assert by_kind["williams_r"]["inputs"] == ["high", "low", "close"]
    assert by_kind["williams_r"]["period_max"] == 100
    assert by_kind["cci"]["inputs"] == ["high", "low", "close"]
    assert by_kind["cci"]["period_max"] == 100
    assert by_kind["wma"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["wma"]["period_max"] == 500
    assert by_kind["momentum"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["momentum"]["period_max"] == 500
    assert by_kind["mfi"]["inputs"] == ["high", "low", "close", "volume"]
    assert by_kind["mfi"]["period_max"] == 100
    assert by_kind["macd"]["inputs"] == ["close"]
    assert by_kind["macd"]["parameter_kind"] == "macd"
    assert by_kind["macd"]["outputs"] == ["macd", "signal", "histogram"]
    assert by_kind["bollinger"]["inputs"] == ["close"]
    assert by_kind["bollinger"]["parameter_kind"] == "bollinger"
    assert by_kind["bollinger"]["outputs"] == ["middle", "upper", "lower"]
    assert by_kind["stochastic"]["inputs"] == ["high", "low", "close"]
    assert by_kind["stochastic"]["parameter_kind"] == "stochastic"
    assert by_kind["stochastic"]["outputs"] == ["k", "d"]
    assert by_kind["adx"]["inputs"] == ["high", "low", "close"]
    assert by_kind["adx"]["outputs"] == ["adx", "plus_di", "minus_di"]
    assert by_kind["adx"]["period_max"] == 100
    assert by_kind["identity"]["inputs"] == ["open", "high", "low", "close", "volume"]
    assert by_kind["identity"]["parameter_kind"] == "none"
    assert by_kind["identity"]["period_min"] is None
    assert by_kind["constant"]["inputs"] == []
    assert by_kind["constant"]["parameter_kind"] == "value"
    assert by_kind["constant"]["supports_offset"] is False
    assert by_kind["supertrend"]["outputs"] == ["value", "direction"]
    assert by_kind["supertrend"]["category"] == "trend"
    assert by_kind["supertrend"]["inputs"] == ["high", "low", "close"]
    assert [item["name"] for item in by_kind["supertrend"]["parameters"]] == [
        "period",
        "multiplier",
    ]
    assert by_kind["supertrend"]["parameters"][1]["default"] == "3"
    assert by_kind["ichimoku"]["outputs"] == ["tenkan", "kijun", "senkou_a", "senkou_b"]
    assert by_kind["ichimoku"]["default_warmup_bars"] == 52
    assert by_kind["donchian"]["inputs"] == ["high", "low"]
    assert by_kind["obv"]["inputs"] == ["close", "volume"]
    assert by_kind["historical_volatility"]["parameters"][1]["optional"] is True
    assert {item["category"] for item in indicators["payload"]["indicators"]} == {
        "trend",
        "momentum",
        "volatility",
        "volume",
        "statistical",
        "price",
    }

    with pytest.raises(SystemExit) as raised:
        main(["--local", "products"])
    products = json.loads(capsys.readouterr().out)
    assert products["report_kind"] == "products"
    assert raised.value.code in {0, 1, 2}
    if products["payload"].get("provider") == "demo" and raised.value.code == 0:
        assert {item["product_id"] for item in products["payload"]["products"]} >= {
            "BTC-USD",
            "ETH-USD",
            "SOL-USD",
        }


def test_operator_health_emits_json_and_nonzero_without_stack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Health JSON is still a v1 report; a running local stack may be healthy."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "health"])
    assert raised.value.code in {0, 1, 2}
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["report_kind"] == "health"
    if raised.value.code == 0:
        assert payload["overall_status"] == "healthy"
        assert payload["payload"]["ops_contract"]["id"] == OPS_CONTRACT_ID
    else:
        assert payload["overall_status"] in {"degraded", "failed"}


def test_operator_rejects_non_loopback_base_url() -> None:
    """HTTP mode must refuse a remote origin instead of falling back to Postgres."""
    with pytest.raises(SystemExit) as raised:
        main(["--base-url", "http://example.com", "health"])
    assert raised.value.code != 0
    assert "loopback" in str(raised.value).lower()


def test_operator_http_failure_does_not_fall_back_to_local_stores() -> None:
    """An unreachable API must not silently open process stores."""
    with pytest.raises(SystemExit) as raised:
        main(["--base-url", "http://127.0.0.1:1", "health"])
    assert raised.value.code != 0
    assert "unreachable" in str(raised.value).lower()


def test_operator_accepts_format_after_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Parent flags such as --format may follow the subcommand."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "health", "--format", "json"])
    assert raised.value.code in {0, 1, 2}
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_kind"] == "health"


def test_operator_rejects_local_and_base_url_together() -> None:
    """HTTP and store-backed modes are exclusive."""
    with pytest.raises(SystemExit) as raised:
        main(["--local", "--base-url", "http://127.0.0.1:8200", "health"])
    assert raised.value.code != 0
    assert "either" in str(raised.value).lower()


def test_operator_http_version_mismatch_fails_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A running API from an older image must fail before printing a report."""
    report = HealthReport(
        application_version="0.0.0",
        generated_at=datetime.now(UTC),
        overall_status=ReportStatus.DEGRADED,
        components=(),
        redaction=STANDARD_REDACTION,
        recommended_next_action="Rebuild and restart with `make run`.",
        payload=HealthPayload(
            api_probed=True,
            database_configured=False,
            coinbase_credentials_configured=False,
            ops_contract=current_ops_contract(),
            applied_schema_revision=EXPECTED_SCHEMA_REVISION,
        ),
    )
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(
                matching_ready_payload(),
                report.model_dump(mode="json"),
            ),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["health"])
    captured = capsys.readouterr()
    assert "make run" in str(raised.value)
    assert captured.out == ""


@pytest.mark.parametrize("command", ["health", "data-catalog", "configuration"])
def test_operator_http_stale_ops_contract_fails_closed(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A healthy 0.1.0 API without the ops contract is a stale Compose image."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(stale_ready_payload()),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main([command])
    captured = capsys.readouterr()
    message = str(raised.value).lower()
    assert "ops contract" in message
    assert "make run" in message
    assert captured.out == ""


def test_operator_http_matching_ops_contract_does_not_hint_rebuild(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A current API that advertises this checkout's contract must stay silent on stderr."""
    report = HealthReport(
        application_version=__version__,
        generated_at=datetime.now(UTC),
        overall_status=ReportStatus.HEALTHY,
        components=(),
        redaction=STANDARD_REDACTION,
        recommended_next_action="No action required.",
        payload=HealthPayload(
            api_probed=True,
            database_configured=False,
            coinbase_credentials_configured=False,
            ops_contract=current_ops_contract(),
            applied_schema_revision=EXPECTED_SCHEMA_REVISION,
        ),
    )
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(
                matching_ready_payload(),
                report.model_dump(mode="json"),
            ),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["health"])
    captured = capsys.readouterr()
    assert raised.value.code == 0
    assert captured.err == ""
    assert json.loads(captured.out)["payload"]["ops_contract"]["id"] == OPS_CONTRACT_ID


def test_operator_schema_check_passes_in_this_checkout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """schema-check is the compatibility gate between skills and SCHEMA_VERSION."""
    with pytest.raises(SystemExit) as raised:
        main(["schema-check"])
    assert raised.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "runtime" in payload["report_kinds"]
    assert "monitor" in payload["report_kinds"]


def test_operator_chat_status_is_http_only_and_omits_the_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """chat-status is not --local and never echoes an LLM secret."""
    with pytest.raises(SystemExit) as local_rejected:
        main(["--local", "chat-status"])
    assert "HTTP-only" in str(local_rejected.value)
    status = {
        "schema_version": "thytrader-operator-chat-v1",
        "llm_configured": True,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "key_storage": "api_process",
        "coinbase_credentials_in_chat": False,
    }
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(matching_ready_payload(), status),
        ),
        pytest.raises(SystemExit) as raised,
    ):
        main(["chat-status"])
    captured = capsys.readouterr()
    assert raised.value.code == 0
    payload = json.loads(captured.out)
    assert payload["llm_configured"] is True
    assert payload["coinbase_credentials_in_chat"] is False
    assert "api_key" not in payload


def test_operator_data_catalog_timeout_names_the_call_instead_of_failing_generically(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A slow data-catalog read says it timed out instead of "failed safely"."""
    ready = matching_ready_payload()

    def slow_catalog(request: object, timeout: object = None) -> object:
        url = request if isinstance(request, str) else getattr(request, "full_url", "")
        if str(url).endswith("/health/ready"):
            del timeout
            return json_urlopen_response(ready)
        raise TimeoutError("timed out")

    with (
        patch("thytrader.agent_http.urlopen", side_effect=slow_catalog),
        pytest.raises(SystemExit) as raised,
    ):
        main(["data-catalog"])
    message = str(raised.value)
    assert "Timed out after" in message
    assert "/api/v1/operator/data-catalog" in message
    assert "failed safely" not in message
    assert capsys.readouterr().out == ""


def test_operator_local_portfolios_degrades_without_a_database(
    capsys: pytest.CaptureFixture[str],
    stub_coinbase: type[_StubCoinbaseRestClient],
) -> None:
    """Without PostgreSQL the portfolios report is degraded, never an empty success."""
    del stub_coinbase
    with pytest.raises(SystemExit) as raised:
        main(["--local", "portfolios"])
    assert raised.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_kind"] == "portfolios"
    assert payload["payload"]["portfolio_storage"] == "unavailable"
    assert payload["payload"]["portfolio_backtest_contract"] == "thytrader-portfolio-backtest-v1"
    assert payload["components"][0]["reason_code"] == "PORTFOLIO_STORAGE_UNAVAILABLE"


def test_operator_data_catalog_dropped_connection_names_the_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A connection the API drops mid-read is reported as such, with a retry instruction."""
    ready = matching_ready_payload()

    def dropping_catalog(request: object, timeout: object = None) -> object:
        del timeout
        url = request if isinstance(request, str) else getattr(request, "full_url", "")
        if str(url).endswith("/health/ready"):
            return json_urlopen_response(ready)
        raise ConnectionResetError(104, "Connection reset by peer")

    with (
        patch("thytrader.agent_http.urlopen", side_effect=dropping_catalog),
        pytest.raises(SystemExit) as raised,
    ):
        main(["data-catalog"])
    message = str(raised.value)
    assert "closed the connection before answering GET /api/v1/operator/data-catalog" in message
    assert "retry the read" in message
    assert "failed safely" not in message
    assert capsys.readouterr().out == ""


def test_operator_report_schema_mismatch_names_the_field() -> None:
    """A report the CLI cannot validate names the failing field instead of "failed safely"."""
    ready = matching_ready_payload()

    def drifted_catalog(request: object, timeout: object = None) -> object:
        del timeout
        url = request if isinstance(request, str) else getattr(request, "full_url", "")
        if str(url).endswith("/health/ready"):
            return json_urlopen_response(ready)
        return json_urlopen_response({"schema_version": "thytrader-operator-report-v1"})

    with (
        patch("thytrader.agent_http.urlopen", side_effect=drifted_catalog),
        pytest.raises(SystemExit) as raised,
    ):
        main(["data-catalog"])
    message = str(raised.value)
    assert message.startswith("Operator diagnostics failed: a payload did not match")
    assert "Trading state was not changed." in message
    assert "make run" in message
    assert "failed safely" not in message
