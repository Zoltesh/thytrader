"""Create and control paper/live deployments of published strategies."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from thytrader.execution.ids import utc_now, uuid7
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
from thytrader.research.multi_timeframe import strategy_requires_htf
from thytrader.risk.gate import evaluate_new_deployment
from thytrader.risk.models import RiskDecision
from thytrader.risk.store import load_effective_policy
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
) -> Deployment:
    """Start one running deployment for an immutable published strategy."""
    _require_mode_prerequisites(mode, paper_starting_cash, live_allowed=live_allowed)
    published = await _load_published(publication_store, strategy_fingerprint)
    definition = published.definition
    _require_executable_definition(mode, definition)
    existing = await store.list_deployments()
    _require_unique_running(existing, strategy_id=definition.strategy_id, mode=mode)
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
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint=published.strategy_fingerprint,
        strategy_id=definition.strategy_id,
        product_id=definition.instrument.product_id,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=paper_starting_cash,
        cash=cash,
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
    )
    return await store.create_deployment(deployment)


async def set_deployment_status(
    *,
    store: ExecutionStore,
    deployment_id: UUID,
    status: DeploymentStatus,
) -> DeploymentSnapshot:
    """Pause, resume, or stop one existing deployment."""
    snapshot = await store.get_deployment(deployment_id)
    current = snapshot.deployment.status
    if current is DeploymentStatus.STOPPED and status is not DeploymentStatus.STOPPED:
        raise ExecutionConflictError("A stopped deployment cannot be resumed.")
    if status is DeploymentStatus.RUNNING and current is DeploymentStatus.RUNNING:
        return snapshot
    updated = with_runtime(
        snapshot.deployment, updated_at=utc_now(), status=status, clear_mismatch=True
    )
    await store.save_deployment(updated)
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


def _require_executable_definition(mode: DeploymentMode, definition: StrategyDefinition) -> None:
    """Reject HTF-filter publications and illegal execution clocks."""
    if strategy_requires_htf(definition):
        raise ExecutionConflictError(
            "Paper and live deployments reject multi-timeframe HTF-filter strategies."
        )
    _require_execution_timeframe(mode, definition.timeframe)


def _require_unique_running(
    existing: tuple[Deployment, ...],
    *,
    strategy_id: UUID,
    mode: DeploymentMode,
) -> None:
    """Keep one running deployment per strategy identity and mode."""
    if any(
        item.strategy_id == strategy_id
        and item.mode is mode
        and item.status is DeploymentStatus.RUNNING
        for item in existing
    ):
        raise ExecutionConflictError(
            "A running deployment already exists for this strategy and mode."
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
        strategy_id=definition.strategy_id,
        paper_starting_cash=paper_starting_cash,
        deployments=deployments,
    )
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(verdict.detail)


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


def parse_decimal(value: str | None) -> Decimal | None:
    """Parse an optional decimal string from an HTTP body."""
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ExecutionConflictError("Cash must be a finite decimal string.") from error
    if not parsed.is_finite():
        raise ExecutionConflictError("Cash must be a finite decimal string.")
    return parsed
