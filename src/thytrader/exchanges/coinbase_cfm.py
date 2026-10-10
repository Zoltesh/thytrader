"""Read-only Coinbase CFM (US futures) account adapter (ADR 0127, slice P0-5).

The adapter can only GET, and only these four paths:

- ``/api/v3/brokerage/cfm/balance_summary``
- ``/api/v3/brokerage/cfm/positions``
- ``/api/v3/brokerage/cfm/intraday/margin_setting``
- ``/api/v3/brokerage/cfm/intraday/current_margin_window``

Its transport type exposes ``get`` alone, and every request passes the allowlist check
before it is sent. It never calls ``orders``, ``orders/preview`` (a P1-3 probe),
``close_position``, sweeps or the margin-setting POST. Response text and URLs never reach
an error message; failures carry a short reason token.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

from thytrader.exchanges.futures_models import (
    FUTURES_ACCOUNT_CURRENCY,
    FuturesAccountReadError,
    FuturesBalanceSummary,
    FuturesMarginMeasure,
    FuturesMarginWindow,
    FuturesPosition,
    FuturesPositionSide,
)
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.market_data.instrument_ids import is_futures_product_id

if TYPE_CHECKING:
    from collections.abc import Mapping

_PREFIX = "/api/v3/brokerage/cfm"
BALANCE_SUMMARY_PATH = f"{_PREFIX}/balance_summary"
POSITIONS_PATH = f"{_PREFIX}/positions"
MARGIN_SETTING_PATH = f"{_PREFIX}/intraday/margin_setting"
MARGIN_WINDOW_PATH = f"{_PREFIX}/intraday/current_margin_window"
CFM_GET_ALLOWLIST: frozenset[str] = frozenset(
    {BALANCE_SUMMARY_PATH, POSITIONS_PATH, MARGIN_SETTING_PATH, MARGIN_WINDOW_PATH}
)
# Documented enum value for a regular retail account; intraday enrollment is not assumed.
MARGIN_PROFILE_TYPE = "MARGIN_PROFILE_TYPE_RETAIL_REGULAR"
_AMOUNT_FIELDS = (
    "futures_buying_power",
    "total_usd_balance",
    "cbi_usd_balance",
    "cfm_usd_balance",
    "total_open_orders_hold_amount",
    "unrealized_pnl",
    "daily_realized_pnl",
    "initial_margin",
    "available_margin",
    "liquidation_threshold",
    "liquidation_buffer_amount",
    "total_pending_transfers_amount",
    "funding_pnl",
)
_SIDES = {
    "LONG": FuturesPositionSide.LONG,
    "SHORT": FuturesPositionSide.SHORT,
    "UNKNOWN": FuturesPositionSide.UNKNOWN,
}


class GetOnlyTransport(Protocol):
    """The only transport capability this adapter receives: a signed JSON GET."""

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """GET one REST path and return the JSON object body."""
        ...


class CoinbaseCfmAccount:
    """Read the CFM balance summary, positions and margin window through GETs only."""

    def __init__(self, transport: GetOnlyTransport) -> None:
        """Bind a signed transport; only its ``get`` is ever called."""
        self._transport = transport

    async def balance_summary(self) -> FuturesBalanceSummary:
        """Read and validate the balance summary; every amount must be USD."""
        payload = await self._get("balance_summary", BALANCE_SUMMARY_PATH)
        summary = _object(payload.get("balance_summary"), "balance_summary")
        amounts = {field: _amount(summary, field) for field in _AMOUNT_FIELDS}
        return FuturesBalanceSummary(
            **amounts,
            liquidation_buffer_percentage=_decimal(summary, "liquidation_buffer_percentage"),
            intraday_margin=_measure(summary.get("intraday_margin_window_measure")),
            overnight_margin=_measure(summary.get("overnight_margin_window_measure")),
        )

    async def positions(self) -> tuple[FuturesPosition, ...]:
        """Read every open position; a malformed row fails the whole read."""
        payload = await self._get("positions", POSITIONS_PATH)
        raw = payload.get("positions", [])
        if not isinstance(raw, list):
            raise FuturesAccountReadError("positions", "malformed")
        return tuple(_position(_object(row, "positions")) for row in raw)

    async def intraday_margin_setting(self) -> str:
        """Read the intraday margin setting token (``INTRADAY_MARGIN_SETTING_STANDARD``)."""
        payload = await self._get("margin_setting", MARGIN_SETTING_PATH)
        setting = payload.get("setting")
        if not isinstance(setting, str) or not setting:
            raise FuturesAccountReadError("margin_setting", "malformed")
        return setting

    async def current_margin_window(self) -> FuturesMarginWindow:
        """Read which margin window is in effect for a regular retail profile."""
        payload = await self._get(
            "margin_window", MARGIN_WINDOW_PATH, {"margin_profile_type": MARGIN_PROFILE_TYPE}
        )
        raw_window = payload.get("margin_window")
        window = _object(raw_window, "margin_window") if raw_window is not None else {}
        return FuturesMarginWindow(
            margin_window_type=_text(window, "margin_window_type", "margin_window"),
            end_time=_instant(window, "end_time", "margin_window"),
            intraday_killswitch_enabled=_bool(payload, "is_intraday_margin_killswitch_enabled"),
            enrollment_killswitch_enabled=_bool(
                payload, "is_intraday_margin_enrollment_killswitch_enabled"
            ),
        )

    async def _get(
        self, operation: str, path: str, params: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        """Send one allowlisted GET; refuse any other path before it reaches the transport."""
        if path not in CFM_GET_ALLOWLIST:
            raise FuturesAccountReadError(operation, "path_not_allowlisted")
        try:
            payload = await asyncio.to_thread(self._transport.get, path, params)
        except CoinbaseHttpStatusError as error:
            raise FuturesAccountReadError(operation, f"http_{error.status_code}") from None
        except OSError, TimeoutError, TypeError, ValueError:
            raise FuturesAccountReadError(operation, "transport") from None
        if not isinstance(payload, dict):
            raise FuturesAccountReadError(operation, "malformed")
        return payload


def _position(row: Mapping[str, object]) -> FuturesPosition:
    """Validate one position row."""
    product_id = row.get("product_id")
    if not isinstance(product_id, str) or not is_futures_product_id(product_id):
        raise FuturesAccountReadError("positions", "malformed")
    side = _SIDES.get(str(row.get("side", "UNKNOWN")))
    contracts = _decimal(row, "number_of_contracts", operation="positions")
    if side is None or contracts is None or contracts < 0:
        raise FuturesAccountReadError("positions", "malformed")
    return FuturesPosition(
        product_id=product_id,
        side=side,
        number_of_contracts=contracts,
        current_price=_decimal(row, "current_price", operation="positions"),
        avg_entry_price=_decimal(row, "avg_entry_price", operation="positions"),
        unrealized_pnl=_decimal(row, "unrealized_pnl", operation="positions"),
        daily_realized_pnl=_decimal(row, "daily_realized_pnl", operation="positions"),
        expiration_time=_instant(row, "expiration_time", "positions"),
    )


def _measure(raw: object) -> FuturesMarginMeasure | None:
    """Validate one margin-window measure block; absent is ``None``."""
    if raw is None:
        return None
    measure = _object(raw, "balance_summary")
    return FuturesMarginMeasure(
        margin_window_type=_text(measure, "margin_window_type", "balance_summary"),
        margin_level=_text(measure, "margin_level", "balance_summary"),
        initial_margin=_amount(measure, "initial_margin"),
        maintenance_margin=_amount(measure, "maintenance_margin"),
        liquidation_buffer=_amount(measure, "liquidation_buffer"),
        total_hold=_amount(measure, "total_hold"),
        futures_buying_power=_amount(measure, "futures_buying_power"),
    )


def _amount(payload: Mapping[str, object], field: str) -> Decimal | None:
    """Parse an ``{value, currency}`` amount; a non-USD currency fails the read.

    A bare decimal string is accepted for measure fields the venue sends unwrapped.
    """
    raw = payload.get(field)
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        return _decimal(payload, field)
    amount = _object(raw, "balance_summary")
    currency = amount.get("currency")
    if currency not in (None, "", FUTURES_ACCOUNT_CURRENCY):
        # A non-USD amount here would be summed with USD downstream; refuse it.
        raise FuturesAccountReadError("balance_summary", "non_usd_amount")
    return _decimal(amount, "value")


def _decimal(
    payload: Mapping[str, object], field: str, *, operation: str = "balance_summary"
) -> Decimal | None:
    """Parse one finite decimal string; ``None``/``""`` is unknown, anything else fails."""
    raw = payload.get(field)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise FuturesAccountReadError(operation, "malformed")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise FuturesAccountReadError(operation, "malformed") from None
    if not value.is_finite():
        raise FuturesAccountReadError(operation, "malformed")
    return value


def _text(payload: Mapping[str, object], field: str, operation: str) -> str | None:
    """Return optional venue text."""
    raw = payload.get(field)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise FuturesAccountReadError(operation, "malformed")
    return raw


def _bool(payload: Mapping[str, object], field: str) -> bool | None:
    """Return an optional JSON boolean."""
    raw = payload.get(field)
    if raw is None:
        return None
    if not isinstance(raw, bool):
        raise FuturesAccountReadError("margin_window", "malformed")
    return raw


def _instant(payload: Mapping[str, object], field: str, operation: str) -> datetime | None:
    """Return an optional RFC 3339 UTC instant."""
    raw = payload.get(field)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise FuturesAccountReadError(operation, "malformed")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise FuturesAccountReadError(operation, "malformed") from None
    if parsed.tzinfo is None:
        raise FuturesAccountReadError(operation, "malformed")
    return parsed.astimezone(UTC)


def _object(raw: object, operation: str) -> dict[str, object]:
    """Narrow one JSON object with text keys or fail the read."""
    if not isinstance(raw, dict):
        raise FuturesAccountReadError(operation, "malformed")
    narrowed: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise FuturesAccountReadError(operation, "malformed")
        narrowed[key] = value
    return narrowed
