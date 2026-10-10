"""PostgreSQL store for paper futures contract bindings (ADR 0129 §4, Alembic 0073)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.evaluation.futures_spec import InstrumentContract
from thytrader.persistence.schema import deployment_instrument_contracts
from thytrader.trading.futures_book import BoundFuturesContract, FuturesBookUnavailableError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncEngine

_UNAVAILABLE = "Futures book storage is unavailable."


class PostgresFuturesContractStore:
    """Write a deployment's contract binding once; read it back for every cycle."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to one async engine."""
        self._engine = engine

    async def bind_contract(self, binding: BoundFuturesContract) -> None:
        """Insert the binding; an existing binding for the deployment is refused."""
        contract = binding.contract
        statement = (
            insert(deployment_instrument_contracts)
            .values(
                deployment_id=binding.deployment_id,
                product_id=contract.product_id,
                kind=contract.kind,
                underlying=contract.underlying,
                contract_size=contract.contract_size,
                settlement_currency=contract.settlement_currency,
                expires_at=contract.expires_at,
                listed_expiry=contract.listed_expiry,
                catalog_fingerprint=contract.catalog_fingerprint,
                fee_per_contract=str(binding.fee_per_contract),
                bound_at=binding.bound_at,
            )
            .on_conflict_do_nothing(index_elements=["deployment_id"])
            .returning(deployment_instrument_contracts.c.deployment_id)
        )
        try:
            async with self._engine.begin() as connection:
                inserted = (await connection.execute(statement)).first()
        except SQLAlchemyError as error:
            raise FuturesBookUnavailableError(_UNAVAILABLE) from error
        if inserted is None:
            raise FuturesBookUnavailableError("The deployment already has a bound contract.")

    async def load_contract(self, deployment_id: UUID) -> BoundFuturesContract | None:
        """Return the binding, or None for a deployment that has none."""
        table = deployment_instrument_contracts
        try:
            async with self._engine.connect() as connection:
                statement = select(table).where(table.c.deployment_id == deployment_id)
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise FuturesBookUnavailableError(_UNAVAILABLE) from error
        return None if row is None else _binding_from_row(row)


def _binding_from_row(row: RowMapping) -> BoundFuturesContract:
    """Map one stored binding; malformed values fail closed."""
    try:
        contract = InstrumentContract(
            product_id=str(row["product_id"]),
            kind=row["kind"],
            underlying=str(row["underlying"]),
            contract_size=str(row["contract_size"]),
            settlement_currency=row["settlement_currency"],
            expires_at=row["expires_at"],
            listed_expiry=row["listed_expiry"],
            catalog_fingerprint=str(row["catalog_fingerprint"]),
        )
        fee = Decimal(str(row["fee_per_contract"]))
    except (InvalidOperation, ValidationError) as error:
        message = "A stored futures contract binding is malformed."
        raise FuturesBookUnavailableError(message) from error
    return BoundFuturesContract(
        deployment_id=row["deployment_id"],
        contract=contract,
        fee_per_contract=fee,
        bound_at=row["bound_at"],
    )
