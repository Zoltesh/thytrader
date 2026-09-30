"""CLI for publishing immutable unified-model backtest research runs (ADR 0083)."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import re
import secrets
import sys
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import parse_candle_interval
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.research.models import (
    BACKTEST_ENGINE,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    IndicatorTimeframeDataset,
    ResearchRunSpecification,
    WarmupWindow,
    removed_engine_selection_message,
    warmup_starts_at,
)
from thytrader.strategies.models import unbound_indicator_timeframes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.strategies.models import StrategyDefinition

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
    parser = argparse.ArgumentParser(
        prog="thytrader-research-run",
        description=(
            "Publish one immutable run for the single unified backtest model: resting "
            "post-only maker entries, bar-extreme stops, taker exits with fixed slippage, and "
            "optional constant spread stress. There is no engine selector."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish-backtest", help="Publish a verified bar-backtest run.")
    publish.add_argument("--strategy-fingerprint", required=True, type=_fingerprint)
    publish.add_argument("--dataset-fingerprint", required=True, type=_fingerprint)
    publish.add_argument("--htf-dataset-fingerprint", type=_fingerprint)
    publish.add_argument(
        "--indicator-dataset-fingerprint",
        action="append",
        default=[],
        metavar="TIMEFRAME=FINGERPRINT",
        help=(
            "Extra indicator-timeframe dataset as TIMEFRAME=sha256:…. Repeatable. "
            "Required for unbound extra indicator clocks; omit when the extra TF equals "
            "htf_filter.timeframe."
        ),
    )
    publish.add_argument("--evaluation-start", required=True, type=_timestamp)
    publish.add_argument("--evaluation-end", required=True, type=_timestamp)
    publish.add_argument("--initial-quote-balance", required=True)
    publish.add_argument("--maker-fee-rate", required=True)
    publish.add_argument("--taker-fee-rate", required=True)
    publish.add_argument("--fixed-slippage-bps", required=True)
    publish.add_argument(
        "--spread-bps",
        default="0",
        help=(
            "Optional constant total bid-ask spread stress in basis points (default 0). "
            "Applied to taker exits, stop triggers, and marks; maker fills stay at the limit."
        ),
    )
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


def backtest_execution_fingerprint(
    arguments: argparse.Namespace,
    *,
    quote_currency: Literal["USD", "USDC", "USDT"],
) -> str:
    """Hash the execution semantics that make repeated CLI publication idempotent."""
    capital = CapitalAssumptions(
        quote_currency=quote_currency, initial_quote_balance=arguments.initial_quote_balance
    )
    costs = _cost_assumptions(arguments)
    payload = {
        "capital": capital.model_dump(mode="json"),
        "costs": costs.model_dump(mode="json"),
        "dataset_fingerprint": arguments.dataset_fingerprint,
        "engine": BACKTEST_ENGINE,
        "evaluation_end": arguments.evaluation_end.isoformat(),
        "evaluation_start": arguments.evaluation_start.isoformat(),
        "random_seed": arguments.random_seed,
        "strategy_fingerprint": arguments.strategy_fingerprint,
    }
    if arguments.htf_dataset_fingerprint is not None:
        payload["htf_dataset_fingerprint"] = arguments.htf_dataset_fingerprint
    if arguments.indicator_dataset_fingerprint:
        payload["indicator_dataset_fingerprints"] = [
            {"timeframe": item.timeframe, "dataset_fingerprint": item.dataset_fingerprint}
            for item in _parsed_indicator_bindings(arguments.indicator_dataset_fingerprint)
        ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


def _cost_assumptions(arguments: argparse.Namespace) -> CostAssumptions:
    """Build the published fee, slippage, and optional spread-stress assumptions."""
    return CostAssumptions(
        maker_fee_rate=arguments.maker_fee_rate,
        taker_fee_rate=arguments.taker_fee_rate,
        fixed_slippage_bps=arguments.fixed_slippage_bps,
        spread_bps=arguments.spread_bps,
    )


def _htf_dataset_fingerprint(
    arguments: argparse.Namespace, definition: StrategyDefinition
) -> str | None:
    """Require an HTF dataset fingerprint iff the published strategy declares a filter."""
    fingerprint = arguments.htf_dataset_fingerprint
    if definition.htf_filter is not None and fingerprint is None:
        raise ValueError("HTF-filter strategies require --htf-dataset-fingerprint")
    if definition.htf_filter is None and fingerprint is not None:
        raise ValueError("--htf-dataset-fingerprint requires a strategy with htf_filter")
    if fingerprint is not None and fingerprint == arguments.dataset_fingerprint:
        raise ValueError("htf dataset fingerprint must differ from the decision dataset")
    return fingerprint


def _parsed_indicator_bindings(
    values: list[str],
) -> tuple[IndicatorTimeframeDataset, ...]:
    """Parse TIMEFRAME=sha256:… bindings and order them by increasing duration."""
    parsed: list[IndicatorTimeframeDataset] = []
    for value in values:
        timeframe, separator, fingerprint = value.partition("=")
        if separator != "=" or not timeframe or not fingerprint:
            raise ValueError(
                "indicator dataset fingerprint must be TIMEFRAME=sha256: followed by 64 hex"
            )
        _fingerprint(fingerprint)
        parsed.append(
            IndicatorTimeframeDataset.model_validate(
                {"timeframe": timeframe, "dataset_fingerprint": fingerprint}
            )
        )
    return tuple(
        sorted(
            parsed,
            key=lambda item: int(parse_candle_interval(item.timeframe).duration.total_seconds()),
        )
    )


def _indicator_dataset_fingerprints(
    arguments: argparse.Namespace, definition: StrategyDefinition
) -> tuple[IndicatorTimeframeDataset, ...]:
    """Require extra-TF dataset fingerprints iff the strategy declares unbound clocks."""
    required = unbound_indicator_timeframes(definition)
    bindings = _parsed_indicator_bindings(list(arguments.indicator_dataset_fingerprint or []))
    declared = tuple(item.timeframe for item in bindings)
    if declared != required:
        raise ValueError(
            "indicator dataset fingerprints must match the strategy extra indicator timeframes"
        )
    reserved = {arguments.dataset_fingerprint}
    if arguments.htf_dataset_fingerprint is not None:
        reserved.add(arguments.htf_dataset_fingerprint)
    if any(item.dataset_fingerprint in reserved for item in bindings):
        raise ValueError(
            "indicator dataset fingerprints must differ from the decision and HTF datasets"
        )
    return bindings


async def _publish(arguments: argparse.Namespace) -> str:
    """Load strategy requirements, derive warmup, and idempotently publish one backtest run."""
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("THYTRADER_DATABASE_URL is required.")
    engine = create_engine(settings.database_url)
    try:
        strategy_store = PostgresStrategyStore(engine)
        strategy = await strategy_store.load(arguments.strategy_fingerprint)
        htf_dataset_fingerprint = _htf_dataset_fingerprint(arguments, strategy.definition)
        indicator_dataset_fingerprints = _indicator_dataset_fingerprints(
            arguments, strategy.definition
        )
        dataset_store = DatasetStore(settings.market_data_dataset_root)
        run_store = PostgresResearchRunStore(engine)
        execution_fingerprint = backtest_execution_fingerprint(
            arguments, quote_currency=strategy.definition.instrument.quote_currency
        )
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
            htf_dataset_fingerprint=htf_dataset_fingerprint,
            indicator_dataset_fingerprints=indicator_dataset_fingerprints,
            evaluation=EvaluationWindow(
                starts_at=arguments.evaluation_start, ends_at=arguments.evaluation_end
            ),
            warmup=WarmupWindow(
                bars=strategy.definition.data_requirements.warmup_bars,
                starts_at=warmup_starts_at(
                    arguments.evaluation_start,
                    strategy.definition.data_requirements.warmup_bars,
                    strategy.definition.timeframe,
                ),
            ),
            capital=CapitalAssumptions(
                quote_currency=strategy.definition.instrument.quote_currency,
                initial_quote_balance=arguments.initial_quote_balance,
            ),
            costs=_cost_assumptions(arguments),
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
    raw = list(sys.argv[1:] if argv is None else argv)
    if any(item.split("=", 1)[0] == "--engine-contract-version" for item in raw):
        raise SystemExit(removed_engine_selection_message("--engine-contract-version"))
    arguments = _parser().parse_args(raw)
    try:
        fingerprint = asyncio.run(_publish(arguments))
    except Exception as error:
        raise SystemExit("Backtest run publication failed safely; no run was published.") from error
    sys.stdout.write(f"{fingerprint}\n")


if __name__ == "__main__":
    main()
