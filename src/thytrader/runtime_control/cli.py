"""Confirmation-gated CLI for paper and live deployment control.

Also exposes write-only Coinbase credential show/set/clear. Mutations stay
``--confirm``-gated; YOLO never covers credential set/clear.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from thytrader.agent_http import AgentHttpError, require_matching_ops_contract, resolve_api_base_url
from thytrader.agent_orchestration.confirmation import (
    require_mutation_confirmation,
    require_paper_runtime_confirmation,
)
from thytrader.agent_orchestration.models import YoloTier
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.operator.redaction import configured_secrets, dumps_redacted
from thytrader.operator.status import EXIT_HEALTHY, EXIT_USAGE
from thytrader.runtime_control.client import (
    RuntimeControlError,
    clear_coinbase_credentials,
    list_deployments,
    place_discretionary_order,
    set_coinbase_credentials,
    set_deployment_status,
    set_risk_policy,
    set_yaml_settings,
    show_coinbase_credentials,
    show_deployment,
    show_risk_policy,
    show_yaml_settings,
    start_deployment,
)
if TYPE_CHECKING:
    from collections.abc import Sequence

_CONFIRM_HELP = (
    "Required for mutations unless YOLO covers that tier. Live start also "
    "requires --i-understand-live. Publishing a risk policy requires --confirm "
    "only; it does not arm live trading. Live place-order, set-settings, and "
    "Coinbase credential set/clear never skip --confirm."
)
_LIVE_HELP = (
    "Required to start live trading or place a live order. Live spends real "
    "money. YOLO never skips this flag."
)
_ALLOCATION_HELP = "Optional strategy_id:allocated_quote reservation. Repeatable."


def _shared_options() -> argparse.ArgumentParser:
    """Global flags that may appear before or after the subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--base-url",
        default=None,
        help="Loopback API origin. Defaults to THYTRADER_API_BASE_URL or settings.",
    )
    return shared


