"""Record per-bar decisions from the execution worker without ever blocking trading.

The worker binds one ``DecisionJournalStore`` for a whole cycle (like the execution
audit scope). Around each closed-bar call it binds fresh observations, then records
the bar after the call returns (or raises). Every write is bounded in time; any
failure is logged and audited and the trading cycle continues unchanged. No
exchange call is ever made here.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.decision_builder import (
    BarContext,
    build_bar_decision,
    build_gate_skip_decision,
)
from thytrader.execution.decision_scope import decision_observation_scope
from thytrader.execution.decisions import (
    DECISION_RETENTION_MAX_AGE,
    DECISION_RETENTION_MAX_ROWS_PER_DEPLOYMENT,
)
from thytrader.execution.ids import utc_now
from thytrader.market_data.no_trade import is_no_trade_bar
from thytrader.persistence.audit_events import AuditEventOutcome

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.decision_scope import DecisionObservations
    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.decisions import DecisionSkipReason
    from thytrader.execution.models import DeploymentSnapshot
    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition

_logger = logging.getLogger(__name__)
_JOURNAL: ContextVar[DecisionJournalStore | None] = ContextVar("decision_journal", default=None)
WRITE_TIMEOUT_SECONDS = 5.0
PRUNE_INTERVAL = timedelta(hours=6)
PRUNE_BATCH_LIMIT = 5_000
_FAILURE_AUDIT_INTERVAL = timedelta(minutes=15)
_last_failure_audit: dict[UUID, datetime] = {}
_last_gate_skip: dict[tuple[UUID, str], tuple[datetime, str]] = {}


@contextmanager
def decision_journal_scope(store: DecisionJournalStore | None) -> Iterator[None]:
    """Bind the journal store for one worker cycle. None disables journaling."""
    token = _JOURNAL.set(store)
    try:
        yield
    finally:
        _JOURNAL.reset(token)


def decision_journal_active() -> bool:
    """Return whether this cycle journals decisions."""
    return _JOURNAL.get() is not None


@contextmanager
def observe_bar() -> Iterator[DecisionObservations | None]:
    """Bind fresh loop observations for one bar when a journal is active."""
    with decision_observation_scope(enabled=decision_journal_active()) as observations:
        yield observations


async def record_bar_decision(
    *,
    strategy: StrategyDefinition,
    product_id: str,
    candle: Candle,
    before: DeploymentSnapshot,
    after: DeploymentSnapshot | None,
    observations: DecisionObservations | None,
    allow_new_entries: bool,
    error: str | None = None,
) -> None:
    """Journal one processed bar; failures are logged and audited, never raised."""
    store = _JOURNAL.get()
    if store is None:
        return
    deployment_id = before.deployment.id
    try:
        async with asyncio.timeout(WRITE_TIMEOUT_SECONDS):
            previous = await store.latest_before(deployment_id, product_id, candle.starts_at)
            decision = build_bar_decision(
                BarContext(
                    strategy=strategy,
                    product_id=product_id,
                    bar_starts_at=candle.starts_at,
                    close_price=candle.close,
                    evaluated_at=utc_now(),
                    allow_new_entries=allow_new_entries,
                    before=before,
                    after=after,
                    observations=observations,
                    previous_evaluated_at=None if previous is None else previous.evaluated_at,
                    error=error,
                    no_trade_bar=is_no_trade_bar(candle),
                )
            )
            await store.upsert(decision)
    except Exception as failure:
        _logger.exception(
            "decision_journal_write_failed deployment_id=%s product_id=%s bar=%s",
            deployment_id,
            product_id,
            candle.starts_at.isoformat(),
        )
        await _audit_failure(deployment_id, product_id, type(failure).__name__)


async def record_gate_skip(
    *,
    snapshot: DeploymentSnapshot,
    strategy: StrategyDefinition,
    product_ids: Sequence[str],
    bar_starts_at: datetime,
    reason: DecisionSkipReason,
    detail: str,
) -> None:
    """Journal a bar the worker could not evaluate (data gap, user-feed gate).

    A bar that was already evaluated keeps its real decision and is not overwritten,
    and a gate that persists across cycles writes its bar once per process.
    """
    store = _JOURNAL.get()
    last = snapshot.deployment.last_evaluated_bar
    if store is None or (last is not None and bar_starts_at <= last):
        return
    for product_id in product_ids:
        gate_key = (snapshot.deployment.id, product_id)
        if _last_gate_skip.get(gate_key) == (bar_starts_at, reason.value):
            continue
        try:
            async with asyncio.timeout(WRITE_TIMEOUT_SECONDS):
                await store.upsert(
                    build_gate_skip_decision(
                        snapshot=snapshot,
                        strategy=strategy,
                        product_id=product_id,
                        bar_starts_at=bar_starts_at,
                        evaluated_at=utc_now(),
                        reason=reason,
                        detail=detail,
                    )
                )
            _last_gate_skip[gate_key] = (bar_starts_at, reason.value)
        except Exception as failure:
            _logger.exception(
                "decision_journal_write_failed deployment_id=%s product_id=%s reason=%s",
                snapshot.deployment.id,
                product_id,
                reason.value,
            )
            await _audit_failure(snapshot.deployment.id, product_id, type(failure).__name__)


async def prune_decisions(store: DecisionJournalStore, *, now: datetime) -> int:
    """Run one bounded retention pass; failures are logged and audited, never raised."""
    try:
        removed = await store.prune(
            now=now,
            max_rows_per_deployment=DECISION_RETENTION_MAX_ROWS_PER_DEPLOYMENT,
            max_age=DECISION_RETENTION_MAX_AGE,
            batch_limit=PRUNE_BATCH_LIMIT,
        )
    except Exception as failure:
        _logger.exception("decision_journal_prune_failed")
        await _safe_audit(
            action="decision_journal_prune_failed",
            outcome=AuditEventOutcome.FAILURE,
            detail=f"Decision-journal retention pass failed ({type(failure).__name__}).",
            product_id=None,
        )
        return 0
    if removed:
        await _safe_audit(
            action="decision_journal_pruned",
            outcome=AuditEventOutcome.SUCCESS,
            detail=(
                f"removed={removed}: kept the newest "
                f"{DECISION_RETENTION_MAX_ROWS_PER_DEPLOYMENT} decisions per bot within "
                f"{DECISION_RETENTION_MAX_AGE.days} days."
            ),
            product_id=None,
        )
    return removed


async def _audit_failure(deployment_id: UUID, product_id: str, error_class: str) -> None:
    """Audit a journal write failure at most once per bot per interval."""
    now = utc_now()
    last = _last_failure_audit.get(deployment_id)
    if last is not None and now - last < _FAILURE_AUDIT_INTERVAL:
        return
    _last_failure_audit[deployment_id] = now
    await _safe_audit(
        action="decision_journal_write_failed",
        outcome=AuditEventOutcome.FAILURE,
        detail=(
            f"deployment_id={deployment_id} error={error_class}: the per-bar decision was not "
            "journaled; trading continued unchanged."
        ),
        product_id=product_id,
    )


async def _safe_audit(
    *, action: str, outcome: AuditEventOutcome, detail: str, product_id: str | None
) -> None:
    """Append one audit row without letting audit storage failures escape."""
    try:
        await record_execution_audit(
            action=action, outcome=outcome, detail=detail, product_id=product_id
        )
    except Exception:
        _logger.exception("decision_journal_audit_failed action=%s", action)
