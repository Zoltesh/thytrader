"""Confirmation-gated CLI for journals, sentiment/pattern hooks, monitor, and notify."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from thytrader.agent_http import AgentHttpError, require_matching_ops_contract, resolve_api_base_url
from thytrader.agent_orchestration.confirmation import require_mutation_confirmation
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.memory.client import (
    add_journal,
    add_pattern,
    add_sentiment,
    list_journals,
    list_notifications,
    list_patterns,
    list_sentiment,
    memory_status,
    monitor,
    notify,
)
from thytrader.memory.models import (
    ActorOrigin,
    EvidenceKind,
    JournalKind,
    LessonOutcome,
    NotifySeverity,
    PatternStatus,
    RuntimeMode,
    SentimentLabel,
)
from thytrader.operator.redaction import configured_secrets, dumps_redacted

if TYPE_CHECKING:
    from collections.abc import Sequence

_CONFIRM_HELP = (
    "Required for journal, sentiment, pattern, and notify mutations. YOLO never covers this lane."
)
_CONFIRM_MESSAGE = (
    "Pass --confirm to write journals, sentiment, pattern observations, or notifications."
)


class MemoryControlError(RuntimeError):
    """Report a memory CLI usage or API failure without trading authority."""


def _shared_options() -> argparse.ArgumentParser:
    """Global flags that may appear before or after the subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--base-url",
        default=None,
        help="Loopback API origin. Defaults to THYTRADER_API_BASE_URL or settings.",
    )
    return shared


def _origin_arg(parser: argparse.ArgumentParser, *, required: bool) -> None:
    """Add human/agent origin."""
    parser.add_argument(
        "--origin",
        required=required,
        choices=tuple(item.value for item in ActorOrigin),
        help="human or agent. Required so later learning can separate authors.",
    )


