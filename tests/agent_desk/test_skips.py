"""A wake that would read the same picture again is journaled as skipped, with no model call."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from typing import TYPE_CHECKING

import pytest

from tests.agent_desk.test_service import _desk, _Execution, _open
from thytrader.agent_desk import service
from thytrader.agent_desk.answers import AnswerFingerprints
from thytrader.agent_desk.models import DeskAction, DeskStatus, PendingOpen, WakeOutcome
from thytrader.agent_desk.ports import journal_id
from thytrader.agent_desk.service import briefing, record_wake, score
from thytrader.agent_desk.skips import MAX_UNANSWERED, SKIP_CODE, Runner, skip_unchanged
from thytrader.agent_desk.store import DeskStoreUnavailableError
from thytrader.agent_desk.update import DeskChanges, update_desk

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.agent_desk.market import MarketRow
    from thytrader.agent_desk.models import DeskWake
    from thytrader.agent_desk.skips import SkipVerdict
    from thytrader.agent_desk.store import InMemoryDeskStore
    from thytrader.market_data.service import MarketDataService

pytestmark = pytest.mark.anyio
_NO_TRADE = json.dumps({"action": "no_trade", "thesis": "nothing clears the fees"})
_RUNNER = "sha256:" + "c" * 64


async def _answer(
    store: InMemoryDeskStore,
    execution: _Execution,
    desk_id: str,
    answer: str = _NO_TRADE,
    *,
    harness: str = "test",
    model: str | None = None,
    runner: str | None = _RUNNER,
    harness_error: str | None = None,
    started_at: datetime | None = None,
) -> DeskWake:
    """Brief, then record an answer against that briefing's fingerprints."""
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    return await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=answer,
        harness=harness,
        model=model,
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(
            material=brief.material_fingerprint, config=brief.config_fingerprint, runner=runner
        ),
        started_at=started_at or datetime.now(UTC),
        harness_error=harness_error,
    )


async def _skip(
    store: InMemoryDeskStore,
    execution: _Execution,
    desk_id: str,
    *,
    harness: str = "test",
    model: str | None = None,
    runner: str | None = _RUNNER,
    now: datetime | None = None,
) -> SkipVerdict:
    """Ask ThyTrader to skip this wake."""
    return await skip_unchanged(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        runner=Runner(harness=harness, model=model, fingerprint=runner),
        started_at=datetime.now(UTC),
        now=now,
    )


async def test_the_first_wake_always_asks_the_model() -> None:
    """With nothing to compare against, the harness runs."""
    store, execution, desk_id = await _desk()
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_FIRST")
    assert await store.list_wakes(desk_id, limit=5) == ()


async def test_an_unchanged_picture_is_journaled_as_a_skip_and_skips_chain() -> None:
    """Same bars, same flat desk, same harness: a skipped wake, then another one."""
    store, execution, desk_id = await _desk()
    answered = await _answer(store, execution, desk_id)
    first = await _skip(store, execution, desk_id)
    second = await _skip(store, execution, desk_id)
    assert first.skipped
    assert second.skipped
    assert first.wake is not None
    assert first.wake.outcome is WakeOutcome.SKIPPED
    assert first.wake.action is DeskAction.SKIPPED
    assert first.wake.outcome_code == SKIP_CODE
    assert first.wake.raw_response == ""
    assert first.wake.material_fingerprint == answered.material_fingerprint
    assert [wake.outcome for wake in await store.list_wakes(desk_id, limit=5)] == [
        WakeOutcome.SKIPPED,
        WakeOutcome.SKIPPED,
        WakeOutcome.APPLIED,
    ]


async def test_a_new_closed_bar_asks_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any change to the market rows is new information."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    original = service.market_rows

    async def next_bar(
        market_data: MarketDataService, products: Sequence[str], *, now: datetime | None = None
    ) -> tuple[MarketRow, ...]:
        rows = await original(market_data, products, now=now)
        return tuple(_later(row) if row.last_bar_starts_at is not None else row for row in rows)

    monkeypatch.setattr(service, "market_rows", next_bar)
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_CHANGED")


def _later(row: MarketRow) -> MarketRow:
    """The same row one bar later."""
    assert row.last_bar_starts_at is not None
    return row.model_copy(
        update={"last_bar_starts_at": row.last_bar_starts_at + timedelta(hours=1)}
    )


