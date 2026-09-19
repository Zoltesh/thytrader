"""Confirmation-gated CLI for market-data watchlist, ingest, and gap fill."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from thytrader.agent_http import AgentHttpError, require_matching_ops_contract, resolve_api_base_url
from thytrader.agent_orchestration.confirmation import require_mutation_confirmation
from thytrader.agent_orchestration.models import YoloTier
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.data_control.client import (
    add_watch,
    fill_gaps,
    ingest,
    inspect_gaps,
    list_watchlist,
)
from thytrader.data_control.models import DataControlError
from thytrader.market_data.models import DATASET_TIMEFRAMES
from thytrader.operator.redaction import configured_secrets, dumps_redacted

if TYPE_CHECKING:
    from collections.abc import Sequence

_CONFIRM_HELP = "Required for watchlist and ingest mutations."


def _shared_options() -> argparse.ArgumentParser:
    """Global flags that may appear before or after the subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--base-url",
        default=None,
        help="Loopback API origin. Defaults to THYTRADER_API_BASE_URL or settings.",
    )
    return shared


def _parser() -> argparse.ArgumentParser:
    """Build the confirmation-gated data-control argument parser."""
    shared = _shared_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-data",
        description=(
            "Manage the market-data watchlist and run complete-only ingest through "
            "the loopback HTTP API. Mutations require --confirm. This is not the "
            "operator, research, or runtime CLI. It does not place orders."
        ),
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "watchlist-list",
        parents=[trailing],
        help="List watched products and timeframes.",
    )
    watch_add = subparsers.add_parser(
        "watch-add",
        parents=[trailing],
        help="Watch one USD spot product and timeframe.",
    )
    _target_args(watch_add)
    watch_add.add_argument("--lookback-hours", type=int, default=168)
    watch_add.add_argument("--disabled", action="store_true", help="Store the target as disabled.")
    watch_add.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    ingest_cmd = subparsers.add_parser(
        "ingest",
        parents=[trailing],
        help="Queue complete-only ingest for the market-data worker.",
    )
    _target_args(ingest_cmd)
    ingest_cmd.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    ingest_cmd.add_argument(
        "--no-wait",
        action="store_true",
        help=(
            "Return immediately after the 202 with the ingest state instead of "
            "polling until the worker finishes (long backfills can take minutes)."
        ),
    )
    gaps = subparsers.add_parser(
        "inspect-gaps",
        parents=[trailing],
        help=(
            "Classify missing bars without writing. "
            "May return truncated with a partial gap_summary."
        ),
    )
    _target_args(gaps)
    fill = subparsers.add_parser(
        "fill-gaps",
        parents=[trailing],
        help="Re-run complete-only ingest. Does not interpolate missing bars.",
    )
    _target_args(fill)
    fill.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    return parser


def _target_args(parser: argparse.ArgumentParser) -> None:
    """Require a spot product and a complete-only dataset timeframe."""
    parser.add_argument("--product-id", required=True, help="Spot product such as ETH-USDC.")
    parser.add_argument(
        "--timeframe",
        required=True,
        choices=DATASET_TIMEFRAMES,
        help=(
            "Complete-only dataset clock. Any ingested venue TF "
            "(1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d), including HTF and "
            "per-indicator extra clocks."
        ),
    )


_DATA_CONFIRM_MESSAGE = "Pass --confirm to change the watchlist or ingest market data."


def _require_confirm(confirm: bool, *, base_url: str, command: str) -> None:
    """Refuse mutations unless `--confirm` is present or YOLO covers data."""
    require_mutation_confirmation(
        confirmed=confirm,
        missing_message=_DATA_CONFIRM_MESSAGE,
        error_type=DataControlError,
        base_url=base_url,
        tier=YoloTier.DATA,
        command=command,
    )


def _run(arguments: argparse.Namespace) -> str:
    """Execute one data command against the loopback API."""
    settings = Settings()
    secrets = configured_secrets(settings)
    base_url = resolve_api_base_url(explicit=arguments.base_url, settings=settings)
    payload = _dispatch(arguments, base_url)
    return dumps_redacted(payload, secrets)


def _dispatch(arguments: argparse.Namespace, base_url: str) -> object:
    """Route one parsed command to the HTTP helper."""
    command = arguments.command
    if command == "watchlist-list":
        require_matching_ops_contract(base_url)
        return list_watchlist(base_url)
    if command == "watch-add":
        _require_confirm(arguments.confirm, base_url=base_url, command="watch-add")
        require_matching_ops_contract(base_url)
        return add_watch(
            base_url,
            product_id=arguments.product_id,
            timeframe=arguments.timeframe,
            lookback_hours=arguments.lookback_hours,
            enabled=not arguments.disabled,
        )
    if command == "ingest":
        _require_confirm(arguments.confirm, base_url=base_url, command="ingest")
        require_matching_ops_contract(base_url)
        return ingest(
            base_url,
            product_id=arguments.product_id,
            timeframe=arguments.timeframe,
            wait=not arguments.no_wait,
        )
    if command == "inspect-gaps":
        require_matching_ops_contract(base_url)
        return inspect_gaps(
            base_url,
            product_id=arguments.product_id,
            timeframe=arguments.timeframe,
        )
    if command == "fill-gaps":
        _require_confirm(arguments.confirm, base_url=base_url, command="fill-gaps")
        require_matching_ops_contract(base_url)
        return fill_gaps(
            base_url,
            product_id=arguments.product_id,
            timeframe=arguments.timeframe,
        )
    raise AssertionError(f"unsupported data command: {command}")


def main(argv: Sequence[str] | None = None) -> None:
    """Print JSON and exit non-zero on usage or API errors."""
    parser = _parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else 2) from error
    try:
        sys.stdout.write(f"{_run(arguments)}\n")
    except DataControlError as error:
        raise SystemExit(str(error)) from error
    except AgentHttpError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
