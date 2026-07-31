"""Read-only CLI for deterministic evaluation of published research runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import TYPE_CHECKING

from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.research.signal_service import evaluate_published_signal_run
from thytrader.research.trace import (
    canonical_signal_trace_bytes,
    signal_trace_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.research.trace import SignalTrace

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ResearchCliError(RuntimeError):
    """Report a safe operator-facing research command failure."""


def _run_fingerprint(value: str) -> str:
    """Require a lowercase canonical SHA-256 run fingerprint argument."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be sha256: followed by 64 lowercase hex characters")
    return value


def _parser() -> argparse.ArgumentParser:
    """Build the read-only published signal-evaluation argument parser."""
    parser = argparse.ArgumentParser(
        prog="thytrader-research-evaluate",
        description=(
            "Evaluate one exact published research run into a deterministic signal trace. "
            "This command does not create runs, trades, fills, positions, or PnL."
        ),
    )
    parser.add_argument(
        "run_fingerprint",
        type=_run_fingerprint,
        help="Exact fingerprint of the published research run to evaluate.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print trace JSON for human inspection instead of canonical compact bytes.",
    )
    return parser


async def _evaluate(run_fingerprint: str) -> SignalTrace:
    """Build authoritative stores, evaluate one publication, and release database resources."""
    settings = Settings()
    if settings.database_url is None:
        raise ResearchCliError("THYTRADER_DATABASE_URL is required.")
    engine = create_engine(settings.database_url)
    try:
        return await evaluate_published_signal_run(
            run_fingerprint,
            run_store=PostgresResearchRunStore(engine),
            strategy_store=PostgresStrategyPublicationStore(engine),
            dataset_store=DatasetStore(settings.market_data_dataset_root),
        )
    finally:
        await dispose(engine)


def _render_trace(trace: SignalTrace, *, pretty: bool) -> str:
    """Render canonical or indented trace JSON without changing trace identity."""
    canonical = canonical_signal_trace_bytes(trace)
    if not pretty:
        return canonical.decode("utf-8")
    document = json.loads(canonical)
    return json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)


def main(argv: Sequence[str] | None = None) -> None:
    """Evaluate one published run and print its deterministic trace and identity."""
    arguments = _parser().parse_args(argv)
    try:
        trace = asyncio.run(_evaluate(arguments.run_fingerprint))
    except Exception as error:
        message = "Signal evaluation failed safely; published artifacts were not changed."
        raise SystemExit(message) from error
    sys.stdout.write(f"{_render_trace(trace, pretty=arguments.pretty)}\n")
    sys.stderr.write(f"trace_fingerprint={signal_trace_fingerprint(trace)}\n")


if __name__ == "__main__":
    main()
