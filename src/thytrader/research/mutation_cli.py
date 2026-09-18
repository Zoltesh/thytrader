"""Confirmation-gated CLI for strategy drafts, publication, and backtests."""

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
from thytrader.backtest.models import backtest_result_fingerprint
from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    PostgresBacktestSubmitter,
)
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import EXECUTION_TIMEFRAMES, published_execution_timeframe
from thytrader.operator.status import EXIT_HEALTHY, EXIT_USAGE
from thytrader.ops_contract import STALE_IMAGE_REBUILD
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.persistence.postgres_studies import PostgresResearchStudyCatalog
from thytrader.research import http as research_http
from thytrader.research.catalog import (
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogUnavailableError,
)
from thytrader.research.engine_support import engine_support_matrix
from thytrader.research.mutation import ResearchMutationError, ResearchMutator
from thytrader.research.studies import (
    ResearchStudyError,
    ResearchStudyRequest,
    ResearchStudyService,
    StudyPlanningError,
    summarize_research_study_plan,
)
from thytrader.strategies.models import StrategyDefinition
from thytrader.strategies.publication import StrategyPublicationError
from thytrader.strategies.templates import template_catalog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

_CONFIRM_HELP = (
    "Required for mutations. This CLI cannot deploy, paper-trade, live-trade, or cancel orders."
)


class ResearchCliError(RuntimeError):
    """Report a safe operator-facing research command failure."""


def _validation_error_message(error: ValidationError) -> str:
    """Return the first semantic Pydantic error without wrapping it as a safe no-op."""
    issues = error.errors()
    if not issues:
        return "Document failed validation."
    return str(issues[0].get("msg", "Document failed validation."))


