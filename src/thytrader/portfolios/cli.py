"""Confirmation-gated ``thytrader-portfolio`` CLI over the loopback HTTP API.

Lists, shows, creates, and edits portfolios (sleeves, weights, limits, manager settings),
runs portfolio backtests, and reads the portfolio journal (ADR 0088). For the manager loop
(ADR 0091) it reads the deployment and the one-call briefing, submits proposals, and
records a person's approve/decline. Every mutation requires ``--confirm``; YOLO never
skips it on this lane. It never starts or stops a portfolio (``thytrader-runtime
portfolio-*`` does) and never places orders: strategies place every trade.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError

from thytrader.agent_http import AgentHttpError, require_matching_ops_contract, resolve_api_base_url
from thytrader.agent_orchestration.confirmation import require_mutation_confirmation
from thytrader.cli_errors import describe_unexpected_failure
from thytrader.cli_parse import trailing_options
from thytrader.config import Settings
from thytrader.market_data.products import SPOT_QUOTE_CURRENCIES
from thytrader.operator.status import EXIT_HEALTHY, EXIT_USAGE
from thytrader.portfolios import client
from thytrader.portfolios.backtest import PortfolioBacktestJob, PortfolioBacktestRequest
from thytrader.portfolios.manager_cli import (
    MANAGER_HANDLERS,
    MANAGER_MUTATIONS,
    ManagerCliError,
    add_manager_commands,
)
from thytrader.portfolios.models import (
    ManagerPermissions,
    ManagerSettings,
    PortfolioCreateRequest,
    PortfolioLimits,
    PortfolioUpdateRequest,
    SetWeightsRequest,
    SleeveAddRequest,
    WeightAssignment,
)
from thytrader.portfolios.views import PortfolioResponse
from thytrader.research.jobs import ResearchJobStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_CONFIRM_HELP = (
    "Required for every mutation. YOLO never skips it on this lane. This CLI cannot start or "
    "stop a portfolio (thytrader-runtime portfolio-* does) or place orders."
)
_CONFIRM_MESSAGE = (
    "Pass --confirm to change portfolios, submit or decide a proposal, or start a portfolio "
    "backtest. This command never places orders."
)
_MUTATIONS = frozenset(
    {"create", "update", "add-sleeve", "remove-sleeve", "set-weights", "backtest"}
    | MANAGER_MUTATIONS
)
_TERMINAL = frozenset(
    {
        ResearchJobStatus.COMPLETED,
        ResearchJobStatus.FAILED,
        ResearchJobStatus.CANCELLED,
        ResearchJobStatus.EXPIRED,
    }
)
_POLL_SECONDS = 2.0
_SHOW_CURVE_POINTS = 200


class PortfolioCliError(RuntimeError):
    """A safe operator-facing portfolio command failure."""


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
    """Build the portfolio argument parser."""
    shared = _shared_options()
    trailing = trailing_options(shared)
    parser = argparse.ArgumentParser(
        prog="thytrader-portfolio",
        description=(
            "Manage portfolios: sleeves (one strategy each, with a capital weight), the cash "
            "reserve, shared limits, and manager settings; run portfolio backtests; read the "
            "portfolio journal; act as the manager agent (deployment, briefing, propose, "
            "proposals) and record a person's approve/decline. Mutations require --confirm. "
            "Default transport is the loopback HTTP API. Start, pause, resume, and stop are "
            "thytrader-runtime portfolio-* commands; nothing here places orders."
        ),
        parents=[shared],
    )
    commands = parser.add_subparsers(dest="command", required=True)
    _add_read_commands(commands, trailing)
    _add_create_update(commands, trailing)
    _add_sleeve_commands(commands, trailing)
    _add_backtest_commands(commands, trailing)
    add_manager_commands(commands, trailing, confirm_help=_CONFIRM_HELP)
    return parser


def _add_read_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register list, show, and journal."""
    listing = commands.add_parser(
        "list", parents=[trailing], help="List portfolios (oldest first) with their sleeves."
    )
    listing.add_argument("--limit", type=int, default=50, help="Page size, 1-100. Default 50.")
    listing.add_argument("--cursor", default=None, help="Opaque next_cursor from the last page.")
    show = commands.add_parser(
        "show",
        parents=[trailing],
        help="Show one portfolio: sleeves, issues, allocation, limits, manager settings, revision.",
    )
    show.add_argument("--portfolio-id", required=True)
    journal = commands.add_parser(
        "journal", parents=[trailing], help="Read the portfolio journal, newest first."
    )
    journal.add_argument("--portfolio-id", required=True)
    journal.add_argument("--limit", type=int, default=50, help="Page size, 1-200. Default 50.")
    journal.add_argument("--cursor", default=None, help="Opaque next_cursor from the last page.")


