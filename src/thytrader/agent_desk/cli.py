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
    update_desk,
)
from thytrader.agent_desk.harness import BUILTIN_HARNESSES, harness_argv
from thytrader.agent_desk.runner import (
    DEFAULT_TIMEOUT_SECONDS,
    DeskRunError,
    json_object,
    resolve_template,
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
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.operator.redaction import configured_secrets, dumps_redacted

if TYPE_CHECKING:
    from collections.abc import Sequence

_MUTATIONS = frozenset({"create", "wake", "record", "status", "update"})
_CONFIRM_HELP = "Required for create, update, wake, record and status. YOLO never covers this lane."
_CONFIRM_MESSAGE = (
    "Pass --confirm to create or update a desk, run or record a wake, or change its status."
)


class DeskControlError(RuntimeError):
    """A desk CLI usage error."""


def _parser() -> argparse.ArgumentParser:
    """Build the ``thytrader-desk`` argument parser."""
    shared = base_url_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-desk",
        description=(
            "Agent desks (ADR 0147, 0148). A desk is a cash pool, a product universe and "
            "caps. Each wake ThyTrader writes a briefing, a model harness (claude, pi, codex, "
            "hermes, or any --command) answers with one JSON decision, and ThyTrader applies it "
            "to a fresh discretionary book under the caps and journals the wake. Desks are paper "
            "unless created with --mode live --i-understand-live, whose orders also need the risk "
            "policy to reserve the desk in desk_allocations. Mutations require --confirm."
        ),
        parents=[shared],
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create",
        parents=[trailing],
        help="Create a desk (paper; live with --mode live --i-understand-live).",
    )
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
        "--timeframe",
        default="5m",
        choices=EXECUTION_TIMEFRAMES,
        help="Book clock its stop and target are worked on (default 5m; 1h suits swing desks).",
    )
    create.add_argument(
        "--harness", default="claude", help=f"Default harness: {', '.join(BUILTIN_HARNESSES)}."
    )
    create.add_argument("--model", default=None, help="Model the harness runs, if it takes one.")
    create.add_argument("--mandate", default="", help="Free-text mandate shown in every briefing.")
    create.add_argument("--max-open-positions", type=int, default=None)
    create.add_argument("--max-position-fraction", default=None, help="Of equity, e.g. 0.25.")
    create.add_argument("--max-entries-per-day", type=int, default=None)
    create.add_argument("--daily-loss-fraction", default=None, help="Of day-open equity: 0.05.")
    create.add_argument(
        "--mode",
        choices=("paper", "live"),
        default="paper",
        help="live trades real money (ADR 0148); it also needs --i-understand-live.",
    )
    create.add_argument(
        "--i-understand-live",
        action="store_true",
        help="Acknowledge that a live desk's model spends real money. YOLO never covers it.",
    )
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
    run.add_argument(
        "--always",
        action="store_true",
        help=(
            "Run the harness even when nothing changed since the newest wake. Without it, an "
            "unchanged picture (same closed bars, flat desk, same harness and model) journals "
            "a skipped wake with no model call."
        ),
    )
    run.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    _update_parser(commands, trailing)
    record = commands.add_parser(
        "record",
        parents=[trailing],
        help="Apply an answer an agent produced itself from `briefing` (no harness run).",
    )
    record.add_argument("desk_id")
    record.add_argument("--fingerprint", required=True, help="The briefing's fingerprint.")
    record.add_argument(
        "--runner-fingerprint",
        default=None,
        help="Who answered (sha256 digest); later wakes skip only for the same runner.",
    )
    record.add_argument(
        "--material-fingerprint",
        default=None,
        help="The briefing's material_fingerprint, so a later unchanged wake can be skipped.",
    )
    record.add_argument(
        "--config-fingerprint",
        required=True,
        help=(
            "The briefing's config_fingerprint; an open is refused DESK_CONFIG_CHANGED if the "
            "desk's harness, model, mandate or caps changed since."
        ),
    )
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
    schedule.add_argument(
        "--command",
        default=None,
        dest="harness_command",
        help="Harness command line to schedule (required when the desk's harness is not built in).",
    )
    commands.add_parser("harnesses", parents=[trailing], help="Built-in harness command lines.")
    return parser


