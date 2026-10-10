"""PostgreSQL store for CFM futures account mirror snapshots (ADR 0127)."""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal, InvalidOperation
import json
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError

from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountStoreUnavailableError,
    FuturesBalanceSummary,
    FuturesEnablement,
    FuturesMarginMeasure,
    FuturesMarginWindow,
    FuturesPosition,
    FuturesPositionSide,
)
from thytrader.persistence.schema import futures_account_snapshots, futures_position_snapshots

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncEngine

_PROVIDER = "coinbase"
_BALANCE_AMOUNTS = tuple(
    field.name
    for field in fields(FuturesBalanceSummary)
    if field.name not in {"intraday_margin", "overnight_margin"}
)
_MEASURE_FIELDS = tuple(field.name for field in fields(FuturesMarginMeasure))
_MEASURE_DECIMALS = frozenset(
    {
        "initial_margin",
        "maintenance_margin",
        "liquidation_buffer",
        "total_hold",
        "futures_buying_power",
    }
)


class PostgresFuturesAccountStore:
    """Append-only mirror snapshots; one observation is one transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to one async engine."""
        self._engine = engine

    async def record(self, observation: FuturesAccountObservation) -> None:
        """Append one observation and its positions atomically."""
        snapshot_id = uuid4()
        balance = observation.balance
        window = observation.margin_window
        positions = observation.positions
        values: dict[str, object] = {
            "id": snapshot_id,
            "provider": _PROVIDER,
            "observed_at": observation.observed_at,
            "enablement": observation.enablement.value,
            "read_failures": json.dumps(list(observation.read_failures)),
            "intraday_margin_setting": observation.intraday_margin_setting,
            "margin_window_type": None if window is None else window.margin_window_type,
            "margin_window_end_at": None if window is None else window.end_time,
            "intraday_killswitch_enabled": (
                None if window is None else window.intraday_killswitch_enabled
            ),
            "enrollment_killswitch_enabled": (
                None if window is None else window.enrollment_killswitch_enabled
            ),
            "position_count": None if positions is None else len(positions),
            "intraday_margin_measure": _measure_json(
                None if balance is None else balance.intraday_margin
            ),
            "overnight_margin_measure": _measure_json(
                None if balance is None else balance.overnight_margin
            ),
        }
        for name in _BALANCE_AMOUNTS:
            amount = None if balance is None else getattr(balance, name)
            values[name] = None if amount is None else format(amount, "f")
        try:
            async with self._engine.begin() as connection:
                await connection.execute(insert(futures_account_snapshots).values(**values))
                if positions:
                    await connection.execute(
                        insert(futures_position_snapshots),
                        [_position_values(snapshot_id, position) for position in positions],
                    )
        except SQLAlchemyError as error:
            message = "Futures account mirror storage is unavailable."
            raise FuturesAccountStoreUnavailableError(message) from error

    async def latest(self) -> FuturesAccountObservation | None:
        """Return the newest observation with its positions, or ``None``."""
        snapshots = futures_account_snapshots
        statement = (
            select(snapshots)
            .where(snapshots.c.provider == _PROVIDER)
            .order_by(snapshots.c.observed_at.desc())
            .limit(1)
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
                if row is None:
                    return None
                position_rows = (
                    await connection.execute(
                        select(futures_position_snapshots)
                        .where(futures_position_snapshots.c.snapshot_id == row.id)
                        .order_by(futures_position_snapshots.c.product_id)
                    )
                ).all()
        except SQLAlchemyError as error:
            message = "Futures account mirror storage is unavailable."
            raise FuturesAccountStoreUnavailableError(message) from error
        return _observation(row, position_rows)


def _position_values(snapshot_id: object, position: FuturesPosition) -> dict[str, object]:
    """Map one position to its child row."""
    return {
        "snapshot_id": snapshot_id,
        "product_id": position.product_id,
        "side": position.side.value,
        "number_of_contracts": format(position.number_of_contracts, "f"),
        "current_price": _text(position.current_price),
        "avg_entry_price": _text(position.avg_entry_price),
        "unrealized_pnl": _text(position.unrealized_pnl),
        "daily_realized_pnl": _text(position.daily_realized_pnl),
        "expiration_time": position.expiration_time,
    }


def _measure_json(measure: FuturesMarginMeasure | None) -> str | None:
    """Serialize one measure as canonical JSON of exact strings."""
    if measure is None:
        return None
    payload = {
        name: (
            _text(getattr(measure, name)) if name in _MEASURE_DECIMALS else getattr(measure, name)
        )
        for name in _MEASURE_FIELDS
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _measure_from_json(raw: object) -> FuturesMarginMeasure | None:
    """Rebuild one stored measure; corruption fails closed."""
    if raw is None:
        return None
    try:
        payload = json.loads(str(raw))
    except ValueError as error:
        raise FuturesAccountStoreUnavailableError("A stored margin measure is corrupt.") from error
    if not isinstance(payload, dict):
        raise FuturesAccountStoreUnavailableError("A stored margin measure is corrupt.")
    window_type = payload.get("margin_window_type")
    level = payload.get("margin_level")
    return FuturesMarginMeasure(
        margin_window_type=window_type if isinstance(window_type, str) else None,
        margin_level=level if isinstance(level, str) else None,
        initial_margin=_decimal(payload.get("initial_margin")),
        maintenance_margin=_decimal(payload.get("maintenance_margin")),
        liquidation_buffer=_decimal(payload.get("liquidation_buffer")),
        total_hold=_decimal(payload.get("total_hold")),
        futures_buying_power=_decimal(payload.get("futures_buying_power")),
    )


def _observation(
    row: Row[tuple[object, ...]], position_rows: Sequence[Row[tuple[object, ...]]]
) -> FuturesAccountObservation:
    """Rebuild one stored observation."""
    amounts = {name: _decimal(getattr(row, name)) for name in _BALANCE_AMOUNTS}
    enablement = FuturesEnablement(str(row.enablement))
    has_balance = enablement is FuturesEnablement.ENABLED
    balance = (
        FuturesBalanceSummary(
            **amounts,
            intraday_margin=_measure_from_json(row.intraday_margin_measure),
            overnight_margin=_measure_from_json(row.overnight_margin_measure),
        )
        if has_balance
        else None
    )
    window = (
        None
        if row.margin_window_type is None
        and row.margin_window_end_at is None
        and row.intraday_killswitch_enabled is None
        and row.enrollment_killswitch_enabled is None
        else FuturesMarginWindow(
            margin_window_type=row.margin_window_type,
            end_time=row.margin_window_end_at,
            intraday_killswitch_enabled=row.intraday_killswitch_enabled,
            enrollment_killswitch_enabled=row.enrollment_killswitch_enabled,
        )
    )
    failures = json.loads(str(row.read_failures))
    if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures):
        raise FuturesAccountStoreUnavailableError("Stored read failures are corrupt.")
    return FuturesAccountObservation(
        observed_at=row.observed_at,
        enablement=enablement,
        balance=balance,
        positions=(
            None
            if row.position_count is None
            else tuple(_position(position) for position in position_rows)
        ),
        intraday_margin_setting=row.intraday_margin_setting,
        margin_window=window,
        read_failures=tuple(failures),
    )


def _position(row: Row[tuple[object, ...]]) -> FuturesPosition:
    """Rebuild one stored position."""
    contracts = _decimal(row.number_of_contracts)
    if contracts is None:
        raise FuturesAccountStoreUnavailableError("A stored position is corrupt.")
    return FuturesPosition(
        product_id=str(row.product_id),
        side=FuturesPositionSide(str(row.side)),
        number_of_contracts=contracts,
        current_price=_decimal(row.current_price),
        avg_entry_price=_decimal(row.avg_entry_price),
        unrealized_pnl=_decimal(row.unrealized_pnl),
        daily_realized_pnl=_decimal(row.daily_realized_pnl),
        expiration_time=row.expiration_time,
    )


def _text(value: Decimal | None) -> str | None:
    """Exact decimal string or ``None``."""
    return None if value is None else format(value, "f")


def _decimal(raw: object) -> Decimal | None:
    """Parse one stored exact decimal string; corruption fails closed."""
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise FuturesAccountStoreUnavailableError("A stored amount is corrupt.") from error
    if not value.is_finite():
        raise FuturesAccountStoreUnavailableError("A stored amount is corrupt.")
    return value
