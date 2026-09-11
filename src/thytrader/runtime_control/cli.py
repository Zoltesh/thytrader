"""Confirmation-gated CLI for paper and live deployment control."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from thytrader.agent_http import AgentHttpError, resolve_api_base_url
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.operator.redaction import configured_secrets, dumps_redacted
from thytrader.operator.status import EXIT_HEALTHY, EXIT_USAGE
from thytrader.runtime_control.client import (
    RuntimeControlError,
    list_deployments,
    set_deployment_status,
    show_deployment,
    start_deployment,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_CONFIRM_HELP = "Required for mutations. Live start also requires --i-understand-live."
_LIVE_HELP = "Required with --confirm to start live trading. Live spends real money."


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
            "Start, pause, resume, or stop paper and live deployments through the "
            "loopback HTTP API. Mutations require --confirm. Live start also "
            "requires --i-understand-live. This is not the operator or research CLI."
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
    for action in ("pause", "resume", "stop"):
        command = subparsers.add_parser(
            action,
            parents=[trailing],
            help=f"{action.title()} one deployment.",
        )
        command.add_argument("deployment_id", help="Deployment UUID.")
        command.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    return parser


def _require_confirm(confirm: bool) -> None:
    """Refuse mutations unless the operator passed an explicit confirmation flag."""
    if not confirm:
        raise RuntimeControlError(
            "Pass --confirm to change paper or live runtimes. "
            "Live start also requires --i-understand-live."
        )


def _require_live_ack(*, mode: str, acknowledged: bool) -> None:
    """Refuse live arming unless the dedicated live acknowledgement flag is set."""
    if mode == "live" and not acknowledged:
        raise RuntimeControlError(
            "Live trading spends real money. Pass --confirm and --i-understand-live."
        )


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
        return list_deployments(base_url)
    if command == "show":
        return show_deployment(base_url, arguments.deployment_id)
    if command == "start":
        _require_confirm(arguments.confirm)
        _require_live_ack(mode=arguments.mode, acknowledged=arguments.i_understand_live)
        cash = _paper_cash(mode=arguments.mode, cash=arguments.cash)
        return start_deployment(
            base_url,
            strategy_fingerprint=arguments.strategy_fingerprint,
            mode=arguments.mode,
            paper_starting_cash=cash,
        )
    if command in {"pause", "resume", "stop"}:
        _require_confirm(arguments.confirm)
        return set_deployment_status(base_url, arguments.deployment_id, command)
    raise AssertionError(f"unsupported runtime command: {command}")


def _paper_cash(*, mode: str, cash: str | None) -> str | None:
    """Require paper cash and reject it for live start."""
    if mode == "live":
        if cash is not None:
            raise RuntimeControlError("Live start does not accept --cash.")
        return None
    if cash is None:
        raise RuntimeControlError("Paper start requires --cash.")
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
