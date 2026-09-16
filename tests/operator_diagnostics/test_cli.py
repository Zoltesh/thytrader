"""CLI tests for the read-only operator command."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from unittest.mock import patch

import pytest

from tests.http_fakes import matching_ready_payload, stale_ready_payload, urlopen_ready_then
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
    assert "chat-status" in output
    assert "loopback HTTP" in output or "--local" in output


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