def _parser() -> argparse.ArgumentParser:
    """Build the confirmation-gated runtime-control argument parser."""
    shared = _shared_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-runtime",
        description=(
            "Start, pause, resume, or stop paper and live deployments, place "
            "discretionary orders, publish the risk-policy registry, update YAML "
            "non-secret settings (including YOLO), and set or clear write-only "
            "Coinbase credentials, through the loopback HTTP API. Mutations "
            "require --confirm unless YOLO covers that tier. Live start and live "
            "place-order also require --i-understand-live. Live place-order, "
            "set-risk-policy, set-settings, and Coinbase credential set/clear "
            "never skip --confirm. This is not the operator or research CLI."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "list",
        parents=[trailing],
        help="List deployments without mutating them.",
    )
    show = subparsers.add_parser("show", parents=[trailing], help="Show one deployment snapshot.")
    show.add_argument("deployment_id", help="Deployment UUID.")
    start = subparsers.add_parser(
        "start",
        parents=[trailing],
        help="Start one paper or live deployment.",
    )
    start.add_argument("--strategy-fingerprint", required=True)
    start.add_argument("--mode", required=True, choices=("paper", "live"))
    start.add_argument("--cash", default=None, help="Paper starting cash decimal string.")
    start.add_argument(
        "--maker-fee-rate",
        default=None,
        help=(
            "Paper maker fee assumption as a decimal string. Optional with "
            "--taker-fee-rate; omitted paper uses documented 0.001 / 0.002. "
            "Not observed Coinbase fees. Live rejects these flags."
        ),
    )
    start.add_argument(
        "--taker-fee-rate",
        default=None,
        help="Paper taker fee assumption as a decimal string. See --maker-fee-rate.",
    )
    start.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    start.add_argument("--i-understand-live", action="store_true", help=_LIVE_HELP)
    place = subparsers.add_parser(
        "place-order",
        parents=[trailing],
        help="Place one long or short discretionary order with required SL/TP.",
    )
    place.add_argument("--mode", required=True, choices=("paper", "live"))
    place.add_argument("--product-id", required=True)
    place.add_argument("--side", default="long", choices=("long", "short"))
    place.add_argument("--stop-price", required=True)
    place.add_argument("--take-profit-price", required=True)
    place.add_argument("--idempotency-key", required=True)
    place.add_argument("--origin", default="agent", choices=("human", "agent"))
    place.add_argument(
        "--entry-kind",
        default="post_only_limit",
        choices=("post_only_limit", "marketable"),
    )
    place.add_argument(
        "--timeframe",
        default="5m",
        choices=EXECUTION_TIMEFRAMES,
        help=(
            "Discretionary book clock. Default 5m. Any ingested venue clock "
            "(1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d)."
        ),
    )
    place.add_argument("--quantity", default=None)
    place.add_argument("--quote-notional", default=None)
    place.add_argument("--limit-price", default=None)
    place.add_argument("--cash", default=None, help="Paper starting cash decimal string.")
    place.add_argument(
        "--maker-fee-rate",
        default=None,
        help=(
            "Paper maker fee assumption as a decimal string. Optional with "
            "--taker-fee-rate on a new paper book. Live rejects these flags."
        ),
    )
    place.add_argument(
        "--taker-fee-rate",
        default=None,
        help="Paper taker fee assumption as a decimal string. See --maker-fee-rate.",
    )
    place.add_argument(
        "--note",
        default=None,
        help="Optional attributed why-note frozen onto the trade-reason record.",
    )
    place.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    place.add_argument("--i-understand-live", action="store_true", help=_LIVE_HELP)
    for action in ("pause", "resume", "stop"):
        command = subparsers.add_parser(
            action,
            parents=[trailing],
            help=f"{action.title()} one deployment.",
        )
        command.add_argument("deployment_id", help="Deployment UUID.")
        command.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    subparsers.add_parser(
        "show-risk-policy",
        parents=[trailing],
        help="Show the effective compiled or published risk policy.",
    )
    set_policy = subparsers.add_parser(
        "set-risk-policy",
        parents=[trailing],
        help="Publish a new immutable risk-policy version.",
    )
    set_policy.add_argument(
        "--product-allowlist",
        action="append",
        default=[],
        help="Optional BASE-USD product. Repeatable. Empty means no extra restriction.",
    )
    set_policy.add_argument("--max-concurrent-running-deployments", type=int, required=True)
    set_policy.add_argument("--max-concurrent-open-positions", type=int, required=True)
    set_policy.add_argument("--max-portfolio-exposure-fraction", required=True)
    set_policy.add_argument("--per-product-max-exposure-fraction", required=True)
    set_policy.add_argument("--paper-capital-quote", required=True)
    set_policy.add_argument(
        "--daily-loss-limit-fraction",
        default="1",
        help="UTC-day realized plus unrealized loss cap as a fraction of the mode capital base.",
    )
    set_policy.add_argument(
        "--max-strategy-drawdown-fraction",
        default="1",
        help="Per-strategy fill-ledger drawdown cap as a fraction of peak marked equity.",
    )
    set_policy.add_argument(
        "--max-entry-orders-per-minute",
        type=int,
        default=60,
        help="Rolling 60-second cap on new orders that blocks risk-increasing entries.",
    )
    set_policy.add_argument(
        "--max-cancellations-per-minute",
        type=int,
        default=60,
        help="Rolling 60-second cancel cap that blocks further risk-increasing entries.",
    )
    set_policy.add_argument(
        "--reference-price-collar-fraction",
        default="0.5",
        help="Maximum |limit-last_close|/last_close for risk-increasing priced entries.",
    )
    set_policy.add_argument(
        "--allow-intra-strategy-pyramiding",
        action="store_true",
        help="Permit same-side adds when the published strategy also enables pyramiding.",
    )
    set_policy.add_argument(
        "--max-daily-loss-quote",
        default=None,
        help=(
            "Optional absolute quote ceiling enforced alongside --daily-loss-limit-fraction "
            "(whichever bound is tighter trips first). Unset by default; this CLI does not "
            "assert a universal safe amount."
        ),
    )
    set_policy.add_argument(
        "--max-portfolio-exposure-quote",
        default=None,
        help=(
            "Optional absolute quote ceiling enforced alongside "
            "--max-portfolio-exposure-fraction. Unset by default."
        ),
    )
    set_policy.add_argument(
        "--max-venue-order-actions-per-minute",
        type=int,
        default=None,
        help=(
            "Optional combined per-minute cap across entry-order and cancellation requests "
            "(a coarse venue-request budget). Denies only new entries; unset by default."
        ),
    )
    set_policy.add_argument(
        "--allocation",
        action="append",
        default=[],
        help=_ALLOCATION_HELP,
    )
    set_policy.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    subparsers.add_parser(
        "show-settings",
        parents=[trailing],
        help="Show YAML non-secret settings and YOLO without echoing secrets.",
    )
    set_settings = subparsers.add_parser(
        "set-settings",
        parents=[trailing],
        help="Write YAML non-secret settings. YOLO applies without restart.",
    )
    set_settings.add_argument(
        "--yolo-enabled",
        choices=("true", "false"),
        default=None,
        help="YOLO on/off. Independent of leftover THYTRADER_YOLO_ENABLED env.",
    )
    set_settings.add_argument(
        "--yolo-tiers",
        default=None,
        help=(
            "Independent YOLO tiers: data, research, paper, and/or live. "
            "Scalar paper is valid. Live still needs --i-understand-live. "
            "Empty string clears tiers (requires --yolo-enabled false)."
        ),
    )
    set_settings.add_argument(
        "--log-level",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        default=None,
    )
    set_settings.add_argument("--snapshot-interval-seconds", type=int, default=None)
    set_settings.add_argument("--market-data-worker-interval-seconds", type=int, default=None)
    set_settings.add_argument("--market-data-worker-lookback-hours", type=int, default=None)
    set_settings.add_argument("--market-data-worker-product-id", default=None)
    set_settings.add_argument("--execution-worker-interval-seconds", type=int, default=None)
    set_settings.add_argument(
        "--notify-provider",
        choices=("none", "log", "webhook"),
        default=None,
        help="Webhook URL stays in ignored .env and still needs a restart.",
    )
    set_settings.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    subparsers.add_parser(
        "show-coinbase-credentials",
        parents=[trailing],
        help="Show whether Coinbase secrets are configured; never prints them.",
    )
    set_creds = subparsers.add_parser(
        "set-coinbase-credentials",
        parents=[trailing],
        help="Set or rotate Coinbase Advanced Trade secrets from a private-key file.",
    )
    set_creds.add_argument("--api-key-name", required=True, help="Coinbase API key name.")
    set_creds.add_argument(
        "--private-key-file",
        required=True,
        help="Path to the PEM private key. Never pass the key on the command line.",
    )
    set_creds.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    clear_creds = subparsers.add_parser(
        "clear-coinbase-credentials",
        parents=[trailing],
        help="Clear Coinbase Advanced Trade secrets and return this API process to demo.",
    )
    clear_creds.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    return parser


