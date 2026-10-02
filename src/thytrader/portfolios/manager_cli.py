"""Manager-loop commands of the ``thytrader-portfolio`` CLI (ADR 0091).

Read: ``deployment`` (state, sleeve bots, breakers, exposure) and ``briefing`` (everything
a manager agent reasons from, in one call), ``proposals``, and ``show-proposal``. Mutate
(always ``--confirm``; YOLO never skips it): ``propose`` (rebalance, pause or resume a
sleeve, add a sleeve, with a rationale and cited evidence), and ``approve`` / ``decline``
(a person's decision; ``approve`` of a live resume also needs ``--i-understand-live``).
No command places an order: strategies place every trade.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.portfolios import client
from thytrader.portfolios.models import WeightAssignment
from thytrader.portfolios.proposals import (
    PROPOSAL_KINDS,
    PROPOSAL_STATUSES,
    AddSleeveChange,
    PauseSleeveChange,
    ProposalDecisionRequest,
    ProposalEvidence,
    ProposalSubmitRequest,
    RebalanceChange,
    ResumeSleeveChange,
)
from thytrader.portfolios.views import PortfolioResponse

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

MANAGER_MUTATIONS = frozenset({"propose", "approve", "decline"})
_EVIDENCE_KINDS = ("backtest_result", "portfolio_backtest", "study", "decision", "deployment")


class ManagerCliError(RuntimeError):
    """A safe operator-facing manager command failure."""


def add_manager_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
    *,
    confirm_help: str,
) -> None:
    """Register deployment, briefing, propose, proposals, show-proposal, approve, decline."""
    deployment = commands.add_parser(
        "deployment",
        parents=[trailing],
        help="Read-only: the portfolio's state, each sleeve's bot, breakers, and exposure.",
    )
    deployment.add_argument("--portfolio-id", required=True)
    briefing = commands.add_parser(
        "briefing",
        parents=[trailing],
        help=(
            "Read-only manager briefing: mandate, permissions and weekly budget, performance, "
            "risk, sleeves vs backtest evidence, recent decisions, proposals, journal."
        ),
    )
    briefing.add_argument("--portfolio-id", required=True)
    briefing.add_argument("--decisions-per-sleeve", type=int, default=5, help="0-50. Default 5.")
    briefing.add_argument("--journal-limit", type=int, default=20, help="1-200. Default 20.")
    _add_propose(commands, trailing, confirm_help=confirm_help)
    proposals = commands.add_parser(
        "proposals", parents=[trailing], help="List proposals newest first."
    )
    proposals.add_argument("--portfolio-id", required=True)
    proposals.add_argument("--status", choices=PROPOSAL_STATUSES, default=None)
    proposals.add_argument("--limit", type=int, default=20, help="1-100. Default 20.")
    proposals.add_argument("--cursor", default=None)
    show = commands.add_parser("show-proposal", parents=[trailing], help="Show one proposal.")
    show.add_argument("--portfolio-id", required=True)
    show.add_argument("--proposal-id", required=True)
    for decision in ("approve", "decline"):
        command = commands.add_parser(
            decision,
            parents=[trailing],
            help=(
                f"A person's decision: {decision} one pending proposal. Never run this on "
                "your own proposal unless the user explicitly told you to."
            ),
        )
        command.add_argument("--portfolio-id", required=True)
        command.add_argument("--proposal-id", required=True)
        command.add_argument("--note", default=None, help="Optional note (500 characters).")
        command.add_argument("--confirm", action="store_true", help=confirm_help)
        if decision == "approve":
            command.add_argument(
                "--i-understand-live",
                action="store_true",
                help="Required to approve resuming a live sleeve (re-arms live orders).",
            )


def _add_propose(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    trailing: argparse.ArgumentParser,
    *,
    confirm_help: str,
) -> None:
    """Register ``propose``."""
    propose = commands.add_parser(
        "propose",
        parents=[trailing],
        help=(
            "Submit a manager proposal: rebalance, pause_sleeve, resume_sleeve, or add_sleeve. "
            "It auto-applies only inside the manager's permissions; otherwise it waits for a "
            "person. There is no order proposal."
        ),
    )
    propose.add_argument("--portfolio-id", required=True)
    propose.add_argument(
        "--revision", type=int, required=True, help="The revision your briefing showed."
    )
    propose.add_argument(
        "--kind",
        required=True,
        choices=(*PROPOSAL_KINDS, *(kind.replace("_", "-") for kind in PROPOSAL_KINDS)),
    )
    propose.add_argument(
        "--weight",
        action="append",
        default=[],
        help="rebalance: ID=FRACTION for every sleeve (ID is a sleeve or strategy id).",
    )
    propose.add_argument("--cash-reserve-fraction", default=None, help="rebalance: optional.")
    propose.add_argument(
        "--sleeve-id", default=None, help="pause/resume: a sleeve id or its strategy id."
    )
    propose.add_argument("--strategy-id", default=None, help="add_sleeve: the strategy.")
    propose.add_argument("--weight-fraction", default=None, help="add_sleeve: e.g. 0.1.")
    propose.add_argument("--note", default=None, help="add_sleeve: optional sleeve note.")
    text = propose.add_mutually_exclusive_group(required=True)
    text.add_argument("--rationale", default=None, help="Why, citing the evidence (2000 chars).")
    text.add_argument("--rationale-file", default=None, help="Read the rationale from a file.")
    propose.add_argument(
        "--evidence",
        action="append",
        default=[],
        help=(
            "KIND=REF, repeatable. KIND is backtest_result, portfolio_backtest, or study "
            "(REF sha256:...), decision (REF <deployment_id>/<product_id>@<bar_starts_at> from "
            "the briefing), or deployment (REF deployment id)."
        ),
    )
    propose.add_argument("--confirm", action="store_true", help=confirm_help)


def _uuid(value: object, flag: str) -> UUID:
    """Parse one UUID flag."""
    try:
        return UUID(str(value))
    except ValueError as error:
        raise ManagerCliError(f"{flag} must be a UUID.") from error


def _portfolio(base_url: str, portfolio_id: UUID) -> PortfolioResponse:
    """Read and validate the current portfolio."""
    return PortfolioResponse.model_validate(client.show_portfolio(base_url, portfolio_id))


def _sleeve(portfolio: PortfolioResponse, identity: object, flag: str) -> UUID:
    """Map a sleeve id or a strategy id to the portfolio's sleeve id."""
    wanted = _uuid(identity, flag)
    for sleeve in portfolio.sleeves:
        if wanted in (sleeve.sleeve_id, sleeve.strategy_id):
            return sleeve.sleeve_id
    raise ManagerCliError(f"{wanted} is neither a sleeve nor a sleeve strategy of this portfolio.")


