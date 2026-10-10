"""``thytrader-runtime`` handlers for installation configuration commands.

YAML non-secret settings, write-only Coinbase credentials, and the risk-policy write
body. Every write here is confirmation-hard-gated (YOLO never skips it).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from thytrader.agent_http import require_matching_ops_contract
from thytrader.runtime_control.client import (
    RuntimeControlError,
    clear_coinbase_credentials,
    set_coinbase_credentials,
    set_yaml_settings,
    show_coinbase_credentials,
    show_yaml_settings,
)
from thytrader.runtime_control.gates import _CREDENTIALS_CONFIRM_MESSAGE, _require_confirm

if TYPE_CHECKING:
    import argparse

    from thytrader.config import Settings


def _settings_command(
    arguments: argparse.Namespace,
    base_url: str,
    settings: Settings,
) -> object:
    """Read or replace YAML non-secrets. Writes never inherit YOLO skip-confirm."""
    require_matching_ops_contract(base_url)
    if arguments.command == "show-settings":
        return show_yaml_settings(base_url)
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command="set-settings",
        hard_gate=True,
    )
    current = show_yaml_settings(base_url)
    if not isinstance(current, dict):
        raise RuntimeControlError("Settings response omitted YAML fields.")
    typed_current: dict[str, object] = {str(key): value for key, value in current.items()}
    return set_yaml_settings(
        base_url,
        _settings_write_payload(arguments, typed_current),
        settings=settings,
    )


def _settings_write_payload(
    arguments: argparse.Namespace,
    current: dict[str, object],
) -> dict[str, object]:
    """Overlay CLI flags onto the current YAML settings document."""
    tiers: object = current.get("yolo_tiers", ())
    if arguments.yolo_tiers is not None:
        tiers = [
            str(part).strip().lower()
            for part in arguments.yolo_tiers.split(",")
            if str(part).strip()
        ]
    enabled = current.get("yolo_enabled", False)
    if arguments.yolo_enabled is not None:
        enabled = arguments.yolo_enabled == "true"
    return {
        "yolo_enabled": enabled,
        "yolo_tiers": tiers,
        "log_level": arguments.log_level or current.get("log_level", "INFO"),
        "snapshot_interval_seconds": _int_or_current(
            arguments.snapshot_interval_seconds,
            current.get("snapshot_interval_seconds"),
            300,
        ),
        "market_data_worker_interval_seconds": _int_or_current(
            arguments.market_data_worker_interval_seconds,
            current.get("market_data_worker_interval_seconds"),
            300,
        ),
        "market_data_worker_lookback_hours": _int_or_current(
            arguments.market_data_worker_lookback_hours,
            current.get("market_data_worker_lookback_hours"),
            168,
        ),
        "market_data_worker_product_id": (
            arguments.market_data_worker_product_id
            or current.get("market_data_worker_product_id")
            or "BTC-USD"
        ),
        "execution_worker_interval_seconds": _int_or_current(
            arguments.execution_worker_interval_seconds,
            current.get("execution_worker_interval_seconds"),
            30,
        ),
        "notify_provider": arguments.notify_provider or current.get("notify_provider") or "none",
    }


def _int_or_current(override: int | None, current: object, default: int) -> int:
    """Prefer a CLI int, then the current payload, then a compiled default."""
    if override is not None:
        return override
    if isinstance(current, int):
        return current
    return default


def _credentials_command(
    arguments: argparse.Namespace,
    base_url: str,
    settings: Settings,
) -> object:
    """Show, set, or clear Coinbase secrets; mutations are confirmation-hard-gated."""
    command = arguments.command
    if command == "show-coinbase-credentials":
        require_matching_ops_contract(base_url)
        return show_coinbase_credentials(base_url)
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command=command,
        hard_gate=True,
        missing_message=_CREDENTIALS_CONFIRM_MESSAGE,
    )
    require_matching_ops_contract(base_url)
    if command == "clear-coinbase-credentials":
        return clear_coinbase_credentials(base_url, settings=settings)
    return set_coinbase_credentials(
        base_url,
        api_key_name=arguments.api_key_name.strip(),
        private_key=_read_private_key_file(arguments.private_key_file),
        settings=settings,
    )


def _read_private_key_file(path_value: str) -> str:
    """Read a PEM file for set-coinbase-credentials without logging its contents."""
    path = Path(path_value)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeControlError("Could not read --private-key-file.") from error
    private_key = text.replace("\\n", "\n").strip()
    if not private_key:
        raise RuntimeControlError("Private key file is empty.")
    return private_key


def _risk_policy_payload(arguments: argparse.Namespace) -> dict[str, object]:
    """Map CLI flags onto the HTTP write body."""
    return {
        "quote_currency": arguments.quote_currency,
        "product_allowlist": tuple(arguments.product_allowlist),
        "max_concurrent_running_deployments": arguments.max_concurrent_running_deployments,
        "max_concurrent_open_positions": arguments.max_concurrent_open_positions,
        "max_portfolio_exposure_fraction": arguments.max_portfolio_exposure_fraction,
        "per_product_max_exposure_fraction": arguments.per_product_max_exposure_fraction,
        "paper_capital_quote": arguments.paper_capital_quote,
        "daily_loss_limit_fraction": arguments.daily_loss_limit_fraction,
        "max_strategy_drawdown_fraction": arguments.max_strategy_drawdown_fraction,
        "max_entry_orders_per_minute": arguments.max_entry_orders_per_minute,
        "max_cancellations_per_minute": arguments.max_cancellations_per_minute,
        "reference_price_collar_fraction": arguments.reference_price_collar_fraction,
        "allow_intra_strategy_pyramiding": arguments.allow_intra_strategy_pyramiding,
        "max_daily_loss_quote": arguments.max_daily_loss_quote,
        "max_portfolio_exposure_quote": arguments.max_portfolio_exposure_quote,
        "max_venue_order_actions_per_minute": arguments.max_venue_order_actions_per_minute,
        "max_order_quantity": arguments.max_order_quantity,
        "max_order_notional_quote": arguments.max_order_notional_quote,
        "min_available_quote_reserve": arguments.min_available_quote_reserve,
        "max_fleet_entries_per_window": arguments.max_fleet_entries_per_window,
        "fleet_entry_window_minutes": arguments.fleet_entry_window_minutes,
        "max_btc_beta_exposure_fraction": arguments.max_btc_beta_exposure_fraction,
        "max_btc_beta_exposure_quote": arguments.max_btc_beta_exposure_quote,
        "allocations": tuple(_parse_allocation(item) for item in arguments.allocation),
        "futures": _futures_block(arguments),
    }


def _futures_block(arguments: argparse.Namespace) -> dict[str, str] | None:
    """Map the futures flags onto the optional policy block (ADR 0129); ``None`` when unset."""
    reserve = arguments.futures_live_spot_collateral_reserve_quote
    haircut = arguments.futures_peg_haircut
    if reserve is None and haircut is None:
        return None
    block: dict[str, str] = {}
    if reserve is not None:
        block["live_spot_collateral_reserve_quote"] = reserve
    if haircut is not None:
        block["peg_haircut"] = haircut
    return block


def _parse_allocation(value: str) -> dict[str, str]:
    """Parse one strategy_id:allocated_quote reservation."""
    strategy_id, separator, allocated_quote = value.partition(":")
    if separator != ":" or not strategy_id or not allocated_quote:
        raise RuntimeControlError("Allocations must be strategy_id:allocated_quote.")
    return {"strategy_id": strategy_id, "allocated_quote": allocated_quote}
