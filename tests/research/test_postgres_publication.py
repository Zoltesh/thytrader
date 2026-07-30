"""Live PostgreSQL tests for immutable research-run specification publication."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast
from uuid import UUID

from pydantic import SecretStr
import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_range
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyPublicationStore
from thytrader.persistence.schema import (
    published_research_run_specs,
    published_strategy_versions,
    strategy_dataset_bindings,
)
from thytrader.research.models import (
    BarExecutionAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
    canonical_research_run_bytes,
    research_run_fingerprint,
)
from thytrader.research.publication import (
    PublishedResearchRunSpecification,
    ResearchRunPublicationError,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")


class _ExplodingEngine:
    """Fail if invalid-spec publication reaches the database boundary."""

    def begin(self) -> NoReturn:
        """Expose accidental database access as an immediate test failure."""
        raise AssertionError("database access must not occur")


def _strategy() -> StrategyDefinition:
    """Load the deterministic published reference strategy."""
    return StrategyDefinition.model_validate_json(
        Path("tests/strategies/golden/reference_strategy_v1.json").read_text(encoding="utf-8")
    )


def _dataset(dataset_store: DatasetStore) -> str:
    """Publish exact contiguous coverage for warmup, evaluation, and final next-open fill."""
    starts_at = datetime(2026, 7, 7, 22, tzinfo=UTC)
    candle_count = 291
    candles = tuple(
        Candle(
            starts_at=starts_at + timedelta(hours=index),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("12.5"),
        )
        for index in range(candle_count)
    )
    ends_at = starts_at + timedelta(hours=candle_count)
    report = analyze_range(
        candles,
        CandleInterval.ONE_HOUR,
        starts_at,
        ends_at,
        now=ends_at,
    )
    return dataset_store.write("coinbase", "BTC-USD", report).content_fingerprint


def _specification(
    strategy_fingerprint_value: str,
    dataset_fingerprint: str,
) -> ResearchRunSpecification:
    """Return one deterministic run specification matching the reference artifacts."""
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000067"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint_value,
        dataset_fingerprint=dataset_fingerprint,
        evaluation=EvaluationWindow(
            starts_at=datetime(2026, 7, 10, tzinfo=UTC),
            ends_at=datetime(2026, 7, 20, tzinfo=UTC),
        ),
        warmup=WarmupWindow(
            bars=50,
            starts_at=datetime(2026, 7, 7, 22, tzinfo=UTC),
        ),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.004",
            taker_fee_rate="0.006",
            fixed_slippage_bps="2.5",
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="next_candle_open",
        ),
        engine_contract_version="thytrader-bar-v1",
        random_seed=42,
    )


async def _cleanup(
    engine: AsyncEngine,
    run_fingerprints: set[str],
    strategy_fingerprint_value: str,
    dataset_fingerprint: str,
) -> None:
    """Remove rows created by this integration test in foreign-key order."""
    async with engine.begin() as connection:
        if run_fingerprints:
            await connection.execute(
                delete(published_research_run_specs).where(
                    published_research_run_specs.c.run_fingerprint.in_(run_fingerprints)
                )
            )
        await connection.execute(
            delete(strategy_dataset_bindings).where(
                strategy_dataset_bindings.c.strategy_fingerprint == strategy_fingerprint_value,
                strategy_dataset_bindings.c.dataset_fingerprint == dataset_fingerprint,
            )
        )
        await connection.execute(
            delete(published_strategy_versions).where(
                published_strategy_versions.c.strategy_fingerprint == strategy_fingerprint_value
            )
        )


def test_publication_revalidates_forged_spec_before_external_access(tmp_path: Path) -> None:
    """Invalid typed instances must fail before artifact lookup or transaction creation."""
    store = PostgresResearchRunStore(cast("AsyncEngine", _ExplodingEngine()))
    specification = _specification("sha256:" + "1" * 64, "sha256:" + "2" * 64)
    forged = specification.model_copy(update={"random_seed": -1})

    with pytest.raises(ResearchRunPublicationError, match="invalid"):
        asyncio.run(store.publish(forged, dataset_store=DatasetStore(tmp_path)))
    with pytest.raises(ResearchRunPublicationError, match="Invalid research run fingerprint"):
        asyncio.run(
            store.load(
                cast("str", b"sha256:" + b"1" * 64),
                dataset_store=DatasetStore(tmp_path),
            )
        )


@pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)
def test_postgres_publishes_and_reverifies_exact_research_run_spec(tmp_path: Path) -> None:
    """Publication is immutable, concurrent-safe, binding-gated, and fail-closed on reload."""

    async def exercise() -> None:
        if _TEST_DATABASE_URL is None:
            raise AssertionError("PostgreSQL integration URL was not configured.")
        engine = create_engine(SecretStr(_TEST_DATABASE_URL))
        strategy_store = PostgresStrategyPublicationStore(engine)
        run_store = PostgresResearchRunStore(engine)
        dataset_store = DatasetStore(tmp_path)
        definition = _strategy()
        strategy_fingerprint_value = strategy_fingerprint(definition)
        dataset_fingerprint = _dataset(dataset_store)
        specification = _specification(strategy_fingerprint_value, dataset_fingerprint)
        run_fingerprints: set[str] = set()

        try:
            await strategy_store.publish(definition)

            with pytest.raises(ResearchRunPublicationError, match="binding"):
                await run_store.publish(specification, dataset_store=dataset_store)

            await strategy_store.bind_dataset(
                strategy_fingerprint_value,
                dataset_fingerprint,
                dataset_store=dataset_store,
                bound_at=datetime(2026, 7, 29, 20, 1, tzinfo=UTC),
            )

            forged = specification.model_copy(update={"random_seed": -1})
            with pytest.raises(ResearchRunPublicationError, match="invalid"):
                await run_store.publish(forged, dataset_store=dataset_store)
            serializer_broken = specification.model_copy(update={"created_at": "bad"})
            with pytest.raises(ResearchRunPublicationError, match="invalid"):
                await run_store.publish(serializer_broken, dataset_store=dataset_store)

            second_engine = create_engine(SecretStr(_TEST_DATABASE_URL))
            second_store = PostgresResearchRunStore(second_engine)
            try:
                identical = await asyncio.gather(
                    run_store.publish(specification, dataset_store=dataset_store),
                    second_store.publish(specification, dataset_store=dataset_store),
                )
                run_fingerprints.add(identical[0].run_fingerprint)
                assert identical[0] == identical[1]

                conflict_base = specification.model_copy(
                    update={"run_id": UUID("019faf76-6600-7000-8000-000000000068")}
                )
                conflicting = conflict_base.model_copy(
                    update={
                        "capital": CapitalAssumptions(
                            quote_currency="USD",
                            initial_quote_balance="20000",
                        )
                    }
                )
                conflict_results = await asyncio.gather(
                    run_store.publish(conflict_base, dataset_store=dataset_store),
                    second_store.publish(conflicting, dataset_store=dataset_store),
                    return_exceptions=True,
                )
                successes = [
                    result
                    for result in conflict_results
                    if isinstance(result, PublishedResearchRunSpecification)
                ]
                errors = [
                    result
                    for result in conflict_results
                    if isinstance(result, ResearchRunPublicationError)
                ]
                assert len(successes) == 1
                assert len(errors) == 1
                run_fingerprints.add(successes[0].run_fingerprint)
                assert successes[0].specification.run_id == conflict_base.run_id
            finally:
                await dispose(second_engine)

            published = identical[0]
            assert published.specification == specification
            assert await run_store.publish(specification, dataset_store=dataset_store) == published
            assert (
                await run_store.load(published.run_fingerprint, dataset_store=dataset_store)
                == published
            )

            async with engine.begin() as connection:
                await connection.execute(
                    update(published_research_run_specs)
                    .where(
                        published_research_run_specs.c.run_fingerprint == published.run_fingerprint
                    )
                    .values(run_id="019faf76-6600-7000-8000-000000000069")
                )
            with pytest.raises(ResearchRunPublicationError, match="row identity"):
                await run_store.load(published.run_fingerprint, dataset_store=dataset_store)
            async with engine.begin() as connection:
                await connection.execute(
                    update(published_research_run_specs)
                    .where(
                        published_research_run_specs.c.run_fingerprint == published.run_fingerprint
                    )
                    .values(run_id=str(specification.run_id))
                )

            canonical = canonical_research_run_bytes(specification).decode("utf-8")
            async with engine.begin() as connection:
                await connection.execute(
                    update(published_research_run_specs)
                    .where(
                        published_research_run_specs.c.run_fingerprint == published.run_fingerprint
                    )
                    .values(canonical_specification=f" {canonical}")
                )
            with pytest.raises(ResearchRunPublicationError, match="not canonical"):
                await run_store.load(published.run_fingerprint, dataset_store=dataset_store)
            async with engine.begin() as connection:
                await connection.execute(
                    update(published_research_run_specs)
                    .where(
                        published_research_run_specs.c.run_fingerprint == published.run_fingerprint
                    )
                    .values(canonical_specification=canonical)
                )

            async with engine.begin() as connection:
                await connection.execute(
                    update(published_research_run_specs)
                    .where(
                        published_research_run_specs.c.run_fingerprint == published.run_fingerprint
                    )
                    .values(created_at=specification.created_at + timedelta(milliseconds=1))
                )
            with pytest.raises(ResearchRunPublicationError, match="row identity"):
                await run_store.load(published.run_fingerprint, dataset_store=dataset_store)
            async with engine.begin() as connection:
                await connection.execute(
                    update(published_research_run_specs)
                    .where(
                        published_research_run_specs.c.run_fingerprint == published.run_fingerprint
                    )
                    .values(created_at=specification.created_at)
                )

            for column, value in (
                (published_research_run_specs.c.strategy_fingerprint, "sha256:" + "3" * 64),
                (published_research_run_specs.c.dataset_fingerprint, "sha256:" + "4" * 64),
            ):
                with pytest.raises(IntegrityError):
                    async with engine.begin() as connection:
                        await connection.execute(
                            update(published_research_run_specs)
                            .where(
                                published_research_run_specs.c.run_fingerprint
                                == published.run_fingerprint
                            )
                            .values({column: value})
                        )

            manifest_path = (
                tmp_path / "manifests" / (f"{dataset_fingerprint.removeprefix('sha256:')}.json")
            )
            manifest_path.unlink()
            with pytest.raises(ResearchRunPublicationError, match="artifact"):
                await run_store.load(published.run_fingerprint, dataset_store=dataset_store)
        finally:
            await _cleanup(
                engine,
                run_fingerprints | {research_run_fingerprint(specification)},
                strategy_fingerprint_value,
                dataset_fingerprint,
            )
            await dispose(engine)

    asyncio.run(exercise())
