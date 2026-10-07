"""``thytrader-research`` argument parser, one registration function per command group.

Strategies, backtests and jobs, studies and templates, and campaigns, plus the bounded
argparse types for page sizes and the submit timeout.
"""

from __future__ import annotations

import argparse

from thytrader.cli_parse import trailing_options
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.strategies.library import StrategyOrigin
from thytrader.strategies.templates import StrategyTemplateId

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


_STUDY_FILE_HELP = (
    "Study start JSON naming strategies by id. Dataset fingerprints and both evaluation bounds "
    "may be omitted (the newest complete catalog datasets and their common covered window are "
    "bound and echoed, including each reference instrument). cross_market takes "
    "markets[].strategy_id, or one strategy_id plus markets[].product_id to derive per-market "
    "variants; a variant keeps the base strategy's reference instruments (BTC stays BTC)."
)


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
    explain = subparsers.add_parser(
        "explain-bars",
        parents=[trailing],
        help=(
            "Read one bounded page of per-bar signal and fill explanations for a saved result. "
            "Read-only; does not rerun or mutate the backtest."
        ),
    )
    explain.add_argument("--result-fingerprint", required=True)
    explain.add_argument(
        "--limit",
        type=_explanation_limit,
        default=100,
        help="Bars per page, 1-500. Default 100.",
    )
    explain.add_argument("--cursor", default=None, help="next_cursor from the previous page.")
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


def _explanation_limit(value: str) -> int:
    """Parse a 1-500 bar-explanation page size."""
    parsed = int(value)
    if parsed < 1 or parsed > 500:
        message = "limit must be between 1 and 500"
        raise argparse.ArgumentTypeError(message)
    return parsed
