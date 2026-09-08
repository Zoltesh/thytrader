"""Read-only operator CLI backed by the same diagnostics as the HTTP API."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.config import Settings
from thytrader.operator.redaction import configured_secrets, dumps_redacted, redact_text
from thytrader.operator.session import operator_diagnostics
from thytrader.operator.status import EXIT_USAGE, exit_code_for

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
            "This command cannot place, edit, or cancel orders, or arm live trading."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json is the agent contract; text is a short human summary.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="API, workers, database, and exchange health.")
    subparsers.add_parser("configuration", help="Redacted configuration validity.")
    subparsers.add_parser("exchange", help="Coinbase connectivity and permissions.")
    market = subparsers.add_parser("market-data", help="1h freshness and gap report.")
    market.add_argument(
        "--product-id",
        default=None,
        help="USD spot product, default from settings.",
    )
    subparsers.add_parser("strategies", help="Draft, publication, and runtime status.")
    performance = subparsers.add_parser(
        "performance",
        help="Backtest or runtime performance slice.",
    )
    performance.add_argument("--result-fingerprint", default=None)
    performance.add_argument("--deployment-id", default=None)
    subparsers.add_parser("risk", help="Pause and mismatch findings.")
    subparsers.add_parser("reconciliation", help="Unknown orders and mismatch findings.")
    subparsers.add_parser("support-bundle", help="Redacted bundle of the supported reports.")
    return parser


async def _dispatch(
    diagnostics: OperatorDiagnostics,
    arguments: argparse.Namespace,
) -> OperatorEnvelope:
    """Run one read-only report."""
    command = arguments.command
    if command == "health":
        return await diagnostics.health(probe_api=True)
    if command == "configuration":
        return await diagnostics.configuration()
    if command == "exchange":
        return await diagnostics.exchange()
    if command == "market-data":
        return await diagnostics.market_data(arguments.product_id)
    if command == "strategies":
        return await diagnostics.strategies()
    if command == "performance":
        deployment_id = UUID(arguments.deployment_id) if arguments.deployment_id else None
        return await diagnostics.performance(
            result_fingerprint=arguments.result_fingerprint,
            deployment_id=deployment_id,
        )
    if command == "risk":
        return await diagnostics.risk()
    if command == "reconciliation":
        return await diagnostics.reconciliation()
    if command == "support-bundle":
        return await diagnostics.support_bundle()
    raise AssertionError(f"unsupported operator command: {command}")


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


async def _run(arguments: argparse.Namespace) -> int:
    """Load diagnostics, emit one report, and map status to an exit code."""
    settings = Settings()
    secrets = configured_secrets(settings)
    async with operator_diagnostics(settings) as diagnostics:
        report = await _dispatch(diagnostics, arguments)
    sys.stdout.write(f"{_render(report, fmt=arguments.format, secrets=secrets)}\n")
    return exit_code_for(report.overall_status)


def main(argv: Sequence[str] | None = None) -> None:
    """Print one operator report and exit with 0/1/2 for healthy/degraded/failed."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else EXIT_USAGE) from error
    try:
        code = asyncio.run(_run(arguments))
    except Exception as error:
        message = "Operator diagnostics failed safely; trading state was not changed."
        raise SystemExit(message) from error
    raise SystemExit(code)


if __name__ == "__main__":
    main()
