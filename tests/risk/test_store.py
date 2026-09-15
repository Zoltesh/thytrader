"""In-memory and disabled risk-policy store behavior."""

import asyncio

import pytest

from thytrader.risk.models import (
    RiskPolicySource,
    RiskPolicyWrite,
    compiled_default_active_policy,
)
from thytrader.risk.service import publish_risk_policy
from thytrader.risk.store import (
    DisabledRiskPolicyStore,
    InMemoryRiskPolicyStore,
    RiskPolicyStoreError,
    load_effective_policy,
)


def test_disabled_store_serves_compiled_default_and_refuses_publish() -> None:
    """Without PostgreSQL, observation uses the compiled envelope and PUT fails closed."""

    async def _scenario() -> None:
        store = DisabledRiskPolicyStore()
        active = await load_effective_policy(store)
        assert active.source is RiskPolicySource.COMPILED_DEFAULT
        assert active.policy_fingerprint == compiled_default_active_policy().policy_fingerprint
        write = RiskPolicyWrite(
            max_concurrent_running_deployments=4,
            max_concurrent_open_positions=4,
            max_portfolio_exposure_fraction="1",
            per_product_max_exposure_fraction="1",
            paper_capital_quote="50000",
        )
        with pytest.raises(RiskPolicyStoreError, match="durable storage"):
            await publish_risk_policy(store, write)

    asyncio.run(_scenario())


def test_in_memory_store_publishes_an_immutable_version() -> None:
    """Publishing replaces the compiled default and increments version."""

    async def _scenario() -> None:
        store = InMemoryRiskPolicyStore()
        write = RiskPolicyWrite(
            product_allowlist=("BTC-USD", "ETH-USD"),
            max_concurrent_running_deployments=2,
            max_concurrent_open_positions=2,
            max_portfolio_exposure_fraction="1",
            per_product_max_exposure_fraction="1",
            paper_capital_quote="25000",
        )
        published = await publish_risk_policy(store, write)
        assert published.source is RiskPolicySource.PUBLISHED
        assert published.definition.version == 1
        assert published.definition.product_allowlist == ("BTC-USD", "ETH-USD")
        loaded = await store.load_active()
        assert loaded.policy_fingerprint == published.policy_fingerprint
        second = await publish_risk_policy(store, write)
        assert second.definition.version == 2

    asyncio.run(_scenario())
