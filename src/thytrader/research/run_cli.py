"""CLI for publishing immutable executable bar-backtest research runs."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import re
import secrets
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.research.models import (
    BarExecutionAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


def _fingerprint(value: str) -> str:
    """Require canonical immutable artifact identities before publication."""
    if _FINGERPRINT.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be sha256: followed by 64 lowercase hex characters")
    return value


def _timestamp(value: str) -> datetime:
    """Parse one explicit UTC evaluation boundary from operator input."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise argparse.ArgumentTypeError("must be a UTC ISO-8601 timestamp")
    return parsed


def _parser() -> argparse.ArgumentParser:
    """Build the explicit backtest-run publication command parser."""
    parser = argparse.ArgumentParser(prog="thytrader-research-run")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish-backtest", help="Publish a verified bar-backtest run.")
    publish.add_argument("--strategy-fingerprint", required=True, type=_fingerprint)
    publish.add_argument("--dataset-fingerprint", required=True, type=_fingerprint)
    publish.add_argument("--evaluation-start", required=True, type=_timestamp)
    publish.add_argument("--evaluation-end", required=True, type=_timestamp)
    publish.add_argument("--initial-quote-balance", required=True)
    publish.add_argument("--maker-fee-rate", required=True)
    publish.add_argument("--taker-fee-rate", required=True)
    publish.add_argument("--fixed-slippage-bps", required=True)
    publish.add_argument("--random-seed", type=int, default=0)
    return parser


def _uuid7(created_at: datetime) -> UUID:
    """Create one UUIDv7 whose encoded timestamp exactly matches the run creation millisecond."""
    milliseconds = int(created_at.timestamp() * 1000)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)


def backtest_execution_fingerprint(arguments: argparse.Namespace) -> str:
    """Hash the execution semantics that make repeated CLI publication idempotent."""
    payload = {
        "bar_execution": {
            "fill_timing": "next_candle_open",
            "signal_timing": "completed_candle_close",
        },
        "capital": {
            "initial_quote_balance": arguments.initial_quote_balance,
            "quote_currency": "USD",
        },
        "costs": {
            "fixed_slippage_bps": arguments.fixed_slippage_bps,
            "maker_fee_rate": arguments.maker_fee_rate,
            "taker_fee_rate": arguments.taker_fee_rate,
        },
        "dataset_fingerprint": arguments.dataset_fingerprint,
        "engine_contract_version": "thytrader-bar-backtest-v1",
        "evaluation_end": arguments.evaluation_end.isoformat(),
        "evaluation_start": arguments.evaluation_start.isoformat(),
        "random_seed": arguments.random_seed,
        "strategy_fingerprint": arguments.strategy_fingerprint,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


async def _publish(arguments: argparse.Namespace) -> str:
    """Load strategy requirements, derive warmup, and idempotently publish one backtest run."""
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("THYTRADER_DATABASE_URL is required.")
    engine = create_engine(settings.database_url)
    try:
        strategy_store = PostgresStrategyPublicationStore(engine)
        strategy = await strategy_store.load(arguments.strategy_fingerprint)
        dataset_store = DatasetStore(settings.market_data_dataset_root)
        run_store = PostgresResearchRunStore(engine)
        execution_fingerprint = backtest_execution_fingerprint(arguments)
        existing = await run_store.load_by_execution_fingerprint(
            execution_fingerprint, dataset_store=dataset_store
        )
        if existing is not None:
            return existing.run_fingerprint
        now = datetime.now(UTC)
        created_at = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        specification = ResearchRunSpecification(
            schema_version="1.0",
            run_id=_uuid7(created_at),
            created_at=created_at,
            strategy_fingerprint=arguments.strategy_fingerprint,
            dataset_fingerprint=arguments.dataset_fingerprint,
            evaluation=EvaluationWindow(
                starts_at=arguments.evaluation_start, ends_at=arguments.evaluation_end
            ),
            warmup=WarmupWindow(
                bars=strategy.definition.data_requirements.warmup_bars,
                starts_at=arguments.evaluation_start
                - timedelta(hours=strategy.definition.data_requirements.warmup_bars),
            ),
            capital=CapitalAssumptions(
                quote_currency="USD", initial_quote_balance=arguments.initial_quote_balance
            ),
            costs=CostAssumptions(
                maker_fee_rate=arguments.maker_fee_rate,
                taker_fee_rate=arguments.taker_fee_rate,
                fixed_slippage_bps=arguments.fixed_slippage_bps,
            ),
            bar_execution=BarExecutionAssumptions(
                signal_timing="completed_candle_close", fill_timing="next_candle_open"
            ),
            engine_contract_version="thytrader-bar-backtest-v1",
            random_seed=arguments.random_seed,
        )
        published = await run_store.publish(
            specification,
            dataset_store=dataset_store,
            execution_fingerprint=execution_fingerprint,
        )
        return published.run_fingerprint
    finally:
        await dispose(engine)


def main(argv: Sequence[str] | None = None) -> None:
    """Publish a verified executable run and print only its immutable identity."""
    arguments = _parser().parse_args(argv)
    try:
        fingerprint = asyncio.run(_publish(arguments))
    except Exception as error:
        raise SystemExit("Backtest run publication failed safely; no run was published.") from error
    sys.stdout.write(f"{fingerprint}\n")


if __name__ == "__main__":
    main()
