"""Publish and serialize the effective risk policy."""

from __future__ import annotations

from thytrader.risk.models import ActiveRiskPolicy, RiskPolicyDefinition, RiskPolicyWrite
from thytrader.risk.store import RiskPolicyStore, load_effective_policy, next_policy_version


async def publish_risk_policy(store: RiskPolicyStore, write: RiskPolicyWrite) -> ActiveRiskPolicy:
    """Validate operator fields, assign the next version, and persist the document."""
    current = await load_effective_policy(store)
    definition = RiskPolicyDefinition(
        policy_id=current.definition.policy_id,
        version=next_policy_version(current),
        product_allowlist=write.product_allowlist,
        max_concurrent_running_deployments=write.max_concurrent_running_deployments,
        max_concurrent_open_positions=write.max_concurrent_open_positions,
        max_portfolio_exposure_fraction=write.max_portfolio_exposure_fraction,
        per_product_max_exposure_fraction=write.per_product_max_exposure_fraction,
        paper_capital_quote=write.paper_capital_quote,
        allocations=write.allocations,
        daily_loss_limit_fraction=write.daily_loss_limit_fraction,
        max_strategy_drawdown_fraction=write.max_strategy_drawdown_fraction,
        max_entry_orders_per_minute=write.max_entry_orders_per_minute,
        max_cancellations_per_minute=write.max_cancellations_per_minute,
        reference_price_collar_fraction=write.reference_price_collar_fraction,
    )
    return await store.publish(definition)
