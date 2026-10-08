"""Stable inventory pages and explicit fleet controls."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.entry_latch import (
    clear_entry_inhibition_cache,
    process_entry_inhibited,
    remember_entry_inhibition,
)
from thytrader.execution.ids import uuid7
from thytrader.execution.lifecycle import entries_allowed
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    InstrumentRuntime,
    IntentPurpose,
    LifecycleCommand,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.fleet_control.admission import refresh_process_entry_inhibition
from thytrader.fleet_control.inventory import page_deployments
from thytrader.fleet_control.models import (
    ExpectedInhibition,
    ExpectedTarget,
    FleetAction,
    FleetExecuteRequest,
    FleetModeScope,
    FleetOperationStatus,
    FleetTargetStatus,
)
from thytrader.fleet_control.service import execute_fleet, preview_fleet
from thytrader.fleet_control.store import InMemoryFleetControlStore


def _book(index: int, *, mode: DeploymentMode = DeploymentMode.PAPER) -> Deployment:
    """A discretionary book with a distinct created-at and no strategy identity."""
    created = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(seconds=index)
    return Deployment(
        id=uuid7(created),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id="BTC-USDC",
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10"),
        phase=RuntimePhase.FLAT,
        created_at=created,
        updated_at=created,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="5m",
        revision=4,
        paper_maker_fee_rate=Decimal("0.001"),
        paper_taker_fee_rate=Decimal("0.002"),
    )


def test_offset_pages_do_not_skip_when_updated_at_moves() -> None:
    """A supervised book's newer updated_at must not duplicate or skip inventory."""
    rows = [_book(index) for index in range(60)]
    as_of = datetime(2026, 10, 6, tzinfo=UTC)
    first = page_deployments(rows, limit=50, offset=0, strategy_id=None, as_of=as_of)
    second = page_deployments(rows, limit=50, offset=50, strategy_id=None, as_of=as_of)
    assert first.returned == 50
    assert first.has_more is True
    assert first.total == 60
    assert second.has_more is False
    rows[0] = replace(rows[0], updated_at=datetime(2026, 10, 7, tzinfo=UTC))
    again = page_deployments(rows, limit=50, offset=50, strategy_id=None, as_of=as_of)
    reread = page_deployments(rows, limit=50, offset=0, strategy_id=None, as_of=as_of)
    assert [item.id for item in again.deployments] == [item.id for item in second.deployments]
    assert [item.id for item in reread.deployments] == [item.id for item in first.deployments]


def test_as_of_excludes_a_row_created_during_the_walk() -> None:
    """Inserts after the pinned snapshot do not shift later offsets."""
    rows = [_book(index) for index in range(3)]
    as_of = datetime(2026, 10, 2, tzinfo=UTC)
    first = page_deployments(rows, limit=2, offset=0, strategy_id=None, as_of=as_of)
    late = _book(10_000)
    second = page_deployments([*rows, late], limit=2, offset=2, strategy_id=None, as_of=first.as_of)
    assert late.id not in {item.id for item in second.deployments}
    assert {item.id for item in first.deployments + second.deployments} == {
        item.id for item in rows
    }


def test_entries_allowed_uses_the_process_snapshot() -> None:
    """Boot/restart without proven latch admission inhibits entries."""
    book = _book(1)
    clear_entry_inhibition_cache()
    assert entries_allowed(book) is False
    remember_entry_inhibition({"paper": False, "live": False})
    assert entries_allowed(book) is True
    remember_entry_inhibition({"paper": True, "live": False})
    try:
        assert process_entry_inhibited(book.mode) is True
        assert entries_allowed(book) is False
        assert entries_allowed(_book(2, mode=DeploymentMode.LIVE)) is True
    finally:
        clear_entry_inhibition_cache()


def test_refresh_failure_fails_closed_for_entries_only() -> None:
    """A latch read failure inhibits entries without raising into the worker cycle."""

    class Broken:
        async def read_entry_inhibition(self) -> dict[str, bool]:
            raise ExecutionStoreError("unavailable")

    asyncio.run(refresh_process_entry_inhibition(Broken()))
    try:
        assert process_entry_inhibited(DeploymentMode.PAPER) is True
        assert process_entry_inhibited(DeploymentMode.LIVE) is True
    finally:
        clear_entry_inhibition_cache()


