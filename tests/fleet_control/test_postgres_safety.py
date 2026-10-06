"""Isolated PostgreSQL regressions for fleet fencing, receipts, and fail-closed admission.

Every schema is initialized by the real Alembic revision upgrades, not create_all.
The URL must name a loopback test database and may never use production port 5439.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import os
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from thytrader.execution.entry_latch import clear_entry_inhibition_cache
from thytrader.execution.ids import uuid7
from thytrader.execution.lifecycle import entries_allowed
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    IntentPurpose,
    LifecycleCommand,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.fleet_control import postgres as fleet_postgres, service as fleet_service
from thytrader.fleet_control.admission import refresh_process_entry_inhibition
from thytrader.fleet_control.inventory import read_stable_inventory
from thytrader.fleet_control.models import (
    ExpectedInhibition,
    ExpectedTarget,
    FleetAction,
    FleetExecuteRequest,
    FleetModeScope,
    FleetOperation,
    FleetOperationStatus,
    FleetTargetStatus,
    TargetResult,
)
from thytrader.fleet_control.postgres import PostgresFleetControlStore
from thytrader.fleet_control.service import execute_fleet
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.schema import (
    deployments,
    fleet_control_operations,
    fleet_entry_inhibition,
    order_intents,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from sqlalchemy.engine import Connection

    from thytrader.execution.store import ExecutionStore

_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
_NOW = datetime(2026, 10, 6, tzinfo=UTC)
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(_URL is None, reason="Private test PostgreSQL URL required."),
]


def _safe_url() -> str:
    """Reject non-test/non-loopback databases before even creating an engine."""
    if _URL is None:
        raise RuntimeError("Private test database URL required.")
    parsed = urlsplit(_URL)
    if (
        parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port == 5439
        or "test" not in parsed.path
    ):
        raise RuntimeError("Refusing unsafe test database target.")
    return _URL


def _engine(schema: str) -> AsyncEngine:
    """Use only this fixture's generated private schema, without pooled loop state."""
    return create_async_engine(
        _safe_url(), poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
    )


def _migrate(connection: Connection) -> None:
    """Execute the real revision chain with Alembic's operations proxy.

    Revision modules are Alembic's dynamic boundary; validate the upgrade callable
    immediately before invoking it. No Settings/.env or production URL is read.
    """
    context = MigrationContext.configure(connection)
    script = ScriptDirectory("alembic")
    with Operations.context(context):
        for revision in reversed(list(script.walk_revisions())):
            upgrade = getattr(revision.module, "upgrade", None)
            if not callable(upgrade):
                raise TypeError("Migration has no upgrade callable.")
            cast("Callable[[], None]", upgrade)()


@pytest.fixture
def anyio_backend() -> str:
    """Run database tests on asyncio, the driver's supported backend."""
    return "asyncio"


@pytest.fixture(scope="module")
def private_schema() -> Iterator[str]:
    """Migrate and remove an exclusively owned test schema on the private database."""
    schema = f"controls_test_{uuid4().hex}"

    async def prepare() -> None:
        admin = create_async_engine(_safe_url(), poolclass=NullPool)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await admin.dispose()
        engine = _engine(schema)
        async with engine.begin() as connection:
            await connection.run_sync(_migrate)
        await engine.dispose()

    async def remove() -> None:
        admin = create_async_engine(_safe_url(), poolclass=NullPool)
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()

    asyncio.run(prepare())
    yield schema
    asyncio.run(remove())


@pytest.fixture
async def database(private_schema: str) -> AsyncIterator[AsyncEngine]:
    """Reset only the fixture's schema; durable latch rows originate in migration 0067."""
    engine = _engine(private_schema)
    async with engine.begin() as connection:
        await connection.execute(delete(fleet_control_operations))
        await connection.execute(delete(order_intents))
        await connection.execute(delete(deployments))
        await connection.execute(
            fleet_entry_inhibition.update().values(inhibited=False, revision=0)
        )
    yield engine
    await engine.dispose()


def _book(_index: int = 0) -> Deployment:
    """An exact-decimal paper book; timestamp ties deliberately exercise UUID ordering."""
    return Deployment(
        id=uuid7(_NOW),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USDC",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="5m",
        revision=4,
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
    )


