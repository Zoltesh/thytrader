"""Live PostgreSQL tests for immutable risk-policy publication."""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr
import pytest

from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_risk import PostgresRiskPolicyStore
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import (
    RiskPolicyDefinition,
    RiskPolicySource,
    compiled_default_risk_policy,
)
from thytrader.risk.store import next_policy_version

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None, reason="An isolated PostgreSQL test DB is required."
)
def test_live_futures_policy_round_trip_and_clear() -> None:
    """P2-3 fields, including explicit false, survive canonical storage and can be unset."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        store = PostgresRiskPolicyStore(engine)
        try:
            current = await store.load_active()
            block = FuturesRiskPolicy(
                live_enabled=False,
                live_capital_usd="10000",
                product_allowlist=("BIP-20DEC30-CDE",),
                live_derisk_margin_ratio="2",
                live_funding_drift_tolerance_usd="10",
            )
            definition = compiled_default_risk_policy().model_copy(
                update={"version": next_policy_version(current), "futures": block}
            )
            published = await store.publish(definition)
            active = await store.load_active()
            assert active.definition.futures == block
            assert active.policy_fingerprint == published.policy_fingerprint
            cleared = definition.model_copy(
                update={"version": next_policy_version(active), "futures": None}
            )
            await store.publish(cleared)
            reloaded = await store.load_active()
            assert reloaded.definition.futures is None
            assert "futures" not in reloaded.definition.model_dump()
        finally:
            await dispose(engine)

    asyncio.run(exercise())


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


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_postgres_publishes_and_reloads_the_fleet_clustering_cap() -> None:
    """ADR 0125 fields round-trip through canonical JSON storage without a migration."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        store = PostgresRiskPolicyStore(engine)
        try:
            current = await store.load_active()
            definition = RiskPolicyDefinition.model_validate(
                {
                    **compiled_default_risk_policy().model_dump(mode="python"),
                    "version": next_policy_version(current),
                    "max_fleet_entries_per_window": 4,
                    "fleet_entry_window_minutes": 120,
                    "max_btc_beta_exposure_fraction": "0.6",
                    "max_btc_beta_exposure_quote": "300",
                }
            )

            published = await store.publish(definition)
            active = await store.load_active()
            assert active.policy_fingerprint == published.policy_fingerprint
            assert active.definition.max_fleet_entries_per_window == 4
            assert active.definition.fleet_entry_window_minutes == 120
            assert active.definition.max_btc_beta_exposure_fraction == "0.6"
            assert active.definition.max_btc_beta_exposure_quote == "300"

            cleared = definition.model_copy(
                update={
                    "version": next_policy_version(active),
                    "max_fleet_entries_per_window": None,
                    "fleet_entry_window_minutes": None,
                }
            )
            await store.publish(cleared)
            reloaded = await store.load_active()
            assert reloaded.definition.max_fleet_entries_per_window is None
            assert reloaded.definition.fleet_entry_window_minutes is None
        finally:
            await dispose(engine)

    asyncio.run(exercise())
