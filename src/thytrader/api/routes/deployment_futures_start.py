"""Futures start terms on the deployment start route (ADR 0129 §4, ADR 0134 P2-7).

Paper futures name ``paper_fee_per_contract`` with explicit maker and taker rates; a live
futures start names ``fee_per_contract`` and ``futures_capital_usd`` and needs its own
acknowledgement on top of the live one. Each fee field belongs to one mode only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from thytrader.api.live_ack import require_live_futures_acknowledgement
from thytrader.execution.service import parse_decimal
from thytrader.trading.models import DeploymentMode, ExecutionConflictError

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.api.routes.deployment_models import CreateDeploymentRequest
    from thytrader.strategies.models import StrategyDefinition


@dataclass(frozen=True, slots=True)
class FuturesStartFields:
    """The per-contract fee and live USD capital a start passes to the service."""

    fee_per_contract: Decimal | None
    futures_capital_usd: Decimal | None


def futures_start_fields(
    body: CreateDeploymentRequest, definition: StrategyDefinition
) -> FuturesStartFields:
    """Check the live futures acknowledgement and pick the mode's fee field (409 otherwise)."""
    live = body.mode is DeploymentMode.LIVE
    if live and definition.instrument.is_future:
        require_live_futures_acknowledgement(acknowledged=body.i_understand_live_futures)
    if live and body.paper_fee_per_contract is not None:
        _conflict(
            "paper_fee_per_contract is for paper futures; a live futures start sends "
            "fee_per_contract."
        )
    if not live and body.fee_per_contract is not None:
        _conflict(
            "fee_per_contract is for live futures; a paper futures start sends "
            "paper_fee_per_contract."
        )
    fee = body.fee_per_contract if live else body.paper_fee_per_contract
    field = "fee_per_contract" if live else "paper_fee_per_contract"
    try:
        return FuturesStartFields(
            fee_per_contract=parse_decimal(fee, field=field),
            futures_capital_usd=parse_decimal(
                body.futures_capital_usd, field="futures_capital_usd"
            ),
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None


def _conflict(detail: str) -> None:
    """Refuse a start whose fields contradict its mode."""
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
