"""GET-only CFM account adapter (ADR 0127): allowlist, parsing, redacted failures."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import inspect
from typing import TYPE_CHECKING, Any

import pytest

from tests.exchanges import cfm_fixtures
from thytrader.exchanges import coinbase_cfm
from thytrader.exchanges.coinbase_cfm import (
    BALANCE_SUMMARY_PATH,
    CFM_GET_ALLOWLIST,
    MARGIN_SETTING_PATH,
    MARGIN_WINDOW_PATH,
    POSITIONS_PATH,
    CoinbaseCfmAccount,
)
from thytrader.exchanges.futures_models import FuturesAccountReadError, FuturesPositionSide
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class _GetOnly:
    """A transport with ``get`` only; there is no ``post`` to call."""

    def __init__(self, responses: dict[str, Callable[[], dict[str, Any]]]) -> None:
        """Map each path to a response factory (which may raise)."""
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Record and answer one GET."""
        self.calls.append((path, dict(params or {})))
        return self.responses[path]()


def _transport(**overrides: Callable[[], dict[str, Any]]) -> _GetOnly:
    """Documented responses for all four reads, with optional overrides."""
    responses: dict[str, Callable[[], dict[str, Any]]] = {
        BALANCE_SUMMARY_PATH: cfm_fixtures.balance_summary,
        POSITIONS_PATH: cfm_fixtures.positions,
        MARGIN_SETTING_PATH: cfm_fixtures.margin_setting,
        MARGIN_WINDOW_PATH: cfm_fixtures.margin_window,
    }
    responses.update({_PATHS[key]: value for key, value in overrides.items()})
    return _GetOnly(responses)


_PATHS = {
    "balance": BALANCE_SUMMARY_PATH,
    "positions": POSITIONS_PATH,
    "setting": MARGIN_SETTING_PATH,
    "window": MARGIN_WINDOW_PATH,
}


def test_allowlist_is_exactly_the_four_documented_get_paths() -> None:
    """No orders, preview, close_position, sweeps or margin-setting POST path is reachable."""
    assert (
        frozenset(
            {
                "/api/v3/brokerage/cfm/balance_summary",
                "/api/v3/brokerage/cfm/positions",
                "/api/v3/brokerage/cfm/intraday/margin_setting",
                "/api/v3/brokerage/cfm/intraday/current_margin_window",
            }
        )
        == CFM_GET_ALLOWLIST
    )
    source = inspect.getsource(coinbase_cfm)
    for forbidden in (".post(", ".delete(", "/orders", "preview", "close_position", "sweeps"):
        assert forbidden not in source.split('"""', 2)[2]
    public = {
        name
        for name, _ in inspect.getmembers(CoinbaseCfmAccount, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {
        "balance_summary",
        "positions",
        "intraday_margin_setting",
        "current_margin_window",
    }


def test_a_path_outside_the_allowlist_is_refused_before_any_request() -> None:
    """The guard runs before the transport is touched."""
    transport = _transport()
    account = CoinbaseCfmAccount(transport)
    with pytest.raises(FuturesAccountReadError) as caught:
        asyncio.run(account._get("orders", "/api/v3/brokerage/orders"))
    assert caught.value.reason == "path_not_allowlisted"
    assert transport.calls == []


def test_documented_responses_parse_exactly() -> None:
    """Every amount is an exact USD decimal; positions are in contracts."""
    transport = _transport()
    account = CoinbaseCfmAccount(transport)
    balance = asyncio.run(account.balance_summary())
    assert balance.cbi_usd_balance == Decimal("425.00")
    assert balance.cfm_usd_balance == Decimal("75.00")
    assert balance.funding_pnl == Decimal("-0.04")
    assert balance.liquidation_buffer_percentage == Decimal("1023.04")
    assert balance.overnight_margin is not None
    assert balance.overnight_margin.initial_margin == Decimal("75.10")
    (position,) = asyncio.run(account.positions())
    assert position.side is FuturesPositionSide.SHORT
    assert position.number_of_contracts == Decimal(1)
    assert position.expiration_time == datetime(2030, 12, 20, 16, tzinfo=UTC)
    assert asyncio.run(account.intraday_margin_setting()) == "INTRADAY_MARGIN_SETTING_STANDARD"
    window = asyncio.run(account.current_margin_window())
    assert window.margin_window_type == "MARGIN_WINDOW_TYPE_OVERNIGHT"
    assert window.intraday_killswitch_enabled is False
    assert transport.calls[-1] == (
        MARGIN_WINDOW_PATH,
        {"margin_profile_type": "MARGIN_PROFILE_TYPE_RETAIL_REGULAR"},
    )


def test_a_non_usd_amount_fails_the_read_instead_of_being_summed() -> None:
    """CFM amounts are USD; a USDC-labelled amount is refused, never mixed."""

    def usdc_balance() -> dict[str, Any]:
        payload = cfm_fixtures.balance_summary()
        payload["balance_summary"]["cfm_usd_balance"] = {"value": "75", "currency": "USDC"}
        return payload

    with pytest.raises(FuturesAccountReadError) as caught:
        asyncio.run(CoinbaseCfmAccount(_transport(balance=usdc_balance)).balance_summary())
    assert caught.value.reason == "non_usd_amount"


def test_unknown_amounts_stay_unknown() -> None:
    """A missing amount is ``None``, never zero."""

    def sparse() -> dict[str, Any]:
        payload = cfm_fixtures.balance_summary()
        del payload["balance_summary"]["funding_pnl"]
        payload["balance_summary"]["unrealized_pnl"] = {"value": "", "currency": "USD"}
        return payload

    balance = asyncio.run(CoinbaseCfmAccount(_transport(balance=sparse)).balance_summary())
    assert balance.funding_pnl is None
    assert balance.unrealized_pnl is None


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (CoinbaseHttpStatusError(401, "UNAUTHENTICATED"), "http_401"),
        (CoinbaseHttpStatusError(404, None), "http_404"),
        (TimeoutError("https://api.coinbase.com/secret"), "transport"),
    ],
)
def test_read_failures_are_redacted_tokens(failure: Exception, reason: str) -> None:
    """A failed read carries a short reason, never venue text or URLs."""

    def fail() -> dict[str, Any]:
        raise failure

    with pytest.raises(FuturesAccountReadError) as caught:
        asyncio.run(CoinbaseCfmAccount(_transport(balance=fail)).balance_summary())
    assert caught.value.reason == reason
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("override", "mutate"),
    [
        ("positions", lambda p: p["positions"][0].update(product_id="BTC-USD")),
        ("positions", lambda p: p["positions"][0].update(side="SIDEWAYS")),
        ("positions", lambda p: p["positions"][0].update(number_of_contracts="-1")),
        ("positions", lambda p: p.update(positions={})),
        ("balance", lambda p: p.update(balance_summary=[])),
        ("balance", lambda p: p["balance_summary"].update(initial_margin={"value": "x"})),
    ],
)
def test_malformed_payloads_fail_closed(
    override: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
    """One malformed field fails the whole read."""
    factory = cfm_fixtures.positions if override == "positions" else cfm_fixtures.balance_summary

    def broken() -> dict[str, Any]:
        payload = factory()
        mutate(payload)
        return payload

    account = CoinbaseCfmAccount(_transport(**{override: broken}))
    read = account.positions if override == "positions" else account.balance_summary
    with pytest.raises(FuturesAccountReadError):
        asyncio.run(read())
