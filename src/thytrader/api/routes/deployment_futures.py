"""Read-only futures view of one paper futures bot (ADR 0129 §4, P1-6).

``GET /api/v1/deployments/{id}/futures`` returns the same projection as the operator
``futures-books`` report for one book: bound contract, contracts, mark, equity, notional,
leverage, overnight margin, liquidation buffer and price, the funding ledger and the reasons
new entries are denied, all in USD. A spot bot is a 404.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from thytrader.api.dependencies import (
    get_execution_store,
    get_futures_account_store,
    get_futures_start,
    get_optional_decision_journal_store,
    get_risk_policy_store,
)
from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
from thytrader.execution.decision_store import DecisionJournalStore
from thytrader.execution.futures_start import FuturesStart
from thytrader.execution.futures_visibility import read_visibility_account
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.operator.futures_books_report import (
    FuturesBookPayload,
    effective_policy_or_none,
    futures_book_payload,
)
from thytrader.risk.store import RiskPolicyStore
from thytrader.trading.models import DeploymentMode, ExecutionStoreError
from thytrader.trading.store import ExecutionStore

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


@router.get("/{deployment_id}/futures", response_model=FuturesBookPayload)
async def get_deployment_futures(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    journal: Annotated[DecisionJournalStore | None, Depends(get_optional_decision_journal_store)],
    futures_start: Annotated[FuturesStart | None, Depends(get_futures_start)],
    risk_store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    futures_account: Annotated[
        FuturesAccountSnapshotStore | None, Depends(get_futures_account_store)
    ],
) -> FuturesBookPayload:
    """Return the futures view of one futures bot; 404 for a spot or unknown bot."""
    try:
        snapshot = await store.get_accounting_snapshot(deployment_id)
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    if not is_futures_product_id(snapshot.deployment.product_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This deployment is not a futures book."
        )
    books = (snapshot,)
    account = None
    account_observation = None
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        try:
            books = tuple(
                [
                    await store.get_accounting_snapshot(row.id)
                    for row in await store.list_deployments()
                    if row.mode is DeploymentMode.LIVE and is_futures_product_id(row.product_id)
                ]
            )
            account, account_observation = await read_visibility_account(
                futures_account,
                None if futures_start is None else futures_start.observations,
                books,
            )
        except Exception:  # noqa: BLE001 - read-only evidence failures stay unknown.
            account = None
    return await futures_book_payload(
        snapshot,
        journal=journal,
        contracts=None if futures_start is None else futures_start.contracts,
        observations=None if futures_start is None else futures_start.observations,
        policy=await effective_policy_or_none(risk_store),
        account=account,
        account_observation=account_observation,
        books=books,
    )