def _agent_http_error_message(message: str) -> str:
    """Hint a rebuild when a stale API rejects the current backtest engine."""
    lowered = message.lower()
    lists_v1_v2 = "thytrader-bar-backtest-v1" in lowered and "thytrader-bar-backtest-v2" in lowered
    missing_v3 = lists_v1_v2 and "thytrader-bar-backtest-v3" not in lowered
    missing_v4 = (
        lists_v1_v2
        and "thytrader-bar-backtest-v3" in lowered
        and "thytrader-bar-backtest-v4" not in lowered
    )
    if "422" in message and (missing_v3 or missing_v4):
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
            "Create drafts, publish immutable versions, and submit backtests. "
            "Mutations require --confirm. Default transport is the loopback HTTP API. "
            "Optional --experiential-model-id on create-draft is HTTP-only advisory "
            "input. This command has no paper or live authority."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser(
        "create-draft",
        parents=[trailing],
        help="Create the conservative reference draft.",
    )
    create.add_argument(
        "--product-id",
        default="BTC-USD",
        help="USD spot product. Default BTC-USD.",
    )
    create.add_argument(
        "--timeframe",
        default="1h",
        choices=EXECUTION_TIMEFRAMES,
        help=("Research timeframe. Default 1h. Paper and live may use any ingested venue clock."),
    )
    create.add_argument(
        "--template",
        default="ema-trend",
        help=(
            "Draft template: ema-trend (default), rsi-mean-reversion, "
            "macd-trend, or bollinger-mean-reversion."
        ),
    )
    create.add_argument(
        "--experiential-model-id",
        default=None,
        help=(
            "Optional trained experiential-model UUID. HTTP only; merges the "
            "advisory into create-draft JSON. Not a live policy. --local refuses."
        ),
    )
    create.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    save = subparsers.add_parser(
        "save-draft",
        parents=[trailing],
        help="Replace one draft from a JSON file.",
    )
    save.add_argument("--file", required=True, help="Path to a StrategyDefinition JSON document.")
    save.add_argument(
        "--revision",
        required=True,
        type=int,
        help=(
            "Expected durable revision for an existing draft. New create-draft and "
            "import-draft identities start at revision 1."
        ),
    )
    save.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    import_draft = subparsers.add_parser(
        "import-draft",
        parents=[trailing],
        help="Create a new draft identity from a full StrategyDefinition JSON file.",
    )
    import_draft.add_argument(
        "--file",
        required=True,
        help="Path to a StrategyDefinition JSON document with a new UUIDv7 strategy_id.",
    )
    import_draft.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    publish = subparsers.add_parser(
        "publish",
        parents=[trailing],
        help="Publish the matching durable draft.",
    )
    publish.add_argument("--strategy-id", required=True, help="Server-owned strategy UUID.")
    publish.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    submit = subparsers.add_parser(
        "submit-backtest",
        parents=[trailing],
        help="Submit one idempotent research run.",
    )
    submit.add_argument(
        "--file",
        required=True,
        help="Path to a BacktestSubmissionRequest JSON document.",
    )
    submit.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    submit.add_argument(
        "--async",
        action="store_true",
        help="Queue the backtest (HTTP 202) and return a job id for polling.",
    )
    show_job = subparsers.add_parser(
        "show-backtest-job",
        parents=[trailing],
        help="Poll one async backtest job status.",
    )
    show_job.add_argument("--job-id", required=True)
    listing = subparsers.add_parser(
        "list-results",
        parents=[trailing],
        help="List immutable backtest summaries.",
    )
    listing.add_argument("--strategy-fingerprint", default=None)
    listing.add_argument("--limit", type=int, default=20)
    show = subparsers.add_parser(
        "show-result",
        parents=[trailing],
        help="Show one immutable result summary.",
    )
    show.add_argument("--result-fingerprint", required=True)
    studies = subparsers.add_parser(
        "list-studies",
        parents=[trailing],
        help="List persisted research-study catalog rows.",
    )
    studies.add_argument(
        "--kind",
        default=None,
        choices=(
            "oos_holdout",
            "walk_forward",
            "cross_market",
            "parameter_sweep",
            "walk_forward_optimization",
        ),
        help="Optional study kind filter.",
    )
    studies.add_argument("--limit", type=int, default=50)
    show_study = subparsers.add_parser(
        "show-study",
        parents=[trailing],
        help="Show one persisted research study summary.",
    )
    show_study.add_argument("--study-fingerprint", required=True)
    subparsers.add_parser(
        "list-templates",
        parents=[trailing],
        help="List fail-closed research draft templates.",
    )
    show_template = subparsers.add_parser(
        "show-template",
        parents=[trailing],
        help="Show one template's defaults, indicator ids, and sweepable axes.",
    )
    show_template.add_argument(
        "--template",
        required=True,
        help="Template id from list-templates (for example macd-trend).",
    )
    subparsers.add_parser(
        "engine-support",
        parents=[trailing],
        help="Show the V1-V4 engine-support matrix.",
    )
    plan = subparsers.add_parser(
        "plan-study",
        parents=[trailing],
        help="Plan OOS, walk-forward, cross-market, sweep, or WFO windows without submitting.",
    )
    plan.add_argument("--file", required=True, help="Path to a ResearchStudyRequest JSON document.")
    study = subparsers.add_parser(
        "submit-study",
        parents=[trailing],
        help="Submit one composed research study (OOS, walk-forward, sweep, or WFO).",
    )
    study.add_argument(
        "--file",
        required=True,
        help="Path to a ResearchStudyRequest JSON document.",
    )
    study.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    study.add_argument(
        "--async",
        action="store_true",
        help="Queue the study (HTTP 202) and return a job id for polling.",
    )
    show_research_job = subparsers.add_parser(
        "show-research-job",
        parents=[trailing],
        help="Poll one async research job (backtest or study).",
    )
    show_research_job.add_argument("--job-id", required=True)
    find_study = subparsers.add_parser(
        "find-study-by-request",
        parents=[trailing],
        help=(
            "Read back one persisted study by request fingerprint after an "
            "ambiguous submit-study failure."
        ),
    )
    find_study.add_argument("--request-fingerprint", required=True)
    cancel_research_job = subparsers.add_parser(
        "cancel-research-job",
        parents=[trailing],
        help="Cancel one queued or running research job.",
    )
    cancel_research_job.add_argument("--job-id", required=True)
    cancel_research_job.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    return parser


