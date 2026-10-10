"""Load fleet entry readiness evidence the way entry admission loads it (ADR 0130).

Entry admission (``execution.entry_admission``) reloads every book's fresh accounting
snapshot, prices open inventory at last closes, and reads BTC-beta evidence and the CFM
futures account before it calls the gate. This module does the same once for the whole
fleet and hands the result to the pure evaluator ``risk.fleet_entry_health``. Shared by the
execution worker's supervision cycle (the durable alert) and the operator reports.

Unreadable evidence is reported, never replaced: a book whose snapshot fails is named as
unreadable, and a failed β or futures-account read makes that check ``unknown``.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING

from thytrader.execution.futures_paper import load_futures_legs
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.models import parse_candle_interval
from thytrader.risk.accounting_evidence import accounting_snapshot, risk_market_data_scope
from thytrader.risk.beta_evidence import load_entry_beta
from thytrader.risk.breakers import FUTURES_SCOPE, EntryObservation, _product_quote
from thytrader.risk.fleet_entry_health import evaluate_fleet_entry_health, fleet_entry_scopes
from thytrader.risk.fleet_entry_models import BlockingBook, FleetEntryHealth, FleetScopeEvidence
from thytrader.risk.futures_collateral import load_futures_collateral
from thytrader.trading.ids import utc_now
from thytrader.trading.models import ExecutionStoreError, resolved_product_id, snapshot_positions

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from thytrader.exchanges.futures_models import FuturesAccountSnapshotStore
    from thytrader.market_data.service import MarketDataService
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import Deployment, DeploymentMode, DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

_READ_ERRORS = (ExecutionStoreError, RuntimeError, ValueError, OSError)
_EVIDENCE_ERRORS = (RuntimeError, ValueError, TypeError, OSError, LookupError, AttributeError)


async def load_fleet_entry_health(
    store: ExecutionStore,
    policy: RiskPolicyDefinition,
    *,
    market_data: MarketDataService | None,
    futures_account: FuturesAccountSnapshotStore | None,
    now: datetime | None = None,
) -> FleetEntryHealth:
    """Evaluate every occupied scope; ``complete`` is false only when listing failed."""
    as_of = utc_now() if now is None else now
    try:
        deployments = await store.list_deployments()
    except _READ_ERRORS:
        return FleetEntryHealth(
            evaluated_at=as_of,
            complete=False,
            scopes=(),
            detail="Deployments could not be listed; fleet entry readiness is unknown.",
        )
    inhibition = await _entry_inhibition(store)
    scope_binding = nullcontext() if market_data is None else risk_market_data_scope(market_data)
    with scope_binding:
        snapshots, unreadable = await _accounting_books(store, deployments, as_of=as_of)
        observation = EntryObservation(
            as_of=as_of,
            proposed_price=None,
            reference_price=None,
            marks=await _inventory_marks(market_data, snapshots),
        )
        evidence = [
            await _scope_evidence(
                policy,
                snapshots,
                mode=mode,
                scope=scope,
                deployments=deployments,
                market_data=market_data,
                futures_account=futures_account,
                as_of=as_of,
                inhibited=None if inhibition is None else inhibition.get(mode.value),
            )
            for mode, scope in fleet_entry_scopes(deployments)
        ]
    return FleetEntryHealth(
        evaluated_at=as_of,
        complete=True,
        scopes=evaluate_fleet_entry_health(
            policy,
            snapshots=snapshots,
            evidence=evidence,
            observation=observation,
            unreadable=unreadable,
        ),
    )


async def _accounting_books(
    store: ExecutionStore, deployments: Sequence[Deployment], *, as_of: datetime
) -> tuple[tuple[DeploymentSnapshot, ...], tuple[BlockingBook, ...]]:
    """Fresh accounting snapshots with recovered midnight marks, and the unreadable books."""
    snapshots: list[DeploymentSnapshot] = []
    unreadable: list[BlockingBook] = []
    for deployment in deployments:
        try:
            snapshots.append(await accounting_snapshot(store, deployment.id, as_of=as_of))
        except _READ_ERRORS as error:
            unreadable.append(
                BlockingBook(
                    deployment_id=deployment.id,
                    status=deployment.status.value,
                    product_id=deployment.product_id,
                    detail=f"accounting snapshot unavailable: {str(error)[:200]}",
                )
            )
    return tuple(snapshots), tuple(unreadable)


async def _inventory_marks(
    market_data: MarketDataService | None, snapshots: Sequence[DeploymentSnapshot]
) -> dict[str, Decimal]:
    """Last-close marks for every product with open inventory; a failed read stays unmarked."""
    marks: dict[str, Decimal] = {}
    if market_data is None:
        return marks
    for snapshot in snapshots:
        timeframe = snapshot.deployment.timeframe or "1h"
        for position in snapshot_positions(snapshot):
            product_id = resolved_product_id(position.product_id, snapshot.deployment)
            if product_id in marks:
                continue
            try:
                preview = await market_data.get_preview(
                    product_id, parse_candle_interval(timeframe)
                )
            except _EVIDENCE_ERRORS:
                continue
            candles = preview.quality.candles
            if candles:
                marks[product_id] = candles[-1].close
    return marks


async def _scope_evidence(
    policy: RiskPolicyDefinition,
    snapshots: Sequence[DeploymentSnapshot],
    *,
    mode: DeploymentMode,
    scope: str,
    deployments: Sequence[Deployment],
    market_data: MarketDataService | None,
    futures_account: FuturesAccountSnapshotStore | None,
    as_of: datetime,
    inhibited: bool | None,
) -> FleetScopeEvidence:
    """β, futures legs and the collateral state, read as entry admission reads them."""
    probe = _probe(scope, deployments, mode)
    try:
        legs = await load_futures_legs(policy, snapshots, mode=mode, now=as_of)
    except _READ_ERRORS:
        legs = None
    beta_loaded = True
    beta = None
    if scope != FUTURES_SCOPE:
        try:
            beta = await load_entry_beta(
                policy, market_data, mode=mode, snapshots=snapshots, product_id=probe, as_of=as_of
            )
        except _EVIDENCE_ERRORS:
            beta_loaded = False
    collateral_loaded = True
    collateral = None
    try:
        collateral = await load_futures_collateral(futures_account, mode=mode, as_of=as_of)
    except _EVIDENCE_ERRORS:
        collateral_loaded = False
    return FleetScopeEvidence(
        mode=mode,
        scope=scope,
        probe_product=probe,
        beta=beta,
        beta_loaded=beta_loaded,
        futures_collateral=collateral,
        collateral_loaded=collateral_loaded,
        futures_legs=legs,
        entries_inhibited=inhibited,
    )


async def _entry_inhibition(store: ExecutionStore) -> dict[str, bool] | None:
    """The durable fleet disarm latch per mode, or None when it cannot be read.

    Read the way the worker refreshes it before admitting entries
    (``fleet_control.admission``): no reader or a failed read inhibits, so it is unknown.
    """
    reader = getattr(store, "read_entry_inhibition", None)
    if not callable(reader):
        return None
    try:
        snapshot = await reader()
    except _READ_ERRORS:
        return None
    if not isinstance(snapshot, dict):
        return None
    return {mode: value for mode, value in snapshot.items() if isinstance(value, bool)}


def _probe(scope: str, deployments: Sequence[Deployment], mode: DeploymentMode) -> str:
    """The scope's BTC reference, or the contract of one of its futures books."""
    if scope != FUTURES_SCOPE:
        return f"BTC-{scope}"
    return next(
        deployment.product_id
        for deployment in deployments
        if deployment.mode is mode
        and is_futures_product_id(deployment.product_id)
        and _product_quote(deployment.product_id) == scope
    )
