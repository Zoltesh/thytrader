"""Exact CFM quantities and dormant request validation, never venue authority."""

from __future__ import annotations

from decimal import Decimal, DecimalException, Inexact, localcontext
from enum import StrEnum
import re
from typing import TYPE_CHECKING

from thytrader.execution.broker import BrokerError
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.models import IntentPurpose, OrderKind, OrderSide

if TYPE_CHECKING:
    from thytrader.execution.broker import ContractSizeSource


class FuturesReductionMode(StrEnum):
    """Explicit dormant candidates; neither selection proves venue acceptance."""

    CLOSE_POSITION = "close_position"
    REDUCE_ONLY_MARKET = "reduce_only_market"


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CLIENT = re.compile(rf"{_UUID}:([a-z_]+):[0-9]{{8}}T[0-9]{{4}}:{_UUID}")
_REDUCTIONS = frozenset(
    {
        IntentPurpose.STOP,
        IntentPurpose.TAKE_PROFIT,
        IntentPurpose.TIME_EXIT,
        IntentPurpose.SIGNAL_EXIT,
        IntentPurpose.LIQUIDATION,
        IntentPurpose.BRACKET,
    }
)


def request_refusal(
    *,
    client_order_id: str,
    product_id: str,
    side: OrderSide,
    kind: OrderKind,
    price: Decimal | None,
    stop_trigger_price: Decimal | None,
    take_profit_price: Decimal | None,
) -> str | None:
    """Validate intent semantics before requests; client ids use submit_intent's exact shape.

    Broker has no purpose parameter. Do not add a caller-supplied override: the persisted
    intent's purpose encoded by submit_intent is the only accepted reduction discriminator.
    This is defense in depth, not authentication or a replacement for runtime exclusivity.
    """
    if not isinstance(product_id, str) or not is_futures_product_id(product_id):
        return "FUTURES_PRODUCT_UNSUPPORTED"
    if not isinstance(client_order_id, str):
        return "FUTURES_INTENT_INVALID"
    match = _CLIENT.fullmatch(client_order_id)
    if match is None:
        return "FUTURES_INTENT_INVALID"
    try:
        purpose = IntentPurpose(match[1])
    except ValueError:
        return "FUTURES_INTENT_INVALID"
    if not isinstance(side, OrderSide) or not _allowed_kind(kind, purpose):
        return "FUTURES_ORDER_KIND_UNSUPPORTED"
    if take_profit_price is not None or (
        kind in {OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE} and stop_trigger_price is not None
    ):
        return "FUTURES_ATTACHMENT_UNVERIFIED"
    if kind is not OrderKind.MARKETABLE and not positive(price):
        return "FUTURES_PRICE_INVALID"
    if kind in {OrderKind.STOP_LIMIT, OrderKind.TRIGGER_BRACKET} and not positive(
        stop_trigger_price
    ):
        return "FUTURES_PRICE_INVALID"
    return None


def _allowed_kind(kind: OrderKind, purpose: IntentPurpose) -> bool:
    """No market entry or adoption; protection requires its matching exit purpose."""
    if kind is OrderKind.POST_ONLY_LIMIT:
        return purpose in {IntentPurpose.ENTRY, IntentPurpose.TAKE_PROFIT}
    if kind is OrderKind.MARKETABLE:
        return purpose in _REDUCTIONS
    if kind is OrderKind.STOP_LIMIT:
        return purpose is IntentPurpose.STOP
    if kind is OrderKind.TRIGGER_BRACKET:
        return purpose is IntentPurpose.BRACKET
    return False


def positive(value: Decimal | None) -> bool:
    """Reject nonfinite or non-Decimal monetary values before comparisons."""
    return isinstance(value, Decimal) and value.is_finite() and value > 0


async def require_contract_size(source: ContractSizeSource, product_id: str) -> Decimal:
    """Fail closed on unavailable, invalid or non-futures catalog observations."""
    if not is_futures_product_id(product_id):
        raise BrokerError("FUTURES_PRODUCT_UNSUPPORTED")
    try:
        size = await source.contract_size(product_id)
    except (OSError, TimeoutError, ValueError, BrokerError) as error:
        raise BrokerError("FUTURES_CONTRACT_SIZE_UNKNOWN") from error
    if size is None or not positive(size):
        raise BrokerError("FUTURES_CONTRACT_SIZE_UNKNOWN")
    return size


def to_contracts(quantity: Decimal, size: Decimal) -> Decimal:
    """Require an exact positive integral quotient, even with caller traps disabled."""
    if not positive(quantity) or not positive(size):
        raise BrokerError("FUTURES_FRACTIONAL_CONTRACTS")
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            contracts = quantity / size
    except DecimalException as error:
        raise BrokerError("FUTURES_FRACTIONAL_CONTRACTS") from error
    if not positive(contracts) or contracts != contracts.to_integral_value():
        raise BrokerError("FUTURES_FRACTIONAL_CONTRACTS")
    return contracts.to_integral_value()


def to_base(contracts: Decimal, size: Decimal, *, allow_zero: bool = False) -> Decimal:
    """Convert whole venue contracts exactly; never silently round ledger quantity."""
    if (
        not contracts.is_finite()
        or contracts < 0
        or (contracts == 0 and not allow_zero)
        or contracts != contracts.to_integral_value()
        or not positive(size)
    ):
        raise BrokerError("Invalid futures contract count or size.")
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            quantity = contracts * size
    except DecimalException as error:
        raise BrokerError("Inexact futures base quantity.") from error
    if not quantity.is_finite():
        raise BrokerError("Invalid futures base quantity.")
    return quantity
