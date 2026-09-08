"""Confirmation-gated CLI for strategy drafts, publication, and backtests."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.backtest.models import backtest_result_fingerprint
from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    PostgresBacktestSubmitter,
)
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetStore
from thytrader.operator.status import EXIT_HEALTHY, EXIT_USAGE
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_audit_events import PostgresAuditEventStore
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.research.mutation import ResearchMutationError, ResearchMutator
from thytrader.strategies.models import StrategyDefinition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

_CONFIRM_HELP = (
    "Required for mutations. This CLI cannot deploy, paper-trade, live-trade, or cancel orders."
)


class ResearchCliError(RuntimeError):
    """Report a safe operator-facing research command failure."""


def _parser() -> argparse.ArgumentParser:
    """Build the bounded research-mutation argument parser."""
    parser = argparse.ArgumentParser(
        prog="thytrader-research",
        description=(
            "Create drafts, publish immutable versions, and submit backtests. "
            "Mutations require --confirm. This command has no paper or live authority."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-draft", help="Create the conservative reference draft.")
    create.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    save = subparsers.add_parser("save-draft", help="Replace one draft from a JSON file.")
    save.add_argument("--file", required=True, help="Path to a StrategyDefinition JSON document.")
    save.add_argument("--revision", required=True, type=int, help="Expected durable revision.")
    save.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    publish = subparsers.add_parser("publish", help="Publish the matching durable draft.")
    publish.add_argument("--strategy-id", required=True, help="Server-owned strategy UUID.")
    publish.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    submit = subparsers.add_parser("submit-backtest", help="Submit one idempotent research run.")
    submit.add_argument(
        "--file",
        required=True,
        help="Path to a BacktestSubmissionRequest JSON document.",
    )
    submit.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    listing = subparsers.add_parser("list-results", help="List immutable backtest summaries.")
    listing.add_argument("--strategy-fingerprint", default=None)
    listing.add_argument("--limit", type=int, default=20)
    show = subparsers.add_parser("show-result", help="Show one immutable result summary.")
    show.add_argument("--result-fingerprint", required=True)
    return parser


def _require_confirm(confirm: bool) -> None:
    """Refuse mutations unless the operator passed an explicit confirmation flag."""
    if not confirm:
        raise ResearchCliError(
            "Pass --confirm to change research artifacts. "
            "This command cannot deploy, paper-trade, or live-trade."
        )


def _load_json(path_text: str) -> object:
    """Load one UTF-8 JSON document from disk."""
    path = Path(path_text)
    return json.loads(path.read_text(encoding="utf-8"))


async def _mutator(settings: Settings) -> tuple[ResearchMutator, AsyncEngine | None]:
    """Build the mutator from PostgreSQL when configured."""
    if settings.database_url is None:
        raise ResearchCliError("THYTRADER_DATABASE_URL is required for research commands.")
    engine = create_engine(settings.database_url)
    dataset_store = DatasetStore(settings.market_data_dataset_root)
    strategy_store = PostgresStrategyPublicationStore(engine)
    run_store = PostgresResearchRunStore(engine)
    result_store = PostgresBacktestResultStore(
        engine,
        research_run_store=run_store,
        dataset_store=dataset_store,
    )
    mutator = ResearchMutator(
        drafts=strategy_store,
        publications=strategy_store,
        submitter=PostgresBacktestSubmitter(engine, dataset_store),
        results=result_store,
        audit=PostgresAuditEventStore(engine),
    )
    return mutator, engine


async def _dispatch(arguments: argparse.Namespace) -> str:
    """Execute one research command and return stdout JSON."""
    settings = Settings()
    if arguments.command == "create-draft":
        _require_confirm(arguments.confirm)
        return await _with_mutator(settings, _create_draft)
    if arguments.command == "save-draft":
        _require_confirm(arguments.confirm)
        definition = StrategyDefinition.model_validate(_load_json(arguments.file))
        return await _with_mutator(
            settings,
            lambda mutator: _save_draft(mutator, definition, arguments.revision),
        )
    if arguments.command == "publish":
        _require_confirm(arguments.confirm)
        strategy_id = UUID(arguments.strategy_id)
        return await _with_mutator(settings, lambda mutator: _publish(mutator, strategy_id))
    if arguments.command == "submit-backtest":
        _require_confirm(arguments.confirm)
        request = BacktestSubmissionRequest.model_validate(_load_json(arguments.file))
        return await _with_mutator(settings, lambda mutator: _submit(mutator, request))
    if arguments.command == "list-results":
        return await _with_mutator(
            settings,
            lambda mutator: _list_results(
                mutator,
                arguments.strategy_fingerprint,
                arguments.limit,
            ),
        )
    if arguments.command == "show-result":
        return await _with_mutator(
            settings,
            lambda mutator: _show_result(mutator, arguments.result_fingerprint),
        )
    raise AssertionError(f"unsupported research command: {arguments.command}")


async def _with_mutator(
    settings: Settings,
    operation: Callable[[ResearchMutator], Awaitable[str]],
) -> str:
    """Run one async mutator operation and always dispose the engine."""
    mutator, engine = await _mutator(settings)
    try:
        return await operation(mutator)
    finally:
        if engine is not None:
            await dispose(engine)


async def _create_draft(mutator: ResearchMutator) -> str:
    """Create the reference draft and return identities."""
    draft = await mutator.create_reference_draft()
    return _encode(
        {
            "strategy_id": str(draft.definition.strategy_id),
            "revision": draft.revision,
            "version": draft.definition.version,
            "name": draft.definition.name,
        }
    )


async def _save_draft(
    mutator: ResearchMutator,
    definition: StrategyDefinition,
    revision: int,
) -> str:
    """Save one draft JSON document."""
    draft = await mutator.save_draft(definition, expected_revision=revision)
    return _encode(
        {
            "strategy_id": str(draft.definition.strategy_id),
            "revision": draft.revision,
            "version": draft.definition.version,
        }
    )


async def _publish(mutator: ResearchMutator, strategy_id: UUID) -> str:
    """Publish one draft identity."""
    published = await mutator.publish(strategy_id)
    return _encode(
        {
            "strategy_id": str(published.definition.strategy_id),
            "strategy_fingerprint": published.strategy_fingerprint,
            "version": published.definition.version,
        }
    )


async def _submit(mutator: ResearchMutator, request: BacktestSubmissionRequest) -> str:
    """Submit one backtest request document."""
    run_fingerprint, result_fingerprint = await mutator.submit_backtest(request)
    return _encode(
        {
            "run_fingerprint": run_fingerprint,
            "result_fingerprint": result_fingerprint,
        }
    )


async def _list_results(
    mutator: ResearchMutator,
    strategy_fingerprint: str | None,
    limit: int,
) -> str:
    """List bounded result summaries."""
    rows = await mutator.list_results(strategy_fingerprint=strategy_fingerprint, limit=limit)
    return _encode(
        {
            "results": [
                {
                    "result_fingerprint": row.result_fingerprint,
                    "run_fingerprint": row.run_fingerprint,
                    "strategy_fingerprint": row.strategy_fingerprint,
                    "dataset_fingerprint": row.dataset_fingerprint,
                    "engine_contract_version": row.engine_contract_version,
                    "published_at": row.published_at.isoformat(),
                    "trade_count": row.summary.trade_count,
                    "total_net_pnl": row.summary.total_net_pnl,
                }
                for row in rows
            ]
        }
    )


async def _show_result(mutator: ResearchMutator, result_fingerprint: str) -> str:
    """Load one result summary without dumping the full trade ledger."""
    result = await mutator.results.load(result_fingerprint)
    computed = backtest_result_fingerprint(result)
    return _encode(
        {
            "result_fingerprint": computed,
            "run_fingerprint": result.run_fingerprint,
            "strategy_fingerprint": result.strategy_fingerprint,
            "dataset_fingerprint": result.dataset_fingerprint,
            "engine_contract_version": result.engine_contract_version,
            "mode": "backtest",
            "timeframe": "1h",
            "currency": "USD",
            "summary": result.summary.model_dump(mode="json"),
        }
    )


def _encode(payload: object) -> str:
    """Render stable JSON for agent consumption."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> None:
    """Run one research command; mutations require --confirm."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else EXIT_USAGE) from error
    try:
        output = asyncio.run(_dispatch(arguments))
    except ResearchCliError as error:
        raise SystemExit(str(error)) from error
    except ResearchMutationError as error:
        raise SystemExit(str(error)) from error
    except BacktestSubmissionRejectedError as error:
        raise SystemExit(str(error)) from error
    except BacktestSubmissionError as error:
        raise SystemExit("Backtest submission is unavailable.") from error
    except Exception as error:
        message = "Research command failed safely; paper and live state were not changed."
        raise SystemExit(message) from error
    sys.stdout.write(f"{output}\n")
    raise SystemExit(EXIT_HEALTHY)


if __name__ == "__main__":
    main()
