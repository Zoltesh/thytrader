"""Parsers for ``thytrader-runtime`` commands that act on one deployment.

Inventory reads (list, show), decisions, twins, start, place-order, pause/resume/stop,
and reset-breaker-latches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.decisions import DECISION_PAGE_MAX_LIMIT, DecisionOutcome
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.runtime_control.inventory_commands import (
    add_inventory_arguments,
    add_show_arguments,
)
from thytrader.runtime_control.parsers.common import _CONFIRM_HELP, _LIVE_HELP

if TYPE_CHECKING:
    import argparse


def add_inventory_read_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register ``list`` and ``show`` (read-only deployment inventory)."""
    listing = subparsers.add_parser(
        "list",
        parents=[trailing],
        help=(
            "List deployments. Default is a complete stable snapshot, not a silent 50-row page. "
            "--limit/--offset return one page with has_more."
        ),
    )
    add_inventory_arguments(listing)
    show = subparsers.add_parser(
        "show",
        parents=[trailing],
        help="Show one deployment. Default summary labels omitted historical orders and fills.",
    )
    show.add_argument("deployment_id", help="Deployment UUID.")
    add_show_arguments(show)


def _add_decisions_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register the read-only per-bar decision timeline command (never mutates)."""
    decisions = subparsers.add_parser(
        "decisions",
        parents=[trailing],
        help=(
            "Read-only per-bar decision timeline, newest first: what a paper/live bot "
            "decided on every completed bar and why (rule values, risk verdict, orders). "
            "Pass a deployment id, or --strategy-id for all of a strategy's bots."
        ),
    )
    decisions.add_argument(
        "deployment_id",
        nargs="?",
        default=None,
        help="Deployment UUID (optional with --strategy-id, where it narrows to one bot).",
    )
    decisions.add_argument(
        "--strategy-id", default=None, help="Strategy UUID: decisions across its bots."
    )
    decisions.add_argument(
        "--outcome",
        action="append",
        choices=tuple(item.value for item in DecisionOutcome),
        default=None,
        help="Repeatable filter, e.g. --outcome entry_signal --outcome exit (trades).",
    )
    decisions.add_argument(
        "--limit",
        type=int,
        default=50,
        help=f"Page size 1..{DECISION_PAGE_MAX_LIMIT} (default 50).",
    )
    decisions.add_argument(
        "--cursor", default=None, help="next_cursor from the previous page (older rows)."
    )


def _add_twin_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Expose metadata-only twin controls with an unconditional confirmation gate."""
    for command in ("show-twin", "link-twin", "unlink-twin"):
        parser = subparsers.add_parser(
            command,
            parents=[trailing],
            help="Read or edit the explicit paper/live comparison pair; no trading action.",
        )
        parser.add_argument("deployment_id", help="Paper or live strategy bot UUID.")
        if command != "show-twin":
            parser.add_argument(
                "--counterpart-deployment-id",
                required=True,
                help="Expected opposite-mode bot UUID.",
            )
            parser.add_argument(
                "--confirm",
                action="store_true",
                help="Required even with YOLO; changes comparison metadata only.",
            )


