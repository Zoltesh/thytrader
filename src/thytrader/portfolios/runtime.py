"""Deploy a portfolio: start, pause, resume, and stop its sleeves (ADR 0091).

Starting a portfolio starts (or attaches) one deployment per sleeve, tagged with the
portfolio's id. A paper sleeve starts with ``weight * capital`` of paper cash; a live
sleeve's allocated capital is ``weight * capital``, and on a live portfolio that sleeve
allocation counts as risk-policy allocation membership. Every sleeve book still passes
every risk-policy rule (allowlist, slots, paper book, account exposure, breakers), and
the start is planned for every sleeve before anything starts, so a refused sleeve leaves
the whole portfolio untouched. Pause, resume, and stop apply the single-deployment
semantics (managed stop or flatten) per sleeve. Every action is journaled.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING, Literal

from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.ledger import resolve_paper_fee_schedule
from thytrader.execution.lifecycle import occupies_running_slot
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    RuntimePhase,
)
from thytrader.execution.paper_fees import PaperFeesUnavailableError, paper_fee_rates
from thytrader.execution.service import (
    PortfolioSleeveStart,
    ReferenceWatchlist,
    create_deployment,
    set_deployment_status,
)
from thytrader.market_data.models import parse_candle_interval
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
)
from thytrader.portfolios.deployment import (
    PortfolioBooks,
    SleeveBook,
    begin_run,
    deployment_mode,
    members,
    occupied_members,
    run_equity,
    run_members,
    sleeve_books,
    utc_day_start,
)
from thytrader.portfolios.models import (
    JournalDetail,
    JournalEntry,
    JournalKind,
    JournalReason,
    MutationContext,
    PortfolioConflictError,
    PortfolioLiveAcknowledgementError,
    PortfolioRuntimeState,
    PortfolioSleeveNotFoundError,
    PortfolioStartRejectedError,
    PortfolioValidationError,
    StartProblem,
    sleeve_issues,
    utc_millisecond,
)
from thytrader.portfolios.rules import journal_entry, quote_text, require_revision
from thytrader.risk.gate import evaluate_new_deployment
from thytrader.risk.models import RiskDecision
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.library import StrategyLibraryError
from thytrader.strategies.models import covered_product_ids

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.execution.store import ExecutionStore
    from thytrader.persistence.audit_events import AuditEventStore
    from thytrader.portfolios.models import PortfolioAggregate, SleeveView
    from thytrader.portfolios.store import PortfolioStorage
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.strategies.library import StrategyStore
    from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotReader

_logger = logging.getLogger(__name__)

SleeveOutcomeKind = Literal[
    "started", "attached", "paused", "resumed", "stopped", "unchanged", "failed"
]
PortfolioAction = Literal["start", "pause", "resume", "stop"]
NOT_DEPLOYED_MESSAGE = "No sleeve of this portfolio is running or paused."


@dataclass(frozen=True, slots=True)
class SleeveOutcome:
    """What one portfolio action did to one sleeve's book."""

    sleeve_id: UUID | None
    strategy_id: UUID | None
    strategy_name: str
    outcome: SleeveOutcomeKind
    deployment_id: UUID | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioActionResult:
    """One start, pause, resume, or stop and its per-sleeve outcomes."""

    action: PortfolioAction
    outcomes: tuple[SleeveOutcome, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDeploymentSnapshot:
    """A portfolio with its runtime state, sleeve books, and each book's snapshot."""

    aggregate: PortfolioAggregate
    runtime: PortfolioRuntimeState
    books: PortfolioBooks
    tagged: tuple[Deployment, ...]
    snapshots: tuple[DeploymentSnapshot, ...]
    equity: Decimal


@dataclass(frozen=True, slots=True)
class FeeAssumptions:
    """Optional paper maker/taker rates applied to every paper sleeve."""

    maker_fee_rate: Decimal | None = None
    taker_fee_rate: Decimal | None = None


@dataclass(frozen=True, slots=True)
class _PlannedSleeve:
    """One sleeve the start will create a book for."""

    view: SleeveView
    capital: Decimal
    snapshot: StrategySnapshot


class PortfolioRuntimeService:
    """Start, pause, resume, and stop a portfolio and its sleeves; reset its breaker."""

    def __init__(
        self,
        *,
        portfolios: PortfolioStorage,
        execution: ExecutionStore,
        strategies: StrategyStore,
        publication: StrategySnapshotReader,
        risk_store: RiskPolicyStore | None,
        live_allowed: bool,
        audit: AuditEventStore | None = None,
        reference_watches: ReferenceWatchlist | None = None,
    ) -> None:
        """Bind the stores the actions read and write.

        ``reference_watches`` lets a sleeve whose strategy reads reference instruments
        (ADR 0096) start only when those series are watched, like a single bot.
        """
        self._portfolios = portfolios
        self._execution = execution
        self._strategies = strategies
        self._publication = publication
        self._risk_store = risk_store
        self._live_allowed = live_allowed
        self._audit = audit
        self._reference_watches = reference_watches

    async def snapshot(self, portfolio_id: UUID) -> PortfolioDeploymentSnapshot:
        """Read the portfolio, its runtime state, and every sleeve's book."""
        aggregate = await self._portfolios.get(portfolio_id)
        runtime = await self._portfolios.runtime_state(portfolio_id)
        tagged = members(await self._execution.list_deployments(), portfolio_id)
        in_run = {item.id for item in run_members(tagged, runtime)}
        snapshots = tuple(
            [
                await self._execution.get_deployment(item.id)
                for item in tagged
                if item.id in in_run or occupies_running_slot(item)
            ]
        )
        return PortfolioDeploymentSnapshot(
            aggregate=aggregate,
            runtime=runtime,
            books=sleeve_books(aggregate, tagged),
            tagged=tagged,
            snapshots=snapshots,
            equity=run_equity(aggregate, run_members(tagged, runtime)),
        )

    async def start(
        self,
        portfolio_id: UUID,
        *,
        revision: int,
        context: MutationContext,
        live_acknowledged: bool = False,
        sleeve_id: UUID | None = None,
        fees: FeeAssumptions | None = None,
        paper_fee_source: PaperFeeSource | None = None,
    ) -> PortfolioActionResult:
        """Start (or attach) a book for every sleeve, or for one sleeve.

        Every target sleeve is planned first (issues, a strategy busy elsewhere, the
        snapshot, the clock, and the risk policy with every planned book counted), so a
        refused sleeve starts nothing (:class:`PortfolioStartRejectedError`). New paper
        books that omit fee rates take the account's rates from ``paper_fee_source``; an
        unreadable account refuses the start before any book is created.
        """
        aggregate = await self._portfolios.get(portfolio_id)
        require_revision(aggregate.portfolio, revision)
        mode = deployment_mode(aggregate)
        _require_live_acknowledgement(mode, acknowledged=live_acknowledged)
        if mode is DeploymentMode.LIVE and not self._live_allowed:
            raise PortfolioConflictError(
                "live_credentials_missing", "Live trading requires configured Coinbase credentials."
            )
        runtime = await self._portfolios.runtime_state(portfolio_id)
        _require_breaker_clear(runtime)
        if not aggregate.sleeves:
            raise PortfolioValidationError(
                "portfolio_has_no_sleeves", "Add at least one sleeve before starting the portfolio."
            )
        targets = aggregate.sleeves if sleeve_id is None else (aggregate.sleeve(sleeve_id),)
        fee_rates = _fee_rates(mode, fees or FeeAssumptions())
        deployments = await self._execution.list_deployments()
        tagged = members(deployments, portfolio_id)
        attached, planned = await self._plan_start(aggregate, targets, deployments, tagged, mode)
        if planned and mode is DeploymentMode.PAPER:
            fee_rates = await _account_fee_rates(fee_rates, paper_fee_source)
        context = _millisecond(context)
        if planned and not occupied_members(tagged):
            await self._begin_run(aggregate, runtime, now=context.occurred_at)
        outcomes = list(attached)
        outcomes.extend(
            [
                await self._start_sleeve(aggregate, item, mode=mode, fees=fee_rates)
                for item in planned
            ]
        )
        result = PortfolioActionResult(action="start", outcomes=tuple(outcomes))
        await self._journal_action(
            aggregate, result, kind="deployment_started", context=context, sleeve_id=sleeve_id
        )
        return result

    async def pause(
        self,
        portfolio_id: UUID,
        *,
        context: MutationContext,
        sleeve_id: UUID | None = None,
        reason: JournalReason = "operator",
        note: str | None = None,
    ) -> PortfolioActionResult:
        """Pause every running sleeve book (or one sleeve's): no new entries, exits go on."""
        aggregate, books = await self._books(portfolio_id)
        targets = _targets(books, sleeve_id, include_detached=True)
        _require_deployed(targets, sleeve_id=sleeve_id)
        outcomes = []
        for name, view, deployment in targets:
            if deployment is None or deployment.status is DeploymentStatus.STOPPED:
                outcomes.append(_outcome(name, view, deployment, "unchanged", "Not deployed."))
                continue
            if deployment.status is DeploymentStatus.PAUSED:
                outcomes.append(_outcome(name, view, deployment, "unchanged", "Already paused."))
                continue
            outcomes.append(
                await self._set_status(name, view, deployment, DeploymentStatus.PAUSED, "paused")
            )
        result = PortfolioActionResult(action="pause", outcomes=tuple(outcomes))
        await self._journal_action(
            aggregate,
            result,
            kind="deployment_paused",
            context=_millisecond(context),
            sleeve_id=sleeve_id,
            reason=reason,
            note=note,
        )
        return result

    async def resume(
        self,
        portfolio_id: UUID,
        *,
        context: MutationContext,
        live_acknowledged: bool = False,
        sleeve_id: UUID | None = None,
        reason: JournalReason = "operator",
        note: str | None = None,
    ) -> PortfolioActionResult:
        """Resume every paused sleeve book (or one sleeve's). Live needs the acknowledgement."""
        aggregate, books = await self._books(portfolio_id)
        _require_live_acknowledgement(deployment_mode(aggregate), acknowledged=live_acknowledged)
        _require_breaker_clear(await self._portfolios.runtime_state(portfolio_id))
        targets = _targets(books, sleeve_id, include_detached=False)
        _require_deployed(targets, sleeve_id=sleeve_id)
        outcomes = []
        for name, view, deployment in targets:
            if deployment is None or deployment.status is DeploymentStatus.STOPPED:
                outcomes.append(_outcome(name, view, deployment, "unchanged", "Not deployed."))
                continue
            if deployment.status is DeploymentStatus.RUNNING:
                outcomes.append(_outcome(name, view, deployment, "unchanged", "Already running."))
                continue
            outcomes.append(
                await self._set_status(name, view, deployment, DeploymentStatus.RUNNING, "resumed")
            )
        result = PortfolioActionResult(action="resume", outcomes=tuple(outcomes))
        await self._journal_action(
            aggregate,
            result,
            kind="deployment_resumed",
            context=_millisecond(context),
            sleeve_id=sleeve_id,
            reason=reason,
            note=note,
        )
        return result

    async def stop(
        self,
        portfolio_id: UUID,
        *,
        context: MutationContext,
        sleeve_id: UUID | None = None,
        flatten: bool = False,
    ) -> PortfolioActionResult:
        """Stop every occupied sleeve book (or one sleeve's): managed stop, or flatten."""
        aggregate, books = await self._books(portfolio_id)
        targets = _targets(books, sleeve_id, include_detached=True)
        _require_deployed(targets, sleeve_id=sleeve_id)
        outcomes = []
        for name, view, deployment in targets:
            if deployment is None or not occupies_running_slot(deployment):
                outcomes.append(_outcome(name, view, deployment, "unchanged", "Not deployed."))
                continue
            outcomes.append(
                await self._set_status(
                    name, view, deployment, DeploymentStatus.STOPPED, "stopped", flatten=flatten
                )
            )
        result = PortfolioActionResult(action="stop", outcomes=tuple(outcomes))
        await self._journal_action(
            aggregate,
            result,
            kind="deployment_stopped",
            context=_millisecond(context),
            sleeve_id=sleeve_id,
            note="Flatten: exit positions at market, then cancel remainders."
            if flatten
            else "Managed stop: protective orders stay until positions close.",
        )
        return result

    async def reset_breaker(
        self, portfolio_id: UUID, *, context: MutationContext
    ) -> PortfolioRuntimeState:
        """Clear a latched portfolio breaker and re-baseline the day open and the peak.

        Sleeves stay paused: resume them explicitly once the reset is done.
        """
        context = _millisecond(context)
        for _attempt in range(2):
            current = await self.snapshot(portfolio_id)
            runtime = current.runtime
            if not runtime.breaker_latched:
                raise PortfolioConflictError(
                    "portfolio_breaker_not_latched", "No portfolio breaker is latched."
                )
            now = context.occurred_at
            cleared = replace(
                runtime,
                breaker_reason=None,
                breaker_detail=None,
                breaker_latched_at=None,
                day_open_equity=current.equity,
                day_open_at=utc_day_start(now),
                high_water_mark_equity=current.equity,
                last_equity=current.equity,
                last_evaluated_at=now,
            )
            equity = quote_text(
                current.equity.quantize(Decimal("0.01")),
                current.aggregate.portfolio.quote_currency,
            )
            entry = journal_entry(
                portfolio_id,
                kind="breaker_reset",
                context=context,
                summary=(
                    f"Reset the {_breaker_label(runtime)} breaker at {equity}; sleeves stay "
                    "paused until resumed."
                ),
                revision=current.aggregate.portfolio.revision,
                detail=JournalDetail(reason="operator", reason_code=runtime.breaker_reason),
            )
            written = await self._portfolios.write_runtime(
                cleared, expected_revision=runtime.revision, journal=(entry,)
            )
            if written is not None:
                await self._audit_event("portfolio_reset_breaker", portfolio_id, detail="")
                return written
        raise PortfolioConflictError(
            "portfolio_runtime_conflict", "The portfolio's runtime state changed; try again."
        )

    async def _books(self, portfolio_id: UUID) -> tuple[PortfolioAggregate, PortfolioBooks]:
        """The portfolio and its sleeve books."""
        aggregate = await self._portfolios.get(portfolio_id)
        tagged = members(await self._execution.list_deployments(), portfolio_id)
        return aggregate, sleeve_books(aggregate, tagged)

    async def _plan_start(
        self,
        aggregate: PortfolioAggregate,
        targets: Sequence[SleeveView],
        deployments: Sequence[Deployment],
        tagged: Sequence[Deployment],
        mode: DeploymentMode,
    ) -> tuple[tuple[SleeveOutcome, ...], tuple[_PlannedSleeve, ...]]:
        """Attach sleeves already running here; plan the rest; refuse on any problem."""
        attached: list[SleeveOutcome] = []
        planned: list[_PlannedSleeve] = []
        problems: list[StartProblem] = []
        for view in targets:
            mine = next(
                (
                    item
                    for item in tagged
                    if item.strategy_id == view.sleeve.strategy_id and occupies_running_slot(item)
                ),
                None,
            )
            if mine is not None:
                attached.append(
                    _outcome(view.strategy.name, view, mine, "attached", "Already deployed.")
                )
                continue
            outcome = await self._plan_sleeve(aggregate, view, deployments, mode)
            if isinstance(outcome, StartProblem):
                problems.append(outcome)
            else:
                planned.append(outcome)
        problems.extend(await self._admission_problems(planned, deployments, mode))
        if problems:
            raise PortfolioStartRejectedError(tuple(problems))
        return tuple(attached), tuple(planned)

    async def _plan_sleeve(
        self,
        aggregate: PortfolioAggregate,
        view: SleeveView,
        deployments: Sequence[Deployment],
        mode: DeploymentMode,
    ) -> _PlannedSleeve | StartProblem:
        """Check one sleeve and snapshot its strategy's current rules."""
        issues = sleeve_issues(view, aggregate.portfolio.quote_currency)
        if issues:
            return _problem(view, "sleeve_issue", f"Fix the sleeve first: {', '.join(issues)}.")
        busy = next(
            (
                item
                for item in deployments
                if item.strategy_id == view.sleeve.strategy_id
                and item.mode is mode
                and occupies_running_slot(item)
            ),
            None,
        )
        if busy is not None:
            owner = (
                "a standalone bot" if busy.portfolio_id is None else "a sleeve of another portfolio"
            )
            return _problem(
                view,
                "strategy_busy",
                f"“{view.strategy.name}” already runs as {owner} (deployment {busy.id}). Stop "
                "it first, or remove this sleeve.",
            )
        try:
            snapshot = await self._strategies.snapshot(view.sleeve.strategy_id)
        except StrategyLibraryError as error:
            return _problem(view, "strategy_invalid", str(error) or "The strategy cannot start.")
        if not parse_candle_interval(snapshot.definition.timeframe).execution_supported:
            return _problem(
                view,
                "timeframe_not_executable",
                f"{snapshot.definition.timeframe} is not an ingested venue clock.",
            )
        capital = _sleeve_capital(aggregate, view)
        return _PlannedSleeve(view=view, capital=capital, snapshot=snapshot)

    async def _admission_problems(
        self,
        planned: Sequence[_PlannedSleeve],
        deployments: Sequence[Deployment],
        mode: DeploymentMode,
    ) -> tuple[StartProblem, ...]:
        """Run the risk policy over every planned book, counting the ones before it."""
        active = await load_effective_policy(self._risk_store)
        occupied = list(deployments)
        problems: list[StartProblem] = []
        for item in planned:
            definition = item.snapshot.definition
            verdict = evaluate_new_deployment(
                active.definition,
                mode=mode,
                product_id=definition.instrument.product_id,
                product_ids=covered_product_ids(definition),
                strategy_id=definition.strategy_id,
                paper_starting_cash=item.capital if mode is DeploymentMode.PAPER else None,
                deployments=occupied,
                policy_source=active.source,
                portfolio_sleeve=mode is DeploymentMode.LIVE,
            )
            if verdict.decision is RiskDecision.DENY:
                problems.append(
                    _problem(
                        item.view, "risk_policy_refused", f"{verdict.reason_code}: {verdict.detail}"
                    )
                )
                continue
            occupied.append(_hypothetical(item, mode))
        return tuple(problems)

    async def _begin_run(
        self, aggregate: PortfolioAggregate, runtime: PortfolioRuntimeState, *, now: datetime
    ) -> None:
        """Re-baseline the breakers at the portfolio's capital for a fresh run."""
        fresh = begin_run(runtime, equity=Decimal(aggregate.portfolio.capital_quote), now=now)
        written = await self._portfolios.write_runtime(fresh, expected_revision=runtime.revision)
        if written is None:
            raise PortfolioConflictError(
                "portfolio_runtime_conflict", "The portfolio's runtime state changed; try again."
            )

    async def _start_sleeve(
        self,
        aggregate: PortfolioAggregate,
        item: _PlannedSleeve,
        *,
        mode: DeploymentMode,
        fees: tuple[Decimal | None, Decimal | None],
    ) -> SleeveOutcome:
        """Create one sleeve's tagged book."""
        try:
            deployment = await create_deployment(
                store=self._execution,
                publication_store=self._publication,
                strategy_fingerprint=item.snapshot.strategy_fingerprint,
                mode=mode,
                paper_starting_cash=item.capital if mode is DeploymentMode.PAPER else None,
                live_allowed=self._live_allowed,
                risk_store=self._risk_store,
                paper_maker_fee_rate=fees[0],
                paper_taker_fee_rate=fees[1],
                portfolio_sleeve=PortfolioSleeveStart(
                    portfolio_id=aggregate.portfolio.portfolio_id, allocated_capital=item.capital
                ),
                reference_watches=self._reference_watches,
            )
        except (ExecutionConflictError, ExecutionStoreError) as error:
            return _outcome(item.view.strategy.name, item.view, None, "failed", str(error))
        action = "portfolio_start_live" if mode is DeploymentMode.LIVE else "portfolio_start_paper"
        await self._audit_event(
            action, aggregate.portfolio.portfolio_id, detail=_deployment_audit(deployment)
        )
        return _outcome(item.view.strategy.name, item.view, deployment, "started", None)

    async def _set_status(
        self,
        name: str,
        view: SleeveView | None,
        deployment: Deployment,
        status: DeploymentStatus,
        outcome: SleeveOutcomeKind,
        *,
        flatten: bool = False,
    ) -> SleeveOutcome:
        """Apply one single-deployment status change to one sleeve's book."""
        try:
            snapshot = await set_deployment_status(
                store=self._execution, deployment_id=deployment.id, status=status, flatten=flatten
            )
        except (ExecutionConflictError, ExecutionStoreError) as error:
            return _outcome(name, view, deployment, "failed", str(error))
        if deployment.portfolio_id is not None:
            await self._audit_event(
                _AUDIT_ACTIONS[status],
                deployment.portfolio_id,
                detail=_deployment_audit(snapshot.deployment),
            )
        return _outcome(name, view, snapshot.deployment, outcome, None)

    async def _journal_action(
        self,
        aggregate: PortfolioAggregate,
        result: PortfolioActionResult,
        *,
        kind: JournalKind,
        context: MutationContext,
        sleeve_id: UUID | None,
        reason: JournalReason = "operator",
        note: str | None = None,
    ) -> None:
        """Journal one action when it changed at least one book."""
        changed = [item for item in result.outcomes if item.outcome not in {"unchanged", "failed"}]
        failed = [item for item in result.outcomes if item.outcome == "failed"]
        if not changed and not failed:
            return
        first = result.outcomes[0] if sleeve_id is not None and result.outcomes else None
        entry = journal_entry(
            aggregate.portfolio.portfolio_id,
            kind=kind,
            context=context,
            summary=_action_summary(aggregate, result, sleeve_id=sleeve_id),
            revision=aggregate.portfolio.revision,
            detail=JournalDetail(
                sleeve_id=sleeve_id,
                strategy_id=first.strategy_id if first is not None else None,
                strategy_name=first.strategy_name[:120] if first is not None else None,
                reason=reason,
                deployment_ids=tuple(
                    item.deployment_id for item in changed if item.deployment_id is not None
                ),
                note=note,
            ),
        )
        await self._portfolios.append_journal((entry,))

    async def _audit_event(self, action: str, portfolio_id: UUID, *, detail: str) -> None:
        """Record one runtime action; an audit outage never reverses or hides it."""
        if self._audit is None:
            return
        event = AuditEvent(
            occurred_at=datetime.now(UTC),
            category=AuditEventCategory.RUNTIME,
            action=action,
            outcome=AuditEventOutcome.SUCCESS,
            detail=f"portfolio_id={portfolio_id} {detail}".strip(),
        )
        try:
            await self._audit.append(event)
        except Exception as error:  # noqa: BLE001 - the runtime change already committed.
            _logger.warning("portfolio_runtime_audit_failed error=%s", type(error).__name__)


def _require_live_acknowledgement(mode: DeploymentMode, *, acknowledged: bool) -> None:
    """Refuse a live start, resume, or approval without ``i_understand_live``."""
    if mode is DeploymentMode.LIVE and not acknowledged:
        raise PortfolioLiveAcknowledgementError(
            "Live trading spends real money: send i_understand_live=true only after the "
            "operator explicitly acknowledged live trading."
        )


def require_live_acknowledgement(mode: DeploymentMode, *, acknowledged: bool) -> None:
    """Public form of the live acknowledgement check (proposal approvals use it)."""
    _require_live_acknowledgement(mode, acknowledged=acknowledged)


def _require_breaker_clear(runtime: PortfolioRuntimeState) -> None:
    """Refuse starting or resuming while a portfolio breaker is latched."""
    if runtime.breaker_latched:
        raise PortfolioConflictError(
            "portfolio_breaker_latched",
            f"The portfolio's {_breaker_label(runtime)} breaker is latched; reset it "
            "before starting or resuming sleeves.",
        )


def _breaker_label(runtime: PortfolioRuntimeState) -> str:
    """Human name of the latched breaker."""
    if runtime.breaker_reason == "PORTFOLIO_DAILY_LOSS_STOP":
        return "daily loss"
    return "drawdown"


def _fee_rates(mode: DeploymentMode, fees: FeeAssumptions) -> tuple[Decimal | None, Decimal | None]:
    """Validate paper fee assumptions once for every sleeve (live takes none)."""
    try:
        resolve_paper_fee_schedule(
            live=mode is DeploymentMode.LIVE,
            maker_fee_rate=fees.maker_fee_rate,
            taker_fee_rate=fees.taker_fee_rate,
        )
    except ValueError as error:
        raise PortfolioValidationError("portfolio_fee_rates_invalid", str(error)) from None
    return fees.maker_fee_rate, fees.taker_fee_rate


async def _account_fee_rates(
    fees: tuple[Decimal | None, Decimal | None], source: PaperFeeSource | None
) -> tuple[Decimal | None, Decimal | None]:
    """Fill omitted paper rates from the account; unknown account rates refuse the start."""
    try:
        return await paper_fee_rates(maker_fee_rate=fees[0], taker_fee_rate=fees[1], source=source)
    except PaperFeesUnavailableError as error:
        raise PortfolioConflictError("paper_fees_unavailable", str(error)) from None


def _sleeve_capital(aggregate: PortfolioAggregate, view: SleeveView) -> Decimal:
    """Exact sleeve capital: weight times the portfolio's capital."""
    return Decimal(aggregate.portfolio.capital_quote) * Decimal(view.sleeve.weight_fraction)


def _hypothetical(item: _PlannedSleeve, mode: DeploymentMode) -> Deployment:
    """A planned book standing in for admission checks of the sleeves after it."""
    now = utc_now()
    definition = item.snapshot.definition
    return Deployment(
        id=uuid7(now),
        strategy_fingerprint=item.snapshot.strategy_fingerprint,
        strategy_id=definition.strategy_id,
        product_id=definition.instrument.product_id,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=item.capital if mode is DeploymentMode.PAPER else Decimal(0),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        paper_starting_cash=item.capital if mode is DeploymentMode.PAPER else None,
    )


def _problem(view: SleeveView, code: str, message: str) -> StartProblem:
    """One sleeve problem naming the sleeve and its strategy."""
    return StartProblem(
        code=code,
        message=message,
        sleeve_id=view.sleeve.sleeve_id,
        strategy_id=view.sleeve.strategy_id,
        strategy_name=view.strategy.name,
    )


def _targets(
    books: PortfolioBooks, sleeve_id: UUID | None, *, include_detached: bool
) -> tuple[tuple[str, SleeveView | None, Deployment | None], ...]:
    """The books an action applies to: one sleeve's, or every sleeve's (plus detached)."""
    if sleeve_id is not None:
        book = _sleeve_book(books, sleeve_id)
        return ((book.view.strategy.name, book.view, book.deployment),)
    targets: list[tuple[str, SleeveView | None, Deployment | None]] = [
        (book.view.strategy.name, book.view, book.deployment) for book in books.sleeves
    ]
    if include_detached:
        targets.extend(
            (item.strategy_name or "removed sleeve", None, item) for item in books.detached
        )
    return tuple(targets)


def _sleeve_book(books: PortfolioBooks, sleeve_id: UUID) -> SleeveBook:
    """One sleeve's book or the sleeve-not-found error."""
    for book in books.sleeves:
        if book.view.sleeve.sleeve_id == sleeve_id:
            return book
    raise PortfolioSleeveNotFoundError("Sleeve was not found in this portfolio.")


def _require_deployed(
    targets: Sequence[tuple[str, SleeveView | None, Deployment | None]],
    *,
    sleeve_id: UUID | None,
) -> None:
    """Refuse an action on a portfolio (or sleeve) with no running or paused book."""
    if any(item is not None and occupies_running_slot(item) for _name, _view, item in targets):
        return
    if sleeve_id is not None:
        raise PortfolioConflictError(
            "portfolio_sleeve_not_deployed", "This sleeve has no running or paused bot."
        )
    raise PortfolioConflictError("portfolio_not_deployed", NOT_DEPLOYED_MESSAGE)


def _outcome(
    name: str,
    view: SleeveView | None,
    deployment: Deployment | None,
    outcome: SleeveOutcomeKind,
    message: str | None,
) -> SleeveOutcome:
    """One sleeve outcome."""
    return SleeveOutcome(
        sleeve_id=None if view is None else view.sleeve.sleeve_id,
        strategy_id=(
            view.sleeve.strategy_id
            if view is not None
            else (None if deployment is None else deployment.strategy_id)
        ),
        strategy_name=name,
        outcome=outcome,
        deployment_id=None if deployment is None else deployment.id,
        message=message,
    )


_AUDIT_ACTIONS: dict[DeploymentStatus, str] = {
    DeploymentStatus.PAUSED: "portfolio_pause",
    DeploymentStatus.RUNNING: "portfolio_resume",
    DeploymentStatus.STOPPED: "portfolio_stop",
}
_VERBS: dict[PortfolioAction, str] = {
    "start": "Started",
    "pause": "Paused",
    "resume": "Resumed",
    "stop": "Stopped",
}


def _action_summary(
    aggregate: PortfolioAggregate, result: PortfolioActionResult, *, sleeve_id: UUID | None
) -> str:
    """One journal line for a portfolio or sleeve action."""
    verb = _VERBS[result.action]
    counts: dict[str, int] = {}
    for item in result.outcomes:
        counts[item.outcome] = counts.get(item.outcome, 0) + 1
    tally = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
    mode = aggregate.portfolio.mode
    if sleeve_id is not None and result.outcomes:
        return f"{verb} {mode} sleeve “{result.outcomes[0].strategy_name}” ({tally})."
    return f"{verb} {mode} portfolio “{aggregate.portfolio.name}” ({tally})."


def _deployment_audit(deployment: Deployment) -> str:
    """Audit detail without cash, quantities, or secrets."""
    return (
        f"deployment_id={deployment.id} mode={deployment.mode.value} "
        f"status={deployment.status.value} fingerprint={deployment.strategy_fingerprint}"
    )


def _millisecond(context: MutationContext) -> MutationContext:
    """Journal instants at the millisecond a UUIDv7 encodes and PostgreSQL round-trips."""
    return MutationContext(
        actor=context.actor,
        channel=context.channel,
        occurred_at=utc_millisecond(context.occurred_at),
    )


def runtime_journal_entry(
    aggregate: PortfolioAggregate,
    *,
    kind: JournalKind,
    context: MutationContext,
    summary: str,
    detail: JournalDetail,
) -> JournalEntry:
    """A runtime journal entry stamped with the portfolio's current revision."""
    return journal_entry(
        aggregate.portfolio.portfolio_id,
        kind=kind,
        context=_millisecond(context),
        summary=summary,
        revision=aggregate.portfolio.revision,
        detail=detail,
    )
