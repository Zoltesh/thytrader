"""Risk admission of one sized closed-bar entry.

Admits an entry only against fresh, complete portfolio accounting and a known live
quote balance, records the verdict for the trade-reason scope and the decision
journal, and never trusts the product view or a cache.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.breaker_pause import _portfolio_with_current
from thytrader.execution.capital import live_capital_base
from thytrader.execution.decision_scope import note_risk
from thytrader.memory.trade_reason_scope import current_trade_reason_scope
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict
from thytrader.risk.portfolio_scope import portfolio_risk_for
from thytrader.trading.ids import utc_now
from thytrader.trading.models import DeploymentMode, DeploymentSnapshot, ExecutionStoreError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.store import ExecutionStore


async def _entry_admitted(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
    quantity: Decimal | None = None,
) -> bool:
    """Return whether the active risk policy allows this sized entry."""
    verdict = await _entry_verdict(
        snapshot,
        store=store,
        product_id=product_id,
        notional=notional,
        quantity=quantity,
        risk_policy=risk_policy,
        portfolio=portfolio,
        observation=observation,
        is_pyramid_add=is_pyramid_add,
    )
    return verdict.decision is RiskDecision.ALLOW


async def _entry_verdict(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
    quantity: Decimal | None = None,
    readmits_working_entry: bool = False,
) -> RiskVerdict:
    """Admit only against fresh full accounting, never the product view or cache.

    ``readmits_working_entry`` is set by a reprice of an already admitted working entry,
    which the fleet clustering cap does not gate again (ADR 0125).
    """
    observation = observation or EntryObservation(
        as_of=utc_now(), proposed_price=None, reference_price=None, marks={}
    )
    try:
        current_portfolio = await _portfolio_with_current(portfolio, snapshot, store=store)
    except ExecutionStoreError:
        return RiskVerdict(
            decision=RiskDecision.DENY,
            reason_code=RiskReasonCode.BREAKER_MARK_MISSING,
            detail="Fresh complete risk accounting evidence is unavailable; entries are disabled.",
        )
    current = next(
        item for item in current_portfolio if item.deployment.id == snapshot.deployment.id
    )
    live_cash = live_capital_base(current.deployment)
    if snapshot.deployment.mode is DeploymentMode.LIVE and live_cash is None:
        return RiskVerdict(
            decision=RiskDecision.DENY,
            reason_code=RiskReasonCode.VENUE_BALANCE_UNKNOWN,
            detail="Venue quote balance is unknown; new entries are disabled.",
        )
    verdict = evaluate_new_entry(
        risk_policy,
        mode=snapshot.deployment.mode,
        proposed=ProposedEntry(
            product_id=product_id,
            strategy_id=snapshot.deployment.strategy_id,
            notional=notional,
            is_pyramid_add=is_pyramid_add,
            quantity=quantity,
            readmits_working_entry=readmits_working_entry,
        ),
        snapshots=current_portfolio,
        live_quote_cash=live_cash,
        observation=observation,
        portfolio=portfolio_risk_for(snapshot.deployment),
    )
    scope = current_trade_reason_scope()
    if scope is not None:
        scope.remember_risk(verdict)
    note_risk(verdict)
    return verdict