_RUNTIME_CONFIRM_MESSAGE = (
    "Pass --confirm to change paper or live runtimes, the risk-policy "
    "registry, or YAML settings. Live start also requires --i-understand-live."
)
_CREDENTIALS_CONFIRM_MESSAGE = (
    "Pass --confirm to set or clear Coinbase credentials. YOLO never covers this command."
)


def _require_confirm(
    confirm: bool,
    *,
    base_url: str,
    command: str,
    hard_gate: bool = False,
    tier: YoloTier = YoloTier.PAPER,
    missing_message: str = _RUNTIME_CONFIRM_MESSAGE,
) -> None:
    """Refuse mutations unless `--confirm` is present or YOLO covers the tier."""
    require_mutation_confirmation(
        confirmed=confirm,
        missing_message=missing_message,
        error_type=RuntimeControlError,
        base_url=base_url,
        tier=tier,
        command=command,
        hard_gate=hard_gate,
    )


def _require_live_ack(*, mode: str, acknowledged: bool) -> None:
    """Refuse live arming unless the dedicated live acknowledgement flag is set."""
    if mode == "live" and not acknowledged:
        raise RuntimeControlError("Live trading spends real money. Pass --i-understand-live.")


def _deployment_mode(payload: object) -> str:
    """Read mode from a deployment snapshot without leaking extra fields."""
    if isinstance(payload, dict):
        mode = payload.get("mode")
        if isinstance(mode, str):
            return mode
    raise RuntimeControlError("Deployment snapshot omitted mode.")


def _start(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Start paper or live; live still requires `--i-understand-live`."""
    live = arguments.mode == "live"
    _require_live_ack(mode=arguments.mode, acknowledged=arguments.i_understand_live)
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command="start",
        tier=YoloTier.LIVE if live else YoloTier.PAPER,
    )
    cash = _paper_cash(mode=arguments.mode, cash=arguments.cash)
    maker, taker = _paper_fees(
        mode=arguments.mode,
        maker_fee_rate=arguments.maker_fee_rate,
        taker_fee_rate=arguments.taker_fee_rate,
    )
    require_matching_ops_contract(base_url)
    return start_deployment(
        base_url,
        strategy_fingerprint=arguments.strategy_fingerprint,
        mode=arguments.mode,
        paper_starting_cash=cash,
        maker_fee_rate=maker,
        taker_fee_rate=taker,
        settings=settings,
    )


def _place_order(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Place one paper (YOLO-eligible) or live (confirm hard-gated) discretionary order."""
    live = arguments.mode == "live"
    _require_live_ack(mode=arguments.mode, acknowledged=arguments.i_understand_live)
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command="place-order",
        hard_gate=live,
        tier=YoloTier.PAPER,
    )
    cash = _paper_cash(mode=arguments.mode, cash=arguments.cash)
    maker, taker = _paper_fees(
        mode=arguments.mode,
        maker_fee_rate=arguments.maker_fee_rate,
        taker_fee_rate=arguments.taker_fee_rate,
    )
    require_matching_ops_contract(base_url)
    return place_discretionary_order(
        base_url,
        settings=settings,
        mode=arguments.mode,
        product_id=arguments.product_id,
        stop_price=arguments.stop_price,
        take_profit_price=arguments.take_profit_price,
        idempotency_key=arguments.idempotency_key,
        origin=arguments.origin,
        entry_kind=arguments.entry_kind,
        timeframe=arguments.timeframe,
        side=arguments.side,
        quantity=arguments.quantity,
        quote_notional=arguments.quote_notional,
        limit_price=arguments.limit_price,
        paper_starting_cash=cash,
        maker_fee_rate=maker,
        taker_fee_rate=taker,
        note=arguments.note,
    )