def add_start_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register ``start`` (paper or live; live also needs ``--i-understand-live``)."""
    start = subparsers.add_parser(
        "start",
        parents=[trailing],
        help=(
            "Start one paper or live deployment from a strategy's current (valid) rules. "
            "The response's strategy_fingerprint names the snapshot the bot runs. A strategy "
            "with reference instruments starts only when each reference series is on the "
            "enabled market-data watchlist (409 names the thytrader-data watch-add command)."
        ),
    )
    start.add_argument(
        "--strategy-id",
        required=True,
        help="Strategy UUID (thytrader-research list-strategies). The server snapshots it.",
    )
    start.add_argument("--mode", required=True, choices=("paper", "live"))
    start.add_argument("--cash", default=None, help="Paper starting cash decimal string.")
    start.add_argument(
        "--maker-fee-rate",
        default=None,
        help=(
            "Paper maker fee rate as a decimal string, with --taker-fee-rate. "
            "Omitted, paper uses the Coinbase account's own rates and is refused "
            "when they cannot be read. Live rejects these flags."
        ),
    )
    start.add_argument(
        "--taker-fee-rate",
        default=None,
        help="Paper taker fee assumption as a decimal string. See --maker-fee-rate.",
    )
    start.add_argument(
        "--fee-per-contract",
        default=None,
        help=(
            "Paper futures only (ADR 0129): USD fee per contract on every fill. Required, "
            "with explicit --maker-fee-rate and --taker-fee-rate (the futures tier from "
            "`thytrader-operator fees`), to start a futures strategy; --cash is USD from "
            "the policy's futures.paper_capital_usd envelope."
        ),
    )
    start.add_argument(
        "--adopt-holdings",
        default=None,
        metavar="N|all",
        help=(
            "Live only: start the bot already holding this base quantity, or 'all', of the "
            "coins the Coinbase account holds unmanaged (see adoption-preview). Nothing is "
            "bought; the stop and target come from the strategy's exits at the mark and the "
            "worker places them on its next cycle. Single-instrument long strategies only. "
            "Always needs --confirm (YOLO never skips it)."
        ),
    )
    start.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    start.add_argument("--i-understand-live", action="store_true", help=_LIVE_HELP)


def add_place_order_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register ``place-order`` (one discretionary order with required SL/TP)."""
    place = subparsers.add_parser(
        "place-order",
        parents=[trailing],
        help=(
            "Place one long or short discretionary order with required SL/TP. "
            "--entry-kind adopt (live only) buys nothing: it adopts coins already held at "
            "Coinbase (--quantity N or all, see adoption-preview) into a discretionary "
            "long book and rests the given stop and take-profit."
        ),
    )
    place.add_argument("--mode", required=True, choices=("paper", "live"))
    place.add_argument("--product-id", required=True)
    place.add_argument("--side", default="long", choices=("long", "short"))
    place.add_argument("--stop-price", required=True)
    place.add_argument("--take-profit-price", required=True)
    place.add_argument("--idempotency-key", required=True)
    place.add_argument("--origin", default="agent", choices=("human", "agent"))
    place.add_argument(
        "--entry-kind",
        default="post_only_limit",
        choices=("post_only_limit", "marketable", "adopt"),
        help=(
            "post_only_limit (default) or marketable buy/sell at the venue; adopt (live "
            "only) takes over coins already held instead of buying (ADR 0124)."
        ),
    )
    place.add_argument(
        "--timeframe",
        default="5m",
        choices=EXECUTION_TIMEFRAMES,
        help=(
            "Discretionary book clock. Default 5m. Any ingested venue clock "
            "(1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d)."
        ),
    )
    place.add_argument(
        "--quantity",
        default=None,
        help="Base quantity. With --entry-kind adopt: a quantity or 'all' unmanaged held coins.",
    )
    place.add_argument("--quote-notional", default=None)
    place.add_argument("--limit-price", default=None)
    place.add_argument("--cash", default=None, help="Paper starting cash decimal string.")
    place.add_argument(
        "--maker-fee-rate",
        default=None,
        help=(
            "Paper maker fee assumption as a decimal string. Optional with "
            "--taker-fee-rate on a new paper book. Live rejects these flags."
        ),
    )
    place.add_argument(
        "--taker-fee-rate",
        default=None,
        help="Paper taker fee assumption as a decimal string. See --maker-fee-rate.",
    )
    place.add_argument(
        "--note",
        default=None,
        help="Optional attributed why-note frozen onto the trade-reason record.",
    )
    place.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    place.add_argument("--i-understand-live", action="store_true", help=_LIVE_HELP)


def add_deployment_action_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register ``pause``/``resume``/``stop`` and ``reset-breaker-latches``."""
    for action in ("pause", "resume", "stop"):
        command = subparsers.add_parser(
            action,
            parents=[trailing],
            help=f"{action.title()} one deployment.",
        )
        command.add_argument("deployment_id", help="Deployment UUID.")
        command.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
        if action == "resume":
            command.add_argument("--i-understand-live", action="store_true", help=_LIVE_HELP)
        if action == "stop":
            command.add_argument(
                "--flatten",
                action="store_true",
                help=(
                    "Marketably exit inventory then cancel remainders. "
                    "Default stop is managed shutdown that keeps protective brackets."
                ),
            )
    reset_latches = subparsers.add_parser(
        "reset-breaker-latches",
        parents=[trailing],
        help="Clear latched daily-loss and drawdown breakers after explicit operator reset.",
    )
    reset_latches.add_argument("deployment_id", help="Deployment UUID.")
    reset_latches.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
