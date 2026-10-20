"""The discretionary paper book operations an agent desk uses, bound per request (ADR 0147)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.audit_events import AuditEvent, AuditEventCategory, AuditEventOutcome
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.discretionary import place_discretionary_order
from thytrader.execution.service import set_deployment_status
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.models import DeploymentStatus, ExecutionConflictError

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.audit_events import AuditEventStore
    from thytrader.execution.broker import Broker
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


class ApiDeskExecution:
    """Paper discretionary books through the same placement path as the order API."""

    def __init__(
        self,
        *,
        store: ExecutionStore,
        paper_broker: Broker,
        market_data: MarketDataService,
        risk_store: RiskPolicyStore,
        memory_store: ExperientialMemoryStore,
        paper_fee_source: PaperFeeSource,
        audit: AuditEventStore,
    ) -> None:
        """Bind the request's stores."""
        self._store = store
        self._paper_broker = paper_broker
        self._market_data = market_data
        self._risk_store = risk_store
        self._memory_store = memory_store
        self._paper_fee_source = paper_fee_source
        self._audit = audit

    async def snapshot(self, deployment_id: str) -> DeploymentSnapshot:
        """Return one book."""
        return await self._store.get_deployment(UUID(deployment_id))

    async def open_book(self, request: DiscretionaryOrderRequest) -> DeploymentSnapshot:
        """Place one paper order on a fresh book and audit it like any discretionary order."""
        with execution_audit_scope(self._audit):
            snapshot = await place_discretionary_order(
                store=self._store,
                broker=self._paper_broker,
                market_data=self._market_data,
                request=request,
                live_allowed=False,
                risk_store=self._risk_store,
                memory_store=self._memory_store,
                paper_fee_source=self._paper_fee_source,
            )
        await self._audit.append(
            AuditEvent(
                occurred_at=snapshot.deployment.updated_at,
                category=AuditEventCategory.RUNTIME,
                action="place_discretionary_order",
                outcome=AuditEventOutcome.SUCCESS,
                detail=(
                    f"deployment_id={snapshot.deployment.id} mode=paper origin=agent "
                    f"idempotency_key={request.idempotency_key}"
                ),
                product_id=request.product_id,
            )
        )
        return snapshot

    async def stop_book(self, deployment_id: str, *, flatten: bool) -> DeploymentSnapshot:
        """Stop one desk book (managed, or flattening)."""
        return await set_deployment_status(
            store=self._store,
            deployment_id=UUID(deployment_id),
            status=DeploymentStatus.STOPPED,
            publication_store=DisabledStrategySnapshotStore(),
            flatten=flatten,
        )

    async def fee_rates(self) -> tuple[Decimal, Decimal] | None:
        """The account's paper rates, or None when they cannot be read."""
        with contextlib.suppress(ExecutionConflictError):
            return await self._paper_fee_source()
        return None
