"""Live PostgreSQL tests for mutable strategies, snapshots, and cascading deletion (ADR 0082)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import func, select, text

from thytrader.backtest.submission import BacktestStartRequest
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)
from thytrader.market_data.datasets import DatasetStore
from thytrader.market_data.models import Candle, CandleInterval
from thytrader.market_data.quality import analyze_range
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_research_jobs import PostgresResearchJobStore
from thytrader.persistence.postgres_risk import PostgresRiskPolicyStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.postgres_studies import PostgresResearchStudyCatalog
from thytrader.persistence.schema import (
    deployments,
    execution_fills,
    execution_orders,
    order_intents,
    published_backtest_results,
    published_research_run_specs,
    published_research_studies,
    research_jobs,
    research_study_strategies,
    strategies,
    strategy_dataset_bindings,
    strategy_snapshots,
)
from thytrader.research.catalog import StudyCatalogSummary
from thytrader.risk.models import CapitalAllocation
from thytrader.strategies.authoring import create_template_strategy, new_strategy_identity
from thytrader.strategies.library import (
    StrategyDeletionBlockedError,
    StrategyInvalidError,
    StrategyNotFoundError,
    StrategyOrigin,
    StrategyRevisionConflictError,
    bulk_delete_strategies,
    create_strategy_from_definition,
)
from thytrader.strategies.models import (
    StrategyDefinition,
    canonical_strategy_bytes,
    strategy_fingerprint,
)
from thytrader.strategies.snapshots import StrategyDatasetMismatchError, StrategySnapshotError

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.strategies.library import StrategyRecord

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    _TEST_DATABASE_URL is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
)


def _engine() -> AsyncEngine:
    """Open one engine against the configured integration database."""
    if _TEST_DATABASE_URL is None:
        raise AssertionError("PostgreSQL integration URL was not configured.")
    return create_engine(SecretStr(_TEST_DATABASE_URL))


async def _template(store: PostgresStrategyStore) -> StrategyRecord:
    """Create one valid template strategy with a fresh identity."""
    return await create_strategy_from_definition(store, create_template_strategy())


def test_snapshots_deduplicate_and_fingerprints_are_stable() -> None:
    """Identical definitions share one snapshot; an edit yields a new, verifiable one."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        try:
            record = await _template(store)
            assert record.definition is not None
            assert record.current_fingerprint == strategy_fingerprint(record.definition)
            first = await store.snapshot(record.strategy_id)
            second = await store.snapshot(record.strategy_id)
            assert first == second
            assert first.strategy_fingerprint == record.current_fingerprint
            async with engine.connect() as connection:
                count = (
                    await connection.execute(
                        select(func.count())
                        .select_from(strategy_snapshots)
                        .where(strategy_snapshots.c.strategy_id == str(record.strategy_id))
                    )
                ).scalar_one()
                stored = (
                    await connection.execute(
                        select(strategy_snapshots.c.canonical_definition).where(
                            strategy_snapshots.c.strategy_fingerprint == first.strategy_fingerprint
                        )
                    )
                ).scalar_one()
            assert count == 1
            assert stored == canonical_strategy_bytes(record.definition).decode("utf-8")
            assert '"version"' not in stored
            assert '"status"' not in stored

            edited = dict(record.document)
            edited["name"] = "Edited name"
            saved = await store.save(record.strategy_id, edited, expected_revision=1)
            assert saved.revision == 2
            assert saved.current_fingerprint != first.strategy_fingerprint
            third = await store.snapshot(record.strategy_id)
            assert third.strategy_fingerprint == saved.current_fingerprint
            reloaded = await store.load(first.strategy_fingerprint)
            assert reloaded.definition == first.definition
            lookup = await store.lookup_snapshot(first.strategy_fingerprint)
            assert lookup.strategy_id == record.strategy_id
            assert lookup.is_current is False
            assert (await store.lookup_snapshot(third.strategy_fingerprint)).is_current is True
            await store.delete(record.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_stale_revision_is_rejected_and_invalid_work_in_progress_blocks_snapshots() -> None:
    """A stale save never overwrites; an invalid saved document cannot start anything."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        try:
            record = await _template(store)
            broken = dict(record.document)
            broken["indicators"] = []
            saved = await store.save(record.strategy_id, broken, expected_revision=1)
            assert saved.revision == 2
            assert saved.validation.valid is False
            assert saved.validation.issues
            assert saved.current_fingerprint is None
            assert saved.definition is None
            assert saved.document["indicators"] == []
            with pytest.raises(StrategyRevisionConflictError) as conflict:
                await store.save(record.strategy_id, record.document, expected_revision=1)
            assert conflict.value.current_revision == 2
            assert (await store.get(record.strategy_id)).document["indicators"] == []
            with pytest.raises(StrategyInvalidError):
                await store.snapshot(record.strategy_id)
            with pytest.raises(StrategyNotFoundError):
                await store.snapshot(uuid4())
            repaired = await store.save(record.strategy_id, record.document, expected_revision=2)
            assert repaired.validation.valid is True
            assert repaired.current_fingerprint == record.current_fingerprint
            await store.delete(record.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_concurrent_saves_with_one_revision_have_exactly_one_winner() -> None:
    """Two writers racing on one revision: one commits, the other gets a conflict."""

    async def exercise() -> None:
        engines = [_engine(), _engine()]
        stores = [PostgresStrategyStore(engine) for engine in engines]
        try:
            record = await _template(stores[0])
            documents = []
            for name in ("Writer A", "Writer B"):
                document = dict(record.document)
                document["name"] = name
                documents.append(document)
            outcomes = await asyncio.gather(
                *(
                    store.save(record.strategy_id, document, expected_revision=1)
                    for store, document in zip(stores, documents, strict=True)
                ),
                return_exceptions=True,
            )
            winners = [item for item in outcomes if not isinstance(item, BaseException)]
            losers = [item for item in outcomes if isinstance(item, StrategyRevisionConflictError)]
            assert len(winners) == 1
            assert len(losers) == 1
            assert (await stores[0].get(record.strategy_id)).revision == 2
            await stores[0].delete(record.strategy_id)
        finally:
            for engine in engines:
                await dispose(engine)

    asyncio.run(exercise())


def _deployment(
    record: StrategyRecord, fingerprint: str, *, mode: DeploymentMode, status: DeploymentStatus
) -> Deployment:
    """Build one strategy deployment bound to a snapshot."""
    identifier, now = new_strategy_identity()
    paper = mode is DeploymentMode.PAPER
    return Deployment(
        id=identifier,
        strategy_fingerprint=fingerprint,
        strategy_id=record.strategy_id,
        strategy_name=record.name,
        product_id="BTC-USD",
        timeframe="1h",
        mode=mode,
        status=status,
        cash=Decimal(100),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        paper_starting_cash=Decimal(100) if paper else None,
        paper_maker_fee_rate=Decimal("0.001") if paper else None,
        paper_taker_fee_rate=Decimal("0.002") if paper else None,
    )


async def _seed_ledger(engine: AsyncEngine, deployment_id: UUID) -> None:
    """Insert one intent, order, and fill for a deployment."""
    intent, order = uuid4(), uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            order_intents.insert().values(
                id=intent,
                deployment_id=deployment_id,
                client_order_id=f"c-{intent}",
                purpose="entry",
                side="buy",
                kind="post_only_limit",
                quantity="0.01",
                candle_starts_at=_NOW,
                status="filled",
                product_id="BTC-USD",
                created_at=_NOW,
            )
        )
        await connection.execute(
            execution_orders.insert().values(
                id=order,
                deployment_id=deployment_id,
                intent_id=intent,
                client_order_id=f"o-{order}",
                side="buy",
                kind="post_only_limit",
                quantity="0.01",
                filled_quantity="0.01",
                status="filled",
                product_id="BTC-USD",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await connection.execute(
            execution_fills.insert().values(
                id=uuid4(),
                deployment_id=deployment_id,
                order_id=order,
                venue_fill_id=f"f-{order}",
                price="60000",
                quantity="0.01",
                fee="0.6",
                filled_at=_NOW,
            )
        )


async def _seed_research(engine: AsyncEngine, record: StrategyRecord, fingerprint: str) -> str:
    """Insert one binding, run spec, result, job, and study for a snapshot."""
    dataset = "sha256:" + uuid4().hex + uuid4().hex
    run = "sha256:" + uuid4().hex + uuid4().hex
    study = "sha256:" + uuid4().hex + uuid4().hex
    strategy_id = str(record.strategy_id)
    async with engine.begin() as connection:
        await connection.execute(
            strategy_dataset_bindings.insert().values(
                strategy_fingerprint=fingerprint,
                dataset_fingerprint=dataset,
                strategy_id=strategy_id,
                bound_at=_NOW,
            )
        )
        await connection.execute(
            published_research_run_specs.insert().values(
                run_fingerprint=run,
                run_id=str(uuid4()),
                created_at=_NOW,
                strategy_fingerprint=fingerprint,
                strategy_id=strategy_id,
                dataset_fingerprint=dataset,
                canonical_specification="{}",
                published_at=_NOW,
            )
        )
        await connection.execute(
            published_backtest_results.insert().values(
                result_fingerprint="sha256:" + uuid4().hex + uuid4().hex,
                run_fingerprint=run,
                strategy_fingerprint=fingerprint,
                strategy_id=strategy_id,
                dataset_fingerprint=dataset,
                signal_trace_fingerprint=run,
                canonical_result="{}",
                published_at=_NOW,
            )
        )
        await connection.execute(
            research_jobs.insert().values(
                job_id=uuid4(),
                kind="backtest",
                status="completed",
                strategy_id=strategy_id,
                strategy_fingerprint=fingerprint,
                payload="{}",
                created_at=_NOW,
                updated_at=_NOW,
                expires_at=_NOW + timedelta(days=1),
            )
        )
        await connection.execute(
            published_research_studies.insert().values(
                study_fingerprint=study,
                strategy_id=strategy_id,
                request_fingerprint=study,
                plan_fingerprint=study,
                kind="walk_forward",
                product_id="BTC-USD",
                timeframe="1h",
                window_count=1,
                canonical_study="{}",
                published_at=_NOW,
            )
        )
        await connection.execute(
            research_study_strategies.insert().values(
                study_fingerprint=study, strategy_id=strategy_id
            )
        )
    return study


async def _count_rows(engine: AsyncEngine, table: str, column: str, value: str) -> int:
    """Count rows of one table matching one column."""
    async with engine.connect() as connection:
        statement = text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :value")  # noqa: S608
        return int((await connection.execute(statement, {"value": value})).scalar_one())


def test_delete_cascades_research_and_paper_but_keeps_stopped_live_history() -> None:
    """Deletion removes research and paper books; stopped live books stay, detached."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        execution = PostgresExecutionStore(engine)
        try:
            record = await _template(store)
            snapshot = await store.snapshot(record.strategy_id)
            fingerprint = snapshot.strategy_fingerprint
            await _seed_research(engine, record, fingerprint)
            paper = _deployment(
                record, fingerprint, mode=DeploymentMode.PAPER, status=DeploymentStatus.STOPPED
            )
            live = _deployment(
                record, fingerprint, mode=DeploymentMode.LIVE, status=DeploymentStatus.STOPPED
            )
            await execution.create_deployment(paper)
            await execution.create_deployment(live)
            await _seed_ledger(engine, paper.id)
            await _seed_ledger(engine, live.id)

            preview = await store.preview_deletion(record.strategy_id)
            assert preview.blocking_deployment_ids == ()
            assert preview.counts.backtests == 1
            assert preview.counts.paper_deployments == 1
            assert preview.counts.live_deployments_kept == 1
            assert preview.counts.snapshots == 0

            result = await store.delete(record.strategy_id)
            assert result.counts.studies == 1
            assert result.counts.research_jobs == 1
            sid = str(record.strategy_id)
            for table in (
                "strategies",
                "published_backtest_results",
                "published_research_run_specs",
                "strategy_dataset_bindings",
                "research_jobs",
                "published_research_studies",
                "research_study_strategies",
            ):
                assert await _count_rows(engine, table, "strategy_id", sid) == 0, table
            assert await _count_rows(engine, "deployments", "id", str(paper.id)) == 0
            assert await _count_rows(engine, "execution_fills", "deployment_id", str(paper.id)) == 0
            kept = (await execution.get_deployment(live.id)).deployment
            assert kept.strategy_id is None
            assert kept.strategy_deleted is True
            assert kept.strategy_name == record.name
            assert kept.strategy_fingerprint == fingerprint
            assert await _count_rows(engine, "execution_fills", "deployment_id", str(live.id)) == 1
            assert await _count_rows(engine, "order_intents", "deployment_id", str(live.id)) == 1
            detached = await store.lookup_snapshot(fingerprint)
            assert detached.strategy_id is None
            assert detached.snapshot.definition == snapshot.definition
            with pytest.raises(StrategyNotFoundError):
                await store.get(record.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_delete_is_refused_while_a_bot_is_running_or_paused() -> None:
    """A running or paused deployment blocks deletion and nothing changes."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        execution = PostgresExecutionStore(engine)
        try:
            record = await _template(store)
            snapshot = await store.snapshot(record.strategy_id)
            running = _deployment(
                record,
                snapshot.strategy_fingerprint,
                mode=DeploymentMode.PAPER,
                status=DeploymentStatus.RUNNING,
            )
            await execution.create_deployment(running)
            with pytest.raises(StrategyDeletionBlockedError) as blocked:
                await store.delete(record.strategy_id)
            assert blocked.value.deployment_ids == (running.id,)
            assert (await store.get(record.strategy_id)).strategy_id == record.strategy_id
            preview = await store.preview_deletion(record.strategy_id)
            assert preview.blocking_deployment_ids == (running.id,)
            async with engine.begin() as connection:
                await connection.execute(
                    deployments.update()
                    .where(deployments.c.id == running.id)
                    .values(status="stopped")
                )
            await store.delete(record.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_bulk_delete_reports_partial_results() -> None:
    """One blocked, one missing, and one deletable strategy each get their own outcome."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        execution = PostgresExecutionStore(engine)
        try:
            deletable = await _template(store)
            blocked = await _template(store)
            snapshot = await store.snapshot(blocked.strategy_id)
            running = _deployment(
                blocked,
                snapshot.strategy_fingerprint,
                mode=DeploymentMode.PAPER,
                status=DeploymentStatus.PAUSED,
            )
            await execution.create_deployment(running)
            missing = uuid4()
            identities = (deletable.strategy_id, blocked.strategy_id, missing)
            preview = await bulk_delete_strategies(store, identities, dry_run=True)
            assert [item.outcome for item in preview.items] == [
                "would_delete",
                "blocked",
                "not_found",
            ]
            assert (await store.get(deletable.strategy_id)).revision == 1
            report = await bulk_delete_strategies(store, identities, dry_run=False)
            assert [item.outcome for item in report.items] == ["deleted", "blocked", "not_found"]
            assert report.items[1].code == "strategy_has_active_deployments"
            assert report.items[1].deployment_ids == (running.id,)
            async with engine.begin() as connection:
                await connection.execute(
                    deployments.update()
                    .where(deployments.c.id == running.id)
                    .values(status="stopped")
                )
            await store.delete(blocked.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_delete_removes_the_strategy_allocation_by_republishing_the_policy() -> None:
    """An allocation for the deleted strategy is dropped in a new policy version."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        risk = PostgresRiskPolicyStore(engine)
        original = await risk.load_active()
        try:
            record = await _template(store)
            other = uuid4()
            with_allocation = original.definition.model_copy(
                update={
                    "version": original.definition.version + 100,
                    "paper_capital_quote": "1000",
                    "allocations": (
                        CapitalAllocation(strategy_id=record.strategy_id, allocated_quote="10"),
                        CapitalAllocation(strategy_id=other, allocated_quote="20"),
                    ),
                }
            )
            published = await risk.publish(with_allocation)
            preview = await store.preview_deletion(record.strategy_id)
            assert preview.counts.allocations_removed == 1
            result = await store.delete(record.strategy_id)
            assert result.risk_policy_republished is True
            active = await risk.load_active()
            assert active.policy_fingerprint != published.policy_fingerprint
            assert active.definition.version == with_allocation.version + 1
            assert [item.strategy_id for item in active.definition.allocations] == [other]
        finally:
            latest = await risk.load_active()
            await risk.publish(
                original.definition.model_copy(update={"version": latest.definition.version + 1})
            )
            await dispose(engine)

    asyncio.run(exercise())


def test_bindings_and_derived_snapshots_belong_to_their_strategy(tmp_path: Path) -> None:
    """Dataset bindings copy the owner; derived snapshots need an existing owner."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        datasets = DatasetStore(tmp_path)
        starts_at = datetime(2026, 7, 29, 15, tzinfo=UTC)
        candles = tuple(
            Candle(
                starts_at=starts_at + timedelta(hours=index),
                open=Decimal(100),
                high=Decimal(110),
                low=Decimal(90),
                close=Decimal(105),
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
        manifest = datasets.write("coinbase", "BTC-USD", report)
        wrong = datasets.write("coinbase", "ETH-USD", report)
        try:
            definition = create_template_strategy(product_id="BTC-USD")
            record = await create_strategy_from_definition(store, definition)
            snapshot = await store.snapshot(record.strategy_id)
            binding = await store.bind_dataset(
                snapshot.strategy_fingerprint,
                manifest.content_fingerprint,
                dataset_store=datasets,
                bound_at=_NOW,
            )
            again = await store.bind_dataset(
                snapshot.strategy_fingerprint,
                manifest.content_fingerprint,
                dataset_store=datasets,
                bound_at=_NOW + timedelta(hours=1),
            )
            assert again == binding
            with pytest.raises(StrategySnapshotError, match="does not match"):
                await store.bind_dataset(
                    snapshot.strategy_fingerprint,
                    wrong.content_fingerprint,
                    dataset_store=datasets,
                    bound_at=_NOW,
                )
            async with engine.connect() as connection:
                owner = (
                    await connection.execute(
                        select(strategy_dataset_bindings.c.strategy_id).where(
                            strategy_dataset_bindings.c.strategy_fingerprint
                            == snapshot.strategy_fingerprint
                        )
                    )
                ).scalar_one()
            assert owner == str(record.strategy_id)
            variant = definition.model_copy(update={"name": "Sweep variant"})
            derived = await store.record_snapshot(variant)
            assert (await store.lookup_snapshot(derived.strategy_fingerprint)).strategy_id == (
                record.strategy_id
            )
            orphan_id, _created = new_strategy_identity()
            with pytest.raises(StrategySnapshotError, match="Owning strategy"):
                await store.record_snapshot(variant.model_copy(update={"strategy_id": orphan_id}))
            result = await store.delete(record.strategy_id)
            assert result.counts.snapshots == 2
            assert result.counts.dataset_bindings == 1
            async with engine.connect() as connection:
                remaining = (
                    await connection.execute(
                        select(func.count())
                        .select_from(strategies)
                        .where(strategies.c.strategy_id == str(record.strategy_id))
                    )
                ).scalar_one()
            assert remaining == 0
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_multi_instrument_documents_bind_every_covered_product(tmp_path: Path) -> None:
    """An additional instrument's dataset binds; a product the document omits does not."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        datasets = DatasetStore(tmp_path)
        starts_at = datetime(2026, 7, 29, 15, tzinfo=UTC)
        candles = tuple(
            Candle(
                starts_at=starts_at + timedelta(hours=index),
                open=Decimal(100),
                high=Decimal(110),
                low=Decimal(90),
                close=Decimal(105),
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
        primary = datasets.write("coinbase", "BTC-USD", report)
        extra = datasets.write("coinbase", "ETH-USD", report)
        uncovered = datasets.write("coinbase", "SOL-USD", report)
        try:
            template = create_template_strategy(product_id="BTC-USD")
            definition = StrategyDefinition.model_validate(
                {
                    **template.model_dump(mode="json"),
                    "additional_instruments": [
                        {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
                    ],
                }
            )
            record = await create_strategy_from_definition(store, definition)
            snapshot = await store.snapshot(record.strategy_id)
            for manifest in (primary, extra):
                binding = await store.bind_dataset(
                    snapshot.strategy_fingerprint,
                    manifest.content_fingerprint,
                    dataset_store=datasets,
                    bound_at=_NOW,
                )
                assert binding.dataset_fingerprint == manifest.content_fingerprint
                loaded = await store.load_binding(
                    snapshot.strategy_fingerprint,
                    manifest.content_fingerprint,
                    dataset_store=datasets,
                )
                assert loaded == binding
            with pytest.raises(StrategyDatasetMismatchError, match="SOL-USD 1h") as rejected:
                await store.bind_dataset(
                    snapshot.strategy_fingerprint,
                    uncovered.content_fingerprint,
                    dataset_store=datasets,
                    bound_at=_NOW,
                )
            assert "BTC-USD, ETH-USD" in str(rejected.value)
            assert isinstance(rejected.value, StrategySnapshotError)
            await store.delete(record.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_studies_are_listed_for_every_member_strategy_and_deleted_with_any_of_them() -> None:
    """A cross-strategy study lists under each member and disappears when one is deleted."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        catalog = PostgresResearchStudyCatalog(engine)
        try:
            first = await _template(store)
            second = await _template(store)
            first_snapshot = await store.snapshot(first.strategy_id)
            second_snapshot = await store.snapshot(second.strategy_id)
            study = "sha256:" + uuid4().hex + uuid4().hex
            canonical = json.dumps(
                {
                    "study_fingerprint": study,
                    "windows": [
                        {"strategy_fingerprint": first_snapshot.strategy_fingerprint},
                        {"strategy_fingerprint": second_snapshot.strategy_fingerprint},
                    ],
                }
            )
            summary = StudyCatalogSummary(
                study_fingerprint=study,
                request_fingerprint=study,
                plan_fingerprint=study,
                kind="cross_market",
                published_at=_NOW,
                product_id="BTC-USD",
                timeframe="1h",
                window_count=2,
            )
            await catalog.persist(summary, canonical)
            for member in (first, second):
                rows = await catalog.list_summaries(strategy_id=member.strategy_id)
                assert [row.study_fingerprint for row in rows] == [study]
            async with engine.connect() as connection:
                owner = (
                    await connection.execute(
                        select(published_research_studies.c.strategy_id).where(
                            published_research_studies.c.study_fingerprint == study
                        )
                    )
                ).scalar_one()
            assert owner == str(first.strategy_id)
            result = await store.delete(second.strategy_id)
            assert result.counts.studies == 1
            assert await catalog.list_summaries(strategy_id=first.strategy_id) == ()
            await store.delete(first.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_research_jobs_list_newest_first_for_one_strategy() -> None:
    """Async jobs carry their strategy and snapshot and list per strategy."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        jobs = PostgresResearchJobStore(engine)
        try:
            record = await _template(store)
            snapshot = await store.snapshot(record.strategy_id)
            request = BacktestStartRequest.model_validate(
                {
                    "strategy_id": str(record.strategy_id),
                    "dataset_fingerprint": "sha256:" + "d" * 64,
                    "initial_quote_balance": "10000",
                    "maker_fee_rate": "0.001",
                    "taker_fee_rate": "0.002",
                    "fixed_slippage_bps": "1",
                }
            ).submission(snapshot.strategy_fingerprint)
            created = await jobs.create_backtest(request, strategy_id=record.strategy_id)
            assert created.strategy_id == record.strategy_id
            assert created.strategy_fingerprint == snapshot.strategy_fingerprint
            listed = await jobs.list_for_strategy(record.strategy_id, limit=5)
            assert [item.job_id for item in listed] == [created.job_id]
            result = await store.delete(record.strategy_id)
            assert result.counts.research_jobs == 1
            assert await jobs.get(created.job_id) is None
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_legacy_marketable_limit_row_cannot_run_but_its_snapshot_still_verifies() -> None:
    """A row saved valid before the value was retired reads invalid; old snapshots load."""

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        try:
            record = await _template(store)
            assert record.definition is not None
            payload = record.definition.model_dump(mode="python")
            payload["execution"]["entry_preference"] = "marketable_limit"
            legacy = StrategyDefinition.model_validate(payload)
            canonical = canonical_strategy_bytes(legacy).decode("utf-8")
            fingerprint = strategy_fingerprint(legacy)
            async with engine.begin() as connection:
                await connection.execute(
                    strategies.update()
                    .where(strategies.c.strategy_id == str(record.strategy_id))
                    .values(document=canonical, is_valid=True, current_fingerprint=fingerprint)
                )
                await connection.execute(
                    strategy_snapshots.insert().values(
                        strategy_fingerprint=fingerprint,
                        strategy_id=str(record.strategy_id),
                        canonical_definition=canonical,
                        created_at=_NOW,
                    )
                )
            loaded = await store.get(record.strategy_id)
            assert loaded.definition is None
            assert [issue.loc for issue in loaded.validation.issues] == [
                "execution.entry_preference"
            ]
            with pytest.raises(StrategyInvalidError):
                await store.snapshot(record.strategy_id)
            assert (await store.load(fingerprint)).definition == legacy
            await store.delete(record.strategy_id)
        finally:
            await dispose(engine)

    asyncio.run(exercise())


def test_library_tag_filter_uses_document_metadata_tags() -> None:
    """``list_page(tag=)`` matches valid and draft documents by ``metadata.tags`` (ADR 0094)."""
    tag = f"batch-{uuid4().hex[:8]}"

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        created: list[StrategyRecord] = []
        try:

            def tagged() -> StrategyDefinition:
                """A fresh template identity carrying the test tag."""
                payload = create_template_strategy().model_dump(mode="python")
                payload["metadata"] = {"tags": (tag, "majors"), "notes": ()}
                return StrategyDefinition.model_validate(payload)

            created.append(await create_strategy_from_definition(store, tagged()))
            created.append(await create_strategy_from_definition(store, tagged()))
            created.append(await _template(store))
            draft = await create_strategy_from_definition(store, tagged())
            created.append(draft)
            document = dict(draft.document)
            document["entry"] = {"when": {"alll": []}}
            saved = await store.save(draft.strategy_id, document, expected_revision=1)
            assert saved.validation.valid is False
            first = await store.list_page(limit=2, offset=0, tag=tag)
            second = await store.list_page(limit=2, offset=2, tag=tag)
            assert first.total == 3
            matched = {record.strategy_id for record in first.records + second.records}
            assert matched == {created[0].strategy_id, created[1].strategy_id, draft.strategy_id}
            assert (await store.list_page(limit=10, offset=0, tag=f"{tag}-absent")).total == 0
            everything = await store.list_page(limit=100, offset=0)
            assert everything.total >= 4
        finally:
            for record in created:
                await store.delete(record.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())


def test_library_origin_filter_matches_research_tags_in_sql() -> None:
    """``list_page(origin=)`` uses a JSON path: claude-research or research-* (ADR 0098)."""
    marker = f"origin-{uuid4().hex[:8]}"

    async def exercise() -> None:
        engine = _engine()
        store = PostgresStrategyStore(engine)
        created: list[StrategyRecord] = []
        try:

            def tagged(*tags: str) -> StrategyDefinition:
                """A fresh template identity carrying ``marker`` plus ``tags``."""
                payload = create_template_strategy().model_dump(mode="python")
                payload["metadata"] = {"tags": (marker, *tags), "notes": ()}
                return StrategyDefinition.model_validate(payload)

            claude = await create_strategy_from_definition(store, tagged("claude-research"))
            variant = await create_strategy_from_definition(store, tagged("research-sweep"))
            mine = await create_strategy_from_definition(store, tagged("researcher-notes"))
            untagged = await _template(store)
            created.extend((claude, variant, mine, untagged))
            research = await store.list_page(
                limit=100, offset=0, tag=marker, origin=StrategyOrigin.RESEARCH
            )
            operator = await store.list_page(
                limit=100, offset=0, tag=marker, origin=StrategyOrigin.OPERATOR
            )
            assert {record.strategy_id for record in research.records} == {
                claude.strategy_id,
                variant.strategy_id,
            }
            assert research.total == 2
            assert [record.strategy_id for record in operator.records] == [mine.strategy_id]
            everyone = await store.list_page(limit=100, offset=0, origin=StrategyOrigin.OPERATOR)
            assert untagged.strategy_id in {record.strategy_id for record in everyone.records}
            assert claude.strategy_id not in {record.strategy_id for record in everyone.records}
        finally:
            for record in created:
                await store.delete(record.strategy_id)
            await dispose(engine)

    asyncio.run(exercise())
