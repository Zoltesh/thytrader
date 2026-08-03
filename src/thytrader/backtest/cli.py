"""CLI for immutable backtest simulation, discovery, and inspection."""

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

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.backtest.models import BacktestResult

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class BacktestCliError(RuntimeError):
    """Report a safe operator-facing immutable-backtest command failure."""


def _fingerprint(value: str) -> str:
    """Require one lowercase canonical SHA-256 artifact fingerprint argument."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be sha256: followed by 64 lowercase hex characters")
    return value


def _parser() -> argparse.ArgumentParser:
    """Build read-only discovery and explicit simulation subcommands."""
    parser = argparse.ArgumentParser(
        prog="thytrader-backtest",
        description=(
            "Inspect immutable results or simulate one exact published research run by "
            "run_fingerprint. "
            "This command does not submit orders or grant trading authority."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    simulate = commands.add_parser("simulate", help="Simulate one exact published backtest run.")
    simulate.add_argument("run_fingerprint", type=_fingerprint)
    simulate.add_argument("--pretty", action="store_true")
    listing = commands.add_parser(
        "list", help="List immutable published backtest result fingerprints."
    )
    filters = listing.add_mutually_exclusive_group()
    filters.add_argument("--run-fingerprint", type=_fingerprint)
    filters.add_argument("--strategy-fingerprint", type=_fingerprint)
    listing.add_argument("--limit", type=int, default=50)
    shown = commands.add_parser("show", help="Load and integrity-check one immutable result.")
    shown.add_argument("result_fingerprint", type=_fingerprint)
    shown.add_argument("--pretty", action="store_true")
    return parser


async def _with_store() -> tuple[PostgresBacktestResultStore, AsyncEngine]:
    """Build the result store and return its engine for one short-lived command."""
    settings = Settings()
    if settings.database_url is None:
        raise BacktestCliError("THYTRADER_DATABASE_URL is required.")
    engine = create_engine(settings.database_url)
    return _result_store(settings, engine), engine


def _result_store(settings: Settings, engine: AsyncEngine) -> PostgresBacktestResultStore:
    """Build a result store with full source-artifact verification enabled."""
    return PostgresBacktestResultStore(
        engine,
        research_run_store=PostgresResearchRunStore(engine),
        dataset_store=DatasetStore(settings.market_data_dataset_root),
    )


async def _evaluate(run_fingerprint: str) -> BacktestResult:
    """Build authoritative stores, simulate one publication, and release resources."""
    settings = Settings()
    if settings.database_url is None:
        raise BacktestCliError("THYTRADER_DATABASE_URL is required.")
    engine = create_engine(settings.database_url)
    try:
        dataset_store = DatasetStore(settings.market_data_dataset_root)
        return await evaluate_and_publish_backtest(
            run_fingerprint,
            run_store=PostgresResearchRunStore(engine),
            strategy_store=PostgresStrategyPublicationStore(engine),
            dataset_store=dataset_store,
            result_store=PostgresBacktestResultStore(
                engine,
                research_run_store=PostgresResearchRunStore(engine),
                dataset_store=dataset_store,
            ),
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


async def _list_results(
    *, run_fingerprint: str | None, strategy_fingerprint: str | None, limit: int
) -> tuple[str, ...]:
    """Load deterministic result identities then always dispose the database engine."""
    if limit < 1 or limit > 100:
        raise BacktestCliError("limit must be between 1 and 100.")
    store, engine = await _with_store()
    try:
        return await store.list_fingerprints(
            run_fingerprint=run_fingerprint,
            strategy_fingerprint=strategy_fingerprint,
            limit=limit,
        )
    finally:
        await dispose(engine)


async def _show_result(result_fingerprint: str) -> BacktestResult:
    """Load a reverified immutable result then always dispose the database engine."""
    store, engine = await _with_store()
    try:
        return await store.load(result_fingerprint)
    finally:
        await dispose(engine)


def main(argv: Sequence[str] | None = None) -> None:
    """Run an explicit immutable backtest operation and print only verified output."""
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "simulate":
            result = asyncio.run(_evaluate(arguments.run_fingerprint))
            sys.stdout.write(f"{_render_result(result, pretty=arguments.pretty)}\n")
            sys.stderr.write(f"result_fingerprint={backtest_result_fingerprint(result)}\n")
        elif arguments.command == "list":
            results = asyncio.run(
                _list_results(
                    run_fingerprint=arguments.run_fingerprint,
                    strategy_fingerprint=arguments.strategy_fingerprint,
                    limit=arguments.limit,
                )
            )
            sys.stdout.write(json.dumps(results, separators=(",", ":")) + "\n")
        else:
            result = asyncio.run(_show_result(arguments.result_fingerprint))
            sys.stdout.write(f"{_render_result(result, pretty=arguments.pretty)}\n")
    except Exception as error:
        message = "Backtest command failed safely; immutable artifacts were not changed."
        raise SystemExit(message) from error


if __name__ == "__main__":
    main()
