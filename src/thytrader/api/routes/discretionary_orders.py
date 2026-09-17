"""On-demand long or short entries with required SL/TP through the existing execution path."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_execution_store,
    get_live_broker,
    get_market_data_service,
    get_memory_store,
    get_paper_broker,
    get_quote_reader,
    get_risk_policy_store,
    get_runtime_state,
)
from thytrader.api.routes.deployments import DeploymentResponse, _snapshot_response
from thytrader.exchanges.protocols import ExchangeAccount  # noqa: TC001 - FastAPI Depends.
from thytrader.execution.broker import Broker  # noqa: TC001 - FastAPI Depends.
from thytrader.execution.discretionary import parse_discretionary_request, place_discretionary_order
from thytrader.execution.geometry import base_currency
from thytrader.execution.models import DeploymentMode, ExecutionConflictError, ExecutionStoreError
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.products import (
    SPOT_PRODUCT_ID_PATTERN,
    quote_currency as spot_quote_currency,
)
from thytrader.market_data.service import MarketDataService  # noqa: TC001 - FastAPI Depends.
from thytrader.memory.store import ExperientialMemoryStore  # noqa: TC001 - FastAPI Depends.
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.risk.store import RiskPolicyStore  # noqa: TC001 - FastAPI Depends.
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.execution.discretionary import DiscretionaryOrderRequest

router = APIRouter(prefix="/api/v1/discretionary-orders", tags=["discretionary-orders"])


class PlaceDiscretionaryOrderRequest(BaseModel):
    """Place one on-demand long or short entry with required stop and take-profit."""

    mode: DeploymentMode
    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    stop_price: str
    take_profit_price: str
    origin: str = Field(pattern=r"^(human|agent)$")
    idempotency_key: str = Field(min_length=1, max_length=128)
    entry_kind: str = "post_only_limit"
    timeframe: str = "5m"
    side: str = "long"
    quantity: str | None = None
    quote_notional: str | None = None
    limit_price: str | None = None
    paper_starting_cash: str | None = None
    maker_fee_rate: str | None = None
    taker_fee_rate: str | None = None
    note: str | None = Field(default=None, max_length=4000)


@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def post_discretionary_order(
    body: PlaceDiscretionaryOrderRequest,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    paper_broker: Annotated[Broker, Depends(get_paper_broker)],
    live_broker: Annotated[Broker | None, Depends(get_live_broker)],
    quote_reader: Annotated[ExchangeAccount | None, Depends(get_quote_reader)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    risk_store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    memory_store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
) -> DeploymentResponse:
    """Persist a discretionary intent, submit once, and never retry an ambiguous timeout."""
    try:
        request = parse_discretionary_request(
            mode=body.mode.value,
            product_id=body.product_id,
            entry_kind=body.entry_kind,
            stop_price=body.stop_price,
            take_profit_price=body.take_profit_price,
            origin=body.origin,
            idempotency_key=body.idempotency_key,
            timeframe=body.timeframe,
            side=body.side,
            quantity=body.quantity,
            quote_notional=body.quote_notional,
            limit_price=body.limit_price,
            paper_starting_cash=body.paper_starting_cash,
            paper_maker_fee_rate=body.maker_fee_rate,
            paper_taker_fee_rate=body.taker_fee_rate,
            note=body.note,
        )
        broker = _broker_for_request(request, paper_broker=paper_broker, live_broker=live_broker)
        snapshot = await place_discretionary_order(
            store=store,
            broker=broker,
            market_data=market_data,
            request=request,
            live_allowed=runtime.settings.coinbase_api_key_name is not None,
            risk_store=risk_store,
            live_quote_cash=await _currency_available(
                quote_reader, mode=request.mode, currency=spot_quote_currency(request.product_id)
            ),
            live_base_available=await _currency_available(
                quote_reader,
                mode=request.mode,
                currency=base_currency(request.product_id),
            ),
            memory_store=memory_store,
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    await audit.append(
        AuditEvent(
            occurred_at=snapshot.deployment.updated_at,
            category=AuditEventCategory.RUNTIME,
            action="place_discretionary_order",
            outcome=AuditEventOutcome.SUCCESS,
            detail=(
                f"deployment_id={snapshot.deployment.id} mode={snapshot.deployment.mode.value} "
                f"origin={request.origin.value} idempotency_key={request.idempotency_key}"
            ),
            product_id=request.product_id,
        )
    )
    return await _snapshot_response(snapshot)


def _broker_for_request(
    request: DiscretionaryOrderRequest,
    *,
    paper_broker: Broker,
    live_broker: Broker | None,
) -> Broker:
    """Select the paper or live broker; live still requires credentials."""
    if request.mode is DeploymentMode.PAPER:
        return paper_broker
    if live_broker is None:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    return live_broker


async def _currency_available(
    quote_reader: ExchangeAccount | None, *, mode: DeploymentMode, currency: str
) -> Decimal | None:
    """Return remaining units of one venue currency when a live reader is attached."""
    if mode is not DeploymentMode.LIVE or quote_reader is None:
        return None
    balances: tuple[ExchangeBalance, ...] = await quote_reader.list_balances()
    for balance in balances:
        if balance.currency == currency:
            return balance.available
    return None
