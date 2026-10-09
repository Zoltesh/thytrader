"""Inventory adoption of coins already held in the live Coinbase account (ADR 0124).

``GET /api/v1/inventory-adoptions/preview`` is read-only. It reports the venue balance,
every live book's claims, the adoptable quantity, the mark, and what blocks each action.
``POST /api/v1/inventory-adoptions`` adopts N or all unmanaged coins:

- ``protect`` puts them in a discretionary book that rests a stop and take-profit;
- ``sell`` puts them in a stopped book flagged FLATTEN that the worker sells.

Live only (paper answers 409 ``ADOPTION_LIVE_ONLY``), and live needs the strict
``i_understand_live`` acknowledgement (428 without it). A replayed idempotency key
returns the original book.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, StrictBool, model_validator

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_execution_store,
    get_inventory_adoption_store,
    get_live_broker,
    get_market_data_service,
    get_memory_store,
    get_quote_reader,
    get_risk_policy_store,
)
from thytrader.api.live_ack import require_live_acknowledgement
from thytrader.api.routes.deployment_models import DeploymentResponse
from thytrader.api.routes.deployment_serializers import snapshot_response
from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.decimal_text import canonical_decimal
from thytrader.exchanges.protocols import ExchangeAccount
from thytrader.execution.adoption import parse_adoption_request, preview_adoption
from thytrader.execution.adoption_discretionary import adopt_held_inventory
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.broker import Broker
from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.market_data.products import (
    SPOT_PRODUCT_ID_PATTERN,
    quote_currency as spot_quote_currency,
)
from thytrader.market_data.service import MarketDataService
from thytrader.memory.store import ExperientialMemoryStore
from thytrader.risk.store import RiskPolicyStore
from thytrader.trading.models import DeploymentMode, ExecutionConflictError, ExecutionStoreError
from thytrader.trading.store import ExecutionStore, InventoryAdoptionStore

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.execution.adoption import AdoptionPreview, AdoptionRequest

router = APIRouter(prefix="/api/v1/inventory-adoptions", tags=["inventory-adoptions"])

_DECIMAL = r"^\d+(\.\d+)?$"
_QUANTITY = r"^(all|\d+(\.\d+)?)$"


class InventoryAdoptionRequest(BaseModel):
    """Adopt held coins into a protected book, or into a book the worker sells."""

    mode: DeploymentMode
    action: Literal["protect", "sell"]
    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    quantity: str = Field(
        pattern=_QUANTITY, description="Base quantity as a decimal, or 'all' unmanaged."
    )
    idempotency_key: str = Field(min_length=1, max_length=128)
    origin: Literal["human", "agent"]
    timeframe: str = Field(default="5m", description="Book clock and mark candle interval.")
    stop_price: str | None = Field(default=None, pattern=_DECIMAL)
    take_profit_price: str | None = Field(default=None, pattern=_DECIMAL)
    note: str | None = Field(default=None, max_length=4000)
    i_understand_live: StrictBool = Field(
        default=False,
        description=(
            "Required true for mode=live (HTTP 428 live_acknowledgement_required otherwise). "
            "Send only after the operator explicitly acknowledged live trading."
        ),
    )

    @model_validator(mode="after")
    def require_action_fields(self) -> InventoryAdoptionRequest:
        """Protect needs both levels; sell takes neither; the clock must be supported."""
        if self.timeframe not in EXECUTION_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(EXECUTION_TIMEFRAMES)}")
        levels = (self.stop_price, self.take_profit_price)
        if self.action == "protect" and None in levels:
            raise ValueError("protect requires stop_price and take_profit_price")
        if self.action == "sell" and levels != (None, None):
            raise ValueError("sell takes no stop_price or take_profit_price")
        return self


class AdoptionClaimsResponse(BaseModel):
    """What live books own or will own of the base."""

    managed_long: str
    working_buys: str
    working_short_entry_sells: str
    claimed: str


class AdoptionPreviewResponse(BaseModel):
    """Read-only adoption figures; unknown values are null, never zero."""

    product_id: str
    base_currency: str
    timeframe: str
    mark: str | None
    mark_bar_starts_at: str | None
    base_increment: str | None
    balance_total: str | None
    balance_available: str | None
    claims: AdoptionClaimsResponse | None
    unmanaged: str | None
    adoptable: str | None
    unresolved_reasons: list[str]
    protect_blocking_reasons: list[str]
    sell_blocking_reasons: list[str]


@router.get("/preview", response_model=AdoptionPreviewResponse)
async def get_adoption_preview(
    product_id: Annotated[str, Query(pattern=SPOT_PRODUCT_ID_PATTERN)],
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    quote_reader: Annotated[ExchangeAccount | None, Depends(get_quote_reader)],
    timeframe: Annotated[str, Query()] = "5m",
) -> AdoptionPreviewResponse:
    """Report what an adoption of ``product_id`` would see now, without writing."""
    if timeframe not in EXECUTION_TIMEFRAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"timeframe must be one of {', '.join(EXECUTION_TIMEFRAMES)}",
        )
    try:
        preview = await preview_adoption(
            store=store,
            market_data=market_data,
            read_balances=None if quote_reader is None else quote_reader.list_balances,
            product_id=product_id,
            timeframe=timeframe,
        )
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return _preview_response(preview)


@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def post_inventory_adoption(
    body: InventoryAdoptionRequest,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    adoption_store: Annotated[InventoryAdoptionStore | None, Depends(get_inventory_adoption_store)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    live_broker: Annotated[Broker | None, Depends(get_live_broker)],
    quote_reader: Annotated[ExchangeAccount | None, Depends(get_quote_reader)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    risk_store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    memory_store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
) -> DeploymentResponse:
    """Adopt held coins once; never submits a buy, and a replayed key returns its book."""
    require_live_acknowledgement(body.mode, acknowledged=body.i_understand_live)
    request = _parsed(body)
    if adoption_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Inventory adoption storage is unavailable.",
        )
    try:
        with execution_audit_scope(audit):
            snapshot = await adopt_held_inventory(
                request,
                store=store,
                adoption_store=adoption_store,
                market_data=market_data,
                broker=live_broker,
                read_balances=None if quote_reader is None else quote_reader.list_balances,
                live_quote_cash=await quote_available(quote_reader, body.product_id),
                risk_store=risk_store,
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
            action="post_inventory_adoption",
            outcome=AuditEventOutcome.SUCCESS,
            detail=(
                f"deployment_id={snapshot.deployment.id} action={body.action} "
                f"quantity={body.quantity} origin={body.origin} "
                f"idempotency_key={body.idempotency_key}"
            ),
            product_id=body.product_id,
        )
    )
    return await snapshot_response(snapshot)


def _parsed(body: InventoryAdoptionRequest) -> AdoptionRequest:
    """Validate the request; paper is 409 ``ADOPTION_LIVE_ONLY``."""
    try:
        return parse_adoption_request(
            mode=body.mode.value,
            action=body.action,
            product_id=body.product_id,
            quantity=body.quantity,
            idempotency_key=body.idempotency_key,
            origin=body.origin,
            timeframe=body.timeframe,
            stop_price=body.stop_price,
            take_profit_price=body.take_profit_price,
            note=body.note,
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None


async def quote_available(quote_reader: ExchangeAccount | None, product_id: str) -> Decimal | None:
    """Available quote of the product's currency, for the live capital base.

    An unreadable venue is unknown quote, which the entry gate treats as no capital.
    """
    if quote_reader is None:
        return None
    currency = spot_quote_currency(product_id)
    try:
        balances = await quote_reader.list_balances()
    # Any venue failure leaves the quote unknown; protect is then refused by the gate.
    except Exception:  # noqa: BLE001
        return None
    for balance in balances:
        if balance.currency == currency:
            return balance.available
    return None


def _text(value: Decimal | None) -> str | None:
    """Canonical decimal text, or null for unknown."""
    return None if value is None else canonical_decimal(value)


def _preview_response(preview: AdoptionPreview) -> AdoptionPreviewResponse:
    """Render the preview with exact decimal strings."""
    figures = preview.availability
    claims = None if figures is None else figures.claims
    return AdoptionPreviewResponse(
        product_id=preview.product_id,
        base_currency=preview.base,
        timeframe=preview.timeframe,
        mark=_text(preview.mark),
        mark_bar_starts_at=preview.mark_bar_starts_at,
        base_increment=_text(preview.base_increment),
        balance_total=None if figures is None else _text(figures.total),
        balance_available=None if figures is None else _text(figures.available),
        claims=None
        if claims is None
        else AdoptionClaimsResponse(
            managed_long=canonical_decimal(claims.managed_long),
            working_buys=canonical_decimal(claims.working_buys),
            working_short_entry_sells=canonical_decimal(claims.working_short_entry_sells),
            claimed=canonical_decimal(claims.claimed),
        ),
        unmanaged=None if figures is None else _text(figures.unmanaged),
        adoptable=None if figures is None else _text(figures.adoptable),
        unresolved_reasons=[] if figures is None else [item.value for item in figures.reasons],
        protect_blocking_reasons=list(preview.protect_blocking_reasons),
        sell_blocking_reasons=list(preview.sell_blocking_reasons),
    )
