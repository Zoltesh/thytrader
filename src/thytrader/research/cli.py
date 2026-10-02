"""Read-only CLI for the entry-condition trace behind one published backtest result.

ADR 0090 ports ``thytrader-research-evaluate`` to the HTTP API. The API evaluates the
exact published run against its verified datasets (which Compose keeps inside the API
container) and returns one bounded page of ``SignalTraceRecord`` rows plus outcome
counts. The CLI accepts a ``result_fingerprint`` or the ``run_fingerprint`` of a
completed backtest and never creates runs, trades, orders, or PnL.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from thytrader.agent_http import (
    AgentHttpError,
    request_json,
    require_matching_ops_contract,
    resolve_api_base_url,
)
from thytrader.config import Settings
from thytrader.research.trace_service import (
    SIGNAL_TRACE_PAGE_DEFAULT_LIMIT,
    SIGNAL_TRACE_PAGE_MAX_LIMIT,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_NOT_FOUND = 404
_OUTCOMES = ("all", "matched", "not_matched", "undefined")


class ResearchCliError(RuntimeError):
    """Report a safe operator-facing research command failure."""


def _fingerprint(value: str) -> str:
    """Require a lowercase canonical SHA-256 fingerprint argument."""
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be sha256: followed by 64 lowercase hex characters")
    return value


def _limit(value: str) -> int:
    """Require a page size the API accepts."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if not 1 <= parsed <= SIGNAL_TRACE_PAGE_MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"must be between 1 and {SIGNAL_TRACE_PAGE_MAX_LIMIT}")
    return parsed


def _parser() -> argparse.ArgumentParser:
    """Build the read-only signal-trace argument parser."""
    parser = argparse.ArgumentParser(
        prog="thytrader-research-evaluate",
        description=(
            "Print the entry-condition trace (per-bar indicator values and matched / "
            "not_matched / undefined outcome) of the exact run behind one backtest result, "
            "evaluated by the ThyTrader API. Read-only: it creates no runs, trades, orders, "
            "fills, positions, or PnL."
        ),
    )
    parser.add_argument(
        "fingerprint",
        type=_fingerprint,
        help=(
            "A backtest result_fingerprint, or the run_fingerprint of a completed backtest "
            "(resolved to its newest result)."
        ),
    )
    parser.add_argument(
        "--outcome",
        choices=_OUTCOMES,
        default="all",
        help="Only return bars with this entry-condition outcome (default: all).",
    )
    parser.add_argument(
        "--limit",
        type=_limit,
        default=SIGNAL_TRACE_PAGE_DEFAULT_LIMIT,
        help=(
            f"Records per page, 1-{SIGNAL_TRACE_PAGE_MAX_LIMIT} "
            f"(default {SIGNAL_TRACE_PAGE_DEFAULT_LIMIT})."
        ),
    )
    parser.add_argument("--cursor", help="Opaque next_cursor from the previous page.")
    parser.add_argument("--base-url", help="Loopback ThyTrader API origin.")
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON page.")
    return parser


def _trace_url(base_url: str, result_fingerprint: str, arguments: argparse.Namespace) -> str:
    """Build the bounded signal-trace page URL for one result."""
    query: dict[str, str | int] = {"outcome": arguments.outcome, "limit": arguments.limit}
    if arguments.cursor:
        query["cursor"] = arguments.cursor
    return f"{base_url}/api/v1/backtests/{result_fingerprint}/signal-trace?{urlencode(query)}"


def _result_for_run(base_url: str, run_fingerprint: str) -> str | None:
    """Return the newest result fingerprint published for one run, if any."""
    query = urlencode({"run_fingerprint": run_fingerprint, "limit": 1})
    listing = request_json(method="GET", url=f"{base_url}/api/v1/backtests?{query}")
    if not isinstance(listing, dict):
        raise ResearchCliError("ThyTrader API returned an unexpected backtest listing.")
    entries = listing.get("entries")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    fingerprint = entries[0].get("result_fingerprint")
    return fingerprint if isinstance(fingerprint, str) else None


def fetch_signal_trace(base_url: str, arguments: argparse.Namespace) -> object:
    """GET one trace page, resolving a run fingerprint to its newest result on a 404."""
    fingerprint: str = arguments.fingerprint
    try:
        return request_json(method="GET", url=_trace_url(base_url, fingerprint, arguments))
    except AgentHttpError as error:
        if error.status != _NOT_FOUND:
            raise
    result_fingerprint = _result_for_run(base_url, fingerprint)
    if result_fingerprint is None:
        raise ResearchCliError(
            "No backtest result has this fingerprint, and no completed backtest used it as "
            "its run. Pass a result_fingerprint (or run_fingerprint) from "
            "`thytrader-research list-results` or `show-result`."
        )
    return request_json(method="GET", url=_trace_url(base_url, result_fingerprint, arguments))


def main(argv: Sequence[str] | None = None) -> None:
    """Print one verified trace page as JSON; fail with the API's redacted reason."""
    arguments = _parser().parse_args(argv)
    try:
        base_url = resolve_api_base_url(explicit=arguments.base_url, settings=Settings())
        require_matching_ops_contract(base_url)
        page = fetch_signal_trace(base_url, arguments)
    except (AgentHttpError, ResearchCliError) as error:
        raise SystemExit(f"Signal trace unavailable: {error}") from error
    indent = 2 if arguments.pretty else None
    separators = None if arguments.pretty else (",", ":")
    sys.stdout.write(
        json.dumps(page, indent=indent, separators=separators, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
