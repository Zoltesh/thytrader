"""Keyset inventory with a membership/classification fence across page reads.

Mutable updated_at never orders inventory. The cursor binds as_of, strategy
filter, membership digest, and the last immutable created_at/id key. Deletion
or reclassification changes the digest and requires a new walk, never a claim
that an offset prefix is the complete fleet.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from thytrader.execution.models import ExecutionConflictError, ExecutionStoreError
from thytrader.fleet_control.models import INVENTORY_ORDER

if TYPE_CHECKING:
    from thytrader.execution.models import Deployment
    from thytrader.execution.store import ExecutionStore


class InventoryCursor(BaseModel):
    """Strict read-only continuation bound to a membership fence and last key."""

    model_config = ConfigDict(extra="forbid", strict=True)
    as_of: datetime
    strategy_id: UUID | None
    fingerprint: str
    created_at: datetime
    deployment_id: UUID


@dataclass(frozen=True, slots=True)
class InventoryPage:
    """One page with exact membership metadata and its checked continuation."""

    deployments: tuple[Deployment, ...]
    limit: int
    offset: int
    returned: int
    total: int
    has_more: bool
    as_of: datetime
    fingerprint: str
    next_cursor: str | None
    order: str = INVENTORY_ORDER


async def read_stable_inventory(
    store: ExecutionStore,
    *,
    limit: int,
    offset: int,
    strategy_id: UUID | None = None,
    as_of: datetime | None = None,
    cursor: str | None = None,
) -> InventoryPage:
    """Read membership in one store statement and reject stale continuations."""
    if limit < 1 or offset < 0:
        raise ExecutionStoreError("Inventory page bounds are invalid.")
    continuation = _decode(cursor) if cursor is not None else None
    if continuation is not None:
        if offset != 0 or strategy_id != continuation.strategy_id:
            raise ExecutionConflictError(
                "Inventory cursor cannot change offset or strategy filter."
            )
        if as_of is not None and as_of != continuation.as_of:
            raise ExecutionConflictError("Inventory cursor cannot change as_of.")
        as_of = continuation.as_of
    rows = await store.list_deployments()
    return page_deployments(
        rows,
        limit=limit,
        offset=offset,
        strategy_id=strategy_id,
        as_of=as_of or datetime.now(UTC),
        continuation=continuation,
    )


def page_deployments(
    rows: tuple[Deployment, ...] | list[Deployment],
    *,
    limit: int,
    offset: int,
    strategy_id: UUID | None,
    as_of: datetime,
    continuation: InventoryCursor | None = None,
) -> InventoryPage:
    """Keyset a loaded membership set, fencing deletes and classification changes."""
    selected = [
        row
        for row in rows
        if (strategy_id is None or row.strategy_id == strategy_id) and row.created_at <= as_of
    ]
    selected.sort(key=lambda row: (row.created_at, row.id.int), reverse=True)
    identity = [
        (
            str(row.id),
            row.created_at.isoformat(),
            str(row.strategy_id),
            row.strategy_fingerprint,
            row.product_id,
            row.mode.value,
            row.kind.value,
        )
        for row in selected
    ]
    fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    if continuation is not None and fingerprint != continuation.fingerprint:
        raise ExecutionConflictError(
            "inventory_changed: membership or classification changed; restart inventory read."
        )
    total = len(selected)
    if continuation is not None:
        key = (continuation.created_at, continuation.deployment_id.int)
        selected = [row for row in selected if (row.created_at, row.id.int) < key]
        offset = total - len(selected)
    page = tuple(
        selected[:limit] if continuation is not None else selected[offset : offset + limit]
    )
    has_more = offset + len(page) < total
    next_cursor = None
    if has_more and page:
        last = page[-1]
        token = InventoryCursor(
            as_of=as_of,
            strategy_id=strategy_id,
            fingerprint=fingerprint,
            created_at=last.created_at,
            deployment_id=last.id,
        )
        next_cursor = base64.urlsafe_b64encode(token.model_dump_json().encode()).decode()
    return InventoryPage(
        page, limit, offset, len(page), total, has_more, as_of, fingerprint, next_cursor
    )


def _decode(cursor: str) -> InventoryCursor:
    """Validate an untrusted cursor; malformed and naive timestamps are refused."""
    if len(cursor) > 2048:
        raise ExecutionConflictError("Invalid inventory cursor: too long.")
    try:
        decoded = InventoryCursor.model_validate_json(
            base64.b64decode(cursor, altchars=b"-_", validate=True)
        )
    except (ValueError, ValidationError) as error:
        raise ExecutionConflictError("Invalid inventory cursor.") from error
    if decoded.as_of.tzinfo is None or decoded.created_at.tzinfo is None:
        raise ExecutionConflictError("Invalid inventory cursor: naive timestamp.")
    return decoded
