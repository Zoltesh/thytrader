"""Confirmation-gated CLI for paper and live deployment control."""

from __future__ import annotations

import argparse
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
    list_deployments,
    place_discretionary_order,
    set_deployment_status,
    set_risk_policy,
    show_deployment,
    show_risk_policy,
    start_deployment,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_CONFIRM_HELP = (
    "Required for mutations. Live start also requires --i-understand-live. "
    "Publishing a risk policy requires --confirm only; it does not arm live trading."
)
_LIVE_HELP = "Required with --confirm to start live trading. Live spends real money."
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
            "discretionary orders, and publish the risk-policy registry, through "
            "the loopback HTTP API. Mutations require --confirm. Live start and "
            "live place-order also require --i-understand-live. This is not the "
            "operator or research CLI."
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
    start.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    start.add_argument("--i-understand-live", action="store_true", help=_LIVE_HELP)
    place = subparsers.add_parser(
        "place-order",
        parents=[trailing],
        help="Place one long-only discretionary order with required SL/TP.",
    )
    place.add_argument("--mode", required=True, choices=("paper", "live"))
    place.add_argument("--product-id", required=True)
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
        "--allocation",
        action="append",
        default=[],
        help=_ALLOCATION_HELP,
    )
    set_policy.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    return parser


_RUNTIME_CONFIRM_MESSAGE = (
    "Pass --confirm to change paper or live runtimes or the risk-policy "
    "registry. Live start also requires --i-understand-live."
)


def _require_confirm(
    confirm: bool,
    *,
    base_url: str,
    command: str,
    hard_gate: bool = False,
) -> None:
    """Refuse mutations unless `--confirm` is present or YOLO covers paper."""
    require_mutation_confirmation(
        confirmed=confirm,
        missing_message=_RUNTIME_CONFIRM_MESSAGE,
        error_type=RuntimeControlError,
        base_url=base_url,
        tier=YoloTier.PAPER,
        command=command,
        hard_gate=hard_gate,
    )


def _require_live_ack(*, mode: str, acknowledged: bool) -> None:
    """Refuse live arming unless the dedicated live acknowledgement flag is set."""
    if mode == "live" and not acknowledged:
        raise RuntimeControlError(
            "Live trading spends real money. Pass --confirm and --i-understand-live."
        )


def _deployment_mode(payload: object) -> str:
    """Read mode from a deployment snapshot without leaking extra fields."""
    if isinstance(payload, dict):
        mode = payload.get("mode")
        if isinstance(mode, str):
            return mode
    raise RuntimeControlError("Deployment snapshot omitted mode.")


def _start(arguments: argparse.Namespace, base_url: str) -> object:
    """Start paper (YOLO-eligible) or live (hard-gated) through the existing client."""
    live = arguments.mode == "live"
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command="start",
        hard_gate=live,
    )
    _require_live_ack(mode=arguments.mode, acknowledged=arguments.i_understand_live)
    cash = _paper_cash(mode=arguments.mode, cash=arguments.cash)
    require_matching_ops_contract(base_url)
    return start_deployment(
        base_url,
        strategy_fingerprint=arguments.strategy_fingerprint,
        mode=arguments.mode,
        paper_starting_cash=cash,
    )


def _place_order(arguments: argparse.Namespace, base_url: str) -> object:
    """Place one paper (YOLO-eligible) or live (hard-gated) discretionary long."""
    live = arguments.mode == "live"
    _require_confirm(
        arguments.confirm,
        base_url=base_url,
        command="place-order",
        hard_gate=live,
    )
    _require_live_ack(mode=arguments.mode, acknowledged=arguments.i_understand_live)
    cash = _paper_cash(mode=arguments.mode, cash=arguments.cash)
    require_matching_ops_contract(base_url)
    return place_discretionary_order(
        base_url,
        mode=arguments.mode,
        product_id=arguments.product_id,
        stop_price=arguments.stop_price,
        take_profit_price=arguments.take_profit_price,
        idempotency_key=arguments.idempotency_key,
        origin=arguments.origin,
        entry_kind=arguments.entry_kind,
        timeframe=arguments.timeframe,
        quantity=arguments.quantity,
        quote_notional=arguments.quote_notional,
        limit_price=arguments.limit_price,
        paper_starting_cash=cash,
    )


def _set_status(arguments: argparse.Namespace, base_url: str) -> object:
    """Pause, resume, or stop one deployment; live control never uses YOLO."""
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
    return set_deployment_status(base_url, arguments.deployment_id, command)


def _run(arguments: argparse.Namespace) -> str:
    """Execute one runtime command against the loopback API."""
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    payload = _dispatch(arguments, base_url)
    return dumps_redacted(payload, secrets)


def _dispatch(arguments: argparse.Namespace, base_url: str) -> object:
    """Route one parsed command to the HTTP helper."""
    command = arguments.command
    if command == "list":
        require_matching_ops_contract(base_url)
        return list_deployments(base_url)
    if command == "show":
        require_matching_ops_contract(base_url)
        return show_deployment(base_url, arguments.deployment_id)
    if command == "start":
        return _start(arguments, base_url)
    if command == "place-order":
        return _place_order(arguments, base_url)
    if command in {"pause", "resume", "stop"}:
        return _set_status(arguments, base_url)
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
        return set_risk_policy(base_url, _risk_policy_payload(arguments))
    raise AssertionError(f"unsupported runtime command: {command}")


def _risk_policy_payload(arguments: argparse.Namespace) -> dict[str, object]:
    """Map CLI flags onto the HTTP write body."""
    return {
        "product_allowlist": tuple(arguments.product_allowlist),
        "max_concurrent_running_deployments": arguments.max_concurrent_running_deployments,
        "max_concurrent_open_positions": arguments.max_concurrent_open_positions,
        "max_portfolio_exposure_fraction": arguments.max_portfolio_exposure_fraction,
        "per_product_max_exposure_fraction": arguments.per_product_max_exposure_fraction,
        "paper_capital_quote": arguments.paper_capital_quote,
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


def main(argv: Sequence[str] | None = None) -> None:
    """Run one runtime command; mutations require --confirm."""
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
