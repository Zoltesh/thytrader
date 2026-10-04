"""Confirmation-gated CLI for strategies (create, save, delete) and backtests/studies.

A strategy is one mutable object (ADR 0082). ``submit-backtest`` and
``submit-study`` name strategies by ``strategy_id``; the server snapshots the
current definition and reports the snapshot ``strategy_fingerprint``. This CLI
has no paper or live authority.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError

from thytrader.agent_http import AgentHttpError, require_matching_ops_contract, resolve_api_base_url
from thytrader.agent_orchestration.confirmation import require_mutation_confirmation
from thytrader.agent_orchestration.models import YoloTier
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestEvaluationWindow,
    BacktestResult,
    backtest_evaluation_window,
    backtest_result_fingerprint,
)
from thytrader.backtest.submission import (
    BacktestStartRequest,
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    PostgresBacktestSubmitter,
)
from thytrader.cli_errors import describe_unexpected_failure
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.data_control.service import ingestion_provider
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import EXECUTION_TIMEFRAMES, published_execution_timeframe
from thytrader.operator.status import EXIT_HEALTHY, EXIT_USAGE
from thytrader.ops_contract import STALE_IMAGE_REBUILD
from thytrader.persistence.backtest_results import (
    BacktestDiagnosticsReader,
    BacktestSourceSpecificationReader,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.postgres_studies import PostgresResearchStudyCatalog
from thytrader.research import http as research_http
from thytrader.research.backtest_model import backtest_model_description
from thytrader.research.catalog import (
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogUnavailableError,
)
from thytrader.research.dataset_binding import DatasetsMissingError
from thytrader.research.mutation import ResearchMutationError, ResearchMutator
from thytrader.research.studies import (
    ResearchStudyError,
    ResearchStudyService,
    StudyPlanningError,
    load_candidate_definitions,
    summarize_research_study,
    summarize_research_study_plan,
)
from thytrader.research.study_start import BoundStudyStart, ResearchStudyStartRequest
from thytrader.strategies.advisories import strategy_warnings
from thytrader.strategies.library import (
    BulkDeletionItem,
    BulkDeletionReport,
    StrategyLibraryError,
    StrategyOrigin,
    StrategyRecord,
    parse_document,
)
from thytrader.strategies.snapshots import StrategySnapshotError
from thytrader.strategies.templates import StrategyTemplateId, template_catalog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.strategies.library import StrategyDocument

_CONFIRM_HELP = (
    "Required for mutations. This CLI cannot deploy, paper-trade, live-trade, or cancel orders."
)
_STUDY_KINDS = (
    "oos_holdout",
    "walk_forward",
    "cross_market",
    "parameter_sweep",
    "walk_forward_optimization",
)
_RESEARCH_CONFIRM_MESSAGE = (
    "Pass --confirm to change research artifacts. "
    "This command cannot deploy, paper-trade, or live-trade."
)
_MUTATIONS = frozenset(
    {
        "create-strategy",
        "save-strategy",
        "import-strategy",
        "clone-strategy",
        "delete-strategy",
        "submit-backtest",
        "submit-study",
        "cancel-research-job",
        "create-campaign",
        "refresh-campaign",
    }
)


_STUDY_FILE_HELP = (
    "Study start JSON naming strategies by id. Dataset fingerprints and both evaluation bounds "
    "may be omitted (the newest complete catalog datasets and their common covered window are "
    "bound and echoed, including each reference instrument). cross_market takes "
    "markets[].strategy_id, or one strategy_id plus markets[].product_id to derive per-market "
    "variants; a variant keeps the base strategy's reference instruments (BTC stays BTC)."
)


class ResearchCliError(RuntimeError):
    """Report a safe operator-facing research command failure."""


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


def _shared_options() -> argparse.ArgumentParser:
    """Global flags that may appear before or after the subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--base-url",
        default=None,
        help="Loopback API origin. Defaults to THYTRADER_API_BASE_URL or settings.",
    )
    shared.add_argument(
        "--local",
        action="store_true",
        help="Use PostgreSQL stores instead of HTTP. Do not use as a silent API fallback.",
    )
    return shared


