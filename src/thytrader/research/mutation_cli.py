"""Confirmation-gated CLI for strategies (create, save, delete) and backtests/studies.

A strategy is one mutable object (ADR 0082). ``submit-backtest`` and
``submit-study`` name strategies by ``strategy_id``; the server snapshots the
current definition and reports the snapshot ``strategy_fingerprint``. This CLI
has no paper or live authority.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.agent_http import AgentHttpError
from thytrader.backtest.submission import (
    BacktestStartRequest,
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
)
from thytrader.cli_errors import describe_unexpected_failure
from thytrader.exit_codes import EXIT_HEALTHY, EXIT_USAGE
from thytrader.ops_contract import STALE_IMAGE_REBUILD
from thytrader.research.catalog import (
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogUnavailableError,
)
from thytrader.research.dataset_binding import DatasetsMissingError
from thytrader.research.mutation import ResearchMutationError
from thytrader.research.mutation_common import ResearchCliError, _is_mutation
from thytrader.research.mutation_http import _dispatch_http
from thytrader.research.mutation_local import _dispatch_local
from thytrader.research.mutation_parser import _parser
from thytrader.research.studies import ResearchStudyError, StudyPlanningError
from thytrader.strategies.library import StrategyLibraryError

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

__all__ = [
    "BacktestStartRequest",
    "ResearchCliError",
    "_is_mutation",
    "_parser",
    "invalid_draft_notice",
    "main",
]


def _validation_error_message(error: ValidationError) -> str:
    """Return the first semantic Pydantic error without wrapping it as a safe no-op."""
    issues = error.errors()
    if not issues:
        return "Document failed validation."
    return str(issues[0].get("msg", "Document failed validation."))


# submit-study already names its strategy-scoped readback (research.http); only the
# synchronous backtest submit needs a hint added here.
_SUBMIT_TIMEOUT_HINTS: dict[str, str] = {
    "submit-backtest": (
        "The backtest may still be running (or queued) in the research worker: check "
        "`thytrader-research list-results` or `thytrader-research list-research-jobs` before "
        "submitting again, or re-run with --async to queue it (HTTP 202) and poll "
        "`show-research-job`."
    ),
}


def _agent_http_error_message(error: AgentHttpError, command: str) -> str:
    """Explain an API failure: rebuild hints for stale images, state checks for timeouts."""
    message = str(error)
    if error.timed_out and command in _SUBMIT_TIMEOUT_HINTS:
        return f"{message} {_SUBMIT_TIMEOUT_HINTS[command]}"
    lowered = message.lower()
    stale_engine_demand = "engine_contract_version" in lowered and "was removed" not in lowered
    if "422" in message and stale_engine_demand:
        return f"{message} {STALE_IMAGE_REBUILD}"
    return message


_DRAFT_VERBS: dict[str, str] = {
    "create-strategy": "created",
    "save-strategy": "saved",
    "import-strategy": "imported",
    "clone-strategy": "cloned",
    "show-strategy": "stored",
}


def invalid_draft_notice(command: str, output: str) -> str | None:
    """Return the stderr line for a strategy command whose result is an invalid draft.

    An agent that parses only part of the JSON must still notice that the strategy it
    just wrote cannot be backtested or deployed, so the CLI also prints one human line,
    for example ``saved as an INVALID draft (2 issues): entry.when.all[0].left.input:
    unknown field "input"``.
    """
    verb = _DRAFT_VERBS.get(command)
    if verb is None:
        return None
    try:
        payload = json.loads(output)
    except ValueError:
        return None
    validation = payload.get("validation") if isinstance(payload, dict) else None
    if not isinstance(validation, dict) or validation.get("valid") is not False:
        return None
    issues = validation.get("issues")
    listed = issues if isinstance(issues, list) else []
    count = len(listed)
    first = listed[0] if listed and isinstance(listed[0], dict) else {}
    detail = f"{first.get('loc', '(document)')}: {first.get('message', 'invalid')}"
    noun = "issue" if count == 1 else "issues"
    return (
        f"thytrader-research: {verb} as an INVALID draft ({count} {noun}): {detail}. "
        "It cannot be backtested, studied, or deployed until validation.valid is true."
    )


def _command_output(arguments: argparse.Namespace) -> str:
    """Dispatch one research command and map domain failures to CLI exits."""
    try:
        if arguments.local:
            return asyncio.run(_dispatch_local(arguments))
        return _dispatch_http(arguments)
    except (
        ResearchCliError,
        ResearchMutationError,
        BacktestSubmissionRejectedError,
        DatasetsMissingError,
        StudyPlanningError,
        StudyCatalogNotFoundError,
        StudyCatalogIntegrityError,
        StrategyLibraryError,
    ) as error:
        raise SystemExit(str(error)) from error
    except AgentHttpError as error:
        raise SystemExit(_agent_http_error_message(error, arguments.command)) from error
    except ResearchStudyError as error:
        raise SystemExit("Research study submission is unavailable.") from error
    except StudyCatalogUnavailableError as error:
        raise SystemExit("Research study catalog is unavailable.") from error
    except BacktestSubmissionError as error:
        raise SystemExit("Backtest submission is unavailable.") from error
    except ValidationError as error:
        raise SystemExit(_validation_error_message(error)) from error


def main(argv: Sequence[str] | None = None) -> None:
    """Run one research command; mutations require --confirm."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else EXIT_USAGE) from error
    if arguments.local and arguments.base_url:
        raise SystemExit("Use either --local or --base-url, not both.")
    try:
        output = _command_output(arguments)
    except Exception as error:
        message = describe_unexpected_failure(
            error, lane="Research command", safety="Paper and live state were not changed."
        )
        raise SystemExit(message) from error
    sys.stdout.write(f"{output}\n")
    notice = invalid_draft_notice(arguments.command, output)
    if notice is not None:
        sys.stderr.write(f"{notice}\n")
    raise SystemExit(EXIT_HEALTHY)


if __name__ == "__main__":
    main()