def _request(
    action: FleetAction, key: str, revision: int = 0, books: tuple[Deployment, ...] = ()
) -> FleetExecuteRequest:
    """Explicitly confirm latch/book revisions, never fetch newer consent on retry."""
    return FleetExecuteRequest(
        action,
        FleetModeScope.PAPER,
        key,
        tuple(ExpectedTarget(row.id, row.revision) for row in books),
        False,
        not books,
        ExpectedInhibition(revision, None),
    )


def _intent(book: Deployment, purpose: IntentPurpose) -> OrderIntent:
    """A synthetic test intent, not a venue submission."""
    return OrderIntent(
        id=uuid7(_NOW),
        deployment_id=book.id,
        client_order_id=str(uuid4()),
        purpose=purpose,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        created_at=_NOW,
        candle_starts_at=_NOW,
        status=OrderStatus.PENDING,
    )


async def test_guarded_revision_preserves_a_pause_between_preview_and_write(
    database: AsyncEngine,
) -> None:
    """A racing deliberate pause wins; stale flatten cannot overwrite its revision."""
    book = _book()
    ordinary = PostgresExecutionStore(database)
    await ordinary.create_deployment(book)

    class Racing(PostgresExecutionStore):
        async def record_confirmed_fleet_command(
            self,
            connection: AsyncConnection,
            expected: ExpectedTarget,
            action: FleetAction,
            mode: FleetModeScope,
            *,
            now: datetime,
        ) -> TargetResult:
            """Change the book after the request's snapshot, before the real locked mutation."""
            await ordinary.save_deployment(
                replace(
                    book,
                    status=DeploymentStatus.PAUSED,
                    lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
                ),
                expected_revision=book.revision,
            )
            return await super().record_confirmed_fleet_command(
                connection, expected, action, mode, now=now
            )

    result = await execute_fleet(
        execution=Racing(database),
        fleet=PostgresFleetControlStore(database),
        request=_request(FleetAction.FLATTEN, "race", books=(book,)),
    )
    assert result.status is FleetOperationStatus.PARTIAL
    assert result.targets[0].status is FleetTargetStatus.REVISION_CONFLICT
    current = (await ordinary.get_deployment(book.id)).deployment
    assert current.status is DeploymentStatus.PAUSED
    assert current.lifecycle_command is LifecycleCommand.STOP_NEW_ENTRIES


async def test_two_instances_serialize_an_existing_pending_key(
    database: AsyncEngine, private_schema: str
) -> None:
    """A unique insert alone would allow both instances past the same pending intent."""
    entered, release = asyncio.Event(), asyncio.Event()
    first_execution = PostgresExecutionStore(database)
    book = await first_execution.create_deployment(_book())

    class Waiting(PostgresFleetControlStore):
        async def record_target(
            self,
            execution: ExecutionStore,
            operation: FleetOperation,
            expected: ExpectedTarget,
            *,
            now: datetime,
        ) -> FleetOperation:
            """Hold the first instance after its pending intent has committed."""
            entered.set()
            await release.wait()
            return await super().record_target(execution, operation, expected, now=now)

    other_engine = _engine(private_schema)
    other = PostgresFleetControlStore(other_engine)
    request = _request(FleetAction.MANAGED_STOP, "same-key", books=(book,))
    first = asyncio.create_task(
        execute_fleet(execution=first_execution, fleet=Waiting(database), request=request)
    )
    await asyncio.wait_for(entered.wait(), 5)
    pending = await other.get_operation(request.idempotency_key)
    assert pending is not None and pending.status is FleetOperationStatus.PENDING
    second = asyncio.create_task(
        execute_fleet(execution=PostgresExecutionStore(other_engine), fleet=other, request=request)
    )
    await asyncio.sleep(0.1)
    assert not second.done()
    release.set()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), 10)
    assert one == two
    assert (await first_execution.get_deployment(book.id)).deployment.revision == book.revision + 1
    await other_engine.dispose()