def _add_create_update(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register create and update."""
    create = commands.add_parser(
        "create",
        parents=[trailing],
        help="Create a paper or live portfolio. Mode and quote currency are then fixed.",
    )
    create.add_argument("--name", required=True)
    create.add_argument("--mode", required=True, choices=("paper", "live"))
    create.add_argument(
        "--quote-currency", default="USDC", choices=SPOT_QUOTE_CURRENCIES, help="Default USDC."
    )
    create.add_argument("--capital-quote", required=True, help="Portfolio capital, e.g. 1000.")
    create.add_argument(
        "--cash-reserve-fraction", default="0", help="Cash never allocated, 0-<1. Default 0."
    )
    create.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    update = commands.add_parser(
        "update",
        parents=[trailing],
        help="Change name, capital, cash reserve, limits, or manager settings (one revision).",
    )
    update.add_argument("--portfolio-id", required=True)
    update.add_argument("--revision", type=int, required=True, help="Current revision (show).")
    update.add_argument("--name", default=None)
    update.add_argument("--capital-quote", default=None)
    update.add_argument("--cash-reserve-fraction", default=None)
    update.add_argument("--max-total-exposure-fraction", default=None)
    update.add_argument("--max-per-asset-fraction", default=None)
    daily = update.add_mutually_exclusive_group()
    daily.add_argument("--daily-loss-quote", default=None, help="UTC-day loss stop in quote.")
    daily.add_argument("--clear-daily-loss", action="store_true")
    drawdown = update.add_mutually_exclusive_group()
    drawdown.add_argument("--max-drawdown-fraction", default=None)
    drawdown.add_argument("--clear-max-drawdown", action="store_true")
    mandate = update.add_mutually_exclusive_group()
    mandate.add_argument("--mandate", default=None, help="Manager mandate text.")
    mandate.add_argument("--mandate-file", default=None, help="UTF-8 file with the mandate.")
    for flag in ("--may-rebalance", "--may-pause-sleeves", "--may-propose-sleeves"):
        update.add_argument(flag, choices=("yes", "no"), default=None)
    update.add_argument(
        "--max-weight-change-per-week", default=None, help="Rebalance budget fraction, e.g. 0.1."
    )
    update.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)


def _add_sleeve_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register add-sleeve, remove-sleeve, and set-weights."""
    add = commands.add_parser(
        "add-sleeve",
        parents=[trailing],
        help="Add a strategy as a sleeve (same quote currency; weights + reserve <= 1).",
    )
    add.add_argument("--portfolio-id", required=True)
    add.add_argument("--revision", type=int, required=True)
    add.add_argument("--strategy-id", required=True)
    add.add_argument("--weight-fraction", required=True, help="e.g. 0.5 for 50%%.")
    add.add_argument("--note", default=None)
    add.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    remove = commands.add_parser(
        "remove-sleeve",
        parents=[trailing],
        help="Remove one sleeve by --sleeve-id or by its --strategy-id.",
    )
    remove.add_argument("--portfolio-id", required=True)
    remove.add_argument("--revision", type=int, required=True)
    target = remove.add_mutually_exclusive_group(required=True)
    target.add_argument("--sleeve-id", default=None)
    target.add_argument("--strategy-id", default=None)
    remove.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    weights = commands.add_parser(
        "set-weights",
        parents=[trailing],
        help="Replace every sleeve weight atomically (repeat --weight for each sleeve).",
    )
    weights.add_argument("--portfolio-id", required=True)
    weights.add_argument("--revision", type=int, required=True)
    weights.add_argument(
        "--weight",
        action="append",
        required=True,
        metavar="ID=FRACTION",
        help="Sleeve id or strategy id, '=', weight fraction. Name every sleeve once.",
    )
    weights.add_argument("--cash-reserve-fraction", default=None)
    weights.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)


