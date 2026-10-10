"""Read-only operator CLI backed by the loopback HTTP API by default."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader import __version__
from thytrader.agent_http import (
    AgentHttpError,
    request_json,
    require_matching_ops_contract,
    resolve_api_base_url,
)
from thytrader.cli_errors import describe_unexpected_failure
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.execution.decisions import DECISION_PAGE_MAX_LIMIT, DecisionOutcome
from thytrader.market_data.instrument_ids import normalize_futures_product_id
from thytrader.market_data.models import DATASET_TIMEFRAMES
from thytrader.operator.data_health import data_health_report
from thytrader.operator.funding_report import (
    FUNDING_REPORT_DEFAULT_HOURS,
    FUNDING_REPORT_MAX_HOURS,
)
from thytrader.operator.health_models import HealthReport
from thytrader.operator.http import fetch_operator_report
from thytrader.operator.redaction import configured_secrets, dumps_redacted, redact_text
from thytrader.operator.schema_check import SchemaCheckError, check_operator_schema
from thytrader.operator.session import operator_diagnostics
from thytrader.operator.status import EXIT_FAILED, EXIT_HEALTHY, EXIT_USAGE, exit_code_for
from thytrader.operator_chat.http import fetch_chat_status
from thytrader.ops_contract import EXPECTED_SCHEMA_REVISION, STALE_IMAGE_REBUILD
from thytrader.settings_yaml import SettingsStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.operator.models import OperatorEnvelope
    from thytrader.operator.service import OperatorDiagnostics


def _shared_options() -> argparse.ArgumentParser:
    """Global flags that may appear before or after the subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json is the agent contract; text is a short human summary.",
    )
    shared.add_argument(
        "--base-url",
        default=None,
        help="Loopback API origin. Defaults to THYTRADER_API_BASE_URL or settings.",
    )
    shared.add_argument(
        "--local",
        action="store_true",
        help="Query local stores instead of HTTP. Do not use as a silent API fallback.",
    )
    return shared


