"""Skip a wake that would read the same picture again (ADR 0147).

A desk wakes on its cadence, but its market rows come from closed hourly candles, so a desk that
wakes every 30 minutes would ask its model about the same hour twice. Before running the
harness, the runner asks ThyTrader whether anything the model can act on changed since the
desk's newest wake. If nothing did, ThyTrader journals a ``skipped`` wake (no model call, no
thesis) and the harness does not run.

A skip needs all of, judged under the desk's lock from ThyTrader's own state:

- the newest wake (in journal order) was an applied ``no_trade`` or itself a skip, by the same
  harness, model and runner (command template);
- the briefing's material fingerprint equals that wake's. It covers the market rows, the desk's
  cash, caps, headroom, fee rates and positions, the UTC date, the prompt instructions and the
  desk's configured harness, model, mandate and caps, but not the recent wakes;
- the desk holds no open book and had no pending open before this read (a held book's mark,
  stop or fill can change at any time; a recovered or refunded open is news);
- the last answered wake was journaled less than :data:`MAX_UNANSWERED` ago (server time), so a
  stalled market feed still reaches the model now and then.

An error, an invalid answer or a refusal is never skipped past: the next wake runs the harness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from thytrader.agent_desk.briefing import DeskBriefing
from thytrader.agent_desk.models import DeskAction, DeskWake, WakeOutcome
from thytrader.agent_desk.ports import journal_id
from thytrader.agent_desk.service import desk_lock, locked_briefing

if TYPE_CHECKING:
    from thytrader.agent_desk.ports import DeskExecution
    from thytrader.agent_desk.store import DeskStore
    from thytrader.market_data.service import MarketDataService

SKIP_CODE = "DESK_WAKE_UNCHANGED"
MAX_UNANSWERED = timedelta(hours=6)


class SkipVerdict(BaseModel):
    """Whether the wake was skipped (and journaled), or why the harness must run.

    When it was not skipped, ``briefing`` is the briefing this check built under the desk's lock:
    the runner answers that one, so its harness, model and configuration fingerprint are one
    consistent snapshot.
    """

    model_config = ConfigDict(frozen=True)

    skipped: bool
    reason: str | None = None
    wake: DeskWake | None = None
    briefing: DeskBriefing | None = None


class Runner(BaseModel):
    """Who would answer this wake."""

    model_config = ConfigDict(frozen=True)

    harness: str
    model: str | None = None
    fingerprint: str | None = None


async def skip_unchanged(
    desk_id: str,
    *,
    store: DeskStore,
    execution: DeskExecution,
    market_data: MarketDataService,
    runner: Runner,
    started_at: datetime,
    now: datetime | None = None,
) -> SkipVerdict:
    """Journal a skipped wake when nothing changed, else say why the harness must run."""
    async with desk_lock(desk_id):
        moment = now or datetime.now(UTC)
        before = await store.get_desk(desk_id)
        pending = before is not None and before.pending_open is not None
        brief = await locked_briefing(
            desk_id, store=store, execution=execution, market_data=market_data, now=moment
        )
        newest = await store.list_wakes(desk_id, limit=1)
        answered = await store.list_wakes(desk_id, limit=1, answered_only=True)
        reason = must_wake(
            brief,
            newest[0] if newest else None,
            answered[0] if answered else None,
            runner=runner,
            pending=pending,
            now=moment,
        )
        if reason is not None:
            return SkipVerdict(skipped=False, reason=reason, briefing=brief)
        wake = DeskWake(
            wake_id=journal_id(moment),
            desk_id=desk_id,
            started_at=min(started_at, moment),
            finished_at=moment,
            harness=runner.harness,
            model=runner.model,
            briefing_fingerprint=brief.fingerprint,
            raw_response="",
            action=DeskAction.SKIPPED,
            outcome=WakeOutcome.SKIPPED,
            outcome_code=SKIP_CODE,
            detail=f"Nothing changed since wake {newest[0].wake_id}; no harness ran.",
            equity_quote=brief.desk.equity_quote,
            material_fingerprint=brief.material_fingerprint,
            runner_fingerprint=runner.fingerprint,
        )
        await store.add_wake(wake)
        return SkipVerdict(skipped=True, wake=wake)


def must_wake(
    brief: DeskBriefing,
    newest: DeskWake | None,
    answered: DeskWake | None,
    *,
    runner: Runner,
    pending: bool,
    now: datetime,
) -> str | None:
    """Why this wake must run the harness, or None when it may be skipped."""
    if newest is None or answered is None:
        return "DESK_WAKE_FIRST: the desk has no answered wake to compare with."
    reason = None
    if newest.outcome not in {WakeOutcome.APPLIED, WakeOutcome.SKIPPED}:
        reason = f"DESK_WAKE_RETRY: the newest wake was {newest.outcome.value}."
    elif newest.action not in {DeskAction.NO_TRADE, DeskAction.SKIPPED}:
        reason = f"DESK_WAKE_ACTED: the newest wake applied {newest.action.value}."
    elif (newest.harness, newest.model, newest.runner_fingerprint) != (
        runner.harness,
        runner.model,
        runner.fingerprint,
    ):
        reason = "DESK_WAKE_HARNESS_CHANGED: another harness, model or command answers this wake."
    elif brief.positions or pending:
        reason = "DESK_WAKE_HOLDING: the desk holds an open book or had a pending open."
    elif newest.material_fingerprint is None:
        reason = "DESK_WAKE_UNANCHORED: the newest wake recorded no material fingerprint."
    elif newest.material_fingerprint != brief.material_fingerprint:
        reason = "DESK_WAKE_CHANGED: the market, the desk or its configuration changed."
    elif now - answered.finished_at >= MAX_UNANSWERED:
        reason = "DESK_WAKE_DUE: the model has not been asked for six hours."
    return reason