def _add_backtest_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
) -> None:
    """Register backtest, show-backtest, and list-backtests."""
    backtest = commands.add_parser(
        "backtest",
        parents=[trailing],
        help=(
            "Queue a portfolio backtest: every sleeve on its capital slice over one common "
            "window, then combined. Returns the job (HTTP 202)."
        ),
    )
    backtest.add_argument("--portfolio-id", required=True)
    backtest.add_argument("--maker-fee-rate", required=True, help="e.g. 0.004")
    backtest.add_argument("--taker-fee-rate", required=True, help="e.g. 0.006")
    backtest.add_argument("--fixed-slippage-bps", required=True, help="e.g. 5")
    backtest.add_argument("--spread-bps", default=None, help="Optional spread stress, e.g. 10.")
    backtest.add_argument("--revision", type=int, default=None, help="Refuse if stale.")
    backtest.add_argument(
        "--evaluation-start", default=None, help="ISO UTC; with --evaluation-end."
    )
    backtest.add_argument(
        "--evaluation-end", default=None, help="ISO UTC; with --evaluation-start."
    )
    backtest.add_argument(
        "--datasets-file",
        default=None,
        help="JSON list of per-strategy dataset fingerprints (default: latest verified).",
    )
    backtest.add_argument("--wait", action="store_true", help="Poll until the job finishes.")
    backtest.add_argument("--timeout-seconds", type=int, default=1800)
    backtest.add_argument("--confirm", action="store_true", help=_CONFIRM_HELP)
    show = commands.add_parser(
        "show-backtest",
        parents=[trailing],
        help="Show a job (--job-id) or a stored result (--result-fingerprint).",
    )
    show.add_argument("--portfolio-id", required=True)
    target = show.add_mutually_exclusive_group(required=True)
    target.add_argument("--job-id", default=None)
    target.add_argument("--result-fingerprint", default=None)
    show.add_argument(
        "--full-curve",
        action="store_true",
        help=f"Print every equity point (default: thinned to {_SHOW_CURVE_POINTS}).",
    )
    listing = commands.add_parser(
        "list-backtests",
        parents=[trailing],
        help="List stored portfolio backtests and recent jobs, newest first.",
    )
    listing.add_argument("--portfolio-id", required=True)
    listing.add_argument("--limit", type=int, default=10)


def _uuid(value: str | None, flag: str) -> UUID:
    """Parse one UUID flag."""
    try:
        return UUID(str(value))
    except ValueError as error:
        raise PortfolioCliError(f"{flag} must be a UUID.") from error


def _portfolio(base_url: str, portfolio_id: UUID) -> PortfolioResponse:
    """Read and validate the current portfolio."""
    return PortfolioResponse.model_validate(client.show_portfolio(base_url, portfolio_id))


def _create(base_url: str, args: argparse.Namespace) -> object:
    """Create one portfolio."""
    request = PortfolioCreateRequest(
        name=args.name,
        mode=args.mode,
        quote_currency=args.quote_currency,
        capital_quote=args.capital_quote,
        cash_reserve_fraction=args.cash_reserve_fraction,
    )
    return client.create_portfolio(base_url, request)


def _update(base_url: str, args: argparse.Namespace) -> object:
    """Merge flag changes onto the current limits/manager settings and PATCH once."""
    portfolio_id = _uuid(args.portfolio_id, "--portfolio-id")
    current = _portfolio(base_url, portfolio_id)
    request = PortfolioUpdateRequest(
        revision=args.revision,
        name=args.name,
        capital_quote=args.capital_quote,
        cash_reserve_fraction=args.cash_reserve_fraction,
        limits=_merged_limits(args, current.limits),
        manager=_merged_manager(args, current.manager),
    )
    return client.update_portfolio(base_url, portfolio_id, request)


def _merged_limits(args: argparse.Namespace, current: PortfolioLimits) -> PortfolioLimits | None:
    """Current limits with the flags applied, or None when no limit flag was given."""
    changes: dict[str, str | None] = {}
    if args.max_total_exposure_fraction is not None:
        changes["max_total_exposure_fraction"] = args.max_total_exposure_fraction
    if args.max_per_asset_fraction is not None:
        changes["max_per_asset_fraction"] = args.max_per_asset_fraction
    if args.daily_loss_quote is not None or args.clear_daily_loss:
        changes["daily_loss_quote"] = args.daily_loss_quote
    if args.max_drawdown_fraction is not None or args.clear_max_drawdown:
        changes["max_drawdown_fraction"] = args.max_drawdown_fraction
    if not changes:
        return None
    return PortfolioLimits.model_validate({**current.model_dump(mode="json"), **changes})


