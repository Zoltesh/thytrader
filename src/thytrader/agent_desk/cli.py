"""``thytrader-desk``: paper agent desks woken by a model harness on a cadence (ADR 0147)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from thytrader.agent_desk.client import (
    create_desk,
    desk_briefing,
    desk_score,
    list_desks,
    list_wakes,
    record_wake,
    set_desk_status,
    show_desk,
)
from thytrader.agent_desk.harness import BUILTIN_HARNESSES, harness_argv
from thytrader.agent_desk.runner import (
    DEFAULT_TIMEOUT_SECONDS,
    DeskRunError,
    json_object,
    schedule_text,
    wake,
)
from thytrader.agent_http import (
    AgentHttpError,
    base_url_options,
    require_matching_ops_contract,
    resolve_api_base_url,
)
from thytrader.agent_orchestration.confirmation import require_mutation_confirmation
from thytrader.cli_errors import describe_unexpected_failure
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.operator.redaction import configured_secrets, dumps_redacted

if TYPE_CHECKING:
    from collections.abc import Sequence

_MUTATIONS = frozenset({"create", "wake", "record", "status"})
_CONFIRM_HELP = "Required for create, wake, record and status. YOLO never covers this lane."
_CONFIRM_MESSAGE = "Pass --confirm to create a desk, run or record a wake, or change its status."


class DeskControlError(RuntimeError):
    """A desk CLI usage error."""


def _parser() -> argparse.ArgumentParser:
    """Build the ``thytrader-desk`` argument parser."""
    shared = base_url_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-desk",
        description=(
            "Paper agent desks (ADR 0147). A desk is a paper cash pool, a product universe and "
            "caps. Each wake ThyTrader writes a briefing, a model harness (claude, pi, codex, "
            "hermes, or any --command) answers with one JSON decision, and ThyTrader applies it "
            "to a fresh discretionary paper book under the caps and journals the wake. Paper "
            "only; mutations require --confirm."
        ),
        parents=[shared],
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", parents=[trailing], help="Create a paper desk.")
    create.add_argument("--name", required=True)
    create.add_argument("--quote-currency", required=True, choices=("USD", "USDC", "USDT"))
    create.add_argument("--starting-cash", required=True, help="Paper pool, e.g. 10000.")
    create.add_argument(
        "--product",
        action="append",
        required=True,
        dest="products",
        help="Spot product in the quote currency (repeat, up to 20), e.g. BTC-USDC.",
    )
    create.add_argument("--cadence-minutes", type=int, default=30)
    create.add_argument(
        "--harness", default="claude", help=f"Default harness: {', '.join(BUILTIN_HARNESSES)}."
    )
    create.add_argument("--model", default=None, help="Model the harness runs, if it takes one.")
    create.add_argument("--mandate", default="", help="Free-text mandate shown in every briefing.")
    create.add_argument("--max-open-positions", type=int, default=None)
    create.add_argument("--max-position-fraction", default=None, help="Of equity, e.g. 0.25.")
    create.add_argument("--max-entries-per-day", type=int, default=None)
    create.add_argument("--daily-loss-fraction", default=None, help="Of day-open equity: 0.05.")
    create.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    commands.add_parser("list", parents=[trailing], help="List desks.")
    for name, text in (
        ("show", "One desk and its books."),
        ("briefing", "Settle finished books and print the briefing."),
        ("score", "Return since creation beside BTC and universe buy-and-hold."),
    ):
        command = commands.add_parser(name, parents=[trailing], help=text)
        command.add_argument("desk_id")
        if name == "briefing":
            command.add_argument(
                "--prompt", action="store_true", help="Print only the harness prompt text."
            )
    journal = commands.add_parser("journal", parents=[trailing], help="Newest wakes first.")
    journal.add_argument("desk_id")
    journal.add_argument("--limit", type=int, default=20)
    run = commands.add_parser(
        "wake", parents=[trailing], help="Run one wake: briefing, harness, apply, journal."
    )
    run.add_argument("desk_id")
    run.add_argument("--harness", default=None, help="Override the desk's harness.")
    run.add_argument("--model", default=None, help="Override the desk's model.")
    run.add_argument(
        "--command",
        default=None,
        dest="harness_command",
        help="Any harness command line; prompt on stdin, or as {prompt}; {model} substituted.",
    )
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    run.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    record = commands.add_parser(
        "record",
        parents=[trailing],
        help="Apply an answer an agent produced itself from `briefing` (no harness run).",
    )
    record.add_argument("desk_id")
    record.add_argument("--fingerprint", required=True, help="The briefing's fingerprint.")
    record.add_argument("--response-file", required=True, help="Answer text file, or - for stdin.")
    record.add_argument("--harness", required=True, help="Who answered, e.g. claude.")
    record.add_argument("--model", default=None)
    record.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    status = commands.add_parser("status", parents=[trailing], help="Pause, resume or retire.")
    status.add_argument("desk_id")
    status.add_argument("status", choices=("active", "paused", "retired"))
    status.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    schedule = commands.add_parser(
        "schedule", parents=[trailing], help="Print a systemd user timer or crontab line."
    )
    schedule.add_argument("desk_id")
    schedule.add_argument("--style", choices=("systemd", "cron"), default="systemd")
    commands.add_parser("harnesses", parents=[trailing], help="Built-in harness command lines.")
    return parser


def _run(arguments: argparse.Namespace) -> str:
    """Execute one command and return its output."""
    command = arguments.command
    if command == "harnesses":
        return json.dumps(_harnesses(), indent=2)
    if command in _MUTATIONS:
        require_mutation_confirmation(
            confirmed=bool(arguments.confirm),
            missing_message=_CONFIRM_MESSAGE,
            error_type=DeskControlError,
            hard_gate=True,
        )
    settings = Settings()
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    require_matching_ops_contract(base_url)
    if command == "schedule":
        desk = show_desk(base_url, arguments.desk_id)
        cadence = _cadence(desk)
        return schedule_text(arguments.desk_id, cadence_minutes=cadence, style=arguments.style)
    payload = _dispatch(command, arguments, base_url)
    if command == "briefing" and arguments.prompt and isinstance(payload, dict):
        return str(payload.get("prompt", ""))
    return dumps_redacted(payload, configured_secrets(settings))


def _dispatch(command: str, arguments: argparse.Namespace, base_url: str) -> object:
    """Route one parsed command to its HTTP call or the wake runner."""
    if command == "list":
        return list_desks(base_url)
    if command == "show":
        return show_desk(base_url, arguments.desk_id)
    if command == "briefing":
        return desk_briefing(base_url, arguments.desk_id)
    if command == "score":
        return desk_score(base_url, arguments.desk_id)
    if command == "journal":
        return list_wakes(base_url, arguments.desk_id, limit=arguments.limit)
    if command == "create":
        return create_desk(base_url, _create_payload(arguments))
    if command == "status":
        return set_desk_status(base_url, arguments.desk_id, arguments.status)
    if command == "record":
        return record_wake(
            base_url,
            arguments.desk_id,
            {
                "raw_response": _read_response(arguments.response_file),
                "harness": arguments.harness,
                "model": arguments.model,
                "briefing_fingerprint": arguments.fingerprint,
            },
        )
    return wake(
        base_url,
        arguments.desk_id,
        harness=arguments.harness,
        model=arguments.model,
        command=arguments.harness_command,
        timeout_seconds=arguments.timeout,
    )


def _create_payload(arguments: argparse.Namespace) -> dict[str, object]:
    """The create body; unset caps keep the API defaults."""
    caps = {
        key: value
        for key, value in {
            "max_open_positions": arguments.max_open_positions,
            "max_position_fraction": arguments.max_position_fraction,
            "max_entries_per_day": arguments.max_entries_per_day,
            "daily_loss_fraction": arguments.daily_loss_fraction,
        }.items()
        if value is not None
    }
    payload: dict[str, object] = {
        "name": arguments.name,
        "quote_currency": arguments.quote_currency,
        "starting_cash": arguments.starting_cash,
        "products": arguments.products,
        "cadence_minutes": arguments.cadence_minutes,
        "harness": arguments.harness,
        "model": arguments.model,
        "mandate": arguments.mandate,
    }
    if caps:
        payload["caps"] = caps
    return payload


def _read_response(path: str) -> str:
    """The answer text from a file, or stdin for ``-``."""
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _cadence(desk: object) -> int:
    """The desk's cadence from ``show``."""
    cadence = json_object(json_object(desk).get("desk")).get("cadence_minutes")
    if isinstance(cadence, int):
        return cadence
    raise DeskControlError("DESK_NOT_FOUND: the desk has no cadence.")


def _harnesses() -> dict[str, object]:
    """Every built-in harness with its command line and note."""
    return {
        name: {
            "argv": list(harness_argv(template, model="{model}")),
            "prompt": "argv" if "{prompt}" in template.argv else "stdin",
            "note": template.note,
        }
        for name, template in BUILTIN_HARNESSES.items()
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Print the result and exit non-zero on usage, API or harness setup errors."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else 2) from error
    try:
        sys.stdout.write(f"{_run(arguments).rstrip()}\n")
    except (DeskControlError, DeskRunError, AgentHttpError) as error:
        raise SystemExit(str(error)) from error
    except Exception as error:
        message = describe_unexpected_failure(
            error,
            lane="Desk command",
            safety="Check `thytrader-desk journal` before repeating a wake or record.",
        )
        raise SystemExit(message) from error


if __name__ == "__main__":
    main()
