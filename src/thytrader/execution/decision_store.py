"""Decision-journal persistence contracts, cursors, and non-database implementations."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable
from uuid import UUID

from thytrader.execution.decisions import DecisionPage

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import timedelta

    from thytrader.execution.decisions import BarDecision, DecisionOutcome

DecisionCursorKey = tuple[datetime, UUID, str]


class DecisionStoreError(RuntimeError):
    """Signal that the decision journal is unavailable or a cursor is malformed."""


@runtime_checkable
class DecisionJournalStore(Protocol):
    """Upsert, page, and prune per-bar decisions; newest bar first."""

    async def upsert(self, decision: BarDecision) -> None:
        """Insert or replace the row for ``(deployment_id, product_id, bar_starts_at)``."""
        ...

    async def latest_before(
        self, deployment_id: UUID, product_id: str, bar_starts_at: datetime
    ) -> BarDecision | None:
        """Return the newest decision for one product strictly before one bar."""
        ...

    async def list_for_deployment(
        self,
        deployment_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        product_id: str | None = None,
    ) -> DecisionPage:
        """Return one newest-first page of one deployment's decisions."""
        ...

    async def list_for_strategy(
        self,
        strategy_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        deployment_id: UUID | None = None,
    ) -> DecisionPage:
        """Return one newest-first page across a strategy's deployments."""
        ...

    async def list_recent(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
    ) -> DecisionPage:
        """Return one newest-first page across every bot (operator overview)."""
        ...

    async def prune(
        self,
        *,
        now: datetime,
        max_rows_per_deployment: int,
        max_age: timedelta,
        batch_limit: int,
    ) -> int:
        """Delete at most ``batch_limit`` rows beyond the retention bounds; return the count."""
        ...


def encode_decision_cursor(decision: BarDecision) -> str:
    """Encode the descending sort key of one decision as an opaque cursor."""
    return encode_decision_cursor_key(
        bar_starts_at=decision.bar_starts_at,
        deployment_id=decision.deployment_id,
        product_id=decision.product_id,
    )


