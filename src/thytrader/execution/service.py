"""Create and control paper/live deployments of strategy snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.ledger import resolve_paper_fee_schedule
from thytrader.execution.lifecycle import command_for_status, occupies_running_slot
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    RuntimePhase,
    with_runtime,
)
from thytrader.execution.paper_fees import paper_fee_rates
from thytrader.market_data.lookback import max_watch_lookback_hours
from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.watchlist import MarketDataWatchlistUnavailableError
from thytrader.risk.gate import evaluate_new_deployment
from thytrader.risk.models import RiskDecision, RiskReasonCode
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.models import covered_product_ids, reference_data_requirements
from thytrader.strategies.snapshots import (
    StrategySnapshot,
    StrategySnapshotError,
    StrategySnapshotReader,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.watchlist import MarketDataWatchlistStore
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.strategies.models import ReferenceDataRequirement, StrategyDefinition


async def create_deployment(
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotReader,
    strategy_fingerprint: str,
    mode: DeploymentMode,
    paper_starting_cash: Decimal | None,
    live_allowed: bool,
    risk_store: RiskPolicyStore | None = None,
    paper_maker_fee_rate: Decimal | None = None,
    paper_taker_fee_rate: Decimal | None = None,
    portfolio_sleeve: PortfolioSleeveStart | None = None,
    reference_watches: ReferenceWatchlist | None = None,
    paper_fee_source: PaperFeeSource | None = None,
) -> Deployment:
    """Start one running deployment for one exact strategy snapshot.

    Callers snapshot the strategy's current (valid) definition first; the book
    records that fingerprint and the strategy name so its rules stay exact even
    after later edits or deletion (ADR 0082). ``portfolio_sleeve`` starts the book as
    a sleeve of a deployed portfolio: it is tagged with the portfolio, its allocated
    capital is the sleeve's weight times the portfolio's capital, and on live that
    allocation counts as risk-policy allocation membership (ADR 0091). A strategy that
    reads reference instruments (ADR 0096) starts only when every reference series is on
    the enabled market-data watchlist (``reference_watches``); otherwise the start is
    refused with the ``thytrader-data watch-add`` command for each missing series. A
    paper start that omits both fee rates takes the account's rates from
    ``paper_fee_source`` and is refused when they cannot be read.
    """
    _require_mode_prerequisites(mode, paper_starting_cash, live_allowed=live_allowed)
    if mode is DeploymentMode.PAPER:
        paper_maker_fee_rate, paper_taker_fee_rate = await paper_fee_rates(
            maker_fee_rate=paper_maker_fee_rate,
            taker_fee_rate=paper_taker_fee_rate,
            source=paper_fee_source,
        )
    maker_fee_rate, taker_fee_rate = _paper_fee_schedule(
        mode, paper_maker_fee_rate, paper_taker_fee_rate
    )
    published = await _load_published(publication_store, strategy_fingerprint)
    definition = published.definition
    _require_executable_definition(mode, definition)
    await _require_reference_watches(reference_watches, definition)
    existing = await store.list_deployments()
    _require_unique_active(existing, strategy_id=definition.strategy_id, mode=mode)
    await _require_risk_admission(
        risk_store,
        mode=mode,
        definition=definition,
        paper_starting_cash=paper_starting_cash,
        deployments=existing,
        portfolio_sleeve=portfolio_sleeve is not None,
    )
    now = utc_now()
    cash = paper_starting_cash if mode is DeploymentMode.PAPER else Decimal("0")
    if cash is None:
        cash = Decimal("0")
    if portfolio_sleeve is not None:
        allocated: Decimal | None = portfolio_sleeve.allocated_capital
    else:
        allocated = await _opening_allocation(risk_store, strategy_id=definition.strategy_id)
    # A new live fill ledger starts at zero, independently of its allocated capital.
    initial = cash
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=published.strategy_fingerprint,
        strategy_id=definition.strategy_id,
        strategy_name=definition.name,
        product_id=definition.instrument.product_id,
        timeframe=definition.timeframe,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=paper_starting_cash,
        paper_maker_fee_rate=maker_fee_rate,
        paper_taker_fee_rate=taker_fee_rate,
        cash=cash,
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        allocated_capital=allocated,
        performance_capital_quote=(cash if mode is DeploymentMode.PAPER else allocated),
        initial_equity=initial,
        baseline_equity=initial,
        high_water_mark_equity=initial,
        utc_day_open_equity=initial,
        utc_day_open_at=now if initial is not None else None,
        portfolio_id=None if portfolio_sleeve is None else portfolio_sleeve.portfolio_id,
    )
    return await store.create_deployment(deployment)


@dataclass(frozen=True, slots=True)
class ReferenceWatchlist:
    """The market-data watchlist a reference-instrument deployment must be watched on.

    ``provider`` is the ingestion provider the market-data worker uses
    (``coinbase`` with credentials, otherwise ``demo``).
    """

    store: MarketDataWatchlistStore
    provider: str


async def _require_reference_watches(
    watches: ReferenceWatchlist | None, definition: StrategyDefinition
) -> None:
    """Refuse a start whose reference series are not on the enabled watchlist (ADR 0096).

    Paper and live read every reference series each cycle and skip entries when one is
    stale. Requiring an enabled watch keeps each series ingested and visible in the data
    catalog's freshness. The runtime lane never adds a watch itself: data mutations stay
    in the confirmation-gated data lane, so the message names the exact command.
    """
    requirements = reference_data_requirements(definition)
    if not requirements:
        return
    if watches is None:
        raise ExecutionConflictError(
            "This strategy reads reference instruments, but the market-data watchlist is "
            "unavailable to confirm they are watched. Nothing was started."
        )
    missing: list[ReferenceDataRequirement] = []
    for requirement in requirements:
        interval = parse_candle_interval(requirement.timeframe)
        try:
            target = await watches.store.get(watches.provider, requirement.product_id, interval)
        except MarketDataWatchlistUnavailableError as error:
            raise ExecutionConflictError(
                "The market-data watchlist is unavailable, so the reference instruments cannot "
                "be confirmed as watched. Nothing was started; retry the start."
            ) from error
        if target is None or not target.enabled:
            missing.append(requirement)
    if not missing:
        return
    series = ", ".join(
        f"{item.reference_id} ({item.product_id} {item.timeframe})" for item in missing
    )
    commands = "; ".join(
        f"`uv run thytrader-data watch-add --product-id {item.product_id} --timeframe "
        f"{item.timeframe} --lookback-hours {reference_watch_lookback_hours(item)} --confirm`"
        for item in missing
    )
    raise ExecutionConflictError(
        f"Reference instrument series {series} must be on the enabled market-data watchlist "
        f"before this strategy can run. Nothing was started. Watch each, then retry: {commands}."
    )


def reference_watch_lookback_hours(requirement: ReferenceDataRequirement) -> int:
    """Suggest a watch lookback covering one reference's warmup plus one bar (at least 7 days).

    Capped at the interval's maximum watch lookback.
    """
    interval = parse_candle_interval(requirement.timeframe)
    hours = math.ceil((requirement.warmup_bars + 1) * interval.duration.total_seconds() / 3600)
    return min(max(hours, 168), max_watch_lookback_hours(interval))


@dataclass(frozen=True, slots=True)
class PortfolioSleeveStart:
    """Start a book as one sleeve of a deployed portfolio (ADR 0091)."""

    portfolio_id: UUID
    allocated_capital: Decimal


async def set_deployment_status(
    *,
    store: ExecutionStore,
    deployment_id: UUID,
    status: DeploymentStatus,
    flatten: bool = False,
) -> DeploymentSnapshot:
    """Pause, resume, or stop one existing deployment.

    HTTP stop defaults to managed shutdown: protective brackets stay, residual
    exposure remains in account-level risk. Pass ``flatten=True`` to marketably
    exit and then cancel remainders.
    """
    snapshot = await store.get_deployment(deployment_id)
    current = snapshot.deployment.status
    if current is DeploymentStatus.STOPPED and status is not DeploymentStatus.STOPPED:
        raise ExecutionConflictError("A stopped deployment cannot be resumed.")
    if status is DeploymentStatus.RUNNING and current is DeploymentStatus.RUNNING:
        return snapshot
    command = command_for_status(status, flatten=flatten)
    updated = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=status,
        lifecycle_command=command,
        clear_mismatch=True,
    )
    await store.save_deployment(updated, expected_revision=snapshot.deployment.revision)
    return await store.get_deployment(deployment_id)


async def reset_breaker_latches(
    *,
    store: ExecutionStore,
    deployment_id: UUID,
) -> DeploymentSnapshot:
    """Clear latched daily-loss and drawdown breakers after explicit operator reset."""
    snapshot = await store.get_deployment(deployment_id)
    deployment = snapshot.deployment
    if not deployment.daily_loss_latched and not deployment.drawdown_latched:
        raise ExecutionConflictError("No breaker latches are set on this deployment.")
    mismatch = deployment.mismatch_detail
    clear_breaker_mismatch = False
    if mismatch is not None:
        breaker_prefixes = (
            f"{RiskReasonCode.DAILY_LOSS_LIMIT.value}:",
            f"{RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT.value}:",
        )
        clear_breaker_mismatch = mismatch.startswith(breaker_prefixes)
    updated = with_runtime(
        deployment,
        updated_at=utc_now(),
        daily_loss_latched=False,
        drawdown_latched=False,
        clear_mismatch=clear_breaker_mismatch,
    )
    await store.save_deployment(updated, expected_revision=deployment.revision)
    return await store.get_deployment(deployment_id)


def _require_mode_prerequisites(
    mode: DeploymentMode,
    paper_starting_cash: Decimal | None,
    *,
    live_allowed: bool,
) -> None:
    """Reject live without credentials and paper without positive cash."""
    if mode is DeploymentMode.LIVE and not live_allowed:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    if mode is DeploymentMode.PAPER and (paper_starting_cash is None or paper_starting_cash <= 0):
        raise ExecutionConflictError("Paper deployments require a positive starting cash amount.")


def _paper_fee_schedule(
    mode: DeploymentMode,
    maker_fee_rate: Decimal | None,
    taker_fee_rate: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Bind documented paper fee assumptions; live stores none."""
    try:
        return resolve_paper_fee_schedule(
            live=mode is DeploymentMode.LIVE,
            maker_fee_rate=maker_fee_rate,
            taker_fee_rate=taker_fee_rate,
        )
    except ValueError as error:
        raise ExecutionConflictError(str(error)) from error