_RESEARCH_CONFIRM_MESSAGE = (
    "Pass --confirm to change research artifacts. "
    "This command cannot deploy, paper-trade, or live-trade."
)


def _require_confirm(confirm: bool) -> None:
    """Refuse local mutations unless the operator passed `--confirm`. YOLO is HTTP-only."""
    if not confirm:
        raise ResearchCliError(_RESEARCH_CONFIRM_MESSAGE)


def _require_http_confirm(confirm: bool, *, base_url: str, command: str) -> None:
    """Refuse HTTP mutations unless `--confirm` is present or YOLO covers research."""
    require_mutation_confirmation(
        confirmed=confirm,
        missing_message=_RESEARCH_CONFIRM_MESSAGE,
        error_type=ResearchCliError,
        base_url=base_url,
        tier=YoloTier.RESEARCH,
        command=command,
    )


def _load_json(path_text: str) -> object:
    """Load one UTF-8 JSON document from disk."""
    path = Path(path_text)
    return json.loads(path.read_text(encoding="utf-8"))


async def _mutator(settings: Settings) -> tuple[ResearchMutator, AsyncEngine | None]:
    """Build the mutator from PostgreSQL when configured."""
    if settings.database_url is None:
        raise ResearchCliError("THYTRADER_DATABASE_URL is required for --local research commands.")
    engine = create_engine(settings.database_url)
    dataset_store = DatasetStore(settings.market_data_dataset_root)
    strategy_store = PostgresStrategyPublicationStore(engine)
    run_store = PostgresResearchRunStore(engine)
    result_store = PostgresBacktestResultStore(
        engine,
        research_run_store=run_store,
        dataset_store=dataset_store,
    )
    mutator = ResearchMutator(
        drafts=strategy_store,
        publications=strategy_store,
        submitter=PostgresBacktestSubmitter(engine, dataset_store),
        results=result_store,
        audit=PostgresAuditEventStore(engine),
        catalog=PostgresResearchStudyCatalog(engine),
    )
    return mutator, engine


async def _dispatch_local(arguments: argparse.Namespace) -> str:
    """Execute one research command against PostgreSQL stores."""
    settings = Settings()
    if arguments.command == "create-draft":
        _reject_local_experiential_model(arguments)
        _require_confirm(arguments.confirm)
        return await _with_mutator(
            settings,
            lambda mutator: _create_draft(
                mutator,
                product_id=arguments.product_id,
                timeframe=arguments.timeframe,
                template=arguments.template,
            ),
        )
    if arguments.command == "save-draft":
        _require_confirm(arguments.confirm)
        definition = StrategyDefinition.model_validate(_load_json(arguments.file))
        return await _with_mutator(
            settings,
            lambda mutator: _save_draft(mutator, definition, arguments.revision),
        )
    if arguments.command == "import-draft":
        _require_confirm(arguments.confirm)
        definition = StrategyDefinition.model_validate(_load_json(arguments.file))
        return await _with_mutator(settings, lambda mutator: _import_draft(mutator, definition))
    if arguments.command == "publish":
        _require_confirm(arguments.confirm)
        strategy_id = UUID(arguments.strategy_id)
        return await _with_mutator(settings, lambda mutator: _publish(mutator, strategy_id))
    if arguments.command == "submit-backtest":
        _require_confirm(arguments.confirm)
        request = BacktestSubmissionRequest.model_validate(_load_json(arguments.file))
        return await _with_mutator(settings, lambda mutator: _submit(mutator, request))
    if arguments.command == "list-results":
        return await _with_mutator(
            settings,
            lambda mutator: _list_results(
                mutator,
                arguments.strategy_fingerprint,
                arguments.limit,
            ),
        )
    if arguments.command == "show-result":
        return await _with_mutator(
            settings,
            lambda mutator: _show_result(mutator, arguments.result_fingerprint),
        )
    if arguments.command == "list-studies":
        return await _with_mutator(
            settings,
            lambda mutator: _list_studies(mutator, arguments.kind, arguments.limit),
        )
    if arguments.command == "show-study":
        return await _with_mutator(
            settings,
            lambda mutator: _show_study(mutator, arguments.study_fingerprint),
        )
    return await _dispatch_local_study(settings, arguments)


