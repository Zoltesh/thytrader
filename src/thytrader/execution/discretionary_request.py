"""Parse and validate on-demand discretionary order requests.

Turns the API's or CLI's decimal strings into one frozen ``DiscretionaryOrderRequest``
and rejects illegal combinations (mode, entry kind, origin, venue timeframe, size,
limit, paper cash and fee rates) before any risk check or persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from thytrader.market_data.models import EXECUTION_TIMEFRAMES, parse_candle_interval
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.trading.geometry import parse_position_side
from thytrader.trading.ledger import resolve_paper_fee_schedule
from thytrader.trading.models import (
    DeploymentMode,
    ExecutionConflictError,
    IntentOrigin,
    OrderKind,
    PositionSide,
)

_PRODUCT = re.compile(SPOT_PRODUCT_ID_PATTERN)


@dataclass(frozen=True, slots=True)
class DiscretionaryOrderRequest:
    """One validated on-demand long or short the API or CLI wants to rest."""

    mode: DeploymentMode
    product_id: str
    entry_kind: OrderKind
    stop_price: Decimal
    take_profit_price: Decimal
    origin: IntentOrigin
    idempotency_key: str
    timeframe: str
    side: PositionSide
    quantity: Decimal | None
    quote_notional: Decimal | None
    limit_price: Decimal | None
    paper_starting_cash: Decimal | None
    paper_maker_fee_rate: Decimal | None
    paper_taker_fee_rate: Decimal | None
    note: str | None = None


def parse_discretionary_request(
    *,
    mode: str,
    product_id: str,
    entry_kind: str,
    stop_price: str,
    take_profit_price: str,
    origin: str,
    idempotency_key: str,
    timeframe: str = "5m",
    side: str = "long",
    quantity: str | None = None,
    quote_notional: str | None = None,
    limit_price: str | None = None,
    paper_starting_cash: str | None = None,
    paper_maker_fee_rate: str | None = None,
    paper_taker_fee_rate: str | None = None,
    note: str | None = None,
) -> DiscretionaryOrderRequest:
    """Parse decimal strings and reject illegal combinations before risk or persist."""
    parsed_mode = _parse_mode(mode)
    parsed_kind = _parse_entry_kind(entry_kind)
    parsed_origin = _parse_origin(origin)
    parsed_timeframe = _parse_timeframe(timeframe)
    try:
        parsed_side = parse_position_side(side)
    except ValueError as error:
        raise ExecutionConflictError(str(error)) from error
    if not _PRODUCT.match(product_id):
        raise ExecutionConflictError("product_id must be a BASE-USD or BASE-USDC spot id.")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ExecutionConflictError("idempotency_key must be 1-128 characters.")
    qty = _optional_positive_decimal(quantity, field="quantity")
    notional = _optional_positive_decimal(quote_notional, field="quote_notional")
    if (qty is None) == (notional is None):
        raise ExecutionConflictError("Provide exactly one of quantity or quote_notional.")
    limit = _optional_positive_decimal(limit_price, field="limit_price")
    if parsed_kind is OrderKind.POST_ONLY_LIMIT and limit is None:
        raise ExecutionConflictError("Post-only entries require limit_price.")
    cash = _optional_positive_decimal(paper_starting_cash, field="paper_starting_cash")
    if parsed_mode is DeploymentMode.LIVE and cash is not None:
        raise ExecutionConflictError("Live orders do not accept paper_starting_cash.")
    maker, taker = _parse_paper_fee_rates(
        mode=parsed_mode,
        maker_fee_rate=paper_maker_fee_rate,
        taker_fee_rate=paper_taker_fee_rate,
    )
    return DiscretionaryOrderRequest(
        mode=parsed_mode,
        product_id=product_id,
        entry_kind=parsed_kind,
        stop_price=_require_positive_decimal(stop_price, field="stop_price"),
        take_profit_price=_require_positive_decimal(take_profit_price, field="take_profit_price"),
        origin=parsed_origin,
        idempotency_key=idempotency_key,
        timeframe=parsed_timeframe,
        side=parsed_side,
        quantity=qty,
        quote_notional=notional,
        limit_price=limit,
        paper_starting_cash=cash,
        paper_maker_fee_rate=maker,
        paper_taker_fee_rate=taker,
        note=_optional_note(note),
    )


def _parse_mode(value: str) -> DeploymentMode:
    """Parse paper or live."""
    try:
        return DeploymentMode(value)
    except ValueError as error:
        raise ExecutionConflictError("mode must be paper or live.") from error


def _parse_entry_kind(value: str) -> OrderKind:
    """Parse maker or marketable entry style."""
    try:
        kind = OrderKind(value)
    except ValueError as error:
        raise ExecutionConflictError("entry_kind must be post_only_limit or marketable.") from error
    if kind not in {OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE}:
        raise ExecutionConflictError("entry_kind must be post_only_limit or marketable.")
    return kind


def _parse_origin(value: str) -> IntentOrigin:
    """Parse human or agent origin."""
    try:
        origin = IntentOrigin(value)
    except ValueError as error:
        raise ExecutionConflictError("origin must be human or agent.") from error
    if origin is IntentOrigin.RUNTIME:
        raise ExecutionConflictError("origin must be human or agent.")
    return origin


def _parse_timeframe(value: str) -> str:
    """Allow only ingested venue execution clocks."""
    allowed = ", ".join(EXECUTION_TIMEFRAMES)
    try:
        interval = parse_candle_interval(value)
    except ValueError as error:
        raise ExecutionConflictError(
            f"Discretionary orders require an ingested venue timeframe: {allowed}."
        ) from error
    if not interval.execution_supported:
        raise ExecutionConflictError(
            f"Discretionary orders require an ingested venue timeframe: {allowed}."
        )
    return interval.value


def _require_positive_decimal(value: str, *, field: str) -> Decimal:
    """Parse a required positive finite decimal string."""
    parsed = _parse_decimal(value, field=field)
    if parsed <= 0:
        raise ExecutionConflictError(f"{field} must be a positive decimal string.")
    return parsed


def _parse_paper_fee_rates(
    *,
    mode: DeploymentMode,
    maker_fee_rate: str | None,
    taker_fee_rate: str | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Parse optional paper fee strings without inventing live venue rates.

    Omitted paper rates stay unset so a new book can default and a reused book
    can keep its stored assumptions.
    """
    maker = _optional_non_negative_decimal(maker_fee_rate, field="maker_fee_rate")
    taker = _optional_non_negative_decimal(taker_fee_rate, field="taker_fee_rate")
    live = mode is DeploymentMode.LIVE
    if not live and (maker is None) != (taker is None):
        raise ExecutionConflictError(
            "Paper fee rates require both maker_fee_rate and taker_fee_rate."
        )
    if not live and (maker is None or taker is None):
        return None, None
    try:
        resolve_paper_fee_schedule(live=live, maker_fee_rate=maker, taker_fee_rate=taker)
    except ValueError as error:
        raise ExecutionConflictError(str(error)) from error
    if live:
        return None, None
    return maker, taker


def _optional_non_negative_decimal(value: str | None, *, field: str) -> Decimal | None:
    """Parse an optional non-negative finite decimal string."""
    if value is None or value == "":
        return None
    parsed = _parse_decimal(value, field=field)
    if parsed < 0:
        raise ExecutionConflictError(f"{field} must be a non-negative decimal string.")
    return parsed


def _optional_note(value: str | None) -> str | None:
    """Keep an optional place-order why-note, or omit blank text."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > 4000:
        raise ExecutionConflictError("note must be at most 4000 characters.")
    return stripped


def _optional_positive_decimal(value: str | None, *, field: str) -> Decimal | None:
    """Parse an optional positive finite decimal string."""
    if value is None or value == "":
        return None
    parsed = _parse_decimal(value, field=field)
    if parsed <= 0:
        raise ExecutionConflictError(f"{field} must be a positive decimal string.")
    return parsed


def _parse_decimal(value: str, *, field: str) -> Decimal:
    """Parse one finite decimal string."""
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ExecutionConflictError(f"{field} must be a finite decimal string.") from error
    if not parsed.is_finite():
        raise ExecutionConflictError(f"{field} must be a finite decimal string.")
    return parsed
