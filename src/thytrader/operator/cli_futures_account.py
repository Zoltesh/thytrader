"""``thytrader-operator futures-account`` flags: the latest snapshot or ``--history``.

``--history --since ISO [--until ISO]`` lists the CFM mirror snapshots in a window
(``GET /api/v1/operator/futures-account/history``); without ``--history`` the command
reports the newest snapshot as before. Instants must carry a UTC offset.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from thytrader.operator.futures_account_history_report import FUTURES_HISTORY_MAX_ROWS

HISTORY_ROUTE = "futures-account/history"


def add_futures_account_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register ``futures-account`` with its history flags."""
    parser = subparsers.add_parser(
        "futures-account",
        parents=[trailing],
        help=(
            "Latest read-only Coinbase futures (CFM) account mirror: enablement, USD balance "
            "summary, positions in contracts, margin window, spot USDC/USD, failed reads. "
            "--history --since ISO [--until ISO] lists every snapshot in a window."
        ),
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help=(
            "List the mirror snapshots in [--since, --until), oldest first "
            f"(at most {FUTURES_HISTORY_MAX_ROWS} rows per report)."
        ),
    )
    parser.add_argument(
        "--since",
        type=aware_instant,
        default=None,
        help="History start, ISO 8601 with an offset, e.g. 2026-10-10T15:00:00Z.",
    )
    parser.add_argument(
        "--until",
        type=aware_instant,
        default=None,
        help="History end (exclusive), ISO 8601 with an offset; default now.",
    )


def aware_instant(value: str) -> datetime:
    """Parse an ISO 8601 instant that carries a UTC offset (``Z`` or ``+00:00``)."""
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO 8601 instant.") from None
    if instant.utcoffset() is None:
        message = f"{value!r} needs a UTC offset, for example 2026-10-10T15:00:00Z."
        raise argparse.ArgumentTypeError(message)
    return instant


def wants_history(arguments: argparse.Namespace) -> bool:
    """Whether this invocation is ``futures-account --history``."""
    return arguments.command == "futures-account" and bool(arguments.history)


def history_usage_error(arguments: argparse.Namespace) -> str | None:
    """Reject history flags that do not form one window; ``None`` when they do."""
    if arguments.command != "futures-account":
        return None
    if not arguments.history:
        if arguments.since is not None or arguments.until is not None:
            return "--since and --until need --history."
        return None
    if arguments.since is None:
        return "--history needs --since."
    if arguments.until is not None and arguments.until <= arguments.since:
        return "--until must be later than --since."
    return None


def history_query(arguments: argparse.Namespace) -> dict[str, str]:
    """``since`` / ``until`` query parameters for the history route."""
    query: dict[str, str] = {}
    for name in ("since", "until"):
        instant = getattr(arguments, name, None)
        if isinstance(instant, datetime):
            query[name] = instant.isoformat()
    return query