def _parser() -> argparse.ArgumentParser:
    """Build the bounded research-mutation argument parser."""
    shared = _shared_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-research",
        description=(
            "Create, save, clone, import, and delete strategies; submit backtests and studies "
            "by strategy_id (the server snapshots the current rules). Mutations require "
            "--confirm. Default transport is the loopback HTTP API. This command has no paper "
            "or live authority. Every backtest uses the single unified model (see "
            "backtest-model); there is no engine selector."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_strategy_commands(subparsers, trailing)
    _add_backtest_commands(subparsers, trailing)
    _add_study_commands(subparsers, trailing)
    _add_campaign_commands(subparsers, trailing)
    return parser


def _add_strategy_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register strategy lifecycle subcommands."""
    create = subparsers.add_parser(
        "create-strategy", parents=[trailing], help="Create one strategy from a template."
    )
    create.add_argument("--product-id", default="BTC-USDC", help="Spot product. Default BTC-USDC.")
    create.add_argument(
        "--timeframe",
        default="1h",
        choices=EXECUTION_TIMEFRAMES,
        help="Decision timeframe. Default 1h. Any ingested venue clock (research, paper, live).",
    )
    create.add_argument(
        "--template",
        default="ema-trend",
        help=(
            "Template id from list-templates (default ema-trend): "
            + ", ".join(item.value for item in StrategyTemplateId)
            + "."
        ),
    )
    create.add_argument(
        "--experiential-model-id",
        default=None,
        help="Optional trained experiential-model UUID (HTTP only; advisory JSON only).",
    )
    create.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    listing = subparsers.add_parser(
        "list-strategies", parents=[trailing], help="List strategies, newest updated first."
    )
    listing.add_argument("--limit", type=_page_limit, default=50, help="Page size. Maximum 100.")
    listing.add_argument("--cursor", default=None, help="Opaque next_cursor from the last page.")
    listing.add_argument(
        "--tag",
        default=None,
        help=(
            "Only strategies whose metadata.tags include this tag (pass the same --tag with "
            "--cursor). Rows carry their tags."
        ),
    )
    listing.add_argument(
        "--origin",
        choices=[item.value for item in StrategyOrigin],
        default=StrategyOrigin.ALL.value,
        help=(
            "research: only strategies tagged claude-research or research-*; operator: every "
            "other strategy; all (default): no origin filter. Combines with --tag; pass the "
            "same --origin with --cursor."
        ),
    )
    show = subparsers.add_parser(
        "show-strategy",
        parents=[trailing],
        help="Show one strategy's document, validation, revision, and current_fingerprint.",
    )
    show.add_argument("--strategy-id", required=True)
    snapshot = subparsers.add_parser(
        "show-snapshot",
        parents=[trailing],
        help="Show the exact rules one backtest or bot ran (by strategy_fingerprint).",
    )
    snapshot.add_argument("--strategy-fingerprint", required=True)
    save = subparsers.add_parser(
        "save-strategy",
        parents=[trailing],
        help="Save a document in place (invalid work in progress is allowed).",
    )
    save.add_argument("--strategy-id", required=True)
    save.add_argument("--file", required=True, help="Path to a strategy JSON document object.")
    save.add_argument(
        "--revision",
        required=True,
        type=int,
        help="Revision you edited (from show-strategy). A stale revision is rejected.",
    )
    save.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    imported = subparsers.add_parser(
        "import-strategy",
        parents=[trailing],
        help="Create a new strategy (fresh strategy_id) from a JSON document.",
    )
    imported.add_argument("--file", required=True, help="Path to a strategy JSON document.")
    imported.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    clone = subparsers.add_parser(
        "clone-strategy",
        parents=[trailing],
        help="Duplicate one strategy into a new identity (optionally named in the same call).",
    )
    clone.add_argument("--strategy-id", required=True)
    clone.add_argument(
        "--name",
        default=None,
        help="Name of the copy (1-120 characters). Default: '<name> (copy)'.",
    )
    clone.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    remove = subparsers.add_parser(
        "delete-strategy",
        parents=[trailing],
        help=(
            "Hard-delete one strategy with its backtests, studies, jobs, and paper bots. "
            "Refused while a bot is running or paused; stopped live bots are kept."
        ),
    )
    remove.add_argument("--strategy-id", required=True)
    remove.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    bulk = subparsers.add_parser(
        "bulk-delete-strategies",
        parents=[trailing],
        help=(
            "Delete several strategies (per-strategy results), named by repeated --strategy-id "
            "or by --tag. Running or paused bots block their strategy; live ledgers are kept. "
            "Use --dry-run to preview."
        ),
    )
    targets = bulk.add_mutually_exclusive_group(required=True)
    targets.add_argument("--strategy-id", action="append", dest="strategy_ids")
    targets.add_argument(
        "--tag",
        default=None,
        help="Every strategy whose metadata.tags include this tag (sent in batches of 100).",
    )
    bulk.add_argument("--dry-run", action="store_true", help="Preview counts; changes nothing.")
    bulk.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)


def _add_backtest_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register backtest and job subcommands."""
    submit = subparsers.add_parser(
        "submit-backtest",
        parents=[trailing],
        help=(
            "Snapshot one strategy's current rules and run one backtest in the research "
            "worker. Waits up to the server's sync bound (default 25 s); a longer run returns "
            "the queued/running job with next_action instead (poll show-research-job)."
        ),
    )
    submit.add_argument(
        "--file",
        required=True,
        help=(
            "Path to a backtest start JSON document: strategy_id, optional dataset "
            "fingerprint(s) (omitted ones bind the newest complete catalog dataset per clock and "
            "are echoed in bound_datasets; that includes each reference instrument, role "
            "reference, pinnable with reference_dataset_fingerprints[] {reference_id, "
            "product_id, timeframe, dataset_fingerprint}), optional evaluation window, "
            "initial_quote_balance, maker/taker fee rates, fixed_slippage_bps, and optional "
            "spread_bps stress. engine_contract_version is rejected."
        ),
    )
    submit.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    submit.add_argument(
        "--async",
        action="store_true",
        help="Queue it (HTTP 202) and poll show-research-job; 'queued' waits for a free worker.",
    )
    for name, text in (
        ("show-backtest-job", "Poll one async backtest job status."),
        ("show-research-job", "Poll one async research job (backtest or study)."),
    ):
        job = subparsers.add_parser(name, parents=[trailing], help=text)
        job.add_argument("--job-id", required=True)
    jobs = subparsers.add_parser(
        "list-research-jobs",
        parents=[trailing],
        help=(
            "List one strategy's newest research jobs (backtests and studies, sync and async) "
            "with status, progress, attempts, and error_code."
        ),
    )
    jobs.add_argument("--strategy-id", required=True)
    jobs.add_argument("--limit", type=int, default=20)
    cancel = subparsers.add_parser(
        "cancel-research-job", parents=[trailing], help="Cancel one queued or running job."
    )
    cancel.add_argument("--job-id", required=True)
    cancel.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    listing = subparsers.add_parser(
        "list-results", parents=[trailing], help="List backtest summaries."
    )
    listing.add_argument("--strategy-id", default=None, help="Every result of one strategy.")
    listing.add_argument(
        "--strategy-fingerprint", default=None, help="Only results of one exact snapshot."
    )
    listing.add_argument(
        "--limit",
        type=_page_limit,
        default=20,
        help="Page size, maximum 100; the response has has_more and next_cursor.",
    )
    listing.add_argument("--cursor", default=None, help="Opaque next_cursor from the last page.")
    show = subparsers.add_parser("show-result", parents=[trailing], help="Show one result summary.")
    show.add_argument("--result-fingerprint", required=True)
    export = subparsers.add_parser(
        "export-results", parents=[trailing], help="Export a bounded page of result evidence."
    )
    export.add_argument("--strategy-id", default=None)
    export.add_argument(
        "--limit", type=_page_limit, default=50, help="Maximum 100 results per page."
    )
    export.add_argument("--cursor", default=None, help="next_cursor from the previous page.")


def _add_campaign_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Expose persistent research campaigns and fee-aware read-only preflight."""
    for command in ("create-campaign", "economics"):
        command_parser = subparsers.add_parser(command, parents=[trailing])
        command_parser.add_argument("--file", required=True, help="Validated request JSON.")
        if command == "create-campaign":
            command_parser.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    for command in ("show-campaign", "refresh-campaign"):
        command_parser = subparsers.add_parser(command, parents=[trailing])
        command_parser.add_argument("--campaign-id", required=True)
        if command == "refresh-campaign":
            command_parser.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    listing = subparsers.add_parser("list-campaigns", parents=[trailing])
    listing.add_argument("--limit", type=_page_limit, default=20)


def _add_study_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register study, template, and evidence subcommands."""
    studies = subparsers.add_parser(
        "list-studies", parents=[trailing], help="List persisted research studies."
    )
    studies.add_argument("--kind", default=None, choices=_STUDY_KINDS)
    studies.add_argument("--strategy-id", default=None, help="Studies that include a strategy.")
    studies.add_argument("--limit", type=int, default=50)
    show_study = subparsers.add_parser(
        "show-study", parents=[trailing], help="Show one persisted research study summary."
    )
    show_study.add_argument("--study-fingerprint", required=True)
    subparsers.add_parser("list-templates", parents=[trailing], help="List strategy templates.")
    template = subparsers.add_parser(
        "show-template", parents=[trailing], help="Show one template's defaults and axes."
    )
    template.add_argument("--template", required=True)
    subparsers.add_parser(
        "backtest-model",
        parents=[trailing],
        help=(
            "Describe the single backtest model's fill, fee, slippage, and spread-stress "
            "assumptions. There is no engine selector."
        ),
    )
    plan = subparsers.add_parser(
        "plan-study",
        parents=[trailing],
        help="Plan OOS, walk-forward, cross-market, sweep, or WFO windows without submitting.",
    )
    plan.add_argument("--file", required=True, help=_STUDY_FILE_HELP)
    study = subparsers.add_parser(
        "submit-study",
        parents=[trailing],
        help=(
            "Submit one composed research study to the research worker. Waits up to the "
            "server's sync bound (default 25 s); a longer study returns the queued/running job "
            "with next_action instead (poll show-research-job)."
        ),
    )
    study.add_argument("--file", required=True, help=_STUDY_FILE_HELP)
    study.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    study.add_argument(
        "--async",
        action="store_true",
        help=(
            "Queue it (HTTP 202) and poll show-research-job. Required above the synchronous "
            "budget (8 candidates, 128 child windows); async allows 64 candidates and 512 "
            "child windows. The worker plans async studies; acceptance is not proof of "
            "feasibility. Check failed_phase: plan on failure."
        ),
    )
    study.add_argument(
        "--submit-timeout-seconds",
        type=_submit_timeout,
        default=None,
        help=(
            "Client wait for the submit response (1-300 s). Default 30 s with --async (the "
            "API pins strategy, datasets, and bounds before queueing) and 60 s otherwise. A "
            "timeout is ambiguous: read back with list-studies before resubmitting."
        ),
    )
    find = subparsers.add_parser(
        "find-study-by-request",
        parents=[trailing],
        help="Read back one persisted study by request fingerprint.",
    )
    find.add_argument("--request-fingerprint", required=True)
    evidence = subparsers.add_parser(
        "show-evidence",
        parents=[trailing],
        help="Show IS vs OOS vs sweep vs paper vs live evidence for one snapshot.",
    )
    evidence.add_argument("--strategy-fingerprint", required=True)


def _submit_timeout(value: str) -> float:
    """Parse a 1-300 second client submit timeout."""
    parsed = float(value)
    if not 1 <= parsed <= 300:
        message = "submit timeout must be between 1 and 300 seconds"
        raise argparse.ArgumentTypeError(message)
    return parsed


def _page_limit(value: str) -> int:
    """Parse a 1-100 listing page size."""
    parsed = int(value)
    if parsed < 1 or parsed > 100:
        message = "limit must be between 1 and 100"
        raise argparse.ArgumentTypeError(message)
    return parsed


def _is_mutation(arguments: argparse.Namespace) -> bool:
    """True when the command changes research artifacts."""
    if arguments.command == "bulk-delete-strategies":
        return not arguments.dry_run
    return arguments.command in _MUTATIONS


def _require_confirm(arguments: argparse.Namespace) -> None:
    """Refuse local mutations unless the operator passed `--confirm`. YOLO is HTTP-only."""
    if _is_mutation(arguments) and not arguments.confirm:
        raise ResearchCliError(_RESEARCH_CONFIRM_MESSAGE)


def _require_http_confirm(arguments: argparse.Namespace, base_url: str) -> None:
    """Refuse HTTP mutations unless `--confirm` is present or YOLO covers research."""
    if not _is_mutation(arguments):
        return
    require_mutation_confirmation(
        confirmed=bool(arguments.confirm),
        missing_message=_RESEARCH_CONFIRM_MESSAGE,
        error_type=ResearchCliError,
        base_url=base_url,
        tier=YoloTier.RESEARCH,
        command=arguments.command,
    )


def _load_json(path_text: str) -> object:
    """Load one UTF-8 JSON document from disk."""
    path = Path(path_text)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_document(path_text: str) -> StrategyDocument:
    """Load one strategy document object (valid or work in progress)."""
    return parse_document(_load_json(path_text))


def _uuid(value: str | None, flag: str) -> UUID:
    """Parse one required UUID flag."""
    try:
        return UUID(str(value))
    except ValueError as error:
        raise ResearchCliError(f"{flag} must be a UUID.") from error


def _clone_name(arguments: argparse.Namespace) -> str | None:
    """Return the requested clone name, refusing blank or over-long names before any call."""
    name = arguments.name
    if name is None:
        return None
    if not name.strip() or len(name) > 120:
        raise ResearchCliError("--name must be 1-120 characters and not blank.")
    return name


def _optional_uuid(value: str | None, flag: str) -> UUID | None:
    """Parse one optional UUID flag."""
    return None if value is None else _uuid(value, flag)


def _dispatch_http(arguments: argparse.Namespace) -> str:
    """Execute one research command against the loopback HTTP API."""
    settings = Settings()
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    _require_http_confirm(arguments, base_url)
    handler = _HTTP_HANDLERS.get(arguments.command)
    if handler is None:
        raise AssertionError(f"unsupported research command: {arguments.command}")
    if arguments.command not in {"list-templates", "backtest-model"}:
        require_matching_ops_contract(base_url)
    return handler(base_url, arguments)


def _http_create(base_url: str, arguments: argparse.Namespace) -> str:
    """Create one template strategy over HTTP."""
    return research_http.create_strategy(
        base_url,
        product_id=arguments.product_id,
        timeframe=arguments.timeframe,
        template=arguments.template,
        experiential_model_id=_experiential_model_id(arguments),
    )


def _http_list_results(base_url: str, arguments: argparse.Namespace) -> str:
    """List backtest summaries over HTTP."""
    if arguments.strategy_id and arguments.strategy_fingerprint:
        raise ResearchCliError("Use either --strategy-id or --strategy-fingerprint, not both.")
    return research_http.list_results(
        base_url,
        arguments.strategy_fingerprint,
        arguments.limit,
        cursor=arguments.cursor,
        strategy_id=_optional_uuid(arguments.strategy_id, "--strategy-id"),
    )


_HTTP_HANDLERS: dict[str, Callable[[str, argparse.Namespace], str]] = {
    "create-campaign": lambda url, args: research_http.create_campaign(url, _load_json(args.file)),
    "economics": lambda url, args: research_http.economics(url, _load_json(args.file)),
    "list-campaigns": lambda url, args: research_http.list_campaigns(url, limit=args.limit),
    "show-campaign": lambda url, args: research_http.show_campaign(
        url, _uuid(args.campaign_id, "--campaign-id")
    ),
    "refresh-campaign": lambda url, args: research_http.refresh_campaign(
        url, _uuid(args.campaign_id, "--campaign-id")
    ),
    "create-strategy": _http_create,
    "list-strategies": lambda url, args: research_http.list_strategies(
        url, limit=args.limit, cursor=args.cursor, tag=args.tag, origin=StrategyOrigin(args.origin)
    ),
    "show-strategy": lambda url, args: research_http.show_strategy(
        url, _uuid(args.strategy_id, "--strategy-id")
    ),
    "show-snapshot": lambda url, args: research_http.show_snapshot(url, args.strategy_fingerprint),
    "save-strategy": lambda url, args: research_http.save_strategy(
        url, _uuid(args.strategy_id, "--strategy-id"), _load_document(args.file), args.revision
    ),
    "import-strategy": lambda url, args: research_http.import_strategy(
        url, _load_document(args.file)
    ),
    "clone-strategy": lambda url, args: research_http.clone_strategy(
        url, _uuid(args.strategy_id, "--strategy-id"), name=_clone_name(args)
    ),
    "delete-strategy": lambda url, args: research_http.delete_strategy(
        url, _uuid(args.strategy_id, "--strategy-id")
    ),
    "bulk-delete-strategies": lambda url, args: (
        research_http.bulk_delete_tagged(url, args.tag, dry_run=bool(args.dry_run))
        if args.tag is not None
        else research_http.bulk_delete_strategies(
            url,
            tuple(_uuid(item, "--strategy-id") for item in args.strategy_ids),
            dry_run=bool(args.dry_run),
        )
    ),
    "submit-backtest": lambda url, args: research_http.submit_backtest(
        url,
        BacktestStartRequest.model_validate(_load_json(args.file)),
        async_submission=bool(getattr(args, "async", False)),
    ),
    "show-backtest-job": lambda url, args: research_http.show_backtest_job(url, args.job_id),
    "show-research-job": lambda url, args: research_http.show_research_job(url, args.job_id),
    "list-research-jobs": lambda url, args: research_http.list_research_jobs(
        url, _uuid(args.strategy_id, "--strategy-id"), args.limit
    ),
    "cancel-research-job": lambda url, args: research_http.cancel_research_job(url, args.job_id),
    "list-results": _http_list_results,
    "export-results": lambda url, args: research_http.export_results(
        url,
        limit=args.limit,
        cursor=args.cursor,
        strategy_id=_optional_uuid(args.strategy_id, "--strategy-id"),
    ),
    "show-result": lambda url, args: research_http.show_result(url, args.result_fingerprint),
    "list-studies": lambda url, args: research_http.list_studies(
        url, args.kind, args.limit, _optional_uuid(args.strategy_id, "--strategy-id")
    ),
    "show-study": lambda url, args: research_http.show_study(url, args.study_fingerprint),
    "list-templates": lambda url, _args: research_http.list_templates(url),
    "show-template": lambda url, args: research_http.show_template(url, args.template),
    "backtest-model": lambda url, _args: research_http.backtest_model(url),
    "plan-study": lambda url, args: research_http.plan_study(
        url, ResearchStudyStartRequest.model_validate(_load_json(args.file))
    ),
    "submit-study": lambda url, args: research_http.submit_study(
        url,
        ResearchStudyStartRequest.model_validate(_load_json(args.file)),
        async_submission=bool(getattr(args, "async", False)),
        timeout_seconds=getattr(args, "submit_timeout_seconds", None),
    ),
    "find-study-by-request": lambda url, args: research_http.find_study_by_request(
        url, args.request_fingerprint
    ),
    "show-evidence": lambda url, args: research_http.show_evidence(url, args.strategy_fingerprint),
}


async def _mutator(settings: Settings) -> tuple[ResearchMutator, AsyncEngine]:
    """Build the mutator from PostgreSQL when configured."""
    if settings.database_url is None:
        raise ResearchCliError("THYTRADER_DATABASE_URL is required for --local research commands.")
    engine = create_engine(settings.database_url)
    dataset_store = DatasetStore(settings.market_data_dataset_root)
    strategy_store = PostgresStrategyStore(engine)
    run_store = PostgresResearchRunStore(engine)
    result_store = PostgresBacktestResultStore(
        engine,
        research_run_store=run_store,
        dataset_store=dataset_store,
    )
    mutator = ResearchMutator(
        strategies=strategy_store,
        publications=strategy_store,
        submitter=PostgresBacktestSubmitter(engine, dataset_store),
        results=result_store,
        audit=PostgresAuditEventStore(engine),
        catalog=PostgresResearchStudyCatalog(engine),
        datasets=dataset_store,
        dataset_provider=ingestion_provider(settings),
    )
    return mutator, engine


async def _dispatch_local(arguments: argparse.Namespace) -> str:
    """Execute one research command against PostgreSQL stores."""
    _reject_local_experiential_model(arguments)
    _require_confirm(arguments)
    if arguments.command == "list-templates":
        return _encode({"templates": list(template_catalog())})
    if arguments.command == "backtest-model":
        return _encode(backtest_model_description().model_dump(mode="json"))
    handler = _LOCAL_HANDLERS.get(arguments.command)
    if handler is None:
        raise ResearchCliError(f"{arguments.command} requires HTTP; do not use --local.")
    mutator, engine = await _mutator(Settings())
    try:
        return await handler(mutator, arguments)
    finally:
        await dispose(engine)


async def _local_create(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Create one template strategy in PostgreSQL."""
    record = await mutator.create_strategy(
        product_id=arguments.product_id,
        timeframe=arguments.timeframe,
        template=arguments.template,
    )
    return _encode(_record_digest(record))


async def _local_show(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Show one strategy with its document from PostgreSQL."""
    record = await mutator.strategies.get(_uuid(arguments.strategy_id, "--strategy-id"))
    return _encode({**_record_digest(record), "document": record.document})


async def _local_save(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Save one document in place."""
    record = await mutator.save_strategy(
        _uuid(arguments.strategy_id, "--strategy-id"),
        _load_document(arguments.file),
        expected_revision=arguments.revision,
    )
    return _encode(_record_digest(record))


async def _local_import(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Import one document as a new strategy."""
    return _encode(_record_digest(await mutator.import_strategy(_load_document(arguments.file))))


async def _local_clone(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Clone one strategy."""
    record = await mutator.clone_strategy(
        _uuid(arguments.strategy_id, "--strategy-id"), name=_clone_name(arguments)
    )
    return _encode(_record_digest(record))


async def _local_delete(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Delete one or several strategies (or dry-run), named by id or by --tag."""
    tag = getattr(arguments, "tag", None)
    if arguments.command == "bulk-delete-strategies" and tag is not None:
        identities = await _local_tagged_ids(mutator, tag)
    else:
        raw: list[str] = (
            list(arguments.strategy_ids)
            if arguments.command == "bulk-delete-strategies"
            else [arguments.strategy_id]
        )
        identities = tuple(_uuid(item, "--strategy-id") for item in raw)
    dry_run = bool(getattr(arguments, "dry_run", False))
    report = await mutator.delete_strategies(identities, dry_run=dry_run)
    payload = _report_payload(report)
    if tag is not None:
        payload = {"tag": tag, "matched": len(identities), **payload}
    return _encode(payload)


async def _local_tagged_ids(mutator: ResearchMutator, tag: str) -> tuple[UUID, ...]:
    """Every strategy id tagged ``tag`` from PostgreSQL, across library pages."""
    identities: list[UUID] = []
    offset = 0
    while True:
        page = await mutator.strategies.list_page(limit=100, offset=offset, tag=tag)
        identities.extend(record.strategy_id for record in page.records)
        offset += len(page.records)
        if not page.records or offset >= page.total:
            return tuple(dict.fromkeys(identities))


async def _local_backtest(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Snapshot and run one backtest locally."""
    start = BacktestStartRequest.model_validate(_load_json(arguments.file))
    run, result, fingerprint, bound = await mutator.start_backtest(start)
    return _encode(
        {
            "run_fingerprint": run,
            "result_fingerprint": result,
            "strategy_id": str(start.strategy_id),
            "strategy_fingerprint": fingerprint,
            "bound_datasets": [item.model_dump(mode="json") for item in bound],
        }
    )


async def _local_list_results(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """List bounded result summaries."""
    rows = await mutator.list_results(
        strategy_fingerprint=arguments.strategy_fingerprint,
        strategy_id=_optional_uuid(arguments.strategy_id, "--strategy-id"),
        limit=arguments.limit,
    )
    return _encode(
        {
            "results": [
                {
                    "result_fingerprint": row.result_fingerprint,
                    "run_fingerprint": row.run_fingerprint,
                    "strategy_fingerprint": row.strategy_fingerprint,
                    "strategy_id": row.strategy_id,
                    "dataset_fingerprint": row.dataset_fingerprint,
                    "published_at": row.published_at.isoformat(),
                    "trade_count": row.summary.trade_count,
                    "total_net_pnl": row.summary.total_net_pnl,
                    "total_return_fraction": row.summary.total_return_fraction,
                    **research_http.window_fields(
                        None if row.window is None else row.window.model_dump(mode="json")
                    ),
                }
                for row in rows
            ]
        }
    )


async def _local_show_result(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Load one result summary without dumping the full trade ledger."""
    result = await mutator.results.load(arguments.result_fingerprint)
    timeframe, currency = "1h", "USD"
    try:
        snapshot = await mutator.publications.load(result.strategy_fingerprint)
        timeframe = published_execution_timeframe(snapshot.definition.timeframe)
        currency = snapshot.definition.instrument.quote_currency
    except StrategySnapshotError:
        timeframe, currency = "1h", "USD"
    diagnostics = await _local_diagnostics(mutator, backtest_result_fingerprint(result))
    window = await _local_window(mutator, result)
    return _encode(
        {
            "result_fingerprint": backtest_result_fingerprint(result),
            "run_fingerprint": result.run_fingerprint,
            "strategy_fingerprint": result.strategy_fingerprint,
            "dataset_fingerprint": result.dataset_fingerprint,
            "mode": "backtest",
            "timeframe": timeframe,
            "currency": currency,
            "summary": result.summary.model_dump(mode="json"),
            "window": None if window is None else window.model_dump(mode="json"),
            "diagnostics": None if diagnostics is None else diagnostics.model_dump(mode="json"),
        }
    )


async def _local_window(
    mutator: ResearchMutator, result: BacktestResult
) -> BacktestEvaluationWindow | None:
    """Derive the evaluated window from the result's source run when the store has it."""
    store = mutator.results
    if not isinstance(store, BacktestSourceSpecificationReader):
        return None
    specification = await store.load_source_specification(result)
    return backtest_evaluation_window(specification, result.summary.evaluation_bars)


async def _local_diagnostics(
    mutator: ResearchMutator, result_fingerprint: str
) -> BacktestDiagnostics | None:
    """Read the entry funnel stored beside one result when the local store records it."""
    store = mutator.results
    if not isinstance(store, BacktestDiagnosticsReader):
        return None
    return await store.load_diagnostics(result_fingerprint)


async def _local_list_studies(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """List persisted study catalog rows."""
    rows = await mutator.list_studies(
        kind=arguments.kind,
        strategy_id=_optional_uuid(arguments.strategy_id, "--strategy-id"),
        limit=arguments.limit,
    )
    return _encode({"studies": [row.model_dump(mode="json") for row in rows]})


async def _local_show_study(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Load one persisted study without dumping child equity curves."""
    study = await mutator.show_study(arguments.study_fingerprint)
    definitions = await load_candidate_definitions(mutator.publications, study)
    return _encode(summarize_research_study(study, definitions=definitions).model_dump(mode="json"))


async def _local_plan_study(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Plan study windows without submitting child backtests."""
    start = ResearchStudyStartRequest.model_validate(_load_json(arguments.file))
    service = ResearchStudyService(
        publications=mutator.publications,
        submitter=mutator.submitter,
        results=mutator.results,
        catalog=mutator.catalog,
        datasets=mutator.datasets,
    )
    bound = await mutator.bind_study(start)
    plan = await service.plan(bound.request)
    summary = summarize_research_study_plan(plan).model_dump(mode="json")
    return _encode({**summary, **_study_echo(bound)})


async def _local_submit_study(mutator: ResearchMutator, arguments: argparse.Namespace) -> str:
    """Submit one composed study and return the derived document."""
    start = ResearchStudyStartRequest.model_validate(_load_json(arguments.file))
    bound = await mutator.bind_study(start)
    study = await mutator.submit_study(bound.request)
    return _encode({**study.model_dump(mode="json"), **_study_echo(bound)})


def _study_echo(bound: BoundStudyStart) -> dict[str, object]:
    """Echo every bound dataset and the exact evaluation window, as the HTTP API does."""
    request = bound.request
    return {
        "bound_datasets": [item.model_dump(mode="json") for item in bound.bound_datasets],
        "evaluation_start": request.evaluation_start.isoformat().replace("+00:00", "Z"),
        "evaluation_end": request.evaluation_end.isoformat().replace("+00:00", "Z"),
    }


_LOCAL_HANDLERS: dict[str, Callable[[ResearchMutator, argparse.Namespace], Awaitable[str]]] = {
    "create-strategy": _local_create,
    "show-strategy": _local_show,
    "save-strategy": _local_save,
    "import-strategy": _local_import,
    "clone-strategy": _local_clone,
    "delete-strategy": _local_delete,
    "bulk-delete-strategies": _local_delete,
    "submit-backtest": _local_backtest,
    "list-results": _local_list_results,
    "show-result": _local_show_result,
    "list-studies": _local_list_studies,
    "show-study": _local_show_study,
    "plan-study": _local_plan_study,
    "submit-study": _local_submit_study,
}


def _record_digest(record: StrategyRecord) -> dict[str, object]:
    """Summarize one strategy without its full document, shaped like the HTTP digest.

    ``validation`` matches ``show-strategy``; the top-level copies are deprecated
    (kept for one release, ADR 0094).
    """
    validation: dict[str, object] = {
        "valid": record.validation.valid,
        "issues": [
            {"loc": issue.loc, "message": issue.message} for issue in record.validation.issues
        ],
        "warnings": []
        if record.definition is None
        else [
            {"code": item.code.value, "loc": ".".join(item.loc), "message": item.message}
            for item in strategy_warnings(record.definition)
        ],
    }
    return {
        "strategy_id": str(record.strategy_id),
        "name": record.name,
        "revision": record.revision,
        "validation": validation,
        "valid": validation["valid"],
        "issues": validation["issues"],
        "warnings": validation["warnings"],
        "current_fingerprint": record.current_fingerprint,
    }


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


def _report_payload(report: BulkDeletionReport) -> dict[str, object]:
    """Render one bulk deletion report like the HTTP response."""
    return {
        "dry_run": report.dry_run,
        "results": [_bulk_item_payload(item) for item in report.items],
        "deleted": report.count("deleted"),
        "would_delete": report.count("would_delete"),
        "blocked": report.count("blocked"),
        "not_found": report.count("not_found"),
        "failed": report.count("failed"),
    }


def _bulk_item_payload(item: BulkDeletionItem) -> dict[str, object]:
    """Render one bulk item."""
    counts = item.counts
    return {
        "strategy_id": str(item.strategy_id),
        "name": item.name,
        "outcome": item.outcome,
        "code": item.code,
        "message": item.message,
        "deployment_ids": [str(value) for value in item.deployment_ids],
        "counts": None
        if counts is None
        else {
            "snapshots": counts.snapshots,
            "backtests": counts.backtests,
            "research_runs": counts.research_runs,
            "studies": counts.studies,
            "research_jobs": counts.research_jobs,
            "dataset_bindings": counts.dataset_bindings,
            "paper_deployments": counts.paper_deployments,
            "live_deployments_kept": counts.live_deployments_kept,
            "allocations_removed": counts.allocations_removed,
            "portfolio_sleeves": counts.portfolio_sleeves,
        },
        "risk_policy_republished": item.risk_policy_republished,
    }


def _experiential_model_id(arguments: argparse.Namespace) -> str | None:
    """Parse the optional trained-model UUID or return None."""
    raw = getattr(arguments, "experiential_model_id", None)
    if raw is None:
        return None
    return str(_uuid(raw, "--experiential-model-id"))


def _reject_local_experiential_model(arguments: argparse.Namespace) -> None:
    """Refuse gated advisory input on --local PostgreSQL research."""
    if getattr(arguments, "experiential_model_id", None):
        raise ResearchCliError(
            "--experiential-model-id requires HTTP transport; do not pass --local."
        )


def _encode(payload: object) -> str:
    """Render stable JSON for agent consumption."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
