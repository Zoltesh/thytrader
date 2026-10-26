"""The discretionary book operations an agent desk uses, bound per request (ADR 0147, 0148)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.api.live_order_inputs import (
    broker_for_request,
    currency_available,
    unmanaged_base_available,
)
from thytrader.audit_events import AuditEvent, AuditEventCategory, AuditEventOutcome
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.discretionary import place_discretionary_order
from thytrader.execution.service import set_deployment_status
from thytrader.market_data.products import quote_currency as spot_quote_currency
from thytrader.risk.futures_collateral import risk_futures_account_scope
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.models import (
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.agent_desk.models import PendingOpen
    from thytrader.audit_events import AuditEventStore
    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.exchanges.protocols import ExchangeAccount
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
        live_broker: Broker | None = None,
        quote_reader: ExchangeAccount | None = None,
        futures_account: FuturesAccountSnapshotStore | None = None,
        live_allowed: bool = False,
    ) -> None:
        """Bind the request's stores; live desks also need the live broker and venue reader."""
        self._store = store
        self._live_broker = live_broker
        self._quote_reader = quote_reader
        self._futures_account = futures_account
        self._live_allowed = live_allowed
        self._paper_broker = paper_broker
        self._market_data = market_data
        self._risk_store = risk_store
        self._memory_store = memory_store
        self._paper_fee_source = paper_fee_source
        self._audit = audit

    async def snapshot(self, deployment_id: str) -> DeploymentSnapshot:
        """Return one book from a single consistent accounting read (cash and inventory agree)."""
        return await self._store.get_accounting_snapshot(UUID(deployment_id))

    async def open_book(self, request: DiscretionaryOrderRequest) -> DeploymentSnapshot:
        """Place one order on a fresh book in the desk's mode; audit it like any ticket.

        A live order goes through the same path as a live discretionary ticket: the live broker,
        the venue's remaining quote and unclaimed base, and the shared-collateral scope.
        """
        broker = broker_for_request(
            request, paper_broker=self._paper_broker, live_broker=self._live_broker
        )
        currency = spot_quote_currency(request.product_id)
        with execution_audit_scope(self._audit), risk_futures_account_scope(self._futures_account):
            snapshot = await place_discretionary_order(
                store=self._store,
                broker=broker,
                market_data=self._market_data,
                request=request,
                live_allowed=self._live_allowed,
                risk_store=self._risk_store,
                live_quote_cash=await currency_available(
                    self._quote_reader, mode=request.mode, currency=currency
                ),
                live_base_available=await unmanaged_base_available(
                    self._quote_reader,
                    store=self._store,
                    mode=request.mode,
                    product_id=request.product_id,
                ),
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
                    f"deployment_id={snapshot.deployment.id} "
                    f"mode={snapshot.deployment.mode.value} origin=agent "
                    f"idempotency_key={request.idempotency_key}"
                ),
                product_id=request.product_id,
            )
        )
        return snapshot

    async def find_book(self, pending: PendingOpen) -> str | None:
        """The reservation's own book: the pre-chosen id, in the desk's mode, on its product.

        A reservation written before book ids were chosen is found by its order's key.
        """
        if pending.deployment_id is None:
            return await self.intent_book(pending.idempotency_key)
        try:
            snapshot = await self._store.get_deployment(UUID(pending.deployment_id))
        except ExecutionStoreError as error:
            if "not found" in str(error).lower():
                return None
            raise
        deployment = snapshot.deployment
        if deployment.mode.value != pending.mode or deployment.product_id != pending.product_id:
            message = f"Book {pending.deployment_id} does not match its desk reservation."
            raise ExecutionStoreError(message)
        return str(deployment.id)

    async def intent_book(self, idempotency_key: str) -> str | None:
        """The book an order placed under ``idempotency_key`` belongs to, if one was placed."""
        intent = await self._store.get_intent_by_idempotency_key(idempotency_key)
        return None if intent is None else str(intent.deployment_id)

    async def stop_book(
        self, deployment_id: str, *, flatten: bool, expected_revision: int | None = None
    ) -> DeploymentSnapshot:
        """Stop one desk book (managed, or flattening)."""
        return await set_deployment_status(
            store=self._store,
            deployment_id=UUID(deployment_id),
            status=DeploymentStatus.STOPPED,
            publication_store=DisabledStrategySnapshotStore(),
            flatten=flatten,
            expected_revision=expected_revision,
        )

    async def fee_rates(self) -> tuple[Decimal, Decimal] | None:
        """The account's paper rates, or None when they cannot be read."""
        with contextlib.suppress(ExecutionConflictError):
            return await self._paper_fee_source()
        return None
