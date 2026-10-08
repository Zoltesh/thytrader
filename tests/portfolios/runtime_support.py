"""Shared in-memory world for portfolio deployment and manager tests (ADR 0091)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.portfolios.manager import ProposalService
from thytrader.portfolios.models import (
    ManagerPermissions,
    ManagerSettings,
    MutationContext,
    PortfolioCreateRequest,
    PortfolioLimits,
    SleeveAddRequest,
)
from thytrader.portfolios.runtime import PortfolioRuntimeService
from thytrader.portfolios.store import InMemoryPortfolioStore
from thytrader.risk.models import CapitalAllocation, compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.portfolios.models import PortfolioAggregate
    from thytrader.portfolios.vocabulary import PortfolioMode
    from thytrader.strategies.library import StrategyRecord
    from thytrader.trading.models import Deployment

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


def operator(at: datetime | None = None) -> MutationContext:
    """An operator acting through the API."""
    return MutationContext(actor="operator", channel="api", occurred_at=at or datetime.now(UTC))


@dataclass
class World:
    """In-memory strategies, deployments, portfolios, risk policy, and the services."""

    strategies: InMemoryStrategyStore
    execution: InMemoryExecutionStore
    portfolios: InMemoryPortfolioStore
    risk: InMemoryRiskPolicyStore
    audit: InMemoryAuditEventStore
    decisions: InMemoryDecisionJournalStore
    runtime: PortfolioRuntimeService
    proposals: ProposalService

    async def publish_policy(
        self,
        *,
        allocations: Sequence[tuple[UUID, str]] = (),
        paper_capital_quote: str = "100000",
    ) -> None:
        """Publish an operator policy (live requires one), optionally with allocations."""
        policy = compiled_default_risk_policy().model_copy(
            update={
                "paper_capital_quote": paper_capital_quote,
                "allocations": tuple(
                    CapitalAllocation(strategy_id=strategy_id, allocated_quote=quote)
                    for strategy_id, quote in allocations
                ),
            }
        )
        await self.risk.publish(policy)

    async def tagged(self, portfolio_id: UUID) -> tuple[Deployment, ...]:
        """Every deployment tagged with the portfolio, oldest first."""
        rows = await self.execution.list_deployments()
        return tuple(
            sorted(
                (item for item in rows if item.portfolio_id == portfolio_id),
                key=lambda item: item.created_at,
            )
        )

    async def mark_equity(self, deployment_id: UUID, *, pnl: str) -> None:
        """Persist a sleeve's bar-close equity at ``starting + pnl`` (as the loop would)."""
        snapshot = await self.execution.get_deployment(deployment_id)
        deployment = snapshot.deployment
        starting = deployment.initial_equity or deployment.paper_starting_cash or Decimal(0)
        equity = starting + Decimal(pnl)
        peak = max(deployment.high_water_mark_equity or starting, equity)
        await self.execution.save_deployment(
            replace(
                deployment,
                initial_equity=starting,
                performance_equity=equity,
                high_water_mark_equity=peak,
            )
        )


def world(*, live_allowed: bool = True) -> World:
    """A fresh world with every store in memory."""
    strategies = InMemoryStrategyStore()
    execution = InMemoryExecutionStore()
    portfolios = InMemoryPortfolioStore(strategies=strategies, execution=execution)
    risk = InMemoryRiskPolicyStore()
    audit = InMemoryAuditEventStore()
    runtime = PortfolioRuntimeService(
        portfolios=portfolios,
        execution=execution,
        strategies=strategies,
        publication=strategies,
        risk_store=risk,
        live_allowed=live_allowed,
        audit=audit,
    )
    return World(
        strategies=strategies,
        execution=execution,
        portfolios=portfolios,
        risk=risk,
        audit=audit,
        decisions=InMemoryDecisionJournalStore(),
        runtime=runtime,
        proposals=ProposalService(portfolios=portfolios, execution=execution, runtime=runtime),
    )


async def portfolio(
    state: World,
    *,
    mode: PortfolioMode = "paper",
    capital: str = "1000",
    reserve: str = "0.2",
    sleeves: Sequence[tuple[str, str]] = (("BTC-USDC", "0.5"), ("ETH-USDC", "0.3")),
    limits: PortfolioLimits | None = None,
    permissions: ManagerPermissions | None = None,
) -> tuple[PortfolioAggregate, tuple[StrategyRecord, ...]]:
    """Create one portfolio with a sleeve per (product, weight)."""
    current = await state.portfolios.create(
        PortfolioCreateRequest(
            name="Core",
            mode=mode,
            capital_quote=capital,
            cash_reserve_fraction=reserve,
            limits=limits or PortfolioLimits(),
            manager=ManagerSettings(
                mandate="Grow steadily.", permissions=permissions or ManagerPermissions()
            ),
        ),
        context=operator(),
    )
    records = []
    for product_id, weight in sleeves:
        record = await _strategy(state.strategies, product_id)
        records.append(record)
        current = await state.portfolios.add_sleeve(
            current.portfolio.portfolio_id,
            SleeveAddRequest(
                revision=current.portfolio.revision,
                strategy_id=record.strategy_id,
                weight_fraction=weight,
            ),
            context=operator(),
        )
    return current, tuple(records)


async def _strategy(store: InMemoryStrategyStore, product_id: str) -> StrategyRecord:
    """One valid 1h template strategy on ``product_id``."""
    definition = create_template_strategy(
        product_id=product_id, timeframe="1h", template="ema-trend"
    )
    return await create_strategy_from_definition(store, definition)