@pytest.mark.parametrize("action", [FleetAction.DISARM, FleetAction.REARM])
async def test_crash_latch_receipt_cannot_undo_a_newer_opposite_command(
    database: AsyncEngine, action: FleetAction
) -> None:
    """Old disarm/rearm replay returns its receipt without overwriting newer consent."""
    execution = PostgresExecutionStore(database)

    class CrashAfterLatch(PostgresFleetControlStore):
        async def apply_latch(
            self, operation: FleetOperation, request: FleetExecuteRequest, *, now: datetime
        ) -> FleetOperation:
            """Lose the response after latch and receipt commit, before operation completion."""
            await super().apply_latch(operation, request, now=now)
            raise RuntimeError("lost response")

    old = _request(action, "old")
    with pytest.raises(RuntimeError, match="lost response"):
        await execute_fleet(execution=execution, fleet=CrashAfterLatch(database), request=old)
    restarted = PostgresFleetControlStore(database)
    receipt = await restarted.get_operation("old")
    assert receipt is not None and receipt.latch_applied
    opposite = FleetAction.REARM if action is FleetAction.DISARM else FleetAction.DISARM
    await execute_fleet(
        execution=execution, fleet=restarted, request=_request(opposite, "newer", 1)
    )
    replay = await execute_fleet(
        execution=execution, fleet=PostgresFleetControlStore(database), request=old
    )
    assert replay.id == receipt.id
    snapshot = await restarted.read_inhibition()
    assert snapshot.paper_revision == 2
    assert snapshot.paper_inhibited is (opposite is FleetAction.DISARM)


@pytest.mark.parametrize("action", [FleetAction.DISARM, FleetAction.REARM])
async def test_unreceipted_latch_write_rolls_back_and_stale_retry_is_fenced(
    database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    action: FleetAction,
) -> None:
    """Crash inside latch/receipt transaction leaves neither an effect nor a receipt."""
    execution = PostgresExecutionStore(database)
    fleet = PostgresFleetControlStore(database)
    original = fleet_postgres._save

    async def crash(connection: AsyncConnection, operation: FleetOperation) -> None:
        """Interrupt between latch UPDATE and its causal receipt write."""
        if operation.latch_applied:
            raise RuntimeError("unreceipted latch")
        await original(connection, operation)

    monkeypatch.setattr(fleet_postgres, "_save", crash)
    request = _request(action, "unreceipted")
    with pytest.raises(RuntimeError, match="unreceipted latch"):
        await execute_fleet(execution=execution, fleet=fleet, request=request)
    assert (await fleet.read_inhibition()).paper_revision == 0
    pending = await fleet.get_operation(request.idempotency_key)
    assert pending is not None and not pending.latch_applied
    monkeypatch.setattr(fleet_postgres, "_save", original)
    opposite = FleetAction.REARM if action is FleetAction.DISARM else FleetAction.DISARM
    await execute_fleet(execution=execution, fleet=fleet, request=_request(opposite, "later"))
    with pytest.raises(ExecutionConflictError, match="inhibition_revision_conflict"):
        await execute_fleet(
            execution=execution, fleet=PostgresFleetControlStore(database), request=request
        )
    snapshot = await fleet.read_inhibition()
    assert snapshot.paper_revision == 1
    assert snapshot.paper_inhibited is (opposite is FleetAction.DISARM)


async def test_queued_retries_do_not_reserve_every_database_connection(
    database: AsyncEngine,
) -> None:
    """Local serialization bounds sessions; the durable lock still decides correctness."""
    fleet = PostgresFleetControlStore(database)
    execution = PostgresExecutionStore(database)
    request = _request(FleetAction.DISARM, "queued")
    results = await asyncio.wait_for(
        asyncio.gather(
            *(execute_fleet(execution=execution, fleet=fleet, request=request) for _ in range(12))
        ),
        10,
    )
    assert all(result.id == results[0].id for result in results)
    assert (await fleet.read_inhibition()).paper_revision == 1


async def test_stale_rearm_preview_cannot_clear_a_repeated_deliberate_disarm(
    database: AsyncEngine,
) -> None:
    """Even an unchanged disarm bit advances its confirmation fence."""
    fleet, execution = PostgresFleetControlStore(database), PostgresExecutionStore(database)
    await execute_fleet(
        execution=execution, fleet=fleet, request=_request(FleetAction.DISARM, "first")
    )
    await execute_fleet(
        execution=execution, fleet=fleet, request=_request(FleetAction.DISARM, "deliberate", 1)
    )
    with pytest.raises(ExecutionConflictError, match="inhibition_revision_conflict"):
        await execute_fleet(
            execution=execution, fleet=fleet, request=_request(FleetAction.REARM, "stale", 1)
        )
    assert (await fleet.read_inhibition()).paper_inhibited


