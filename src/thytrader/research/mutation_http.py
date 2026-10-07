"""``thytrader-research`` command handlers over the loopback HTTP API (the default)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.agent_http import require_matching_ops_contract, resolve_api_base_url
from thytrader.backtest.submission import BacktestStartRequest
from thytrader.config import Settings
from thytrader.research import http as research_http
from thytrader.research.mutation_common import (
    ResearchCliError,
    _clone_name,
    _load_document,
    _load_json,
    _optional_uuid,
    _require_http_confirm,
    _uuid,
)
from thytrader.research.study_start import ResearchStudyStartRequest
from thytrader.strategies.library import StrategyOrigin

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable


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
    "explain-bars": lambda url, args: research_http.explain_bars(
        url,
        args.result_fingerprint,
        limit=args.limit,
        cursor=args.cursor,
    ),
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


def _experiential_model_id(arguments: argparse.Namespace) -> str | None:
    """Parse the optional trained-model UUID or return None."""
    raw = getattr(arguments, "experiential_model_id", None)
    if raw is None:
        return None
    return str(_uuid(raw, "--experiential-model-id"))
