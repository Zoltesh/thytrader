"""Documented-shape CFM account responses (Coinbase API reference, 2026-10-10).

Shapes follow the documented schemas of ``GET /cfm/balance_summary`` (Amount objects
``{value, currency}``), ``GET /cfm/positions``, ``GET /cfm/intraday/margin_setting`` and
``GET /cfm/intraday/current_margin_window``. Values are synthetic.
"""

from __future__ import annotations

from typing import Any


def _usd(value: str) -> dict[str, str]:
    """One documented Amount object."""
    return {"value": value, "currency": "USD"}


def balance_summary() -> dict[str, Any]:
    """A populated balance summary for an enabled account with one ETP short."""
    return {
        "balance_summary": {
            "futures_buying_power": _usd("412.50"),
            "total_usd_balance": _usd("500.00"),
            "cbi_usd_balance": _usd("425.00"),
            "cfm_usd_balance": _usd("75.00"),
            "total_open_orders_hold_amount": _usd("0"),
            "unrealized_pnl": _usd("-3.20"),
            "daily_realized_pnl": _usd("0"),
            "initial_margin": _usd("75.10"),
            "available_margin": _usd("421.70"),
            "liquidation_threshold": _usd("37.55"),
            "liquidation_buffer_amount": _usd("384.15"),
            "liquidation_buffer_percentage": "1023.04",
            "total_pending_transfers_amount": _usd("0"),
            "funding_pnl": _usd("-0.04"),
            "intraday_margin_window_measure": {
                "margin_window_type": "FCM_MARGIN_WINDOW_TYPE_INTRADAY",
                "margin_level": "MARGIN_LEVEL_TYPE_BASE",
                "initial_margin": "25.02",
                "maintenance_margin": "25.02",
                "liquidation_buffer": "471.78",
                "total_hold": "0",
                "futures_buying_power": "471.78",
            },
            "overnight_margin_window_measure": {
                "margin_window_type": "FCM_MARGIN_WINDOW_TYPE_OVERNIGHT",
                "margin_level": "MARGIN_LEVEL_TYPE_BASE",
                "initial_margin": "75.10",
                "maintenance_margin": "75.10",
                "liquidation_buffer": "421.70",
                "total_hold": "0",
                "futures_buying_power": "412.50",
            },
        }
    }


def positions() -> dict[str, Any]:
    """One short ETP perp position."""
    return {
        "positions": [
            {
                "product_id": "ETP-20DEC30-CDE",
                "expiration_time": "2030-12-20T16:00:00Z",
                "side": "SHORT",
                "number_of_contracts": "1",
                "current_price": "2480.5",
                "avg_entry_price": "2448.5",
                "unrealized_pnl": "-3.20",
                "daily_realized_pnl": "0",
            }
        ]
    }


def margin_setting() -> dict[str, Any]:
    """The standard (non-intraday) margin setting."""
    return {"setting": "INTRADAY_MARGIN_SETTING_STANDARD"}


def margin_window() -> dict[str, Any]:
    """The overnight window is in effect."""
    return {
        "margin_window": {
            "margin_window_type": "MARGIN_WINDOW_TYPE_OVERNIGHT",
            "end_time": "2026-10-10T12:00:00Z",
        },
        "is_intraday_margin_killswitch_enabled": False,
        "is_intraday_margin_enrollment_killswitch_enabled": False,
    }