def _dispatch_http_draft(base_url: str, arguments: argparse.Namespace) -> str | None:
    """Handle draft create/save/import mutations over HTTP."""
    if arguments.command == "create-draft":
        _require_http_confirm(arguments.confirm, base_url=base_url, command="create-draft")
        require_matching_ops_contract(base_url)
        return research_http.create_draft(
            base_url,
            product_id=arguments.product_id,
            timeframe=arguments.timeframe,
            template=arguments.template,
            experiential_model_id=_experiential_model_id(arguments),
        )
    if arguments.command == "save-draft":
        _require_http_confirm(arguments.confirm, base_url=base_url, command="save-draft")
        definition = StrategyDefinition.model_validate(_load_json(arguments.file))
        require_matching_ops_contract(base_url)
        return research_http.save_draft(base_url, definition, arguments.revision)
    if arguments.command == "import-draft":
        _require_http_confirm(arguments.confirm, base_url=base_url, command="import-draft")
        definition = StrategyDefinition.model_validate(_load_json(arguments.file))
        require_matching_ops_contract(base_url)
        return research_http.import_draft(base_url, definition)
    return None


def _dispatch_http_jobs(base_url: str, arguments: argparse.Namespace) -> str | None:
    """Handle backtest and research job commands over HTTP."""
    if arguments.command == "submit-backtest":
        _require_http_confirm(arguments.confirm, base_url=base_url, command="submit-backtest")
        request = BacktestSubmissionRequest.model_validate(_load_json(arguments.file))
        require_matching_ops_contract(base_url)
        return research_http.submit_backtest(
            base_url,
            request,
            async_submission=bool(getattr(arguments, "async", False)),
        )
    if arguments.command == "show-backtest-job":
        require_matching_ops_contract(base_url)
        return research_http.show_backtest_job(base_url, arguments.job_id)
    if arguments.command == "show-research-job":
        require_matching_ops_contract(base_url)
        return research_http.show_research_job(base_url, arguments.job_id)
    if arguments.command == "find-study-by-request":
        require_matching_ops_contract(base_url)
        return research_http.find_study_by_request(base_url, arguments.request_fingerprint)
    if arguments.command == "cancel-research-job":
        _require_http_confirm(arguments.confirm, base_url=base_url, command="cancel-research-job")
        require_matching_ops_contract(base_url)
        return research_http.cancel_research_job(base_url, arguments.job_id)
    return None


def _dispatch_http(arguments: argparse.Namespace) -> str:
    """Execute one research command against the loopback HTTP API."""
    settings = Settings()
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    draft_output = _dispatch_http_draft(base_url, arguments)
    if draft_output is not None:
        return draft_output
    if arguments.command == "publish":
        _require_http_confirm(arguments.confirm, base_url=base_url, command="publish")
        strategy_id = UUID(arguments.strategy_id)
        require_matching_ops_contract(base_url)
        return research_http.publish(base_url, strategy_id)
    job_output = _dispatch_http_jobs(base_url, arguments)
    if job_output is not None:
        return job_output
    if arguments.command == "list-results":
        require_matching_ops_contract(base_url)
        return research_http.list_results(
            base_url,
            arguments.strategy_fingerprint,
            arguments.limit,
        )
    if arguments.command == "show-result":
        require_matching_ops_contract(base_url)
        return research_http.show_result(base_url, arguments.result_fingerprint)
    if arguments.command == "list-studies":
        require_matching_ops_contract(base_url)
        return research_http.list_studies(base_url, arguments.kind, arguments.limit)
    if arguments.command == "show-study":
        require_matching_ops_contract(base_url)
        return research_http.show_study(base_url, arguments.study_fingerprint)
    return _dispatch_http_study(base_url, arguments)


async def _with_mutator(
    settings: Settings,
    operation: Callable[[ResearchMutator], Awaitable[str]],
) -> str:
    """Run one async mutator operation and always dispose the engine."""
    mutator, engine = await _mutator(settings)
    try:
        return await operation(mutator)
    finally:
        if engine is not None:
            await dispose(engine)