def _parser() -> argparse.ArgumentParser:
    """Build the read-only operator argument parser."""
    shared = _shared_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-operator",
        description=(
            "Read-only diagnostics for a running ThyTrader instance. "
            "This command cannot place, edit, or cancel orders, or arm live trading. "
            "Default transport is the loopback HTTP API; --local uses process stores. "
            "chat-status is HTTP-only and reports the in-app LLM key flag, not Coinbase."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "health",
        parents=[trailing],
        help="API, workers, database, and exchange health.",
    )
    subparsers.add_parser(
        "configuration",
        parents=[trailing],
        help="Redacted configuration validity.",
    )
    subparsers.add_parser(
        "exchange",
        parents=[trailing],
        help="Coinbase connectivity and permissions.",
    )
    market = subparsers.add_parser(
        "market-data",
        parents=[trailing],
        help="1h, 5m, 15m, 30m, 6h, 1d, 1m, 2h, or 4h freshness and gap report.",
    )
    market.add_argument(
        "--product-id",
        default=None,
        help=(
            "USD, USDC, or USDT spot product, or a watched futures contract such as "
            "BIP-20DEC30-CDE; default from settings."
        ),
    )
    market.add_argument(
        "--timeframe",
        default="1h",
        choices=DATASET_TIMEFRAMES,
        help="Dataset candle interval. Default 1h.",
    )
    products = subparsers.add_parser(
        "products",
        parents=[trailing],
        help=(
            "Enabled USD, USDC, and USDT spot products from the current catalog; "
            "--kind future|all adds the read-only Coinbase futures listing."
        ),
    )
    products.add_argument(
        "--kind",
        choices=("spot", "future", "all"),
        default="spot",
        help=(
            "spot (default, unchanged report), future (futures contracts only; not orderable), "
            "or all."
        ),
    )
    funding = subparsers.add_parser(
        "funding",
        parents=[trailing],
        help=(
            "Recorded Coinbase futures (CFM) funding-rate history and futures poller health. "
            "Read-only; futures cannot be ordered."
        ),
    )
    funding.add_argument(
        "--product-id",
        type=_futures_product_id,
        default=None,
        help="One futures contract, e.g. BIP-20DEC30-CDE, to list every stored hour.",
    )
    funding.add_argument(
        "--hours",
        type=_funding_hours,
        metavar=f"1..{FUNDING_REPORT_MAX_HOURS}",
        default=FUNDING_REPORT_DEFAULT_HOURS,
        help=f"Window ending at the current hour. Default {FUNDING_REPORT_DEFAULT_HOURS}.",
    )
    subparsers.add_parser(
        "futures-account",
        parents=[trailing],
        help=(
            "Latest read-only Coinbase futures (CFM) account mirror: enablement, USD balance "
            "summary, positions in contracts, margin window, failed reads."
        ),
    )
    subparsers.add_parser(
        "data-catalog",
        parents=[trailing],
        help=(
            "Local datasets, watchlist, and watch coverage (X of Y bars, no-trade bars, "
            "listing floors)."
        ),
    )
    subparsers.add_parser(
        "data-health",
        parents=[trailing],
        help="All enabled watched markets: expected close, tail lag and historical coverage.",
    )
    subparsers.add_parser(
        "indicators",
        parents=[trailing],
        help="Implemented indicator kinds and period bounds.",
    )
    subparsers.add_parser(
        "strategies",
        parents=[trailing],
        help="Strategy library (revision, validity, current fingerprint) and deployment status.",
    )
    performance = subparsers.add_parser(
        "performance",
        parents=[trailing],
        help="Backtest or runtime performance slice.",
    )
    performance.add_argument("--result-fingerprint", default=None)
    performance.add_argument("--deployment-id", default=None)
    subparsers.add_parser(
        "risk",
        parents=[trailing],
        help="Risk-policy registry identity, slot counts, and pause/mismatch findings.",
    )
    subparsers.add_parser(
        "reconciliation",
        parents=[trailing],
        help="Unknown orders and mismatch findings.",
    )
    runtime = subparsers.add_parser(
        "runtime",
        parents=[trailing],
        help="Paper/live status without trading.",
    )
    runtime.add_argument("--deployment-id", default=None)
    subparsers.add_parser(
        "monitor",
        parents=[trailing],
        help="Watch deployments, recent journals, why-trade records, and notify.",
    )
    trade_reasons = subparsers.add_parser(
        "trade-reasons",
        parents=[trailing],
        help="Review why a paper or live intent was persisted.",
    )
    trade_reasons.add_argument("--deployment-id", default=None)
    trade_reasons.add_argument("--intent-id", default=None)
    _add_decisions_parser(subparsers, trailing)
    _add_execution_quality_parser(subparsers, trailing)
    subparsers.add_parser(
        "studies",
        parents=[trailing],
        help="Persisted research-study catalog rows.",
    )
    subparsers.add_parser(
        "portfolio",
        parents=[trailing],
        help="Current Coinbase or demo balances without credentials.",
    )
    subparsers.add_parser(
        "fees",
        parents=[trailing],
        help="Current fee tier and research-only suggested maker/taker rates.",
    )
    subparsers.add_parser(
        "portfolios",
        parents=[trailing],
        help=(
            "Portfolios: sleeves, allocation, limits, manager settings, newest portfolio "
            "backtest (read-only; no deployment authority)."
        ),
    )
    readiness = subparsers.add_parser(
        "readiness",
        parents=[trailing],
        help=(
            "Advisory preflight: allocations vs venue balance vs account and portfolio "
            "caps, fee assumptions, and breaker disclosures. Never changes policy."
        ),
    )
    readiness.add_argument("--deployment-id", default=None, help="One book's preflight.")
    readiness.add_argument("--portfolio-id", default=None, help="One portfolio's sleeves and caps.")
    subparsers.add_parser(
        "venue-reconciliation",
        parents=[trailing],
        help=(
            "Managed live inventory and working orders versus the venue listing. "
            "Read-only; never cancels or flattens foreign holdings."
        ),
    )
    subparsers.add_parser(
        "alerts",
        parents=[trailing],
        help=(
            "Durable safety alerts (pause, breaker, stop cover, deadlines, worker failures). "
            "Read-only. Works with notify_provider=none; delivery_warning says so."
        ),
    )
    subparsers.add_parser(
        "support-bundle",
        parents=[trailing],
        help="Redacted bundle of the supported reports.",
    )
    subparsers.add_parser(
        "schema-check",
        parents=[trailing],
        help="Verify skill docs match SCHEMA_VERSION.",
    )
    subparsers.add_parser(
        "chat-status",
        parents=[trailing],
        help="Whether an LLM key is held in the API process (never prints the key).",
    )
    return parser


