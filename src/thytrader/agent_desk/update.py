"""Change a desk's harness, model, cadence, mandate, caps or name (ADR 0147, 0148).

The desk's mode, quote currency, pool, universe and book timeframe are fixed at creation. The
change is saved under the desk's revision (a lost race, or a stale ``expected_revision``, is a
conflict) and validated like a new desk, and the change itself (old and new values, a mandate
by digest) is appended to the desk's ``changes`` in that same write, so it can never be lost
apart from the change. A new cadence restarts the stall clock (``due_since``). On a live desk,
a change to who trades (harness, model), what it is told (mandate) or its caps needs the same
live acknowledgement as creating it; the name and cadence do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from thytrader.agent_desk.models import MAX_CHANGES, Desk, DeskChange
from thytrader.agent_desk.ports import DeskError, save_desk
from thytrader.agent_desk.service import desk_lock

if TYPE_CHECKING:
    from thytrader.agent_desk.store import DeskStore

#: Fields whose change on a live desk needs the live acknowledgement.
LIVE_ACK_FIELDS = frozenset({"harness", "model", "mandate", "caps"})


@dataclass(frozen=True, slots=True)
class DeskChanges:
    """Requested changes; ``None`` leaves a field as it is, ``clear_model`` unsets the model."""

    name: str | None = None
    harness: str | None = None
    model: str | None = None
    clear_model: bool = False
    cadence_minutes: int | None = None
    mandate: str | None = None
    caps: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class DeskUpdate:
    """The saved desk and the fields that changed (JSON old and new values, mandate digests)."""

    desk: Desk
    changed: dict[str, tuple[JsonValue, JsonValue]]


async def update_desk(
    store: DeskStore,
    desk_id: str,
    changes: DeskChanges,
    *,
    live_acknowledged: bool,
    expected_revision: int | None = None,
) -> DeskUpdate:
    """Validate and save the changes under the desk's lock and revision."""
    if changes.clear_model and changes.model is not None:
        raise DeskError("DESK_UPDATE_INVALID: pass a model or clear it, not both.")
    async with desk_lock(desk_id):
        desk = await store.get_desk(desk_id)
        if desk is None:
            raise DeskError(f"DESK_NOT_FOUND: no desk {desk_id}.")
        if expected_revision is not None and expected_revision != desk.revision:
            raise DeskError(
                f"DESK_CONFLICT: the desk is at revision {desk.revision}, not {expected_revision}."
            )
        updated = _apply(desk, changes)
        changed = _changed(desk, updated)
        if not changed:
            raise DeskError("DESK_UPDATE_EMPTY: nothing would change.")
        if desk.mode == "live" and not live_acknowledged and LIVE_ACK_FIELDS & changed.keys():
            fields = ", ".join(sorted(LIVE_ACK_FIELDS & changed.keys()))
            raise DeskError(
                f"DESK_LIVE_ACK_REQUIRED: changing {fields} on a live desk changes how real "
                "money is traded; acknowledge live."
            )
        now = datetime.now(UTC)
        record = DeskChange(at=now, revision=desk.revision + 1, changed=changed)
        extra: dict[str, object] = {"changes": (*desk.changes, record)[-MAX_CHANGES:]}
        if "cadence_minutes" in changed:
            extra["due_since"] = now
        saved = await save_desk(updated.model_copy(update=extra), store=store, previous=desk)
    return DeskUpdate(saved, changed)


def _changed(desk: Desk, updated: Desk) -> dict[str, tuple[JsonValue, JsonValue]]:
    """The changed fields as JSON values; a mandate as its digest and length."""
    before, after = desk.model_dump(mode="json"), updated.model_dump(mode="json")
    changed: dict[str, tuple[JsonValue, JsonValue]] = {
        name: (before[name], after[name])
        for name in ("name", "harness", "model", "cadence_minutes", "caps")
        if before[name] != after[name]
    }
    if desk.mandate != updated.mandate:
        changed["mandate"] = (_mandate(desk.mandate), _mandate(updated.mandate))
    return changed


def _mandate(text: str) -> str:
    """A mandate by digest and length, so the history stays small."""
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:16]} ({len(text)} chars)"


def _apply(desk: Desk, changes: DeskChanges) -> Desk:
    """The desk with the changes, validated like a new desk."""
    values = desk.model_dump()
    for name in ("name", "harness", "model", "cadence_minutes", "mandate"):
        value = getattr(changes, name)
        if value is not None:
            values[name] = value
    if changes.clear_model:
        values["model"] = None
    if changes.caps:
        values["caps"] = {**desk.caps.model_dump(), **changes.caps}
    try:
        updated = Desk.model_validate(values)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        raise DeskError(f"DESK_UPDATE_INVALID: {where}: {first.get('msg', 'invalid')}") from None
    return updated