def encode_decision_cursor_key(
    *, bar_starts_at: datetime, deployment_id: UUID, product_id: str
) -> str:
    """Encode one ``(bar, deployment, product)`` sort key as an opaque cursor."""
    stamp = bar_starts_at.astimezone(UTC).isoformat()
    payload = f"{stamp}|{deployment_id}|{product_id}"
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_decision_cursor(cursor: str) -> DecisionCursorKey:
    """Decode one cursor; raise ``DecisionStoreError`` when it is malformed."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, deployment_text, product_id = raw.split("|", 2)
        moment = datetime.fromisoformat(stamp)
        deployment_id = UUID(deployment_text)
    except (ValueError, UnicodeDecodeError) as error:
        raise DecisionStoreError("Invalid decision cursor.") from error
    if moment.tzinfo is None or not product_id:
        raise DecisionStoreError("Invalid decision cursor.")
    return moment.astimezone(UTC), deployment_id, product_id


def decision_sort_key(decision: BarDecision) -> DecisionCursorKey:
    """Descending sort key shared by every store: bar, then deployment, then product."""
    return decision.bar_starts_at, decision.deployment_id, decision.product_id


def page_decisions(rows: Iterable[BarDecision], *, limit: int, cursor: str | None) -> DecisionPage:
    """Order rows newest first, apply the cursor, and cut one page."""
    ordered = sorted(rows, key=decision_sort_key, reverse=True)
    if cursor is not None:
        after = decode_decision_cursor(cursor)
        ordered = [row for row in ordered if decision_sort_key(row) < after]
    page = tuple(ordered[:limit])
    next_cursor = encode_decision_cursor(page[-1]) if len(ordered) > limit and page else None
    return DecisionPage(decisions=page, next_cursor=next_cursor)


def decision_storage_label(store: DecisionJournalStore) -> Literal["available", "unavailable"]:
    """Report whether reads come from durable storage."""
    return "unavailable" if isinstance(store, DisabledDecisionJournalStore) else "available"


class DisabledDecisionJournalStore:
    """No durable journal: reads are empty and writes are refused."""

    async def upsert(self, decision: BarDecision) -> None:
        """Refuse journal writes without durable storage."""
        del decision
        raise DecisionStoreError("Decision journal storage is unavailable.")

    async def latest_before(
        self, deployment_id: UUID, product_id: str, bar_starts_at: datetime
    ) -> BarDecision | None:
        """Return nothing without durable storage."""
        del deployment_id, product_id, bar_starts_at
        return None

    async def list_for_deployment(
        self,
        deployment_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        product_id: str | None = None,
    ) -> DecisionPage:
        """Return an empty page without durable storage."""
        del deployment_id, limit, outcomes, product_id
        if cursor is not None:
            decode_decision_cursor(cursor)
        return DecisionPage(decisions=())

    async def list_for_strategy(
        self,
        strategy_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        deployment_id: UUID | None = None,
    ) -> DecisionPage:
        """Return an empty page without durable storage."""
        del strategy_id, limit, outcomes, deployment_id
        if cursor is not None:
            decode_decision_cursor(cursor)
        return DecisionPage(decisions=())

    async def list_recent(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
    ) -> DecisionPage:
        """Return an empty page without durable storage."""
        del limit, outcomes
        if cursor is not None:
            decode_decision_cursor(cursor)
        return DecisionPage(decisions=())

    async def prune(
        self,
        *,
        now: datetime,
        max_rows_per_deployment: int,
        max_age: timedelta,
        batch_limit: int,
    ) -> int:
        """Nothing to prune without durable storage."""
        del now, max_rows_per_deployment, max_age, batch_limit
        return 0


class InMemoryDecisionJournalStore:
    """Process-local journal for tests and storage-free development."""

    def __init__(self) -> None:
        """Start with no decisions."""
        self._rows: dict[tuple[UUID, str, datetime], BarDecision] = {}

    async def upsert(self, decision: BarDecision) -> None:
        """Insert or replace one bar's decision."""
        key = (decision.deployment_id, decision.product_id, decision.bar_starts_at)
        self._rows[key] = decision

    async def latest_before(
        self, deployment_id: UUID, product_id: str, bar_starts_at: datetime
    ) -> BarDecision | None:
        """Return the newest earlier decision for one product."""
        earlier = sorted(
            (
                row
                for (row_deployment, row_product, row_bar), row in self._rows.items()
                if row_deployment == deployment_id
                and row_product == product_id
                and row_bar < bar_starts_at
            ),
            key=decision_sort_key,
        )
        return earlier[-1] if earlier else None

    async def list_for_deployment(
        self,
        deployment_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        product_id: str | None = None,
    ) -> DecisionPage:
        """Return one newest-first page of one deployment's decisions."""
        rows = (
            row
            for row in self._rows.values()
            if row.deployment_id == deployment_id
            and (not outcomes or row.outcome in outcomes)
            and (product_id is None or row.product_id == product_id)
        )
        return page_decisions(rows, limit=limit, cursor=cursor)

    async def list_for_strategy(
        self,
        strategy_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        deployment_id: UUID | None = None,
    ) -> DecisionPage:
        """Return one newest-first page across a strategy's deployments."""
        rows = (
            row
            for row in self._rows.values()
            if row.strategy_id == strategy_id
            and (not outcomes or row.outcome in outcomes)
            and (deployment_id is None or row.deployment_id == deployment_id)
        )
        return page_decisions(rows, limit=limit, cursor=cursor)

    async def list_recent(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
    ) -> DecisionPage:
        """Return one newest-first page across every bot."""
        rows = (row for row in self._rows.values() if not outcomes or row.outcome in outcomes)
        return page_decisions(rows, limit=limit, cursor=cursor)

    async def prune(
        self,
        *,
        now: datetime,
        max_rows_per_deployment: int,
        max_age: timedelta,
        batch_limit: int,
    ) -> int:
        """Drop rows older than ``max_age`` and beyond the per-deployment newest-N."""
        cutoff = now - max_age
        doomed = [key for key, row in self._rows.items() if row.bar_starts_at < cutoff]
        by_deployment: dict[UUID, list[BarDecision]] = {}
        for row in self._rows.values():
            by_deployment.setdefault(row.deployment_id, []).append(row)
        for rows in by_deployment.values():
            rows.sort(key=decision_sort_key, reverse=True)
            doomed.extend(
                (row.deployment_id, row.product_id, row.bar_starts_at)
                for row in rows[max_rows_per_deployment:]
            )
        removed = 0
        for key in dict.fromkeys(doomed):
            if removed >= batch_limit:
                break
            if self._rows.pop(key, None) is not None:
                removed += 1
        return removed

    def rows(self) -> tuple[BarDecision, ...]:
        """Return every stored decision, newest first (test helper)."""
        return tuple(sorted(self._rows.values(), key=decision_sort_key, reverse=True))