def _change(
    args: argparse.Namespace, portfolio: PortfolioResponse
) -> RebalanceChange | PauseSleeveChange | ResumeSleeveChange | AddSleeveChange:
    """Build the typed change from the flags of one ``--kind``."""
    kind = str(args.kind).replace("-", "_")
    if kind == "rebalance":
        if not args.weight:
            raise ManagerCliError("rebalance needs --weight ID=FRACTION for every sleeve.")
        weights = []
        for item in args.weight:
            identity, separator, fraction = str(item).partition("=")
            if not separator:
                raise ManagerCliError("--weight takes ID=FRACTION, for example SLEEVE_ID=0.4.")
            weights.append(
                WeightAssignment(
                    sleeve_id=_sleeve(portfolio, identity.strip(), "--weight ID"),
                    weight_fraction=fraction.strip(),
                )
            )
        return RebalanceChange(
            weights=tuple(weights), cash_reserve_fraction=args.cash_reserve_fraction
        )
    if kind in {"pause_sleeve", "resume_sleeve"}:
        if args.sleeve_id is None:
            raise ManagerCliError(f"{kind} needs --sleeve-id.")
        sleeve_id = _sleeve(portfolio, args.sleeve_id, "--sleeve-id")
        if kind == "pause_sleeve":
            return PauseSleeveChange(sleeve_id=sleeve_id)
        return ResumeSleeveChange(sleeve_id=sleeve_id)
    if args.strategy_id is None or args.weight_fraction is None:
        raise ManagerCliError("add_sleeve needs --strategy-id and --weight-fraction.")
    return AddSleeveChange(
        strategy_id=_uuid(args.strategy_id, "--strategy-id"),
        weight_fraction=args.weight_fraction,
        note=args.note,
    )


def _evidence(items: list[str]) -> tuple[ProposalEvidence, ...]:
    """Parse repeated ``--evidence KIND=REF``."""
    evidence = []
    for item in items:
        kind, separator, ref = str(item).partition("=")
        kind = kind.strip()
        if not separator or kind not in _EVIDENCE_KINDS:
            raise ManagerCliError(
                f"--evidence takes KIND=REF with KIND one of {', '.join(_EVIDENCE_KINDS)}."
            )
        evidence.append(ProposalEvidence.model_validate({"kind": kind, "ref": ref.strip()}))
    return tuple(evidence)


def _propose(base_url: str, args: argparse.Namespace) -> object:
    """Submit one proposal."""
    portfolio_id = _uuid(args.portfolio_id, "--portfolio-id")
    portfolio = _portfolio(base_url, portfolio_id)
    rationale = (
        Path(args.rationale_file).read_text(encoding="utf-8")
        if args.rationale_file is not None
        else str(args.rationale)
    )
    request = ProposalSubmitRequest(
        revision=args.revision,
        change=_change(args, portfolio),
        rationale=rationale,
        evidence=_evidence(args.evidence),
    )
    return client.submit_proposal(base_url, portfolio_id, request)


def _decide(decision: str) -> Callable[[str, argparse.Namespace], object]:
    """Approve or decline one pending proposal."""

    def run(base_url: str, args: argparse.Namespace) -> object:
        """Send the decision."""
        request = ProposalDecisionRequest(
            note=args.note,
            i_understand_live=bool(getattr(args, "i_understand_live", False)),
        )
        return client.decide_proposal(
            base_url,
            _uuid(args.portfolio_id, "--portfolio-id"),
            _uuid(args.proposal_id, "--proposal-id"),
            decision=decision,
            request=request,
        )

    return run


MANAGER_HANDLERS: dict[str, Callable[[str, argparse.Namespace], object]] = {
    "deployment": lambda url, args: client.show_deployment(
        url, _uuid(args.portfolio_id, "--portfolio-id")
    ),
    "briefing": lambda url, args: client.show_briefing(
        url,
        _uuid(args.portfolio_id, "--portfolio-id"),
        decisions_per_sleeve=args.decisions_per_sleeve,
        journal_limit=args.journal_limit,
    ),
    "propose": _propose,
    "proposals": lambda url, args: client.list_proposals(
        url,
        _uuid(args.portfolio_id, "--portfolio-id"),
        status=args.status,
        limit=args.limit,
        cursor=args.cursor,
    ),
    "show-proposal": lambda url, args: client.show_proposal(
        url, _uuid(args.portfolio_id, "--portfolio-id"), _uuid(args.proposal_id, "--proposal-id")
    ),
    "approve": _decide("approve"),
    "decline": _decide("decline"),
}
