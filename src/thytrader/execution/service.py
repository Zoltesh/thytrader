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
from thytrader.strategies.publication import (
    PublishedStrategy,
    StrategyPublicationError,
    StrategyPublicationStore,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.store import ExecutionStore


async def create_deployment(
    *,
    store: ExecutionStore,
    publication_store: StrategyPublicationStore,
    strategy_fingerprint: str,
    mode: DeploymentMode,
    paper_starting_cash: Decimal | None,
    live_allowed: bool,
) -> Deployment:
    """Start one running deployment for an immutable published strategy."""
    if mode is DeploymentMode.LIVE and not live_allowed:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    if mode is DeploymentMode.PAPER and (paper_starting_cash is None or paper_starting_cash <= 0):
        raise ExecutionConflictError("Paper deployments require a positive starting cash amount.")
    published = await _load_published(publication_store, strategy_fingerprint)
    definition = published.definition
    existing = await store.list_by_strategy(str(definition.strategy_id))
    if any(item.mode is mode and item.status is DeploymentStatus.RUNNING for item in existing):
        raise ExecutionConflictError(
            "A running deployment already exists for this strategy and mode."
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
