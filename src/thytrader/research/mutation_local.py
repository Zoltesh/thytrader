"""``thytrader-research --local`` command handlers against the PostgreSQL stores."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from thytrader.backtest.cost_attribution import compute_cost_attribution
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestEvaluationWindow,
    BacktestResult,
    backtest_evaluation_window,
    backtest_result_fingerprint,
)
from thytrader.backtest.results import (
    BacktestDiagnosticsReader,
    BacktestSourceSpecificationReader,
)
from thytrader.backtest.submission import BacktestStartRequest
from thytrader.config import Settings
from thytrader.data_control.service import ingestion_provider
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import published_execution_timeframe
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_backtest_submitter import PostgresBacktestSubmitter
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.postgres_studies import PostgresResearchStudyCatalog
from thytrader.research import http as research_http
from thytrader.research.backtest_model import backtest_model_description
from thytrader.research.mutation import ResearchMutator
from thytrader.research.mutation_common import (
    ResearchCliError,
    _clone_name,
    _load_document,
    _load_json,
    _optional_uuid,
    _require_confirm,
    _uuid,
)
from thytrader.research.studies import (
    ResearchStudyService,
    load_candidate_definitions,
    summarize_research_study,
    summarize_research_study_plan,
)
from thytrader.research.study_start import BoundStudyStart, ResearchStudyStartRequest
from thytrader.strategies.advisories import strategy_warnings
from thytrader.strategies.snapshots import StrategySnapshotError
from thytrader.strategies.templates import template_catalog

if TYPE_CHECKING:
    import argparse
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.strategies.library import (
        BulkDeletionItem,
        BulkDeletionReport,
        StrategyRecord,
    )


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
            "cost_attribution": compute_cost_attribution(result).model_dump(mode="json"),
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


def _reject_local_experiential_model(arguments: argparse.Namespace) -> None:
    """Refuse gated advisory input on --local PostgreSQL research."""
    if getattr(arguments, "experiential_model_id", None):
        raise ResearchCliError(
            "--experiential-model-id requires HTTP transport; do not pass --local."
        )


def _encode(payload: object) -> str:
    """Render stable JSON for agent consumption."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