def _set_status(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Pause, resume, or stop one deployment; YOLO follows paper vs live tiers."""
    command = arguments.command
    require_paper_runtime_confirmation(
        confirmed=arguments.confirm,
        missing_message=_RUNTIME_CONFIRM_MESSAGE,
        error_type=RuntimeControlError,
        base_url=base_url,
        command=command,
        deployment_mode=lambda: _deployment_mode(
            show_deployment(base_url, arguments.deployment_id)
        ),
    )
    require_matching_ops_contract(base_url)
    return set_deployment_status(
        base_url,
        arguments.deployment_id,
        command,
        settings=settings,
    )


def _run(arguments: argparse.Namespace) -> str:
    """Execute one runtime command against the loopback API."""
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    payload = _dispatch(arguments, base_url, settings)
    return dumps_redacted(payload, secrets)


def _dispatch(arguments: argparse.Namespace, base_url: str, settings: Settings) -> object:
    """Route one parsed command to the HTTP helper."""
    command = arguments.command
    if command == "list":
        require_matching_ops_contract(base_url)
        return list_deployments(base_url)
    if command == "show":
        require_matching_ops_contract(base_url)
        return show_deployment(base_url, arguments.deployment_id)
    if command == "start":
        return _start(arguments, base_url, settings)
    if command == "place-order":
        return _place_order(arguments, base_url, settings)
    if command in {"pause", "resume", "stop"}:
        return _set_status(arguments, base_url, settings)
    if command == "show-risk-policy":
        require_matching_ops_contract(base_url)
        return show_risk_policy(base_url)
    if command == "set-risk-policy":
        _require_confirm(
            arguments.confirm,
            base_url=base_url,
            command="set-risk-policy",
            hard_gate=True,
        )
        require_matching_ops_contract(base_url)
        return set_risk_policy(
            base_url,
            _risk_policy_payload(arguments),
            settings=settings,
        )
    if command in {"show-settings", "set-settings"}:
        return _settings_command(arguments, base_url, settings)
    if command in {
        "show-coinbase-credentials",
        "set-coinbase-credentials",
        "clear-coinbase-credentials",
    }:
        return _credentials_command(arguments, base_url, settings)
    raise AssertionError(f"unsupported runtime command: {command}")


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
        "allocations": tuple(_parse_allocation(item) for item in arguments.allocation),
    }


def _parse_allocation(value: str) -> dict[str, str]:
    """Parse one strategy_id:allocated_quote reservation."""
    strategy_id, separator, allocated_quote = value.partition(":")
    if separator != ":" or not strategy_id or not allocated_quote:
        raise RuntimeControlError("Allocations must be strategy_id:allocated_quote.")
    return {"strategy_id": strategy_id, "allocated_quote": allocated_quote}


def _paper_cash(*, mode: str, cash: str | None) -> str | None:
    """Require paper cash and reject it for live start."""
    if mode == "live":
        if cash is not None:
            raise RuntimeControlError("Live start does not accept --cash.")
        return None
    if cash is None:
        raise RuntimeControlError("Paper requires --cash.")
    return cash


def _paper_fees(
    *, mode: str, maker_fee_rate: str | None, taker_fee_rate: str | None
) -> tuple[str | None, str | None]:
    """Accept optional paper maker/taker assumptions and reject them for live."""
    if mode == "live":
        if maker_fee_rate is not None or taker_fee_rate is not None:
            raise RuntimeControlError("Live start does not accept paper fee rates.")
        return None, None
    if (maker_fee_rate is None) != (taker_fee_rate is None):
        raise RuntimeControlError(
            "Paper fee rates require both --maker-fee-rate and --taker-fee-rate."
        )
    return maker_fee_rate, taker_fee_rate


def main(argv: Sequence[str] | None = None) -> None:
    """Run one runtime command; mutations require `--confirm` unless YOLO applies."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else EXIT_USAGE) from error
    try:
        output = _run(arguments)
    except RuntimeControlError as error:
        raise SystemExit(str(error)) from error
    except AgentHttpError as error:
        raise SystemExit(str(error)) from error
    except Exception as error:
        message = "Runtime command failed safely; inspect deployments before retrying."
        raise SystemExit(message) from error
    sys.stdout.write(f"{output}\n")
    raise SystemExit(EXIT_HEALTHY)


if __name__ == "__main__":
    main()