def _request(
    action: FleetAction,
    *,
    mode: FleetModeScope = FleetModeScope.PAPER,
    key: str = "key-1",
    targets: tuple[ExpectedTarget, ...] = (),
    live: bool = False,
    allow_empty: bool = False,
    inhibition: ExpectedInhibition | None = None,
) -> FleetExecuteRequest:
    """One confirmed fleet request."""
    return FleetExecuteRequest(
        action=action,
        mode=mode,
        idempotency_key=key,
        expected_targets=targets,
        live_acknowledged=live,
        allow_empty_scope=allow_empty,
        expected_inhibition=inhibition or ExpectedInhibition(0, 0),
    )


def test_disarm_refuses_start_across_a_rebound_store_and_does_not_flatten() -> None:
    """The latch survives a new execution store and never records flatten."""

    async def scenario() -> None:
        gate = InMemoryFleetControlStore()
        store = InMemoryExecutionStore()
        store.bind_entry_gate(gate)
        book = _book(1)
        store.deployments[book.id] = book
        audit = InMemoryAuditEventStore()
        operation = await execute_fleet(
            execution=store,
            fleet=gate,
            request=_request(FleetAction.DISARM, allow_empty=True),
            audit=audit,
        )
        assert operation.status is FleetOperationStatus.COMPLETED
        assert book.lifecycle_command is LifecycleCommand.NONE
        assert book.status is DeploymentStatus.RUNNING
        restarted = InMemoryExecutionStore()
        restarted.bind_entry_gate(gate)
        with pytest.raises(ExecutionConflictError, match="ENTRY_INHIBITED"):
            await restarted.create_deployment(_book(2))
        await execute_fleet(
            execution=restarted,
            fleet=gate,
            request=_request(
                FleetAction.REARM,
                key="rearm",
                allow_empty=True,
                inhibition=ExpectedInhibition(1, 0),
            ),
            audit=audit,
        )
        await restarted.create_deployment(_book(3))

    asyncio.run(scenario())
    clear_entry_inhibition_cache()


def test_double_confirm_returns_the_same_operation() -> None:
    """The same idempotency key does not apply the latch twice."""

    async def scenario() -> None:
        gate = InMemoryFleetControlStore()
        store = InMemoryExecutionStore()
        store.bind_entry_gate(gate)
        audit = InMemoryAuditEventStore()
        request = _request(FleetAction.DISARM, allow_empty=True)
        first = await execute_fleet(execution=store, fleet=gate, request=request, audit=audit)
        second = await execute_fleet(execution=store, fleet=gate, request=request, audit=audit)
        assert first.id == second.id
        assert (await gate.read_inhibition()).paper_revision == 1

    asyncio.run(scenario())
    clear_entry_inhibition_cache()


def test_live_rearm_and_flatten_require_acknowledgement() -> None:
    """Rearm and flatten that include live refuse without the live acknowledgement."""

    async def scenario() -> None:
        gate = InMemoryFleetControlStore()
        store = InMemoryExecutionStore()
        with pytest.raises(ExecutionConflictError, match="live_acknowledgement_required"):
            await execute_fleet(
                execution=store,
                fleet=gate,
                request=_request(FleetAction.REARM, mode=FleetModeScope.LIVE),
            )
        with pytest.raises(ExecutionConflictError, match="live_acknowledgement_required"):
            await execute_fleet(
                execution=store,
                fleet=gate,
                request=_request(FleetAction.FLATTEN, mode=FleetModeScope.ALL, allow_empty=True),
            )
        paper = await execute_fleet(
            execution=store,
            fleet=gate,
            request=_request(FleetAction.REARM, key="paper-rearm"),
        )
        assert paper.status is FleetOperationStatus.COMPLETED

    asyncio.run(scenario())


