"""Confirmation-gated CLI for paper and live deployment control.

Also exposes write-only Coinbase credential show/set/clear. Mutations stay
``--confirm``-gated; YOLO never covers credential set/clear.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from thytrader.agent_http import (
    AgentHttpError,
    require_matching_ops_contract,
    resolve_api_base_url,
)
from thytrader.cli_errors import describe_unexpected_failure
from thytrader.config import Settings
from thytrader.exit_codes import EXIT_HEALTHY, EXIT_USAGE
from thytrader.operator.redaction import configured_secrets, dumps_redacted
from thytrader.runtime_control.adoption_commands import ADOPTION_COMMANDS, run_adoption_command
from thytrader.runtime_control.client import (
    RuntimeControlError,
    set_risk_policy,
    show_risk_policy,
)
from thytrader.runtime_control.configuration_handlers import (
    _credentials_command,
    _risk_policy_payload,
    _settings_command,
)
from thytrader.runtime_control.deployment_handlers import (
    _decisions,
    _place_order,
    _runtime_mutation,
    _start,
    _twin_command,
)
from thytrader.runtime_control.fleet_commands import (
    FLEET_MUTATIONS,
    run_fleet_mutation,
    run_fleet_read,
)
from thytrader.runtime_control.gates import _require_confirm
from thytrader.runtime_control.inventory_commands import run_inventory_read
from thytrader.runtime_control.parsers.root import _parser
from thytrader.runtime_control.portfolio_commands import (
    PORTFOLIO_COMMANDS,
    run_portfolio_command,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

__all__ = [
    "_parser",
    "_risk_policy_payload",
    "main",
    "run_inventory_read",
    "set_risk_policy",
]


def _read_only_command(arguments: argparse.Namespace, base_url: str) -> object:
    """List, show, or page decisions without mutating anything."""
    command = arguments.command
    if command == "decisions":
        return _decisions(arguments, base_url)
    if command in {"fleet-preview", "fleet-status"}:
        return run_fleet_read(arguments, base_url)
    require_matching_ops_contract(base_url)
    return run_inventory_read(arguments, base_url)


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
    if command in {"list", "show", "decisions", "orders", "fills", "fleet-preview", "fleet-status"}:
        return _read_only_command(arguments, base_url)
    if command == "start":
        return _start(arguments, base_url, settings)
    if command == "place-order":
        return _place_order(arguments, base_url, settings)
    if command in {"pause", "resume", "stop", "reset-breaker-latches"}:
        return _runtime_mutation(arguments, base_url, settings)
    if command in {
        *PORTFOLIO_COMMANDS,
        *ADOPTION_COMMANDS,
        "show-twin",
        "link-twin",
        "unlink-twin",
        *FLEET_MUTATIONS,
    }:
        return _extended_runtime_command(arguments, base_url, settings)
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
        message = describe_unexpected_failure(
            error,
            lane="Runtime command",
            safety="Inspect deployments (`uv run thytrader-runtime list`) before retrying.",
        )
        raise SystemExit(message) from error
    sys.stdout.write(f"{output}\n")
    raise SystemExit(EXIT_HEALTHY)


def _extended_runtime_command(
    arguments: argparse.Namespace, base_url: str, settings: Settings
) -> object:
    """Route portfolio lifecycle, adoption, fleet and comparison metadata controls."""
    if arguments.command in ADOPTION_COMMANDS:
        return run_adoption_command(arguments, base_url, settings)
    if arguments.command in PORTFOLIO_COMMANDS:
        return run_portfolio_command(arguments, base_url, settings)
    if arguments.command in FLEET_MUTATIONS:
        return run_fleet_mutation(arguments, base_url, settings)
    return _twin_command(arguments, base_url, settings)


if __name__ == "__main__":
    main()