def _parser() -> argparse.ArgumentParser:
    """Build the confirmation-gated memory argument parser."""
    shared = _shared_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-memory",
        description=(
            "Journals, sentiment and pattern-learning hooks, monitor, and user "
            "notification through the loopback HTTP API. Mutations require --confirm. "
            "YOLO never skips that gate. This is not operator, data, research, "
            "runtime, or playbook. It does not place orders."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", parents=[trailing], help="Counts and redacted notifier flags.")
    subparsers.add_parser(
        "monitor",
        parents=[trailing],
        help="Watch deployments, recent journals, and notification delivery.",
    )
    journals = subparsers.add_parser(
        "list-journals", parents=[trailing], help="List newest journals."
    )
    _origin_arg(journals, required=False)
    journals.add_argument("--kind", choices=tuple(item.value for item in JournalKind), default=None)
    add_j = subparsers.add_parser("add-journal", parents=[trailing], help="Append one journal row.")
    _origin_arg(add_j, required=True)
    add_j.add_argument("--kind", required=True, choices=tuple(item.value for item in JournalKind))
    add_j.add_argument("--title", required=True)
    add_j.add_argument("--body", required=True)
    add_j.add_argument(
        "--evidence-kind",
        default=EvidenceKind.NONE.value,
        choices=tuple(item.value for item in EvidenceKind),
    )
    add_j.add_argument("--evidence-id", default=None)
    add_j.add_argument("--product-id", default=None)
    add_j.add_argument(
        "--runtime-mode",
        default=RuntimeMode.NONE.value,
        choices=tuple(item.value for item in RuntimeMode),
    )
    add_j.add_argument(
        "--lesson-outcome",
        default=LessonOutcome.NONE.value,
        choices=tuple(item.value for item in LessonOutcome),
    )
    add_j.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    sentiments = subparsers.add_parser(
        "list-sentiment", parents=[trailing], help="List sentiment snapshots."
    )
    _origin_arg(sentiments, required=False)
    add_s = subparsers.add_parser(
        "add-sentiment", parents=[trailing], help="Append one sentiment snapshot."
    )
    _origin_arg(add_s, required=True)
    add_s.add_argument(
        "--label", required=True, choices=tuple(item.value for item in SentimentLabel)
    )
    add_s.add_argument("--product-id", default=None)
    add_s.add_argument("--note", default="")
    add_s.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    patterns = subparsers.add_parser(
        "list-patterns", parents=[trailing], help="List pattern observations."
    )
    _origin_arg(patterns, required=False)
    patterns.add_argument("--pattern-key", default=None)
    add_p = subparsers.add_parser(
        "add-pattern", parents=[trailing], help="Append one pattern-learning hook."
    )
    _origin_arg(add_p, required=True)
    add_p.add_argument("--pattern-key", required=True)
    add_p.add_argument("--name", required=True)
    add_p.add_argument("--hypothesis", required=True)
    add_p.add_argument(
        "--status",
        default=PatternStatus.HYPOTHESIZED.value,
        choices=tuple(item.value for item in PatternStatus),
    )
    add_p.add_argument(
        "--evidence-kind",
        default=EvidenceKind.NONE.value,
        choices=tuple(item.value for item in EvidenceKind),
    )
    add_p.add_argument("--evidence-id", default=None)
    add_p.add_argument("--note", default="")
    add_p.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    notes = subparsers.add_parser(
        "list-notifications", parents=[trailing], help="List notification attempts."
    )
    _origin_arg(notes, required=False)
    send = subparsers.add_parser(
        "notify",
        parents=[trailing],
        help="Request one user notification. Default provider sends nothing.",
    )
    _origin_arg(send, required=True)
    send.add_argument("--title", required=True)
    send.add_argument("--body", required=True)
    send.add_argument(
        "--severity",
        default=NotifySeverity.INFO.value,
        choices=tuple(item.value for item in NotifySeverity),
    )
    send.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    return parser


def _require_confirm(confirm: bool) -> None:
    """Refuse mutations unless `--confirm` is present. YOLO never applies."""
    require_mutation_confirmation(
        confirmed=confirm,
        missing_message=_CONFIRM_MESSAGE,
        error_type=MemoryControlError,
        hard_gate=True,
    )


def _run(arguments: argparse.Namespace) -> str:
    """Execute one memory command against the loopback API."""
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    payload = _dispatch(arguments, base_url)
    return dumps_redacted(payload, secrets)


def _dispatch(arguments: argparse.Namespace, base_url: str) -> object:
    """Route one parsed command to the HTTP helper."""
    command = arguments.command
    if command in {
        "status",
        "monitor",
        "list-journals",
        "list-sentiment",
        "list-patterns",
        "list-notifications",
    }:
        require_matching_ops_contract(base_url)
        return _read(command, arguments, base_url)
    _require_confirm(bool(getattr(arguments, "confirm", False)))
    require_matching_ops_contract(base_url)
    return _mutate(command, arguments, base_url)


def _read(command: str, arguments: argparse.Namespace, base_url: str) -> object:
    """Run one read-only memory command."""
    origin = getattr(arguments, "origin", None)
    if command == "status":
        return memory_status(base_url)
    if command == "monitor":
        return monitor(base_url)
    if command == "list-journals":
        return list_journals(base_url, origin=origin, kind=getattr(arguments, "kind", None))
    if command == "list-sentiment":
        return list_sentiment(base_url, origin=origin)
    if command == "list-patterns":
        return list_patterns(
            base_url,
            origin=origin,
            pattern_key=getattr(arguments, "pattern_key", None),
        )
    return list_notifications(base_url, origin=origin)


def _mutate(command: str, arguments: argparse.Namespace, base_url: str) -> object:
    """Run one confirmation-gated memory mutation."""
    if command == "add-journal":
        return add_journal(base_url, _journal_payload(arguments))
    if command == "add-sentiment":
        return add_sentiment(
            base_url,
            {
                "origin": arguments.origin,
                "label": arguments.label,
                "product_id": arguments.product_id,
                "note": arguments.note,
            },
        )
    if command == "add-pattern":
        return add_pattern(base_url, _pattern_payload(arguments))
    if command == "notify":
        return notify(
            base_url,
            {
                "origin": arguments.origin,
                "title": arguments.title,
                "body": arguments.body,
                "severity": arguments.severity,
            },
        )
    raise AssertionError(f"unsupported memory command: {command}")


def _journal_payload(arguments: argparse.Namespace) -> dict[str, object]:
    """Build a journal write body from CLI flags."""
    return {
        "origin": arguments.origin,
        "kind": arguments.kind,
        "title": arguments.title,
        "body": arguments.body,
        "evidence_kind": arguments.evidence_kind,
        "evidence_id": arguments.evidence_id,
        "product_id": arguments.product_id,
        "runtime_mode": arguments.runtime_mode,
        "lesson_outcome": arguments.lesson_outcome,
    }


def _pattern_payload(arguments: argparse.Namespace) -> dict[str, object]:
    """Build a pattern write body from CLI flags."""
    return {
        "origin": arguments.origin,
        "pattern_key": arguments.pattern_key,
        "name": arguments.name,
        "hypothesis": arguments.hypothesis,
        "status": arguments.status,
        "evidence_kind": arguments.evidence_kind,
        "evidence_id": arguments.evidence_id,
        "note": arguments.note,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Print JSON and exit non-zero on usage or API errors."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else 2) from error
    try:
        sys.stdout.write(f"{_run(arguments)}\n")
    except MemoryControlError as error:
        raise SystemExit(str(error)) from error
    except AgentHttpError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
