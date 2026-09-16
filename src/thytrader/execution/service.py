"""Create and control paper/live deployments of published strategies."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
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
from thytrader.market_data.models import parse_candle_interval
from thytrader.risk.gate import evaluate_new_deployment
from thytrader.risk.models import RiskDecision
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.models import covered_product_ids
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyPublicationError,
    StrategyPublicationStore,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.store import ExecutionStore
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.strategies.models import StrategyDefinition


async def create_deployment(
    *,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
    strategy_fingerprint: str,
    mode: DeploymentMode,
    paper_starting_cash: Decimal | None,
    live_allowed: bool,
    risk_store: RiskPolicyStore | None = None,
    paper_maker_fee_rate: Decimal | None = None,
    paper_taker_fee_rate: Decimal | None = None,
) -> Deployment:
    """Start one running deployment for an immutable published strategy."""
    _require_mode_prerequisites(mode, paper_starting_cash, live_allowed=live_allowed)
    maker_fee_rate, taker_fee_rate = _paper_fee_schedule(
        mode, paper_maker_fee_rate, paper_taker_fee_rate
    )
    published = await _load_published(publication_store, strategy_fingerprint)
    definition = published.definition
    _require_executable_definition(mode, definition)
    existing = await store.list_deployments()
    _require_unique_active(existing, strategy_id=definition.strategy_id, mode=mode)
    await _require_risk_admission(
        risk_store,
        mode=mode,
        definition=definition,
        paper_starting_cash=paper_starting_cash,
        deployments=existing,
    )
    now = utc_now()
    cash = paper_starting_cash if mode is DeploymentMode.PAPER else Decimal("0")
    if cash is None:
        cash = Decimal("0")
    allocated = await _opening_allocation(risk_store, strategy_id=definition.strategy_id)
    initial = paper_starting_cash if mode is DeploymentMode.PAPER else None
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=published.strategy_fingerprint,
        strategy_id=definition.strategy_id,
        product_id=definition.instrument.product_id,
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
        initial_equity=initial,
        baseline_equity=initial,
        high_water_mark_equity=initial,
        utc_day_open_equity=initial,
        utc_day_open_at=now if initial is not None else None,
    )
    return await store.create_deployment(deployment)


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
    publication_store: StrategyPublicationStore,
    strategy_fingerprint: str,
) -> PublishedStrategy:
    """Load one published strategy through the optional load contract."""
    loader = getattr(publication_store, "load", None)
    if loader is None:
        raise ExecutionStoreError("Strategy publication store cannot load fingerprints.")
    try:
        return await loader(strategy_fingerprint)
    except StrategyPublicationError as error:
        raise ExecutionStoreError(str(error) or "Published strategy was not found.") from error


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
