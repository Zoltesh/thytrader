"""Per-task attribution bound while an order intent is persisted."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thytrader.risk.models import COMPILED_POLICY_ID, risk_policy_fingerprint

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

    from thytrader.execution.models import Deployment
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
    from thytrader.strategies.models import StrategyDefinition


@dataclass(slots=True)
class TradeReasonScope:
    """Mutable attribution for why-trade records written during one submit path."""

    store: ExperientialMemoryStore
    policy_fingerprint: str
    policy_source: str
    risk_decision: str
    risk_reason_code: str
    risk_detail: str
    strategy_id: UUID | None = None
    strategy_fingerprint: str | None = None
    strategy_name: str | None = None
    strategy_version: int | None = None
    timeframe: str | None = None
    discretionary_note: str | None = None
    note_origin: str | None = None

    def remember_risk(self, verdict: RiskVerdict) -> None:
        """Overwrite the frozen risk fields with one evaluated verdict."""
        self.risk_decision = verdict.decision.value
        self.risk_reason_code = verdict.reason_code.value
        self.risk_detail = verdict.detail


_SCOPE: ContextVar[TradeReasonScope | None] = ContextVar("trade_reason_scope", default=None)


def current_trade_reason_scope() -> TradeReasonScope | None:
    """Return the bound attribution, if any."""
    return _SCOPE.get()


@contextmanager
def trade_reason_scope(scope: TradeReasonScope | None) -> Iterator[TradeReasonScope | None]:
    """Bind attribution for the duration of one persist path. None is a no-op."""
    if scope is None:
        yield None
        return
    token: Token[TradeReasonScope | None] = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def strategy_trade_reason_scope(
    store: ExperientialMemoryStore | None,
    *,
    deployment: Deployment,
    strategy: StrategyDefinition,
    policy: RiskPolicyDefinition,
) -> TradeReasonScope | None:
    """Build attribution for a published-strategy intent persist."""
    if store is None:
        return None
    fingerprint, source = _policy_fields(policy)
    return TradeReasonScope(
        store=store,
        policy_fingerprint=fingerprint,
        policy_source=source,
        risk_decision="allow",
        risk_reason_code="ALLOWED",
        risk_detail="Exit or entry intent persisted after the active risk policy admitted it.",
        strategy_id=strategy.strategy_id,
        strategy_fingerprint=deployment.strategy_fingerprint,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        timeframe=strategy.timeframe,
    )


def discretionary_trade_reason_scope(
    store: ExperientialMemoryStore | None,
    *,
    policy: RiskPolicyDefinition,
    timeframe: str,
    note: str | None,
    note_origin: str | None,
    verdict: RiskVerdict | None = None,
) -> TradeReasonScope | None:
    """Build attribution for an on-demand intent persist."""
    if store is None:
        return None
    fingerprint, source = _policy_fields(policy)
    scope = TradeReasonScope(
        store=store,
        policy_fingerprint=fingerprint,
        policy_source=source,
        risk_decision="allow",
        risk_reason_code="ALLOWED",
        risk_detail="Discretionary intent persisted after the active risk policy admitted it.",
        timeframe=timeframe,
        discretionary_note=note,
        note_origin=note_origin,
    )
    if verdict is not None:
        scope.remember_risk(verdict)
    return scope


def _policy_fields(policy: RiskPolicyDefinition) -> tuple[str, str]:
    """Fingerprint a policy and classify compiled default versus published."""
    source = "compiled_default" if policy.policy_id == COMPILED_POLICY_ID else "published"
    return risk_policy_fingerprint(policy), source
