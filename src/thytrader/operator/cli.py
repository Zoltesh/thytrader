"""Read-only operator CLI backed by the loopback HTTP API by default."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.agent_http import AgentHttpError, resolve_api_base_url
from thytrader.config import Settings
from thytrader.operator.http import fetch_operator_report
from thytrader.operator.redaction import configured_secrets, dumps_redacted, redact_text
from thytrader.operator.schema_check import SchemaCheckError, check_operator_schema
from thytrader.operator.session import operator_diagnostics
from thytrader.operator.status import EXIT_FAILED, EXIT_HEALTHY, EXIT_USAGE, exit_code_for

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.operator.models import OperatorEnvelope
    from thytrader.operator.service import OperatorDiagnostics


def _parser() -> argparse.ArgumentParser:
    """Build the read-only operator argument parser."""
    parser = argparse.ArgumentParser(
        prog="thytrader-operator",
        description=(
            "Read-only diagnostics for a running ThyTrader instance. "
            "This command cannot place, edit, or cancel orders, or arm live trading. "
            "Default transport is the loopback HTTP API; --local uses process stores."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json is the agent contract; text is a short human summary.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Loopback API origin. Defaults to THYTRADER_API_BASE_URL or settings.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Query local stores instead of HTTP. Do not use as a silent API fallback.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="API, workers, database, and exchange health.")
    subparsers.add_parser("configuration", help="Redacted configuration validity.")
    subparsers.add_parser("exchange", help="Coinbase connectivity and permissions.")
    market = subparsers.add_parser("market-data", help="1h or 5m freshness and gap report.")
    market.add_argument(
        "--product-id",
        default=None,
        help="USD spot product, default from settings.",
    )
    market.add_argument(
        "--timeframe",
        default="1h",
        choices=("1h", "5m"),
        help="Candle interval. Default 1h.",
    )
    subparsers.add_parser("products", help="Enabled USD spot products from the current catalog.")
    subparsers.add_parser("data-catalog", help="Local datasets, watchlist, and coverage.")
    subparsers.add_parser("indicators", help="Implemented indicator kinds and period bounds.")
    subparsers.add_parser("strategies", help="Draft, publication, and runtime status.")
    performance = subparsers.add_parser(
        "performance",
        help="Backtest or runtime performance slice.",
    )
    performance.add_argument("--result-fingerprint", default=None)
    performance.add_argument("--deployment-id", default=None)
    subparsers.add_parser("risk", help="Pause and mismatch findings.")
    subparsers.add_parser("reconciliation", help="Unknown orders and mismatch findings.")
    runtime = subparsers.add_parser("runtime", help="Paper/live status without trading.")
    runtime.add_argument("--deployment-id", default=None)
    subparsers.add_parser("support-bundle", help="Redacted bundle of the supported reports.")
    subparsers.add_parser("schema-check", help="Verify skill docs match SCHEMA_VERSION.")
    return parser


async def _dispatch(
    diagnostics: OperatorDiagnostics,
    arguments: argparse.Namespace,
) -> OperatorEnvelope:
    """Run one read-only report from local stores."""
    command = arguments.command
    if command == "market-data":
        return await diagnostics.market_data_report(arguments.product_id, arguments.timeframe)
    if command == "products":
        return await diagnostics.products()
    if command == "data-catalog":
        return await diagnostics.data_catalog()
    if command == "indicators":
        return await diagnostics.indicators()
    if command == "performance":
        return await diagnostics.performance(
            result_fingerprint=arguments.result_fingerprint,
            deployment_id=_uuid_or_none(arguments.deployment_id),
        )
    if command == "runtime":
        return await diagnostics.runtime_report(_uuid_or_none(arguments.deployment_id))
    factories = {
        "health": lambda: diagnostics.health(probe_api=True),
        "configuration": diagnostics.configuration,
        "exchange": diagnostics.exchange,
        "strategies": diagnostics.strategies,
        "risk": diagnostics.risk,
        "reconciliation": diagnostics.reconciliation,
        "support-bundle": diagnostics.support_bundle,
    }
    factory = factories.get(command)
    if factory is None:
        raise AssertionError(f"unsupported operator command: {command}")
    return await factory()


def _uuid_or_none(value: str | None) -> UUID | None:
    """Parse an optional UUID argument."""
    if value is None:
        return None
    return UUID(value)


def _query(arguments: argparse.Namespace) -> dict[str, str]:
    """Collect optional GET query parameters for HTTP mode."""
    query: dict[str, str] = {}
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
    settings = Settings()
    secrets = configured_secrets(settings)
    async with operator_diagnostics(settings) as diagnostics:
        report = await _dispatch(diagnostics, arguments)
    sys.stdout.write(f"{_render(report, fmt=arguments.format, secrets=secrets)}\n")
    return exit_code_for(report.overall_status)


def _run_http(arguments: argparse.Namespace) -> int:
    """Fetch one report from the loopback API without opening PostgreSQL."""
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    report = fetch_operator_report(
        base_url=base_url,
        command=arguments.command,
        query=_query(arguments),
    )
    sys.stdout.write(f"{_render(report, fmt=arguments.format, secrets=secrets)}\n")
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
        message = "Operator diagnostics failed safely; trading state was not changed."
        raise SystemExit(message) from error
    raise SystemExit(code)


if __name__ == "__main__":
    main()
