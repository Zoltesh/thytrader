"""Playbook CLI that sequences existing lane CLIs without live authority."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
from typing import TYPE_CHECKING

from thytrader.agent_http import AgentHttpError, require_matching_ops_contract, resolve_api_base_url
from thytrader.agent_orchestration.client import fetch_orchestration_status
from thytrader.agent_orchestration.models import (
    PlaybookIdentities,
    PlaybookRun,
    PlaybookStep,
)
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.data_control.cli import main as data_main
from thytrader.operator.cli import main as operator_main
from thytrader.operator.redaction import configured_secrets, dumps_redacted
from thytrader.operator.status import EXIT_DEGRADED, EXIT_FAILED, EXIT_HEALTHY, EXIT_USAGE
from thytrader.research.mutation_cli import main as research_main
from thytrader.runtime_control.cli import main as runtime_main

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class PlaybookError(RuntimeError):
    """Report a redacted playbook failure without live authority."""


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
    """Build the playbook argument parser."""
    shared = _shared_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-playbook",
        description=(
            "Sequence data → research → optional paper by calling existing CLIs. "
            "Mutations still require --confirm unless YOLO covers that tier. "
            "This CLI cannot start live trading and is not the operator, data, "
            "research, or runtime skill."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "status",
        parents=[trailing],
        help="Show Safe vs YOLO mode and the playbook sequence.",
    )
    run = subparsers.add_parser(
        "run",
        parents=[trailing],
        help="Run data health, optional ingest, research, and optional paper.",
    )
    run.add_argument("--product-id", default="BTC-USD")
    run.add_argument("--timeframe", default="1h", choices=("1h", "5m"))
    run.add_argument("--lookback-hours", type=int, default=168)
    run.add_argument(
        "--ensure-watch",
        action="store_true",
        help="Add the product/timeframe to the watchlist when missing.",
    )
    run.add_argument(
        "--ingest",
        action="store_true",
        help="Queue complete-only ingest (implies --ensure-watch).",
    )
    run.add_argument("--create-draft", action="store_true")
    run.add_argument("--publish", action="store_true")
    run.add_argument("--strategy-id", default=None)
    run.add_argument("--backtest-file", default=None)
    run.add_argument("--paper-cash", default=None, help="Start paper with this cash decimal.")
    run.add_argument("--strategy-fingerprint", default=None)
    run.add_argument(
        "--confirm",
        action="store_true",
        help="Forwarded to child mutation CLIs. Default remains required unless YOLO applies.",
    )
    return parser


def _base_url(arguments: argparse.Namespace) -> str:
    """Resolve the loopback API origin for this playbook command."""
    settings = Settings()
    return resolve_api_base_url(explicit=arguments.base_url, settings=settings)


def _status_payload(base_url: str) -> dict[str, object]:
    """Return orchestration status plus the playbook CLI identity."""
    require_matching_ops_contract(base_url)
    status = fetch_orchestration_status(base_url)
    payload = status.model_dump(mode="json")
    payload["cli"] = "thytrader-playbook"
    payload["live_started"] = False
    return payload


def _invoke_cli(main: Callable[[Sequence[str] | None], None], argv: list[str]) -> str:
    """Run one existing CLI main and return stdout JSON, mapping failures to PlaybookError."""
    buffer = StringIO()
    try:
        with redirect_stdout(buffer):
            main(argv)
    except SystemExit as error:
        output = buffer.getvalue().rstrip("\n")
        code = error.code
        if code in {0, None, EXIT_HEALTHY}:
            return output
        if isinstance(code, str):
            raise PlaybookError(code) from error
        if int(code) == EXIT_DEGRADED:
            return output
        if int(code) == EXIT_FAILED:
            raise PlaybookError(output or "Child CLI reported a failed status.") from error
        raise PlaybookError(output or f"Child CLI exited {code}.") from error
    return buffer.getvalue().rstrip("\n")


def _parse_object(text: str, label: str) -> dict[str, object]:
    """Parse one JSON object from a child CLI."""
    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise PlaybookError(f"{label} did not return JSON.") from error
    if not isinstance(payload, dict):
        raise PlaybookError(f"{label} did not return a JSON object.")
    typed: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise PlaybookError(f"{label} JSON object had a non-string key.")
        typed[key] = value
    return typed


def _child_argv(arguments: argparse.Namespace, command: list[str]) -> list[str]:
    """Prefix a child command with an optional forwarded --base-url."""
    if arguments.base_url:
        return [*command, "--base-url", arguments.base_url]
    return command


def _confirm_argv(confirm: bool) -> list[str]:
    """Forward --confirm to a child mutation when the playbook received it."""
    return ["--confirm"] if confirm else []


def _watch_present(payload: dict[str, object], *, product_id: str, timeframe: str) -> bool:
    """True when the watchlist already has this product and timeframe."""
    targets = payload.get("targets")
    if not isinstance(targets, list):
        return False
    for item in targets:
        if not isinstance(item, dict):
            continue
        if item.get("product_id") == product_id and item.get("timeframe") == timeframe:
            return True
    return False


def _step(
    name: str,
    cli: str,
    argv: list[str],
    main: Callable[[Sequence[str] | None], None],
) -> PlaybookStep:
    """Invoke one child CLI and capture its JSON payload."""
    output = _invoke_cli(main, argv)
    payload = _parse_object(output, name) if output else {}
    return PlaybookStep(name=name, cli=cli, argv=tuple(argv), payload=payload)


def _identities_model(collected: dict[str, str]) -> PlaybookIdentities:
    """Keep only known playbook identity keys from child CLI output."""
    return PlaybookIdentities(
        strategy_id=collected.get("strategy_id"),
        strategy_fingerprint=collected.get("strategy_fingerprint"),
        run_fingerprint=collected.get("run_fingerprint"),
        result_fingerprint=collected.get("result_fingerprint"),
        deployment_id=collected.get("deployment_id"),
        deployment_mode="paper" if collected.get("deployment_mode") == "paper" else None,
    )


def _identity_str(payload: object, key: str) -> str | None:
    """Return one string identity from a child payload when present."""
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _run_data_steps(
    arguments: argparse.Namespace,
    steps: list[PlaybookStep],
) -> None:
    """Check health and optionally watch or ingest through existing CLIs."""
    steps.append(
        _step("health", "thytrader-operator", _child_argv(arguments, ["health"]), operator_main)
    )
    health = steps[-1].payload
    if health.get("overall_status") == "failed":
        raise PlaybookError("Operator health is failed; stop before data or research mutations.")
    steps.append(
        _step(
            "watchlist",
            "thytrader-data",
            _child_argv(arguments, ["watchlist-list"]),
            data_main,
        )
    )
    watched = _watch_present(
        steps[-1].payload,
        product_id=arguments.product_id,
        timeframe=arguments.timeframe,
    )
    if (arguments.ensure_watch or arguments.ingest) and not watched:
        add_argv = _child_argv(
            arguments,
            [
                "watch-add",
                "--product-id",
                arguments.product_id,
                "--timeframe",
                arguments.timeframe,
                "--lookback-hours",
                str(arguments.lookback_hours),
                *_confirm_argv(arguments.confirm),
            ],
        )
        steps.append(_step("watch-add", "thytrader-data", add_argv, data_main))
    if not arguments.ingest:
        return
    ingest_argv = _child_argv(
        arguments,
        [
            "ingest",
            "--product-id",
            arguments.product_id,
            "--timeframe",
            arguments.timeframe,
            *_confirm_argv(arguments.confirm),
        ],
    )
    steps.append(_step("ingest", "thytrader-data", ingest_argv, data_main))


def _run_research_steps(
    arguments: argparse.Namespace,
    steps: list[PlaybookStep],
    identities: dict[str, str],
) -> str | None:
    """Create, publish, and/or backtest through thytrader-research."""
    strategy_id = arguments.strategy_id
    fingerprint = arguments.strategy_fingerprint
    if arguments.create_draft:
        draft_argv = _child_argv(
            arguments,
            [
                "create-draft",
                "--product-id",
                arguments.product_id,
                "--timeframe",
                arguments.timeframe,
                *_confirm_argv(arguments.confirm),
            ],
        )
        steps.append(_step("create-draft", "thytrader-research", draft_argv, research_main))
        created_id = _identity_str(steps[-1].payload, "strategy_id")
        if created_id is not None:
            strategy_id = created_id
            identities["strategy_id"] = created_id
    if arguments.publish:
        if strategy_id is None:
            raise PlaybookError("Publish requires --strategy-id or --create-draft in the same run.")
        publish_argv = _child_argv(
            arguments,
            ["publish", "--strategy-id", str(strategy_id), *_confirm_argv(arguments.confirm)],
        )
        steps.append(_step("publish", "thytrader-research", publish_argv, research_main))
        published = steps[-1].payload
        published_id = _identity_str(published, "strategy_id")
        published_fp = _identity_str(published, "strategy_fingerprint")
        if published_id is not None:
            identities["strategy_id"] = published_id
        if published_fp is not None:
            fingerprint = published_fp
            identities["strategy_fingerprint"] = published_fp
    if arguments.backtest_file is None:
        return fingerprint
    backtest_argv = _child_argv(
        arguments,
        [
            "submit-backtest",
            "--file",
            arguments.backtest_file,
            *_confirm_argv(arguments.confirm),
        ],
    )
    steps.append(_step("submit-backtest", "thytrader-research", backtest_argv, research_main))
    result = steps[-1].payload
    for key in ("run_fingerprint", "result_fingerprint"):
        value = _identity_str(result, key)
        if value is not None:
            identities[key] = value
    return fingerprint


def _run_paper_step(
    arguments: argparse.Namespace,
    steps: list[PlaybookStep],
    identities: dict[str, str],
    fingerprint: str | None,
) -> None:
    """Start paper through thytrader-runtime; never constructs a live start."""
    if arguments.paper_cash is None:
        return
    if fingerprint is None:
        raise PlaybookError(
            "Paper start requires --strategy-fingerprint or --publish in the same run."
        )
    paper_argv = _child_argv(
        arguments,
        [
            "start",
            "--strategy-fingerprint",
            fingerprint,
            "--mode",
            "paper",
            "--cash",
            arguments.paper_cash,
            *_confirm_argv(arguments.confirm),
        ],
    )
    steps.append(_step("start-paper", "thytrader-runtime", paper_argv, runtime_main))
    started = steps[-1].payload
    if started.get("mode") == "live":
        raise PlaybookError("Playbook refused a live deployment.")
    deployment_id = started.get("id")
    if deployment_id is not None:
        identities["deployment_id"] = str(deployment_id)
        identities["deployment_mode"] = "paper"


def _run_playbook(arguments: argparse.Namespace, base_url: str) -> dict[str, object]:
    """Sequence health → optional data → optional research → optional paper."""
    require_matching_ops_contract(base_url)
    orchestration = fetch_orchestration_status(base_url)
    steps: list[PlaybookStep] = []
    identities: dict[str, str] = {}
    _run_data_steps(arguments, steps)
    fingerprint = _run_research_steps(arguments, steps, identities)
    _run_paper_step(arguments, steps, identities, fingerprint)
    dumped: dict[str, object] = PlaybookRun(
        confirmation_mode=orchestration.confirmation_mode,
        yolo_enabled=orchestration.yolo_enabled,
        yolo_tiers=orchestration.yolo_tiers,
        steps=tuple(steps),
        identities=_identities_model(identities),
    ).model_dump(mode="json", exclude_none=True)
    return dumped


def _run(arguments: argparse.Namespace) -> str:
    """Execute status or run against the loopback API."""
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = _base_url(arguments)
    if arguments.command == "status":
        payload: object = _status_payload(base_url)
    elif arguments.command == "run":
        payload = _run_playbook(arguments, base_url)
    else:
        raise AssertionError(f"unsupported playbook command: {arguments.command}")
    return dumps_redacted(payload, secrets)


def main(argv: Sequence[str] | None = None) -> None:
    """Print playbook JSON; never starts live trading."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else EXIT_USAGE) from error
    try:
        output = _run(arguments)
    except PlaybookError as error:
        raise SystemExit(str(error)) from error
    except AgentHttpError as error:
        raise SystemExit(str(error)) from error
    except Exception as error:
        message = "Playbook failed safely; live trading was not started."
        raise SystemExit(message) from error
    sys.stdout.write(f"{output}\n")
    raise SystemExit(EXIT_HEALTHY)


if __name__ == "__main__":
    main()