def _require_executable_definition(mode: DeploymentMode, definition: StrategyDefinition) -> None:
    """Reject illegal execution clocks. HTF-filter publications are executable."""
    _require_execution_timeframe(mode, definition.timeframe)


def _require_unique_active(
    existing: tuple[Deployment, ...],
    *,
    strategy_id: UUID,
    mode: DeploymentMode,
) -> None:
    """Keep one running or paused deployment per strategy identity and mode."""
    if any(
        item.strategy_id == strategy_id and item.mode is mode and occupies_running_slot(item)
        for item in existing
    ):
        raise ExecutionConflictError(
            "A running or paused deployment already exists for this strategy and mode."
        )


async def _require_risk_admission(
    risk_store: RiskPolicyStore | None,
    *,
    mode: DeploymentMode,
    definition: StrategyDefinition,
    paper_starting_cash: Decimal | None,
    deployments: tuple[Deployment, ...],
    portfolio_sleeve: bool = False,
) -> None:
    """Fail closed when the active risk policy rejects this deployment."""
    active = await load_effective_policy(risk_store)
    verdict = evaluate_new_deployment(
        active.definition,
        mode=mode,
        product_id=definition.instrument.product_id,
        product_ids=covered_product_ids(definition),
        strategy_id=definition.strategy_id,
        paper_starting_cash=paper_starting_cash,
        deployments=deployments,
        policy_source=active.source,
        portfolio_sleeve=portfolio_sleeve and mode is DeploymentMode.LIVE,
    )
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(verdict.detail)


