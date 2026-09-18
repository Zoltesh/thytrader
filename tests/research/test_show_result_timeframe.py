"""HTTP show-result summaries copy the published strategy clock."""

from __future__ import annotations

import json
from unittest.mock import patch

from thytrader.agent_http import AgentHttpError
from thytrader.market_data.models import published_execution_timeframe
from thytrader.research.http import show_result

_RESULT_FINGERPRINT = "sha256:" + "r" * 64
_STRATEGY_FINGERPRINT = "sha256:" + "s" * 64
_RUN_FINGERPRINT = "sha256:" + "u" * 64
_DATASET_FINGERPRINT = "sha256:" + "d" * 64


def _summary_payload() -> dict[str, object]:
    """Return one bounded backtest summary body from detail=summary."""
    return {
        "result_fingerprint": _RESULT_FINGERPRINT,
        "strategy_fingerprint": _STRATEGY_FINGERPRINT,
        "run_fingerprint": _RUN_FINGERPRINT,
        "dataset_fingerprint": _DATASET_FINGERPRINT,
        "engine_contract_version": "thytrader-bar-backtest-v4",
        "summary": {"trade_count": 13},
    }


def _show_result_for_timeframe(timeframe: str, quote_currency: str = "USD") -> dict[str, object]:
    """Run show_result against canned detail and strategy-source JSON."""

    def fake_request_json(*, method: str, url: str, **_kwargs: object) -> dict[str, object]:
        del method
        if f"/api/v1/backtests/{_RESULT_FINGERPRINT}" in url and "detail=summary" in url:
            return _summary_payload()
        if url.endswith(f"/api/v1/strategies/source/{_STRATEGY_FINGERPRINT}"):
            return {
                "strategy": {
                    "timeframe": timeframe,
                    "instrument": {"quote_currency": quote_currency},
                }
            }
        message = f"unexpected research HTTP request: {url}"
        raise AssertionError(message)

    with patch("thytrader.research.http.request_json", side_effect=fake_request_json):
        return json.loads(show_result("http://127.0.0.1:8000", _RESULT_FINGERPRINT))


def test_published_execution_timeframe_keeps_venue_clocks() -> None:
    """2h and 4h stay themselves; unknown tokens still fall back to 1h."""
    assert published_execution_timeframe("2h") == "2h"
    assert published_execution_timeframe("4h") == "4h"
    assert published_execution_timeframe("1h") == "1h"
    assert published_execution_timeframe("5m") == "5m"
    assert published_execution_timeframe("not-a-clock") == "1h"


def test_show_result_reports_two_hour_strategy_clock() -> None:
    """UNI 2h results must not collapse to 1h."""
    payload = _show_result_for_timeframe("2h")
    assert payload["timeframe"] == "2h"
    assert payload["mode"] == "backtest"


def test_show_result_reports_four_hour_strategy_clock() -> None:
    """DOGE 4h results must not collapse to 1h."""
    payload = _show_result_for_timeframe("4h")
    assert payload["timeframe"] == "4h"


def test_show_result_reports_one_hour_strategy_clock() -> None:
    """BTC 1h remains 1h."""
    payload = _show_result_for_timeframe("1h")
    assert payload["timeframe"] == "1h"


def test_show_result_copies_usdc_quote_currency_from_published_strategy() -> None:
    """AAVE-USDC results must be labeled USDC, never a USD default."""
    payload = _show_result_for_timeframe("1h", quote_currency="USDC")
    assert payload["currency"] == "USDC"
    assert payload["timeframe"] == "1h"


def test_show_result_keeps_usd_quote_currency() -> None:
    """BTC-USD results stay USD."""
    payload = _show_result_for_timeframe("1h", quote_currency="USD")
    assert payload["currency"] == "USD"


def test_show_result_falls_back_to_usd_when_source_is_unavailable() -> None:
    """A missing strategy source keeps the historical USD fallback."""

    def missing_source(*, method: str, url: str, **_kwargs: object) -> dict[str, object]:
        del method
        if f"/api/v1/backtests/{_RESULT_FINGERPRINT}" in url and "detail=summary" in url:
            return _summary_payload()
        raise AgentHttpError("HTTP 404: strategy source was not found.")

    with patch("thytrader.research.http.request_json", side_effect=missing_source):
        payload = json.loads(show_result("http://127.0.0.1:8000", _RESULT_FINGERPRINT))
    assert payload["currency"] == "USD"
    assert payload["timeframe"] == "1h"