async def test_repeated_cancellation_waits_for_session_invalidation(
    database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second cancellation must not return an advisory-locked session to its pool."""
    execution = PostgresExecutionStore(database)
    fleet = PostgresFleetControlStore(database)
    command_started = asyncio.Event()
    invalidating = asyncio.Event()
    release = asyncio.Event()
    original_audit = fleet_service._audit
    original_invalidate = AsyncConnection.invalidate

    async def paused(*_args: object, **_kwargs: object) -> bool:
        """Hold the operation after its latch receipt, with the session lock acquired."""
        command_started.set()
        await asyncio.Event().wait()
        return False

    async def delayed(
        connection: AsyncConnection,
        exception: BaseException | None = None,
    ) -> None:
        """Expose cleanup lifetime without ever allowing an actual lock leak."""
        invalidating.set()
        await release.wait()
        await original_invalidate(connection, exception)

    monkeypatch.setattr(fleet_service, "_audit", paused)
    monkeypatch.setattr(AsyncConnection, "invalidate", delayed)
    request = _request(FleetAction.DISARM, "double-cancel")
    task = asyncio.create_task(execute_fleet(execution=execution, fleet=fleet, request=request))
    try:
        await asyncio.wait_for(command_started.wait(), 5)
        task.cancel()
        await asyncio.wait_for(invalidating.wait(), 5)
        task.cancel()
        await asyncio.sleep(0.03)
        assert not task.done(), "cleanup must finish before the guarded connection is released"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    monkeypatch.setattr(fleet_service, "_audit", original_audit)
    monkeypatch.setattr(AsyncConnection, "invalidate", original_invalidate)
    result = await asyncio.wait_for(
        execute_fleet(
            execution=execution,
            fleet=PostgresFleetControlStore(database),
            request=request,
        ),
        5,
    )
    assert result.status is FleetOperationStatus.COMPLETED
    assert (await fleet.read_inhibition()).paper_revision == 1


async def test_target_receipt_survives_crash_and_later_book_changes(database: AsyncEngine) -> None:
    """A replay reports causal evidence, not a guess from current lifecycle state."""
    execution = PostgresExecutionStore(database)
    book = await execution.create_deployment(_book())

    class CrashAfterTarget(PostgresFleetControlStore):
        async def record_target(
            self,
            execution: ExecutionStore,
            operation: FleetOperation,
            expected: ExpectedTarget,
            *,
            now: datetime,
        ) -> FleetOperation:
            """Crash after the coupled command/receipt transaction committed."""
            await super().record_target(execution, operation, expected, now=now)
            raise RuntimeError("target response lost")

    request = _request(FleetAction.FLATTEN, "target-crash", books=(book,))
    with pytest.raises(RuntimeError, match="target response lost"):
        await execute_fleet(execution=execution, fleet=CrashAfterTarget(database), request=request)
    after = (await execution.get_deployment(book.id)).deployment
    assert after.revision == book.revision + 1
    paused = await execution.save_deployment(
        replace(
            after,
            status=DeploymentStatus.PAUSED,
            lifecycle_command=LifecycleCommand.STOP_NEW_ENTRIES,
        ),
        expected_revision=after.revision,
    )
    result = await execute_fleet(
        execution=execution, fleet=PostgresFleetControlStore(database), request=request
    )
    assert result.targets[0].status is FleetTargetStatus.COMMAND_RECORDED
    assert (await execution.get_deployment(book.id)).deployment == paused


async def test_partial_target_transaction_crash_rolls_back_only_unreceipted_command(
    database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First target survives; a crash between second command and receipt rolls back both."""
    execution = PostgresExecutionStore(database)
    books = tuple([await execution.create_deployment(_book(index)) for index in range(3)])
    request = _request(FleetAction.MANAGED_STOP, "partial-crash", books=books)
    original = fleet_postgres._save

    async def crash(connection: AsyncConnection, operation: FleetOperation) -> None:
        """Inject the exact command/receipt gap inside its database transaction."""
        if len(operation.targets) == 2:
            raise RuntimeError("before target receipt")
        await original(connection, operation)

    monkeypatch.setattr(fleet_postgres, "_save", crash)
    with pytest.raises(RuntimeError, match="before target receipt"):
        await execute_fleet(
            execution=execution, fleet=PostgresFleetControlStore(database), request=request
        )
    assert (await execution.get_deployment(books[0].id)).deployment.revision == 5
    assert (await execution.get_deployment(books[1].id)).deployment.revision == 4
    monkeypatch.setattr(fleet_postgres, "_save", original)
    result = await execute_fleet(
        execution=execution, fleet=PostgresFleetControlStore(database), request=request
    )
    assert result.status is FleetOperationStatus.ACCEPTED
    assert all([(await execution.get_deployment(row.id)).deployment.revision == 5 for row in books])


@pytest.mark.parametrize("missing", ["table", "row"])
async def test_missing_latch_refuses_risk_but_allows_protection(
    database: AsyncEngine, missing: str
) -> None:
    """Boot before refresh and absent durable state cannot admit starts or entries."""
    execution = PostgresExecutionStore(database)
    book = await execution.create_deployment(_book())
    async with database.begin() as connection:
        if missing == "table":
            await connection.execute(
                text("ALTER TABLE fleet_entry_inhibition RENAME TO unavailable_latch")
            )
        else:
            await connection.execute(
                delete(fleet_entry_inhibition).where(fleet_entry_inhibition.c.mode == "paper")
            )
    try:
        clear_entry_inhibition_cache()
        assert not entries_allowed(book)
        with pytest.raises((ExecutionConflictError, ExecutionStoreError)):
            await execution.create_deployment(_book())
        with pytest.raises((ExecutionConflictError, ExecutionStoreError)):
            await execution.save_intent(_intent(book, IntentPurpose.ENTRY))
        await execution.save_intent(_intent(book, IntentPurpose.TAKE_PROFIT))
        with pytest.raises(ExecutionStoreError):
            await execution.read_entry_inhibition()
        await refresh_process_entry_inhibition(execution)
        assert not entries_allowed(book)
    finally:
        async with database.begin() as connection:
            if missing == "table":
                await connection.execute(
                    text("ALTER TABLE unavailable_latch RENAME TO fleet_entry_inhibition")
                )
            else:
                await connection.execute(
                    text(
                        "INSERT INTO fleet_entry_inhibition (mode,inhibited,revision,updated_at) "
                        "VALUES ('paper',false,0,CURRENT_TIMESTAMP)"
                    )
                )


async def test_disarm_linearizes_at_admission_and_preserves_preaccepted_intents(
    database: AsyncEngine,
) -> None:
    """Disarm is not cancellation: preaccepted intents remain; later inserts refuse."""
    execution, fleet = PostgresExecutionStore(database), PostgresFleetControlStore(database)
    book = await execution.create_deployment(_book())
    before = await execution.save_intent(_intent(book, IntentPurpose.ENTRY))
    await execute_fleet(
        execution=execution, fleet=fleet, request=_request(FleetAction.DISARM, "linearize")
    )
    with pytest.raises(ExecutionConflictError, match="ENTRY_INHIBITED"):
        await execution.save_intent(_intent(book, IntentPurpose.ENTRY))
    outcomes = await asyncio.gather(
        *(execution.create_deployment(_book()) for _ in range(5)), return_exceptions=True
    )
    assert all(isinstance(item, ExecutionConflictError) for item in outcomes)
    await execution.save_intent(_intent(book, IntentPurpose.TAKE_PROFIT))
    snapshot = await execution.get_deployment(book.id)
    assert before in snapshot.intents
    assert snapshot.deployment.lifecycle_command is LifecycleCommand.NONE


async def test_pg_inventory_deletion_and_tied_timestamps_are_fenced(database: AsyncEngine) -> None:
    """More than 50 tied rows cannot skip a survivor after page-one deletion."""
    execution = PostgresExecutionStore(database)
    books = [await execution.create_deployment(_book(index)) for index in range(61)]
    first = await read_stable_inventory(execution, limit=50, offset=0)
    assert first.next_cursor is not None
    async with database.begin() as connection:
        await connection.execute(
            delete(deployments).where(deployments.c.id == first.deployments[0].id)
        )
    with pytest.raises(ExecutionConflictError, match="inventory_changed"):
        await read_stable_inventory(execution, limit=50, offset=0, cursor=first.next_cursor)
    fresh = await read_stable_inventory(execution, limit=50, offset=0)
    second = await read_stable_inventory(execution, limit=50, offset=0, cursor=fresh.next_cursor)
    survivors = {book.id for book in books} - {first.deployments[0].id}
    assert {row.id for row in (*fresh.deployments, *second.deployments)} == survivors
    assert len(fresh.deployments) + len(second.deployments) == 60