async def _create_draft(
    mutator: ResearchMutator,
    *,
    product_id: str,
    timeframe: str,
    template: str,
) -> str:
    """Create a research template draft and return identities."""
    draft = await mutator.create_reference_draft(
        product_id=product_id,
        timeframe=timeframe,
        template=template,
    )
    return _encode(
        {
            "strategy_id": str(draft.definition.strategy_id),
            "revision": draft.revision,
            "version": draft.definition.version,
            "name": draft.definition.name,
        }
    )


async def _save_draft(
    mutator: ResearchMutator,
    definition: StrategyDefinition,
    revision: int,
) -> str:
    """Save one draft JSON document."""
    draft = await mutator.save_draft(definition, expected_revision=revision)
    return _encode(
        {
            "strategy_id": str(draft.definition.strategy_id),
            "revision": draft.revision,
            "version": draft.definition.version,
        }
    )


async def _import_draft(mutator: ResearchMutator, definition: StrategyDefinition) -> str:
    """Import one custom strategy document as a new draft identity."""
    draft = await mutator.import_draft(definition)
    return _encode(
        {
            "strategy_id": str(draft.definition.strategy_id),
            "revision": draft.revision,
            "version": draft.definition.version,
            "name": draft.definition.name,
        }
    )


async def _publish(mutator: ResearchMutator, strategy_id: UUID) -> str:
    """Publish one draft identity."""
    published = await mutator.publish(strategy_id)
    return _encode(
        {
            "strategy_id": str(published.definition.strategy_id),
            "strategy_fingerprint": published.strategy_fingerprint,
            "version": published.definition.version,
        }
    )


async def _submit(mutator: ResearchMutator, request: BacktestSubmissionRequest) -> str:
    """Submit one backtest request document."""
    run_fingerprint, result_fingerprint = await mutator.submit_backtest(request)
    return _encode(
        {
            "run_fingerprint": run_fingerprint,
            "result_fingerprint": result_fingerprint,
        }
    )


async def _list_results(
    mutator: ResearchMutator,
    strategy_fingerprint: str | None,
    limit: int,
) -> str:
    """List bounded result summaries."""
    rows = await mutator.list_results(strategy_fingerprint=strategy_fingerprint, limit=limit)
    return _encode(
        {
            "results": [
                {
                    "result_fingerprint": row.result_fingerprint,
                    "run_fingerprint": row.run_fingerprint,
                    "strategy_fingerprint": row.strategy_fingerprint,
                    "dataset_fingerprint": row.dataset_fingerprint,
                    "engine_contract_version": row.engine_contract_version,
                    "published_at": row.published_at.isoformat(),
                    "trade_count": row.summary.trade_count,
                    "total_net_pnl": row.summary.total_net_pnl,
                }
                for row in rows
            ]
        }
    )


async def _show_result(mutator: ResearchMutator, result_fingerprint: str) -> str:
    """Load one result summary without dumping the full trade ledger."""
    result = await mutator.results.load(result_fingerprint)
    computed = backtest_result_fingerprint(result)
    timeframe = "1h"
    currency = "USD"
    try:
        published = await mutator.publications.load(result.strategy_fingerprint)
        timeframe = published_execution_timeframe(published.definition.timeframe)
        currency = published.definition.instrument.quote_currency
    except StrategyPublicationError:
        timeframe = "1h"
        currency = "USD"
    return _encode(
        {
            "result_fingerprint": computed,
            "run_fingerprint": result.run_fingerprint,
            "strategy_fingerprint": result.strategy_fingerprint,
            "dataset_fingerprint": result.dataset_fingerprint,
            "engine_contract_version": result.engine_contract_version,
            "mode": "backtest",
            "timeframe": timeframe,
            "currency": currency,
            "summary": result.summary.model_dump(mode="json"),
        }
    )