def _merged_manager(args: argparse.Namespace, current: ManagerSettings) -> ManagerSettings | None:
    """Current manager settings with the flags applied, or None when none was given."""
    mandate = args.mandate
    if args.mandate_file is not None:
        mandate = Path(args.mandate_file).read_text(encoding="utf-8")
    permissions: dict[str, bool | str] = {}
    for name in ("may_rebalance", "may_pause_sleeves", "may_propose_sleeves"):
        value = getattr(args, name)
        if value is not None:
            permissions[name] = value == "yes"
    if args.max_weight_change_per_week is not None:
        permissions["max_weight_change_per_week"] = args.max_weight_change_per_week
    if mandate is None and not permissions:
        return None
    merged = ManagerPermissions.model_validate(
        {**current.permissions.model_dump(mode="json"), **permissions}
    )
    return ManagerSettings(
        mandate=current.mandate if mandate is None else mandate, permissions=merged
    )


def _add_sleeve(base_url: str, args: argparse.Namespace) -> object:
    """Add one sleeve."""
    request = SleeveAddRequest(
        revision=args.revision,
        strategy_id=_uuid(args.strategy_id, "--strategy-id"),
        weight_fraction=args.weight_fraction,
        note=args.note,
    )
    return client.add_sleeve(base_url, _uuid(args.portfolio_id, "--portfolio-id"), request)


def _remove_sleeve(base_url: str, args: argparse.Namespace) -> object:
    """Remove one sleeve, resolving --strategy-id to its sleeve when given."""
    portfolio_id = _uuid(args.portfolio_id, "--portfolio-id")
    if args.sleeve_id is not None:
        sleeve_id = _uuid(args.sleeve_id, "--sleeve-id")
    else:
        sleeve_id = _resolve_sleeve(
            _portfolio(base_url, portfolio_id), _uuid(args.strategy_id, "--strategy-id")
        )
    return client.remove_sleeve(base_url, portfolio_id, sleeve_id, revision=args.revision)


def _resolve_sleeve(portfolio: PortfolioResponse, identity: UUID) -> UUID:
    """Map a sleeve id or a strategy id to the portfolio's sleeve id."""
    for sleeve in portfolio.sleeves:
        if identity in (sleeve.sleeve_id, sleeve.strategy_id):
            return sleeve.sleeve_id
    raise PortfolioCliError(
        f"{identity} is neither a sleeve nor a sleeve strategy of this portfolio."
    )


def _set_weights(base_url: str, args: argparse.Namespace) -> object:
    """Replace every sleeve weight; IDs may be sleeve ids or strategy ids."""
    portfolio_id = _uuid(args.portfolio_id, "--portfolio-id")
    current = _portfolio(base_url, portfolio_id)
    assignments: list[WeightAssignment] = []
    for item in args.weight:
        identity, separator, fraction = str(item).partition("=")
        if not separator:
            raise PortfolioCliError("--weight takes ID=FRACTION, for example SLEEVE_ID=0.4.")
        sleeve_id = _resolve_sleeve(current, _uuid(identity.strip(), "--weight ID"))
        assignments.append(WeightAssignment(sleeve_id=sleeve_id, weight_fraction=fraction.strip()))
    request = SetWeightsRequest(
        revision=args.revision,
        weights=tuple(assignments),
        cash_reserve_fraction=args.cash_reserve_fraction,
    )
    return client.set_weights(base_url, portfolio_id, request)


def _backtest(base_url: str, args: argparse.Namespace) -> object:
    """Queue one portfolio backtest and optionally wait for it."""
    portfolio_id = _uuid(args.portfolio_id, "--portfolio-id")
    datasets: object = ()
    if args.datasets_file is not None:
        datasets = json.loads(Path(args.datasets_file).read_text(encoding="utf-8"))
    request = PortfolioBacktestRequest.model_validate(
        {
            "revision": args.revision,
            "maker_fee_rate": args.maker_fee_rate,
            "taker_fee_rate": args.taker_fee_rate,
            "fixed_slippage_bps": args.fixed_slippage_bps,
            "spread_bps": args.spread_bps,
            "evaluation_start": args.evaluation_start,
            "evaluation_end": args.evaluation_end,
            "datasets": datasets,
        }
    )
    accepted = client.submit_backtest(base_url, portfolio_id, request)
    if not args.wait:
        return accepted
    job = PortfolioBacktestJob.model_validate(accepted.get("job"))
    finished = _wait(base_url, portfolio_id, job, timeout_seconds=args.timeout_seconds)
    return _job_view(base_url, portfolio_id, finished, full_curve=False)