@pytest.mark.parametrize(
    ("answer", "error", "code"),
    [
        ("not json", None, "DESK_WAKE_RETRY"),
        ("", "DESK_HARNESS_EXIT: exit 1: 403", "DESK_WAKE_RETRY"),
    ],
)
async def test_a_failed_wake_is_never_skipped_past(
    answer: str, error: str | None, code: str
) -> None:
    """After an error or an invalid answer, the next wake asks the model again."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    await _answer(store, execution, desk_id, answer, harness_error=error)
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith(code)


async def test_a_refused_decision_is_never_skipped_past() -> None:
    """A refusal is information the model should see on the next wake."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id, json.dumps(_open(product="DOGE-USD")))
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_RETRY")


async def test_a_desk_holding_a_book_always_asks_the_model() -> None:
    """An open book's mark, stop or fill can change at any time."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id, json.dumps(_open()))
    await _answer(store, execution, desk_id)
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_HOLDING")


async def test_another_harness_or_model_asks_the_model() -> None:
    """A switched model has not seen this picture."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id, model="old-model")
    verdict = await _skip(store, execution, desk_id, model="new-model")
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_HARNESS_CHANGED")


async def test_a_wake_without_a_material_fingerprint_never_anchors_a_skip() -> None:
    """A recorded answer that did not carry the briefing's material fingerprint proves nothing."""
    store, execution, desk_id = await _desk()
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=_NO_TRADE,
        harness="test",
        model=None,
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(runner=_RUNNER),
        started_at=datetime.now(UTC),
    )
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_UNANCHORED")


async def test_the_model_is_asked_again_after_six_hours_of_skips() -> None:
    """A stalled market feed still reaches the model now and then (server journal time)."""
    store, execution, desk_id = await _desk()
    answered = await _answer(store, execution, desk_id)
    store.wakes = [
        wake.model_copy(update={"finished_at": wake.finished_at - MAX_UNANSWERED})
        if wake.wake_id == answered.wake_id
        else wake
        for wake in store.wakes
    ]
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_DUE")