async def _dispatch_local_study(settings: Settings, arguments: argparse.Namespace) -> str:
    """Handle Phase 11 study commands against local stores."""
    if arguments.command == "list-templates":
        return _encode({"templates": list(template_catalog())})
    if arguments.command == "engine-support":
        return _encode(engine_support_matrix().model_dump(mode="json"))
    if arguments.command == "plan-study":
        request = ResearchStudyRequest.model_validate(_load_json(arguments.file))
        return await _with_mutator(settings, lambda mutator: _plan_study(mutator, request))
    if arguments.command == "submit-study":
        _require_confirm(arguments.confirm)
        request = ResearchStudyRequest.model_validate(_load_json(arguments.file))
        return await _with_mutator(settings, lambda mutator: _submit_study(mutator, request))
    raise AssertionError(f"unsupported research command: {arguments.command}")


def _dispatch_http_study(base_url: str, arguments: argparse.Namespace) -> str:
    """Handle Phase 11 study commands against the loopback HTTP API."""
    if arguments.command == "submit-study":
        _require_http_confirm(arguments.confirm, base_url=base_url, command="submit-study")
    require_matching_ops_contract(base_url)
    if arguments.command == "list-templates":
        return research_http.list_templates(base_url)
    if arguments.command == "show-template":
        require_matching_ops_contract(base_url)
        return research_http.show_template(base_url, arguments.template)
    if arguments.command == "engine-support":
        return research_http.engine_support(base_url)
    if arguments.command == "plan-study":
        request = ResearchStudyRequest.model_validate(_load_json(arguments.file))
        return research_http.plan_study(base_url, request)
    if arguments.command == "submit-study":
        request = ResearchStudyRequest.model_validate(_load_json(arguments.file))
        return research_http.submit_study(
            base_url,
            request,
            async_submission=bool(getattr(arguments, "async", False)),
        )
    raise AssertionError(f"unsupported research command: {arguments.command}")


async def _plan_study(mutator: ResearchMutator, request: ResearchStudyRequest) -> str:
    """Plan study windows without submitting child backtests."""
    service = ResearchStudyService(
        publications=mutator.publications,
        submitter=mutator.submitter,
        results=mutator.results,
        catalog=mutator.catalog,
    )
    plan = await service.plan(request)
    return _encode(summarize_research_study_plan(plan).model_dump(mode="json"))


async def _submit_study(mutator: ResearchMutator, request: ResearchStudyRequest) -> str:
    """Submit one composed study and return the derived document."""
    study = await mutator.submit_study(request)
    return _encode(study.model_dump(mode="json"))


async def _show_study(mutator: ResearchMutator, study_fingerprint: str) -> str:
    """Load one persisted study without dumping child equity curves."""
    study = await mutator.show_study(study_fingerprint)
    payload = study.model_dump(mode="json")
    payload.pop("windows", None)
    if payload.get("stitched_oos_equity") is not None:
        stitch = payload["stitched_oos_equity"]
        if isinstance(stitch, dict):
            stitch.pop("points", None)
    return _encode(payload)


async def _list_studies(mutator: ResearchMutator, kind: str | None, limit: int) -> str:
    """List persisted study catalog rows."""
    rows = await mutator.list_studies(kind=kind, limit=limit)
    return _encode({"studies": [row.model_dump(mode="json") for row in rows]})


def _experiential_model_id(arguments: argparse.Namespace) -> str | None:
    """Parse the optional trained-model UUID or return None."""
    raw = getattr(arguments, "experiential_model_id", None)
    if raw is None:
        return None
    try:
        return str(UUID(raw))
    except ValueError as error:
        raise ResearchCliError("--experiential-model-id must be a UUID.") from error


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
        StudyPlanningError,
        StudyCatalogNotFoundError,
        StudyCatalogIntegrityError,
    ) as error:
        raise SystemExit(str(error)) from error
    except AgentHttpError as error:
        raise SystemExit(_agent_http_error_message(str(error))) from error
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
        message = "Research command failed safely; paper and live state were not changed."
        raise SystemExit(message) from error
    sys.stdout.write(f"{output}\n")
    raise SystemExit(EXIT_HEALTHY)


if __name__ == "__main__":
    main()
