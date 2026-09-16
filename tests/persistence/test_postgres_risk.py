"""Live PostgreSQL tests for immutable risk-policy publication."""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr
import pytest

from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_risk import PostgresRiskPolicyStore
from thytrader.risk.models import RiskPolicySource, compiled_default_risk_policy
from thytrader.risk.store import next_policy_version

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_postgres_publishes_and_reloads_optional_absolute_caps_and_venue_budget() -> None:
    """Publishing must round-trip the new F25/F35 optional fields through real storage."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        store = PostgresRiskPolicyStore(engine)
        try:
            current = await store.load_active()
            definition = compiled_default_risk_policy().model_copy(
                update={
                    "version": next_policy_version(current),
                    "max_daily_loss_quote": "2500",
                    "max_portfolio_exposure_quote": "50000",
                    "max_venue_order_actions_per_minute": 90,
                }
            )

            published = await store.publish(definition)
            assert published.source is RiskPolicySource.PUBLISHED

            active = await store.load_active()
            assert active.policy_fingerprint == published.policy_fingerprint
            assert active.definition.max_daily_loss_quote == "2500"
            assert active.definition.max_portfolio_exposure_quote == "50000"
            assert active.definition.max_venue_order_actions_per_minute == 90
        finally:
            await dispose(engine)

    asyncio.run(exercise())