async def _opening_allocation(
    risk_store: RiskPolicyStore | None, *, strategy_id: UUID
) -> Decimal | None:
    """Return the reserved quote for this strategy, if the published policy lists one."""
    if risk_store is None:
        return None
    active = await load_effective_policy(risk_store)
    match = next(
        (item for item in active.definition.allocations if item.strategy_id == strategy_id),
        None,
    )
    if match is None:
        return None
    return Decimal(match.allocated_quote)


async def _load_published(
    publication_store: StrategySnapshotReader,
    strategy_fingerprint: str,
) -> StrategySnapshot:
    """Load one published strategy through the optional load contract."""
    loader = getattr(publication_store, "load", None)
    if loader is None:
        raise ExecutionStoreError("Strategy publication store cannot load fingerprints.")
    try:
        return await loader(strategy_fingerprint)
    except StrategySnapshotError as error:
        raise ExecutionStoreError(str(error) or "Published strategy was not found.") from error


async def resolved_deployment_timeframe(
    deployment: Deployment,
    publication_store: StrategySnapshotReader,
) -> str | None:
    """Return the stored book clock or the published strategy clock for observability."""
    if deployment.timeframe is not None:
        return deployment.timeframe
    if deployment.strategy_fingerprint is None:
        return None
    try:
        published = await _load_published(publication_store, deployment.strategy_fingerprint)
    except ExecutionStoreError:
        return None
    return published.definition.timeframe


def _require_execution_timeframe(mode: DeploymentMode, timeframe: str) -> None:
    """Allow paper and live on every ingested venue clock."""
    del mode
    interval = parse_candle_interval(timeframe)
    if not interval.execution_supported:
        raise ExecutionConflictError(
            "Paper and live deployments require an ingested venue timeframe."
        )


def parse_decimal(value: str | None, *, field: str = "Cash") -> Decimal | None:
    """Parse an optional decimal string from an HTTP body."""
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ExecutionConflictError(f"{field} must be a finite decimal string.") from error
    if not parsed.is_finite():
        raise ExecutionConflictError(f"{field} must be a finite decimal string.")
    return parsed