def _add_execution_quality_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register the read-only recorded-fill execution-quality report."""
    quality = subparsers.add_parser(
        "execution-quality",
        parents=[trailing],
        help=(
            "Recorded closed-trade fees, net PnL, and slippage versus journaled closes. "
            "HTTP-only. Missing fees and liquidity are never treated as zero."
        ),
    )
    quality.add_argument("--deployment-id", required=True, help="One paper or live bot.")
    quality.add_argument(
        "--twin",
        action="store_true",
        help="Compare the explicit paper/live twin instead of one book.",
    )


def _add_decisions_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register the read-only per-bar decision timeline report."""
    decisions = subparsers.add_parser(
        "decisions",
        parents=[trailing],
        help=(
            "Per-bar decision timeline (newest first): what each paper/live bot decided on "
            "every completed bar and why. Filter by --deployment-id or --strategy-id."
        ),
    )
    decisions.add_argument("--deployment-id", default=None, help="One bot's decisions.")
    decisions.add_argument(
        "--strategy-id", default=None, help="Decisions across one strategy's bots."
    )
    decisions.add_argument(
        "--outcome",
        action="append",
        choices=tuple(item.value for item in DecisionOutcome),
        default=None,
        help="Repeatable outcome filter, e.g. --outcome entry_signal --outcome exit.",
    )
    decisions.add_argument(
        "--limit", type=int, default=50, help=f"Page size 1..{DECISION_PAGE_MAX_LIMIT}."
    )
    decisions.add_argument("--cursor", default=None, help="next_cursor from the previous page.")


async def _dispatch(
    diagnostics: OperatorDiagnostics,
    arguments: argparse.Namespace,
) -> OperatorEnvelope:
    """Run one read-only report from local stores."""
    command = arguments.command
    if command == "decisions":
        return await diagnostics.decisions(
            deployment_id=_uuid_or_none(arguments.deployment_id),
            strategy_id=_uuid_or_none(arguments.strategy_id),
            outcomes=tuple(DecisionOutcome(item) for item in arguments.outcome or ()),
            limit=arguments.limit,
            cursor=arguments.cursor,
        )
    scoped = await _argument_report(diagnostics, arguments)
    if scoped is not None:
        return scoped
    if command == "products":
        return await diagnostics.products(arguments.kind)
    if command == "data-health":
        return data_health_report(await diagnostics.data_catalog())
    if command == "indicators":
        return await diagnostics.indicators()
    if command == "performance":
        return await diagnostics.performance(
            result_fingerprint=arguments.result_fingerprint,
            deployment_id=_uuid_or_none(arguments.deployment_id),
        )
    if command == "runtime":
        return await diagnostics.runtime_report(_uuid_or_none(arguments.deployment_id))
    if command == "trade-reasons":
        return await diagnostics.trade_reasons(
            intent_id=_uuid_or_none(getattr(arguments, "intent_id", None)),
            deployment_id=_uuid_or_none(getattr(arguments, "deployment_id", None)),
        )
    factories = {
        "data-catalog": diagnostics.data_catalog,
        "health": lambda: diagnostics.health(probe_api=True),
        "configuration": diagnostics.configuration,
        "exchange": diagnostics.exchange,
        "strategies": diagnostics.strategies,
        "risk": diagnostics.risk,
        "reconciliation": diagnostics.reconciliation,
        "monitor": diagnostics.monitor,
        "studies": diagnostics.studies,
        "portfolio": diagnostics.portfolio_report,
        "fees": diagnostics.fees_report,
        "portfolios": diagnostics.portfolios_report,
        "alerts": diagnostics.alerts,
        "support-bundle": diagnostics.support_bundle,
        "futures-account": diagnostics.futures_account,
    }
    factory = factories.get(command)
    if factory is None:
        raise AssertionError(f"unsupported operator command: {command}")
    return await factory()


async def _argument_report(
    diagnostics: OperatorDiagnostics, arguments: argparse.Namespace
) -> OperatorEnvelope | None:
    """Reports whose flags do not fit the no-argument factory table."""
    command = arguments.command
    if command == "market-data":
        return await diagnostics.market_data_report(arguments.product_id, arguments.timeframe)
    if command == "readiness":
        return await diagnostics.readiness_report(
            deployment_id=_uuid_or_none(getattr(arguments, "deployment_id", None)),
            portfolio_id=_uuid_or_none(getattr(arguments, "portfolio_id", None)),
        )
    if command == "venue-reconciliation":
        return await diagnostics.venue_reconciliation_report()
    if command == "funding":
        return await diagnostics.funding(product_id=arguments.product_id, hours=arguments.hours)
    return None


def _funding_hours(value: str) -> int:
    """Parse the funding window in whole hours within the report's bound."""
    try:
        hours = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--hours must be a whole number.") from None
    if not 1 <= hours <= FUNDING_REPORT_MAX_HOURS:
        message = f"--hours must be between 1 and {FUNDING_REPORT_MAX_HOURS}."
        raise argparse.ArgumentTypeError(message)
    return hours