def _update_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """``update``: change a desk's harness, model, cadence, mandate, caps or name."""
    update = commands.add_parser(
        "update",
        parents=[trailing],
        help=(
            "Change a desk's harness, model, cadence, mandate, caps or name (mode, pool, "
            "universe and timeframe are fixed). A live desk's harness, model, mandate or caps "
            "also need --i-understand-live. A new cadence needs a new `schedule` timer."
        ),
    )
    update.add_argument("desk_id")
    update.add_argument("--name", default=None)
    update.add_argument(
        "--harness", default=None, help=f"Built in: {', '.join(BUILTIN_HARNESSES)}."
    )
    update.add_argument("--model", default=None)
    update.add_argument("--clear-model", action="store_true", help="Unset the model.")
    update.add_argument("--cadence-minutes", type=int, default=None)
    update.add_argument("--mandate", default=None)
    update.add_argument("--max-open-positions", type=int, default=None)
    update.add_argument("--max-position-fraction", default=None)
    update.add_argument("--max-entries-per-day", type=int, default=None)
    update.add_argument("--daily-loss-fraction", default=None)
    update.add_argument(
        "--expected-revision", type=int, default=None, help="Refuse if the desk moved on."
    )
    update.add_argument(
        "--i-understand-live",
        action="store_true",
        help="Acknowledge changing how a live desk trades real money. YOLO never covers it.",
    )
    update.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)


def _run(arguments: argparse.Namespace) -> str:
    """Execute one command and return its output."""
    command = arguments.command
    if command == "harnesses":
        return json.dumps(_harnesses(), indent=2)
    if command == "create" and arguments.mode == "live" and not arguments.i_understand_live:
        raise DeskControlError(
            "A live desk trades real money: pass --i-understand-live with --mode live."
        )
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
        desk = json_object(json_object(show_desk(base_url, arguments.desk_id)).get("desk"))
        cadence = desk.get("cadence_minutes")
        if not isinstance(cadence, int):
            raise DeskControlError("DESK_NOT_FOUND: the desk has no cadence.")
        if arguments.harness_command is None:
            resolve_template(str(desk.get("harness") or ""), None)
        elif desk.get("mode") == "live" and desk.get("harness") in BUILTIN_HARNESSES:
            raise DeskControlError(
                "DESK_LIVE_OVERRIDE_REFUSED: a live desk with a built-in harness runs it as built "
                "in; schedule it without --command."
            )
        return schedule_text(
            arguments.desk_id,
            cadence_minutes=cadence,
            style=arguments.style,
            base_url=base_url,
            harness_command=arguments.harness_command,
        )
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
    if command == "update":
        return update_desk(base_url, arguments.desk_id, _update_payload(arguments))
    if command == "record":
        return record_wake(
            base_url,
            arguments.desk_id,
            {
                "raw_response": _read_response(arguments.response_file),
                "harness": arguments.harness,
                "model": arguments.model,
                "briefing_fingerprint": arguments.fingerprint,
                "material_fingerprint": arguments.material_fingerprint,
                "config_fingerprint": arguments.config_fingerprint,
                "runner_fingerprint": arguments.runner_fingerprint,
            },
        )
    return wake(
        base_url,
        arguments.desk_id,
        harness=arguments.harness,
        model=arguments.model,
        command=arguments.harness_command,
        timeout_seconds=arguments.timeout,
        always=arguments.always,
    )


def _caps(arguments: argparse.Namespace) -> dict[str, object]:
    """The caps given on the command line; unset ones are left out."""
    return {
        key: value
        for key, value in {
            "max_open_positions": arguments.max_open_positions,
            "max_position_fraction": arguments.max_position_fraction,
            "max_entries_per_day": arguments.max_entries_per_day,
            "daily_loss_fraction": arguments.daily_loss_fraction,
        }.items()
        if value is not None
    }


def _update_payload(arguments: argparse.Namespace) -> dict[str, object]:
    """The update body: only the fields given."""
    payload: dict[str, object] = {
        key: value
        for key, value in {
            "name": arguments.name,
            "harness": arguments.harness,
            "model": arguments.model,
            "cadence_minutes": arguments.cadence_minutes,
            "mandate": arguments.mandate,
            "expected_revision": arguments.expected_revision,
        }.items()
        if value is not None
    }
    caps = _caps(arguments)
    if caps:
        payload["caps"] = caps
    if arguments.clear_model:
        payload["clear_model"] = True
    if arguments.i_understand_live:
        payload["i_understand_live"] = True
    return payload


def _create_payload(arguments: argparse.Namespace) -> dict[str, object]:
    """The create body; unset caps keep the API defaults."""
    caps = _caps(arguments)
    payload: dict[str, object] = {
        "name": arguments.name,
        "quote_currency": arguments.quote_currency,
        "starting_cash": arguments.starting_cash,
        "products": arguments.products,
        "cadence_minutes": arguments.cadence_minutes,
        "timeframe": arguments.timeframe,
        "harness": arguments.harness,
        "model": arguments.model,
        "mandate": arguments.mandate,
        "mode": arguments.mode,
        "i_understand_live": bool(arguments.i_understand_live),
    }
    if caps:
        payload["caps"] = caps
    return payload


def _read_response(path: str) -> str:
    """The answer text from a file, or stdin for ``-``."""
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


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
