"""``thytrader-runtime portfolio-*`` commands: deploy and control a portfolio (ADR 0091).

``portfolio-start`` starts (or attaches) one bot per sleeve at the reviewed portfolio
revision; ``portfolio-pause`` / ``portfolio-resume`` / ``portfolio-stop`` act on every
sleeve, or on one with ``--sleeve-id`` (a sleeve id or its strategy id). The gates match
single deployments: ``--confirm`` unless YOLO covers the portfolio's mode tier, live start
and live resume also ``--i-understand-live`` (YOLO never skips it), and
``portfolio-reset-breaker`` always needs ``--confirm``. ``portfolio-status`` is read-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.agent_http import require_matching_ops_contract
from thytrader.agent_orchestration.confirmation import (
    require_mutation_confirmation,
    require_paper_runtime_confirmation,
)
from thytrader.runtime_control.client import (
    RuntimeControlError,
    portfolio_action,
    reset_portfolio_breaker,
    show_portfolio,
    show_portfolio_deployment,
    start_portfolio,
)

if TYPE_CHECKING:
    import argparse

    from thytrader.config import Settings

PORTFOLIO_COMMANDS = frozenset(
    {
        "portfolio-status",
        "portfolio-start",
        "portfolio-pause",
        "portfolio-resume",
        "portfolio-stop",
        "portfolio-reset-breaker",
    }
)
_CONFIRM_MESSAGE = (
    "Pass --confirm to start, pause, resume, or stop a portfolio. Live start and live resume "
    "also require --i-understand-live."
)
_RESET_MESSAGE = "Pass --confirm to reset a portfolio breaker. YOLO never covers this command."
_SLEEVE_HELP = "Act on one sleeve only: its sleeve id or its strategy id."


def add_portfolio_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
    *,
    confirm_help: str,
    live_help: str,
) -> None:
    """Register the portfolio deployment commands."""
    status = subparsers.add_parser(
        "portfolio-status",
        parents=[trailing],
        help="Show a portfolio's deployment: state, each sleeve's bot, breakers, exposure.",
    )
    status.add_argument("--portfolio-id", required=True)
    start = subparsers.add_parser(
        "portfolio-start",
        parents=[trailing],
        help=(
            "Start (or attach) one bot per sleeve: paper cash or live allocated capital is "
            "weight x capital. Nothing starts if any sleeve is refused."
        ),
    )
    start.add_argument("--portfolio-id", required=True)
    start.add_argument(
        "--revision", type=int, required=True, help="The portfolio revision you reviewed (show)."
    )
    start.add_argument("--sleeve-id", default=None, help=_SLEEVE_HELP)
    start.add_argument("--maker-fee-rate", default=None, help="Paper only, with taker.")
    start.add_argument("--taker-fee-rate", default=None, help="Paper only, with maker.")
    start.add_argument("--confirm", action="store_true", help=confirm_help)
    start.add_argument("--i-understand-live", action="store_true", help=live_help)
    for action in ("pause", "resume", "stop"):
        command = subparsers.add_parser(
            f"portfolio-{action}",
            parents=[trailing],
            help=f"{action.title()} every sleeve of a portfolio (or one with --sleeve-id).",
        )
        command.add_argument("--portfolio-id", required=True)
        command.add_argument("--sleeve-id", default=None, help=_SLEEVE_HELP)
        command.add_argument("--confirm", action="store_true", help=confirm_help)
        if action == "resume":
            command.add_argument("--i-understand-live", action="store_true", help=live_help)
        if action == "stop":
            command.add_argument(
                "--flatten",
                action="store_true",
                help=(
                    "Exit every sleeve's positions at market, then cancel remainders. The "
                    "default managed stop keeps protective orders until positions close."
                ),
            )
    reset = subparsers.add_parser(
        "portfolio-reset-breaker",
        parents=[trailing],
        help=(
            "Clear a latched portfolio daily-loss or drawdown breaker (sleeves stay paused "
            "until portfolio-resume)."
        ),
    )
    reset.add_argument("--portfolio-id", required=True)
    reset.add_argument("--confirm", action="store_true", help=confirm_help)


def run_portfolio_command(
    arguments: argparse.Namespace, base_url: str, settings: Settings
) -> object:
    """Run one ``portfolio-*`` command."""
    command = arguments.command
    portfolio_id = str(_uuid(arguments.portfolio_id, "--portfolio-id"))
    if command == "portfolio-status":
        require_matching_ops_contract(base_url)
        return show_portfolio_deployment(base_url, portfolio_id)
    if command == "portfolio-reset-breaker":
        require_mutation_confirmation(
            confirmed=bool(arguments.confirm),
            missing_message=_RESET_MESSAGE,
            error_type=RuntimeControlError,
            hard_gate=True,
        )
        require_matching_ops_contract(base_url)
        return reset_portfolio_breaker(base_url, portfolio_id, settings=settings)
    portfolio = _portfolio(base_url, portfolio_id)
    mode = _text(portfolio, "mode")
    sleeve_id = _sleeve(portfolio, getattr(arguments, "sleeve_id", None))
    acknowledged = bool(getattr(arguments, "i_understand_live", False))
    if command in {"portfolio-start", "portfolio-resume"} and mode == "live" and not acknowledged:
        raise RuntimeControlError("Live trading spends real money. Pass --i-understand-live.")
    require_paper_runtime_confirmation(
        confirmed=bool(arguments.confirm),
        missing_message=_CONFIRM_MESSAGE,
        error_type=RuntimeControlError,
        base_url=base_url,
        command=command,
        deployment_mode=lambda: mode,
    )
    require_matching_ops_contract(base_url)
    live_ack = mode == "live" and acknowledged
    if command == "portfolio-start":
        return start_portfolio(
            base_url,
            portfolio_id,
            revision=int(arguments.revision),
            sleeve_id=sleeve_id,
            maker_fee_rate=arguments.maker_fee_rate,
            taker_fee_rate=arguments.taker_fee_rate,
            i_understand_live=live_ack,
            settings=settings,
        )
    return portfolio_action(
        base_url,
        portfolio_id,
        command.removeprefix("portfolio-"),
        sleeve_id=sleeve_id,
        flatten=bool(getattr(arguments, "flatten", False)),
        i_understand_live=live_ack,
        settings=settings,
    )


def _portfolio(base_url: str, portfolio_id: str) -> dict[str, object]:
    """Read the portfolio (mode and sleeves decide the gates and the target)."""
    payload = show_portfolio(base_url, portfolio_id)
    if not isinstance(payload, dict):
        raise RuntimeControlError("Portfolio response was not a JSON object.")
    return {str(key): value for key, value in payload.items()}


def _text(payload: dict[str, object], key: str) -> str:
    """One string field of the portfolio response."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise RuntimeControlError(f"Portfolio response omitted {key}.")
    return value


def _sleeve(portfolio: dict[str, object], identity: str | None) -> str | None:
    """Resolve --sleeve-id (a sleeve id or its strategy id) to the sleeve id."""
    if identity is None:
        return None
    wanted = str(_uuid(identity, "--sleeve-id"))
    sleeves = portfolio.get("sleeves")
    if isinstance(sleeves, list):
        for item in sleeves:
            if not isinstance(item, dict):
                continue
            sleeve_id = item.get("sleeve_id")
            if wanted in {sleeve_id, item.get("strategy_id")} and isinstance(sleeve_id, str):
                return sleeve_id
    raise RuntimeControlError(
        f"{wanted} is neither a sleeve nor a sleeve strategy of this portfolio."
    )


def _uuid(value: object, flag: str) -> UUID:
    """Parse one UUID flag."""
    try:
        return UUID(str(value))
    except ValueError as error:
        raise RuntimeControlError(f"{flag} must be a UUID.") from error