async def test_a_changed_desk_asks_the_model() -> None:
    """A paused desk (a new entry block) is a different picture."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    desk = await store.get_desk(desk_id)
    assert desk is not None
    await service.save_desk(
        desk.model_copy(update={"status": DeskStatus.PAUSED}), store=store, previous=desk
    )
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_CHANGED")


async def test_skips_stay_out_of_the_briefing_and_are_counted_apart_in_the_score() -> None:
    """The model's recent theses are never crowded out; the score counts answered wakes."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    for _ in range(3):
        assert (await _skip(store, execution, desk_id)).skipped
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    scored = await score(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert [view.outcome for view in brief.recent_wakes] == ["applied"]
    assert scored.wakes == 4
    assert scored.answered_wakes == 1
    assert scored.wakes_by_outcome["skipped"] == 3


async def test_the_material_fingerprint_ignores_recent_wakes_but_the_fingerprint_does_not() -> None:
    """A new wake in the history changes what the model reads, not what it can act on."""
    store, execution, desk_id = await _desk()
    before = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    await _answer(store, execution, desk_id)
    after = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert before.fingerprint != after.fingerprint
    assert before.material_fingerprint == after.material_fingerprint
    assert after.material_fingerprint not in after.prompt


async def test_a_model_switched_on_the_desk_is_asked_even_by_a_stale_runner() -> None:
    """A PATCH between the runner's read and its skip request still wakes the model."""
    store, execution, desk_id = await _desk(model="old-model")
    await _answer(store, execution, desk_id, model="old-model")
    await update_desk(store, desk_id, DeskChanges(model="new-model"), live_acknowledged=False)
    verdict = await _skip(store, execution, desk_id, model="old-model")
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_CHANGED")


async def test_another_command_template_asks_the_model() -> None:
    """Two custom commands share the harness name; their runner fingerprints differ."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    verdict = await _skip(store, execution, desk_id, runner="sha256:" + "d" * 64)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_HARNESS_CHANGED")


async def test_a_pending_open_resolved_by_this_read_asks_the_model() -> None:
    """A crashed open that the skip's own read refunds or links is news to the model."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    desk = await store.get_desk(desk_id)
    assert desk is not None
    pending = PendingOpen(
        wake_id="w-crashed",
        idempotency_key="desk:never-placed",
        product_id="BTC-USD",
        side="long",
        allocated_quote=Decimal(100),
        reserved_at=datetime.now(UTC),
    )
    await service.save_desk(
        desk.model_copy(update={"cash": desk.cash - 100, "pending_open": pending}),
        store=store,
        previous=desk,
    )
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_RETRY")
    refunded = await store.get_desk(desk_id)
    assert refunded is not None
    assert refunded.pending_open is None
    newest = (await store.list_wakes(desk_id, limit=1))[0]
    assert newest.outcome_code == "DESK_OPEN_REFUNDED"


async def test_a_new_utc_day_asks_the_model() -> None:
    """The trading date is part of the picture, even before a new bar closes."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    verdict = await _skip(store, execution, desk_id, now=tomorrow)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_CHANGED")


async def test_many_skips_never_crowd_the_answered_history_or_the_six_hour_clock() -> None:
    """Answered theses and the last answer are read apart from any run of skips."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    for _ in range(60):
        assert (await _skip(store, execution, desk_id)).skipped
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    assert [view.outcome for view in brief.recent_wakes] == ["applied"]
    assert (await _skip(store, execution, desk_id)).skipped


async def test_journal_order_not_a_callers_start_time_decides_the_newest_wake() -> None:
    """An error journaled after a skip is newest even if its harness started earlier."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    assert (await _skip(store, execution, desk_id)).skipped
    await _answer(
        store,
        execution,
        desk_id,
        "",
        harness_error="DESK_HARNESS_TIMEOUT: slow",
        started_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_RETRY")


async def test_an_answer_to_a_briefing_of_another_configuration_is_refused() -> None:
    """A model or mandate change while a harness runs refuses the in-flight answer."""
    store, execution, desk_id = await _desk(model="old-model")
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    await update_desk(
        store, desk_id, DeskChanges(mandate="Do not open positions"), live_acknowledged=False
    )
    wake = await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=json.dumps(_open()),
        harness="test",
        model="old-model",
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(config=brief.config_fingerprint),
        started_at=datetime.now(UTC),
    )
    assert wake.outcome is WakeOutcome.REFUSED
    assert wake.outcome_code == "DESK_CONFIG_CHANGED"
    assert await store.list_books(desk_id) == ()


async def test_a_refund_by_an_earlier_read_is_journaled_and_never_skipped_past() -> None:
    """A briefing or score that refunds a crashed open journals it; the next wake asks."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    desk = await store.get_desk(desk_id)
    assert desk is not None
    pending = PendingOpen(
        wake_id="w-crashed",
        idempotency_key="desk:never-placed",
        product_id="BTC-USD",
        side="long",
        allocated_quote=Decimal(100),
        reserved_at=datetime.now(UTC),
    )
    await service.save_desk(
        desk.model_copy(update={"cash": desk.cash - 100, "pending_open": pending}),
        store=store,
        previous=desk,
    )
    await briefing(desk_id, store=store, execution=execution, market_data=execution.market_data)
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_RETRY")


def test_wake_ids_keep_journal_order_within_one_millisecond() -> None:
    """Ids minted in the same millisecond, or after a clock step back, still sort in order."""
    moment = datetime.now(UTC)
    ids = [journal_id(moment) for _ in range(50)]
    ids.append(journal_id(moment - timedelta(seconds=5)))
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


async def test_a_resume_between_two_wakes_asks_the_model() -> None:
    """A pause and resume that leave the desk active again is still news."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    for status in (DeskStatus.PAUSED, DeskStatus.ACTIVE):
        desk = await store.get_desk(desk_id)
        assert desk is not None
        changes: dict[str, object] = {"status": status}
        if status is DeskStatus.ACTIVE:
            changes["due_since"] = datetime.now(UTC)
        await service.save_desk(desk.model_copy(update=changes), store=store, previous=desk)
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith("DESK_WAKE_CHANGED")


async def test_a_material_fingerprint_that_is_not_the_desks_own_picture_is_not_kept() -> None:
    """A recorded fingerprint must match the desk's picture at record time to anchor a skip."""
    store, execution, desk_id = await _desk()
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    wake = await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=_NO_TRADE,
        harness="test",
        model=None,
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(
            material="sha256:" + "0" * 64, config=brief.config_fingerprint, runner=_RUNNER
        ),
        started_at=datetime.now(UTC),
    )
    assert wake.material_fingerprint is None
    honest = await _answer(store, execution, desk_id)
    assert honest.material_fingerprint == brief.material_fingerprint


async def test_a_close_is_applied_even_after_the_configuration_changed() -> None:
    """Closing a book only reduces risk; a stale configuration never keeps it open."""
    store, execution, desk_id = await _desk()
    opened = await _answer(store, execution, desk_id, json.dumps(_open()))
    assert opened.deployment_id is not None
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    await update_desk(store, desk_id, DeskChanges(model="another"), live_acknowledged=False)
    close = json.dumps({"action": "close", "thesis": "done", "deployment_id": opened.deployment_id})
    wake = await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=close,
        harness="test",
        model=None,
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(config=brief.config_fingerprint),
        started_at=datetime.now(UTC),
    )
    assert wake.outcome is WakeOutcome.APPLIED
    assert wake.action is DeskAction.CLOSE


