"""A desk's harness, model, cadence, mandate, caps or name can change; its mode and pool cannot."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.agent_desk.test_service import _desk
from thytrader.agent_desk.ports import DeskError
from thytrader.agent_desk.update import DeskChanges, update_desk

pytestmark = pytest.mark.anyio


async def test_a_paper_desk_switches_harness_and_model_under_a_new_revision() -> None:
    """The change is saved, validated and reported field by field."""
    store, _, desk_id = await _desk(harness="pi", model="old/model:high")
    before = await store.get_desk(desk_id)
    assert before is not None
    update = await update_desk(
        store,
        desk_id,
        DeskChanges(model="new/model:max", cadence_minutes=60),
        live_acknowledged=False,
    )
    assert update.desk.model == "new/model:max"
    assert update.desk.cadence_minutes == 60
    assert update.desk.revision == before.revision + 1
    assert update.changed == {
        "model": ("old/model:high", "new/model:max"),
        "cadence_minutes": (30, 60),
    }
    assert (await store.get_desk(desk_id)) == update.desk


async def test_caps_merge_into_the_current_caps_and_are_validated() -> None:
    """Unnamed caps keep their values; an out-of-range cap is refused."""
    store, _, desk_id = await _desk()
    update = await update_desk(
        store, desk_id, DeskChanges(caps={"max_open_positions": 2}), live_acknowledged=False
    )
    assert update.desk.caps.max_open_positions == 2
    assert update.desk.caps.max_position_fraction == Decimal("0.25")
    with pytest.raises(DeskError, match="DESK_UPDATE_INVALID"):
        await update_desk(
            store, desk_id, DeskChanges(caps={"max_position_fraction": 2}), live_acknowledged=False
        )
    with pytest.raises(DeskError, match="DESK_UPDATE_INVALID"):
        await update_desk(
            store, desk_id, DeskChanges(harness="Not A Name"), live_acknowledged=False
        )


async def test_a_model_can_be_cleared_but_not_set_and_cleared_at_once() -> None:
    """``clear_model`` unsets the model; asking for both is a usage error."""
    store, _, desk_id = await _desk(model="m")
    with pytest.raises(DeskError, match="DESK_UPDATE_INVALID"):
        await update_desk(
            store, desk_id, DeskChanges(model="x", clear_model=True), live_acknowledged=False
        )
    update = await update_desk(
        store, desk_id, DeskChanges(clear_model=True), live_acknowledged=False
    )
    assert update.desk.model is None


async def test_nothing_to_change_and_a_stale_revision_are_refused() -> None:
    """An empty change writes nothing; a revision the desk moved past is a conflict."""
    store, _, desk_id = await _desk(model="m")
    with pytest.raises(DeskError, match="DESK_UPDATE_EMPTY"):
        await update_desk(store, desk_id, DeskChanges(model="m"), live_acknowledged=False)
    with pytest.raises(DeskError, match="DESK_CONFLICT"):
        await update_desk(
            store, desk_id, DeskChanges(model="n"), live_acknowledged=False, expected_revision=99
        )
    with pytest.raises(DeskError, match="DESK_NOT_FOUND"):
        await update_desk(store, "missing", DeskChanges(model="n"), live_acknowledged=False)


@pytest.mark.parametrize(
    "changes",
    [
        DeskChanges(model="other"),
        DeskChanges(harness="codex"),
        DeskChanges(mandate="trade more"),
        DeskChanges(caps={"max_position_fraction": "0.5"}),
    ],
)
async def test_how_a_live_desk_trades_changes_only_with_the_live_acknowledgement(
    changes: DeskChanges,
) -> None:
    """Who trades real money, what it is told and its caps need the same ack as creation."""
    store, _, desk_id = await _desk(mode="live", live_acknowledged=True)
    with pytest.raises(DeskError, match="DESK_LIVE_ACK_REQUIRED"):
        await update_desk(store, desk_id, changes, live_acknowledged=False)
    unchanged = await store.get_desk(desk_id)
    assert unchanged is not None
    assert unchanged.revision == 1
    update = await update_desk(store, desk_id, changes, live_acknowledged=True)
    assert update.desk.mode == "live"
    assert update.desk.revision == 2


async def test_a_live_desk_name_and_cadence_change_without_the_acknowledgement() -> None:
    """Renaming or retiming a live desk does not change how it trades."""
    store, _, desk_id = await _desk(mode="live", live_acknowledged=True)
    update = await update_desk(
        store, desk_id, DeskChanges(name="renamed", cadence_minutes=60), live_acknowledged=False
    )
    assert (update.desk.name, update.desk.cadence_minutes) == ("renamed", 60)


async def test_the_mandate_is_recorded_by_digest_in_the_change_history() -> None:
    """A mandate can be long; the change history keeps its digest and length."""
    store, _, desk_id = await _desk()
    update = await update_desk(
        store, desk_id, DeskChanges(mandate="a new mandate"), live_acknowledged=False
    )
    assert update.desk.mandate == "a new mandate"
    old, new = update.changed["mandate"]
    assert str(old).endswith("(0 chars)")
    assert str(new).startswith("sha256:")
    assert str(new).endswith("(13 chars)")


async def test_every_change_is_kept_on_the_desk_in_the_same_write() -> None:
    """The desk row carries what changed, old and new, so no audit outage can lose it."""
    store, _, desk_id = await _desk(model="m1")
    await update_desk(store, desk_id, DeskChanges(model="m2"), live_acknowledged=False)
    update = await update_desk(
        store, desk_id, DeskChanges(caps={"max_open_positions": 1}), live_acknowledged=False
    )
    saved = await store.get_desk(desk_id)
    assert saved == update.desk
    assert [change.revision for change in saved.changes] == [2, 3]
    assert saved.changes[0].changed == {"model": ("m1", "m2")}
    old_caps, new_caps = saved.changes[1].changed["caps"]
    assert isinstance(old_caps, dict)
    assert isinstance(new_caps, dict)
    assert (old_caps["max_open_positions"], new_caps["max_open_positions"]) == (3, 1)


async def test_a_new_cadence_restarts_the_stall_clock_and_a_model_change_does_not() -> None:
    """``due_since`` moves only when the desk is due to wake on a new schedule."""
    store, _, desk_id = await _desk()
    created = await store.get_desk(desk_id)
    assert created is not None
    assert created.due_since is not None
    model = await update_desk(store, desk_id, DeskChanges(model="m"), live_acknowledged=False)
    assert model.desk.due_since == created.due_since
    cadence = await update_desk(
        store, desk_id, DeskChanges(cadence_minutes=60), live_acknowledged=False
    )
    assert cadence.desk.due_since is not None
    assert cadence.desk.due_since > created.due_since
