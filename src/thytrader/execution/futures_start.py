"""Starting a paper futures deployment: bind the contract once (ADR 0129 §4).

A paper futures start names its own fees (the account's spot rates do not apply): maker and
taker rates and a per-contract fee in USD. The contract is bound from the latest recorded
catalog observation and is never re-read. Perp-style contracts only in P1-4: a dated
contract would need an expiry flatten, which paper books do not run yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.evaluation.futures_spec import contract_from_observation
from thytrader.trading.futures_book import BoundFuturesContract, FuturesBookUnavailableError
from thytrader.trading.ids import utc_now
from thytrader.trading.models import DeploymentMode, ExecutionConflictError, ExecutionStoreError

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from thytrader.evaluation.futures_spec import InstrumentContract
    from thytrader.execution.futures_paper import FuturesObservationReader
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.futures_book import FuturesContractStore


@dataclass(frozen=True, slots=True)
class FuturesStart:
    """The stores a paper futures start binds its contract with."""

    contracts: FuturesContractStore
    observations: FuturesObservationReader


@dataclass(frozen=True, slots=True)
class FuturesStartTerms:
    """What a paper futures start binds: the contract and its per-contract fee."""

    contract: InstrumentContract
    fee_per_contract: Decimal


async def futures_start_terms(
    definition: StrategyDefinition,
    *,
    mode: DeploymentMode,
    start: FuturesStart | None,
    fee_per_contract: Decimal | None,
    maker_fee_rate: Decimal | None,
    taker_fee_rate: Decimal | None,
) -> FuturesStartTerms | None:
    """Check a futures start and resolve its contract; ``None`` for a spot strategy."""
    if not definition.instrument.is_future:
        if fee_per_contract is not None:
            raise ExecutionConflictError(
                "paper_fee_per_contract applies to futures strategies only."
            )
        return None
    fee = _require_paper_futures_fees(
        mode,
        fee_per_contract=fee_per_contract,
        maker_fee_rate=maker_fee_rate,
        taker_fee_rate=taker_fee_rate,
    )
    if start is None:
        raise ExecutionStoreError("Futures observation storage is unavailable.")
    product_id = definition.instrument.product_id
    latest = await start.observations.latest_instrument(product_id)
    if latest is None:
        raise ExecutionConflictError(
            f"FUTURES_CONTRACT_UNOBSERVED: {product_id} has no recorded catalog observation."
        )
    observation, _seen_at = latest
    if observation.underlying != definition.instrument.base_currency:
        raise ExecutionConflictError(
            f"FUTURES_UNDERLYING_MISMATCH: {product_id} settles on {observation.underlying}."
        )
    try:
        contract = contract_from_observation(observation)
    except ValidationError as error:
        raise ExecutionConflictError("The recorded futures contract is invalid.") from error
    if contract.kind != "perpetual_future":
        raise ExecutionConflictError(
            "FUTURES_PAPER_UNSUPPORTED: paper futures books trade perp-style contracts only; "
            "dated contracts need an expiry flatten that paper does not run yet."
        )
    return FuturesStartTerms(contract=contract, fee_per_contract=fee)


def _require_paper_futures_fees(
    mode: DeploymentMode,
    *,
    fee_per_contract: Decimal | None,
    maker_fee_rate: Decimal | None,
    taker_fee_rate: Decimal | None,
) -> Decimal:
    """Paper only, with explicit maker, taker and per-contract fees."""
    if mode is DeploymentMode.LIVE:
        raise ExecutionConflictError(
            "FUTURES_LIVE_UNSUPPORTED: live futures deployments are not supported."
        )
    if fee_per_contract is None or fee_per_contract < 0:
        raise ExecutionConflictError(
            "FUTURES_FEE_REQUIRED: a paper futures start needs paper_fee_per_contract (USD, "
            "see `thytrader-operator fees`)."
        )
    if maker_fee_rate is None or taker_fee_rate is None:
        raise ExecutionConflictError(
            "FUTURES_FEE_REQUIRED: a paper futures start needs explicit maker_fee_rate and "
            "taker_fee_rate (the futures tier in `thytrader-operator fees`); the account's "
            "spot rates do not apply."
        )
    return fee_per_contract


async def bind_futures_contract(
    start: FuturesStart | None, *, deployment_id: UUID, terms: FuturesStartTerms
) -> None:
    """Record the binding of a just-created futures deployment."""
    if start is None:
        raise ExecutionStoreError("Futures book storage is unavailable.")
    try:
        await start.contracts.bind_contract(
            BoundFuturesContract(
                deployment_id=deployment_id,
                contract=terms.contract,
                fee_per_contract=terms.fee_per_contract,
                bound_at=utc_now(),
            )
        )
    except FuturesBookUnavailableError as error:
        raise ExecutionStoreError(str(error)) from error