def _futures_product_id(value: str) -> str:
    """Normalize a futures id argument or reject it with a usage error."""
    try:
        return normalize_futures_product_id(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _uuid_or_none(value: str | None) -> UUID | None:
    """Parse an optional UUID argument."""
    if value is None:
        return None
    return UUID(value)


def _query(arguments: argparse.Namespace) -> dict[str, str | tuple[str, ...]]:
    """Collect optional GET query parameters for HTTP mode."""
    query: dict[str, str | tuple[str, ...]] = {}
    if arguments.command == "decisions":
        return _decision_query(arguments)
    product_id = getattr(arguments, "product_id", None)
    if isinstance(product_id, str) and product_id:
        query["product_id"] = product_id
    timeframe = getattr(arguments, "timeframe", None)
    if isinstance(timeframe, str) and timeframe:
        query["timeframe"] = timeframe
    result_fingerprint = getattr(arguments, "result_fingerprint", None)
    if isinstance(result_fingerprint, str) and result_fingerprint:
        query["result_fingerprint"] = result_fingerprint
    deployment_id = getattr(arguments, "deployment_id", None)
    if isinstance(deployment_id, str) and deployment_id:
        query["deployment_id"] = deployment_id
    intent_id = getattr(arguments, "intent_id", None)
    if isinstance(intent_id, str) and intent_id:
        query["intent_id"] = intent_id
    portfolio_id = getattr(arguments, "portfolio_id", None)
    if isinstance(portfolio_id, str) and portfolio_id:
        query["portfolio_id"] = portfolio_id
    kind = getattr(arguments, "kind", None)
    if isinstance(kind, str) and kind != "spot":
        query["kind"] = kind
    hours = getattr(arguments, "hours", None)
    if isinstance(hours, int):
        query["hours"] = str(hours)
    return query


def _decision_query(arguments: argparse.Namespace) -> dict[str, str | tuple[str, ...]]:
    """Map decisions flags onto the operator route's query (repeated ``outcome``)."""
    query: dict[str, str | tuple[str, ...]] = {"limit": str(arguments.limit)}
    for key in ("deployment_id", "strategy_id", "cursor"):
        value = getattr(arguments, key, None)
        if isinstance(value, str) and value:
            query[key] = value
    if arguments.outcome:
        query["outcome"] = tuple(arguments.outcome)
    return query


def _render(report: OperatorEnvelope, *, fmt: str, secrets: tuple[str, ...]) -> str:
    """Render JSON or a short text summary with residual secrets removed."""
    payload = report.model_dump(mode="json")
    if fmt == "json":
        return dumps_redacted(payload, secrets)
    lines = [
        f"status={report.overall_status.value}",
        f"schema={report.schema_version}",
        f"next={report.recommended_next_action}",
        *(
            f"{component.name}={component.status.value}:{component.reason_code}"
            for component in report.components
        ),
    ]
    return redact_text("\n".join(lines), secrets)


async def _run_local(arguments: argparse.Namespace) -> int:
    """Load diagnostics from process stores and emit one report."""
    settings = SettingsStore.open().current()
    secrets = configured_secrets(settings)
    async with operator_diagnostics(settings) as diagnostics:
        report = await _dispatch(diagnostics, arguments)
    sys.stdout.write(f"{_render(report, fmt=arguments.format, secrets=secrets)}\n")
    sys.stdout.flush()
    return exit_code_for(report.overall_status)


def _reject_stale_report(report: OperatorEnvelope) -> None:
    """Fail closed when a fetched report still disagrees with this checkout."""
    if report.application_version != __version__:
        raise AgentHttpError(
            f"API version {report.application_version} does not match CLI {__version__}. "
            f"{STALE_IMAGE_REBUILD}"
        )
    if not isinstance(report, HealthReport):
        return
    applied = report.payload.applied_schema_revision
    if applied is not None and applied != EXPECTED_SCHEMA_REVISION:
        raise AgentHttpError(
            f"API database schema revision {applied} does not match expected "
            f"{EXPECTED_SCHEMA_REVISION}. {STALE_IMAGE_REBUILD}"
        )


def _run_http(arguments: argparse.Namespace) -> int:
    """Fetch one report from the loopback API without opening PostgreSQL."""
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    require_matching_ops_contract(base_url)
    report = fetch_operator_report(
        base_url=base_url,
        command=arguments.command,
        query=_query(arguments),
    )
    _reject_stale_report(report)
    sys.stdout.write(f"{_render(report, fmt=arguments.format, secrets=secrets)}\n")
    sys.stdout.flush()
    return exit_code_for(report.overall_status)


def _run_schema_check(*, fmt: str) -> int:
    """Verify shipped skill files against the application schema."""
    result = check_operator_schema()
    if fmt == "text":
        sys.stdout.write(f"ok schema={result.schema_version}\n")
        return EXIT_HEALTHY
    payload = {
        "ok": result.ok,
        "schema_version": result.schema_version,
        "report_kinds": list(result.report_kinds),
        "schema_path": result.schema_path,
    }
    sys.stdout.write(f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n")
    return EXIT_HEALTHY


def _run_chat_status(arguments: argparse.Namespace) -> int:
    """Fetch redacted LLM-key status. HTTP-only; keys are not in --local stores."""
    if arguments.local:
        raise AgentHttpError(
            "chat-status is HTTP-only because LLM keys are held in the API process, "
            "not local stores. Do not pass --local."
        )
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    require_matching_ops_contract(base_url)
    status = fetch_chat_status(base_url)
    payload = status.model_dump(mode="json")
    if arguments.format == "text":
        configured = "yes" if status.llm_configured else "no"
        rendered = (
            f"llm_configured={configured}\n"
            f"provider={status.provider or 'none'}\n"
            f"model={status.model or 'none'}\n"
            f"key_storage={status.key_storage}\n"
            "coinbase_credentials_in_chat=false"
        )
        sys.stdout.write(f"{redact_text(rendered, secrets)}\n")
        return EXIT_HEALTHY
    sys.stdout.write(f"{dumps_redacted(payload, secrets)}\n")
    return EXIT_HEALTHY


def _run_execution_quality(arguments: argparse.Namespace) -> int:
    """Fetch recorded execution-quality evidence. HTTP-only; it never mutates fills."""
    if arguments.local:
        raise AgentHttpError(
            "execution-quality is HTTP-only because it reads the API's execution and decision "
            "stores together. Do not pass --local."
        )
    try:
        deployment_id = UUID(arguments.deployment_id)
    except ValueError:
        raise AgentHttpError("--deployment-id must be a UUID.") from None
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    require_matching_ops_contract(base_url)
    suffix = "/twin" if arguments.twin else ""
    payload = request_json(
        method="GET",
        url=f"{base_url}/api/v1/deployments/{deployment_id}/execution-quality{suffix}",
    )
    if not isinstance(payload, dict):
        raise AgentHttpError("Execution-quality response was not a JSON object.")
    evidence = {str(key): value for key, value in payload.items()}
    if arguments.format == "text":
        rendered = _execution_quality_text(evidence, twin=bool(arguments.twin))
        sys.stdout.write(f"{redact_text(rendered, secrets)}\n")
        return EXIT_HEALTHY
    sys.stdout.write(f"{dumps_redacted(payload, secrets)}\n")
    return EXIT_HEALTHY


def _execution_quality_text(payload: dict[str, object], *, twin: bool) -> str:
    """Summarize one execution-quality payload without inventing missing fields."""
    if twin:
        comparable = payload.get("comparable")
        reasons = payload.get("reasons")
        reason_text = ",".join(str(item) for item in reasons) if isinstance(reasons, list) else ""
        return f"comparable={comparable}\nreasons={reason_text}"
    totals = payload.get("totals")
    evidence = payload.get("evidence")
    net = totals.get("net_pnl") if isinstance(totals, dict) else None
    complete = evidence.get("complete") if isinstance(evidence, dict) else None
    return f"net_pnl={net}\nevidence_complete={complete}"


def main(argv: Sequence[str] | None = None) -> None:
    """Print one operator report and exit with 0/1/2 for healthy/degraded/failed."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else EXIT_USAGE) from error
    if arguments.local and arguments.base_url:
        raise SystemExit("Use either --local or --base-url, not both.")
    try:
        if arguments.command == "schema-check":
            code = _run_schema_check(fmt=arguments.format)
        elif arguments.command == "chat-status":
            code = _run_chat_status(arguments)
        elif arguments.command == "execution-quality":
            code = _run_execution_quality(arguments)
        elif arguments.local:
            code = asyncio.run(_run_local(arguments))
        else:
            code = _run_http(arguments)
    except SchemaCheckError as error:
        sys.stderr.write(f"{error}\n")
        raise SystemExit(EXIT_FAILED) from error
    except AgentHttpError as error:
        raise SystemExit(str(error)) from error
    except Exception as error:
        message = describe_unexpected_failure(
            error, lane="Operator diagnostics", safety="Trading state was not changed."
        )
        raise SystemExit(message) from error
    raise SystemExit(code)


if __name__ == "__main__":
    main()
