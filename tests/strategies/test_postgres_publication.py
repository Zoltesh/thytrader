"""Live PostgreSQL tests for immutable strategy publication and dataset binding."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import SecretStr
import pytest
from sqlalchemy import delete, update

from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_range
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.persistence.schema import published_strategy_versions, strategy_dataset_bindings
from thytrader.strategies.models import (
    StrategyDefinition,
    StrategyStatus,
    strategy_fingerprint,
)
from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationError

if TYPE_CHECKING:
    from pathlib import Path

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_postgres_publishes_verifies_and_binds_strategy_to_dataset(tmp_path: Path) -> None:
    """Published strategy bytes and verified dataset bindings are immutable and idempotent."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        store = PostgresStrategyPublicationStore(engine)
        definition = StrategyDefinition.model_validate(
            {
                "schema_version": "1.0",
                "strategy_id": "01985cf0-7b60-7000-8000-000000000001",
                "version": 1,
                "name": "BTC hourly EMA trend",
                "description": "Reference research strategy; not trading authority.",
                "status": "published",
                "created_at": "2026-07-29T18:00:00Z",
                "instrument": {
                    "product_id": "BTC-USD",
                    "base_currency": "BTC",
                    "quote_currency": "USD",
                },
                "timeframe": "1h",
                "data_requirements": {
                    "warmup_bars": 50,
                    "required_fields": ["open", "high", "low", "close", "volume"],
                },
                "indicators": [
                    {"id": "fast", "kind": "ema", "input": "close", "parameters": {"period": 20}},
                    {"id": "slow", "kind": "ema", "input": "close", "parameters": {"period": 50}},
                    {"id": "rsi", "kind": "rsi", "input": "close", "parameters": {"period": 14}},
                    {
                        "id": "atr",
                        "kind": "atr",
                        "input": ["high", "low", "close"],
                        "parameters": {"period": 14},
                    },
                ],
                "entry": {
                    "side": "long",
                    "when": {
                        "all": [
                            {
                                "left": {"indicator": "fast"},
                                "operator": "crosses_above",
                                "right": {"indicator": "slow"},
                            }
                        ]
                    },
                    "cooldown_bars": 3,
                    "max_open_positions": 1,
                },
                "sizing": {
                    "kind": "risk_fraction",
                    "risk_fraction": "0.005",
                    "min_quote_notional": "10",
                    "max_quote_notional": "100",
                },
                "portfolio_limits": {
                    "max_strategy_exposure_fraction": "0.10",
                    "max_concurrent_positions": 1,
                },
                "exits": {
                    "initial_stop": {
                        "kind": "atr_multiple",
                        "atr_indicator": "atr",
                        "multiple": "2.0",
                    },
                    "take_profit": {"kind": "reward_risk", "multiple": "2.0"},
                    "trailing_stop": {"enabled": False},
                    "time_exit": {"max_bars_held": 96},
                },
                "execution": {
                    "entry_preference": "maker_only",
                    "max_entry_wait_bars": 2,
                    "on_unfilled_entry": "cancel",
                },
                "metadata": {"tags": ["reference"], "notes": []},
            }
        )
        draft = definition.model_copy(update={"status": StrategyStatus.DRAFT})
        dataset_store = DatasetStore(tmp_path)
        starts_at = datetime(2026, 7, 29, 15, tzinfo=UTC)
        candles = tuple(
            Candle(
                starts_at=starts_at + timedelta(hours=index),
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("12.5"),
            )
            for index in range(3)
        )
        report = analyze_range(
            candles,
            CandleInterval.ONE_HOUR,
            starts_at,
            starts_at + timedelta(hours=3),
            now=starts_at + timedelta(hours=3),
        )
        manifest = dataset_store.write(
            "coinbase",
            "BTC-USD",
            report,
        )
        incompatible_manifest = dataset_store.write(
            "coinbase",
            "ETH-USD",
            report,
        )
        incompatible_provider_manifest = dataset_store.write(
            "not-coinbase",
            "BTC-USD",
            report,
        )
        published_fingerprints: set[str] = set()

        try:
            with pytest.raises(StrategyPublicationError, match="published"):
                await store.publish(draft)

            serializer_broken = definition.model_copy(update={"created_at": "bad"})
            with pytest.raises(StrategyPublicationError, match="invalid"):
                await store.publish(serializer_broken)

            forged = definition.model_copy(update={"version": 0})
            with pytest.raises(StrategyPublicationError, match="invalid"):
                await store.publish(forged)
            with pytest.raises(StrategyPublicationError, match="not found"):
                await store.load(strategy_fingerprint(forged))

            second_engine = create_engine(SecretStr(_TEST_DATABASE_URL))
            second_store = PostgresStrategyPublicationStore(second_engine)
            try:
                identical_results = await asyncio.gather(
                    store.publish(definition),
                    second_store.publish(definition),
                )
                published_fingerprints.add(identical_results[0].strategy_fingerprint)
                assert identical_results[0] == identical_results[1]

                conflict_identity = UUID("01985cf0-7b60-7000-8000-000000000002")
                first_conflict = definition.model_copy(
                    update={"strategy_id": conflict_identity, "description": "first"}
                )
                second_conflict = definition.model_copy(
                    update={"strategy_id": conflict_identity, "description": "second"}
                )
                conflict_results = await asyncio.gather(
                    store.publish(first_conflict),
                    second_store.publish(second_conflict),
                    return_exceptions=True,
                )
                conflict_successes = [
                    result for result in conflict_results if isinstance(result, PublishedStrategy)
                ]
                conflict_errors = [
                    result
                    for result in conflict_results
                    if isinstance(result, StrategyPublicationError)
                ]
                published_fingerprints.update(
                    result.strategy_fingerprint for result in conflict_successes
                )
                assert len(conflict_successes) == 1
                assert len(conflict_errors) == 1
            finally:
                await dispose(second_engine)

            published = identical_results[0]
            assert published.definition == definition
            assert await store.publish(definition) == published
            assert await store.load(published.strategy_fingerprint) == published

            async with engine.begin() as connection:
                await connection.execute(
                    update(published_strategy_versions)
                    .where(
                        published_strategy_versions.c.strategy_fingerprint
                        == published.strategy_fingerprint
                    )
                    .values(version=2)
                )
            with pytest.raises(StrategyPublicationError, match="row identity"):
                await store.load(published.strategy_fingerprint)
            async with engine.begin() as connection:
                await connection.execute(
                    update(published_strategy_versions)
                    .where(
                        published_strategy_versions.c.strategy_fingerprint
                        == published.strategy_fingerprint
                    )
                    .values(version=1)
                )

            binding = await store.bind_dataset(
                published.strategy_fingerprint,
                manifest.content_fingerprint,
                dataset_store=dataset_store,
                bound_at=datetime(2026, 7, 29, 18, 30, tzinfo=UTC),
            )
            assert binding.strategy_fingerprint == published.strategy_fingerprint
            assert binding.dataset_fingerprint == manifest.content_fingerprint
            assert (
                await store.bind_dataset(
                    published.strategy_fingerprint,
                    manifest.content_fingerprint,
                    dataset_store=dataset_store,
                    bound_at=binding.bound_at + timedelta(minutes=1),
                )
                == binding
            )
            assert (
                await store.load_binding(
                    published.strategy_fingerprint,
                    manifest.content_fingerprint,
                    dataset_store=dataset_store,
                )
                == binding
            )

            with pytest.raises(StrategyPublicationError, match="dataset"):
                await store.bind_dataset(
                    published.strategy_fingerprint,
                    "sha256:" + "0" * 64,
                    dataset_store=dataset_store,
                    bound_at=datetime(2026, 7, 29, 18, 31, tzinfo=UTC),
                )
            with pytest.raises(StrategyPublicationError, match="does not match"):
                await store.bind_dataset(
                    published.strategy_fingerprint,
                    incompatible_manifest.content_fingerprint,
                    dataset_store=dataset_store,
                    bound_at=datetime(2026, 7, 29, 18, 32, tzinfo=UTC),
                )
            with pytest.raises(StrategyPublicationError, match="does not match"):
                await store.bind_dataset(
                    published.strategy_fingerprint,
                    incompatible_provider_manifest.content_fingerprint,
                    dataset_store=dataset_store,
                    bound_at=datetime(2026, 7, 29, 18, 33, tzinfo=UTC),
                )

            manifest_path = (
                tmp_path
                / "manifests"
                / f"{manifest.content_fingerprint.removeprefix('sha256:')}.json"
            )
            manifest_path.unlink()
            with pytest.raises(StrategyPublicationError, match="dataset"):
                await store.load_binding(
                    published.strategy_fingerprint,
                    manifest.content_fingerprint,
                    dataset_store=dataset_store,
                )
        finally:
            if published_fingerprints:
                async with engine.begin() as connection:
                    await connection.execute(
                        delete(strategy_dataset_bindings).where(
                            strategy_dataset_bindings.c.strategy_fingerprint.in_(
                                published_fingerprints
                            )
                        )
                    )
                    await connection.execute(
                        delete(published_strategy_versions).where(
                            published_strategy_versions.c.strategy_fingerprint.in_(
                                published_fingerprints
                            )
                        )
                    )
            await dispose(engine)

    asyncio.run(exercise())