def test_stop_is_partial_when_one_book_fails_and_exit_intent_still_saves() -> None:
    """One storage failure does not hide the other command or block an exit intent."""

    class Flaky(InMemoryExecutionStore):
        """Fail one parent's conditional write without bypassing the atomic runtime contract."""

        def __init__(self, failing: object) -> None:
            """Select the book whose write fails."""
            super().__init__()
            self.failing = failing

        async def save_deployment(
            self,
            deployment: Deployment,
            *,
            expected_revision: int | None = None,
            instrument_runtime: InstrumentRuntime | None = None,
        ) -> Deployment:
            """Preserve the production signature while injecting one storage failure."""
            if deployment.id == self.failing:
                raise ExecutionStoreError("storage unavailable")
            return await super().save_deployment(
                deployment,
                expected_revision=expected_revision,
                instrument_runtime=instrument_runtime,
            )

    async def scenario() -> None:
        gate = InMemoryFleetControlStore()
        first = _book(1)
        second = _book(2)
        store = Flaky(second.id)
        store.bind_entry_gate(gate)
        store.deployments[first.id] = first
        store.deployments[second.id] = second
        await execute_fleet(
            execution=store,
            fleet=gate,
            request=_request(FleetAction.DISARM, key="disarm", allow_empty=True),
        )
        now = datetime.now(UTC)
        await store.save_intent(
            OrderIntent(
                id=uuid7(now),
                deployment_id=first.id,
                client_order_id="exit-1",
                purpose=IntentPurpose.TAKE_PROFIT,
                side=OrderSide.SELL,
                kind=OrderKind.POST_ONLY_LIMIT,
                quantity=Decimal("1"),
                created_at=now,
                candle_starts_at=now,
                status=OrderStatus.PENDING,
            )
        )
        with pytest.raises(ExecutionConflictError, match="ENTRY_INHIBITED"):
            await store.save_intent(
                OrderIntent(
                    id=uuid7(now),
                    deployment_id=first.id,
                    client_order_id="entry-1",
                    purpose=IntentPurpose.ENTRY,
                    side=OrderSide.BUY,
                    kind=OrderKind.POST_ONLY_LIMIT,
                    quantity=Decimal("1"),
                    created_at=now,
                    candle_starts_at=now,
                    status=OrderStatus.PENDING,
                )
            )
        result = await execute_fleet(
            execution=store,
            fleet=gate,
            request=_request(
                FleetAction.MANAGED_STOP,
                key="stop",
                targets=(
                    ExpectedTarget(first.id, first.revision),
                    ExpectedTarget(second.id, second.revision),
                ),
            ),
        )
        assert result.status is FleetOperationStatus.PARTIAL
        by_id = {item.deployment_id: item.status for item in result.targets}
        assert by_id[first.id] is FleetTargetStatus.COMMAND_RECORDED
        assert by_id[second.id] is FleetTargetStatus.FAILED
        saved = store.deployments[first.id]
        assert saved.lifecycle_command is LifecycleCommand.MANAGED_SHUTDOWN
        assert result.note.startswith("Commands were recorded")

    asyncio.run(scenario())
    clear_entry_inhibition_cache()


def test_concurrent_starts_cannot_pass_a_disarm() -> None:
    """Starts waiting on the latch lock observe the inhibit bit."""

    async def scenario() -> None:
        gate = InMemoryFleetControlStore()
        store = InMemoryExecutionStore()
        store.bind_entry_gate(gate)

        async def start(index: int) -> str:
            try:
                await store.create_deployment(_book(100 + index))
            except ExecutionConflictError:
                return "refused"
            return "started"

        await gate.inhibit(("paper",), now=datetime.now(UTC))
        results = await asyncio.gather(*(start(index) for index in range(8)))
        assert results == ["refused"] * 8

    asyncio.run(scenario())
    clear_entry_inhibition_cache()


def test_preview_names_residuals_without_claiming_flatten() -> None:
    """Preview distinguishes disarm from flatten and does not invent an empty book."""

    async def scenario() -> None:
        gate = InMemoryFleetControlStore()
        store = InMemoryExecutionStore()
        book = _book(1)
        store.deployments[book.id] = book
        preview = await preview_fleet(
            execution=store,
            fleet=gate,
            action=FleetAction.DISARM,
            mode=FleetModeScope.PAPER,
        )
        assert preview.flattens is False
        assert preview.pauses is False
        assert preview.cancels_entries is False
        assert "not paused" in preview.targets[0].effect
        assert preview.targets[0].positions == ()

    asyncio.run(scenario())