def _wait(
    base_url: str, portfolio_id: UUID, job: PortfolioBacktestJob, *, timeout_seconds: int
) -> PortfolioBacktestJob:
    """Poll one job until it reaches a terminal status or the timeout passes."""
    deadline = time.monotonic() + max(1, timeout_seconds)
    while job.status not in _TERMINAL:
        if time.monotonic() >= deadline:
            raise PortfolioCliError(
                f"Portfolio backtest {job.job_id} is still {job.status.value}; poll it with "
                "show-backtest --job-id."
            )
        time.sleep(_POLL_SECONDS)
        job = PortfolioBacktestJob.model_validate(
            client.show_backtest_job(base_url, portfolio_id, job.job_id)
        )
    return job


def _show_backtest(base_url: str, args: argparse.Namespace) -> object:
    """Show one job (with its result once completed) or one stored result."""
    portfolio_id = _uuid(args.portfolio_id, "--portfolio-id")
    if args.job_id is not None:
        job = PortfolioBacktestJob.model_validate(
            client.show_backtest_job(base_url, portfolio_id, _uuid(args.job_id, "--job-id"))
        )
        return _job_view(base_url, portfolio_id, job, full_curve=args.full_curve)
    return client.show_backtest_result(
        base_url,
        portfolio_id,
        str(args.result_fingerprint),
        max_points=None if args.full_curve else _SHOW_CURVE_POINTS,
    )


def _job_view(
    base_url: str, portfolio_id: UUID, job: PortfolioBacktestJob, *, full_curve: bool
) -> object:
    """A job plus its stored result once it completed."""
    view: dict[str, object] = {"job": job.model_dump(mode="json")}
    if job.status is ResearchJobStatus.COMPLETED and job.result_fingerprint is not None:
        view["result"] = client.show_backtest_result(
            base_url,
            portfolio_id,
            job.result_fingerprint,
            max_points=None if full_curve else _SHOW_CURVE_POINTS,
        )
    return view


_HANDLERS: dict[str, Callable[[str, argparse.Namespace], object]] = {
    "list": lambda url, args: client.list_portfolios(url, limit=args.limit, cursor=args.cursor),
    "show": lambda url, args: client.show_portfolio(
        url, _uuid(args.portfolio_id, "--portfolio-id")
    ),
    "journal": lambda url, args: client.list_journal(
        url, _uuid(args.portfolio_id, "--portfolio-id"), limit=args.limit, cursor=args.cursor
    ),
    "create": _create,
    "update": _update,
    "add-sleeve": _add_sleeve,
    "remove-sleeve": _remove_sleeve,
    "set-weights": _set_weights,
    "backtest": _backtest,
    "show-backtest": _show_backtest,
    "list-backtests": lambda url, args: client.list_backtests(
        url, _uuid(args.portfolio_id, "--portfolio-id"), limit=args.limit
    ),
    **MANAGER_HANDLERS,
}


def _dispatch(args: argparse.Namespace) -> object:
    """Confirm mutations, check the ops contract, and run one command."""
    base_url = resolve_api_base_url(explicit=args.base_url, settings=Settings())
    if args.command in _MUTATIONS:
        require_mutation_confirmation(
            confirmed=bool(args.confirm),
            missing_message=_CONFIRM_MESSAGE,
            error_type=PortfolioCliError,
            hard_gate=True,
        )
    require_matching_ops_contract(base_url)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        raise AssertionError(f"unsupported portfolio command: {args.command}")
    return handler(base_url, args)


def _first_validation_message(error: ValidationError) -> str:
    """Return the first semantic validation message."""
    issues = error.errors()
    if not issues:
        return "Input failed validation."
    location = ".".join(str(part) for part in issues[0].get("loc", ()))
    message = str(issues[0].get("msg", "Input failed validation."))
    return f"{location}: {message}" if location else message


def main(argv: Sequence[str] | None = None) -> None:
    """Run one portfolio command and print JSON; mutations require --confirm."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        raise SystemExit(int(error.code) if isinstance(error.code, int) else EXIT_USAGE) from error
    try:
        payload = _dispatch(args)
    except (PortfolioCliError, ManagerCliError, AgentHttpError) as error:
        raise SystemExit(str(error)) from error
    except ValidationError as error:
        raise SystemExit(_first_validation_message(error)) from error
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not read an input file: {error}") from error
    except Exception as error:
        message = describe_unexpected_failure(
            error, lane="Portfolio command", safety="Paper and live state were not changed."
        )
        raise SystemExit(message) from error
    sys.stdout.write(f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n")
    raise SystemExit(EXIT_HEALTHY)


if __name__ == "__main__":
    main()
