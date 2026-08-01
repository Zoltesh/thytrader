"""CLI for deterministic simulation and immutable publication of a published research run."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import TYPE_CHECKING

from thytrader.backtest.models import backtest_result_fingerprint, canonical_backtest_result_bytes
from thytrader.backtest.service import evaluate_and_publish_backtest
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.backtest.models import BacktestResult

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class BacktestCliError(RuntimeError):
    """Report a safe operator-facing published-backtest command failure."""


def _run_fingerprint(value: str) -> str:
    """Require a lowercase canonical SHA-256 run fingerprint argument."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be sha256: followed by 64 lowercase hex characters")
    return value


def _parser() -> argparse.ArgumentParser:
    """Build the immutable published-run backtest command parser."""
    parser = argparse.ArgumentParser(
        prog="thytrader-backtest",
        description=(
            "Simulate one exact published research run and append its immutable "
            "deterministic result. "
            "This command does not submit orders or grant trading authority."
        ),
    )
    parser.add_argument(
        "run_fingerprint",
        type=_run_fingerprint,
        help="Exact fingerprint of the published research run to simulate.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print result JSON for human inspection instead of canonical compact bytes.",
    )
    return parser


async def _evaluate(run_fingerprint: str) -> BacktestResult:
    """Build authoritative stores, simulate one publication, and release database resources."""
    settings = Settings()
    if settings.database_url is None:
        raise BacktestCliError("THYTRADER_DATABASE_URL is required.")
    engine = create_engine(settings.database_url)
    try:
        return await evaluate_and_publish_backtest(
            run_fingerprint,
            run_store=PostgresResearchRunStore(engine),
            strategy_store=PostgresStrategyPublicationStore(engine),
            dataset_store=DatasetStore(settings.market_data_dataset_root),
            result_store=PostgresBacktestResultStore(engine),
        )
    finally:
        await dispose(engine)


def _render_result(result: BacktestResult, *, pretty: bool) -> str:
    """Render canonical or indented result JSON without changing result identity."""
    canonical = canonical_backtest_result_bytes(result)
    if not pretty:
        return canonical.decode("utf-8")
    document = json.loads(canonical)
    return json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)


def main(argv: Sequence[str] | None = None) -> None:
    """Simulate one publication, print its result, and report its fingerprint to standard error."""
    arguments = _parser().parse_args(argv)
    try:
        result = asyncio.run(_evaluate(arguments.run_fingerprint))
    except Exception as error:
        message = "Backtest failed safely; source artifacts and results were not changed."
        raise SystemExit(message) from error
    sys.stdout.write(f"{_render_result(result, pretty=arguments.pretty)}\n")
    sys.stderr.write(f"result_fingerprint={backtest_result_fingerprint(result)}\n")


if __name__ == "__main__":
    main()