@pytest.mark.parametrize("between", ["refused", "error"])
async def test_an_answer_that_missed_a_journaled_event_never_anchors_a_skip(between: str) -> None:
    """A refusal or failure journaled while the model thought keeps the next wake asking."""
    store, execution, desk_id = await _desk()
    await _answer(store, execution, desk_id)
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    if between == "refused":
        await _answer(store, execution, desk_id, json.dumps(_open(product="DOGE-USD")))
    else:
        await _answer(store, execution, desk_id, "", harness_error="DESK_HARNESS_TIMEOUT: slow")
    late = await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=_NO_TRADE,
        harness="test",
        model=None,
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(
            material=brief.material_fingerprint, config=brief.config_fingerprint, runner=_RUNNER
        ),
        started_at=datetime.now(UTC),
    )
    assert late.outcome is WakeOutcome.APPLIED
    assert late.material_fingerprint is None
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped


async def test_a_live_desk_refuses_an_answer_from_another_harness_or_model() -> None:
    """A direct record naming a model the live desk was not acknowledged for is refused."""
    store, execution, desk_id = await _desk(mode="live", live_acknowledged=True, model="a")
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    wake = await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=json.dumps(_open(entry="limit", limit_price="1")),
        harness="claude",
        model="unapproved",
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(config=brief.config_fingerprint),
        started_at=datetime.now(UTC),
    )
    assert wake.outcome is WakeOutcome.REFUSED
    assert wake.outcome_code == "DESK_LIVE_IDENTITY_MISMATCH"
    assert await store.list_books(desk_id) == ()


async def test_a_live_close_from_another_identity_is_still_applied() -> None:
    """Closing only reduces risk, whoever answers."""
    store, execution, desk_id = await _desk(model="a")
    opened = await _answer(store, execution, desk_id, json.dumps(_open()), model="a")
    assert opened.deployment_id is not None
    desk = await store.get_desk(desk_id)
    assert desk is not None
    await service.save_desk(
        desk.model_copy(update={"mode": "live", "live_acknowledged_at": datetime.now(UTC)}),
        store=store,
        previous=desk,
    )
    close = json.dumps({"action": "close", "thesis": "x", "deployment_id": opened.deployment_id})
    wake = await _answer(store, execution, desk_id, close, harness="other", model="z")
    assert wake.outcome is WakeOutcome.APPLIED


async def test_a_live_desk_without_a_model_accepts_an_empty_one() -> None:
    """An empty configured model and an absent one are the same identity."""
    store, execution, desk_id = await _desk(mode="live", live_acknowledged=True, model="")
    wake = await _answer(store, execution, desk_id, harness="claude", model=None)
    assert wake.outcome is WakeOutcome.APPLIED


async def test_an_unreadable_history_never_blocks_recording_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed history read leaves the answer unanchored instead of refusing to record it."""
    store, execution, desk_id = await _desk()
    original = store.list_wakes
    armed: list[bool] = []

    async def flaky(desk: str, *, limit: int, answered_only: bool = False) -> tuple:
        if answered_only and limit == service._HISTORY and armed:
            raise DeskStoreUnavailableError("down")
        return await original(desk, limit=limit, answered_only=answered_only)

    monkeypatch.setattr(store, "list_wakes", flaky)
    brief = await briefing(
        desk_id, store=store, execution=execution, market_data=execution.market_data
    )
    armed.append(True)
    wake = await record_wake(
        desk_id,
        store=store,
        execution=execution,
        market_data=execution.market_data,
        raw_response=_NO_TRADE,
        harness="test",
        model=None,
        briefing_fingerprint=brief.fingerprint,
        fingerprints=AnswerFingerprints(
            material=brief.material_fingerprint, config=brief.config_fingerprint
        ),
        started_at=datetime.now(UTC),
    )
    assert wake.outcome is WakeOutcome.APPLIED
    assert wake.material_fingerprint is None


async def test_an_applied_close_is_never_skipped_past() -> None:
    """After an applied close the model hears how its book ended."""
    store, execution, desk_id = await _desk()
    opened = await _answer(store, execution, desk_id, json.dumps(_open()))
    close = json.dumps({"action": "close", "thesis": "x", "deployment_id": opened.deployment_id})
    await _answer(store, execution, desk_id, close)
    verdict = await _skip(store, execution, desk_id)
    assert not verdict.skipped
    assert verdict.reason is not None
    assert verdict.reason.startswith(("DESK_WAKE_ACTED", "DESK_WAKE_HOLDING"))
    newest = (await store.list_wakes(desk_id, limit=1))[0]
    assert newest.action is DeskAction.CLOSE
