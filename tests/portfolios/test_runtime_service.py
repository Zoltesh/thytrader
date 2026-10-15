"""Deploying a portfolio: start, attach, pause, resume, stop, and breaker reset (ADR 0091)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from typing import TYPE_CHECKING

import pytest

from tests.market_history import listed_history
from tests.portfolios.runtime_support import World, operator, portfolio, world
from thytrader.execution.service import create_deployment
from thytrader.portfolios.errors import (
    PortfolioConflictError,
    PortfolioLiveAcknowledgementError,
    PortfolioRevisionConflictError,
    PortfolioStartRejectedError,
)
from thytrader.portfolios.models import SetWeightsRequest
from thytrader.portfolios.runtime import PortfolioRuntimeService
from thytrader.portfolios.runtime_start import FeeAssumptions
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.start_safeguards import StartSafeguards
from thytrader.strategies.models import StrategyDefinition, canonical_strategy_bytes
from thytrader.strategies.warmup_policy import without_warmup_policy
from thytrader.trading.models import DeploymentMode, DeploymentStatus, LifecycleCommand

if TYPE_CHECKING:
    from thytrader.strategies.library import StrategyRecord

pytestmark = pytest.mark.anyio


async def test_paper_start_creates_one_tagged_book_per_sleeve() -> None:
    """Each sleeve starts with weight x capital of paper cash and that allocation."""
    state = world()
    current, records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    result = await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    assert [item.outcome for item in result.outcomes] == ["started", "started"]
    books = await state.tagged(pid)
    assert [book.strategy_id for book in books] == [record.strategy_id for record in records]
    assert [book.paper_starting_cash for book in books] == [Decimal("500"), Decimal("300")]
    assert [book.allocated_capital for book in books] == [Decimal("500"), Decimal("300")]
    assert {book.mode for book in books} == {DeploymentMode.PAPER}
    assert {book.status for book in books} == {DeploymentStatus.RUNNING}
    runtime = await state.portfolios.runtime_state(pid)
    assert runtime.run_started_at is not None
    assert (runtime.day_open_equity, runtime.high_water_mark_equity) == (
        Decimal("1000"),
        Decimal("1000"),
    )
    journal = await state.portfolios.journal(pid, limit=1, offset=0)
    entry = journal.entries[0]
    assert entry.kind == "deployment_started"
    assert set(entry.detail.deployment_ids) == {book.id for book in books}
    assert "2 started" in entry.summary
    actions = {event.action for event in await state.audit.list_recent(limit=10)}
    assert "portfolio_start_paper" in actions


async def test_starting_again_attaches_the_running_sleeves() -> None:
    """A second start is idempotent: running sleeves are attached, nothing is created."""
    state = world()
    current, _records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    again = await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    assert [item.outcome for item in again.outcomes] == ["attached", "attached"]
    assert len(await state.tagged(pid)) == 2


async def test_start_needs_the_reviewed_revision() -> None:
    """A stale revision is a conflict, never a start of a changed composition."""
    state = world()
    current, _records = await portfolio(state)
    with pytest.raises(PortfolioRevisionConflictError):
        await state.runtime.start(
            current.portfolio.portfolio_id,
            revision=current.portfolio.revision - 1,
            context=operator(),
        )


async def test_a_busy_strategy_refuses_the_whole_start() -> None:
    """A sleeve whose strategy already runs standalone blocks the start; nothing starts."""
    state = world()
    current, records = await portfolio(state)
    snapshot = await state.strategies.snapshot(records[0].strategy_id)
    await create_deployment(
        store=state.execution,
        publication_store=state.strategies,
        strategy_fingerprint=snapshot.strategy_fingerprint,
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("100"),
        live_allowed=False,
    )
    with pytest.raises(PortfolioStartRejectedError) as raised:
        await state.runtime.start(
            current.portfolio.portfolio_id,
            revision=current.portfolio.revision,
            context=operator(),
        )
    problems = raised.value.problems
    assert [(item.code, item.strategy_id) for item in problems] == [
        ("strategy_busy", records[0].strategy_id)
    ]
    assert "standalone bot" in problems[0].message
    assert await state.tagged(current.portfolio.portfolio_id) == ()


async def _enforce(state: World, **safeguards: bool) -> None:
    """Publish a policy whose opt-in start safeguards refuse instead of warning (ADR 0142)."""
    await state.risk.publish(
        compiled_default_risk_policy().model_copy(
            update={"start_safeguards": StartSafeguards(**safeguards)}
        )
    )


async def _with_slow_kama(state: World, record: StrategyRecord) -> None:
    """Re-save one sleeve's strategy with a KAMA too slow to declare its convergence warmup."""
    assert record.definition is not None
    document = json.loads(canonical_strategy_bytes(record.definition))
    document["indicators"].append(
        {
            "id": "slow_kama",
            "kind": "kama",
            "input": "close",
            "parameters": {"period": 10, "fast_period": 2, "slow_period": 500},
        }
    )
    saved = await state.strategies.save(
        record.strategy_id, document, expected_revision=record.revision
    )
    assert saved.definition is not None
    assert saved.definition.data_requirements.warmup_policy is None


async def test_a_sleeve_saved_before_the_warmup_policy_adopts_it_on_start() -> None:
    """Starting re-saves a pre-policy strategy, as any save would; nothing is refused."""
    state = world()
    current, records = await portfolio(state)
    stored = records[0]
    assert stored.definition is not None
    document = json.loads(canonical_strategy_bytes(stored.definition))
    legacy = StrategyDefinition.model_validate(without_warmup_policy(document))
    # PostgreSQL keeps such rows from before the policy; the memory store always converges.
    state.strategies._records[stored.strategy_id] = replace(stored, definition=legacy)
    result = await state.runtime.start(
        current.portfolio.portfolio_id, revision=current.portfolio.revision, context=operator()
    )
    assert {item.outcome for item in result.outcomes} == {"started"}
    adopted = await state.strategies.get(stored.strategy_id)
    assert adopted.definition is not None
    assert adopted.definition.data_requirements.warmup_policy == "convergence"
    assert adopted.revision == stored.revision + 1


async def test_a_sleeve_that_cannot_adopt_the_policy_starts_with_a_warning() -> None:
    """A strategy whose convergence cannot be declared starts, its outcome saying why."""
    state = world()
    current, records = await portfolio(state)
    await _with_slow_kama(state, records[0])
    result = await state.runtime.start(
        current.portfolio.portfolio_id, revision=current.portfolio.revision, context=operator()
    )
    outcome = next(item for item in result.outcomes if item.strategy_id == records[0].strategy_id)
    assert outcome.outcome == "started"
    assert (outcome.message or "").startswith("WARMUP_POLICY_MISSING: ")


async def test_the_opt_in_safeguard_refuses_a_sleeve_without_the_policy() -> None:
    """Under require_convergence_warmup, nothing starts and the sleeve's problem names it."""
    state = world()
    await _enforce(state, require_convergence_warmup=True)
    current, records = await portfolio(state)
    await _with_slow_kama(state, records[0])
    with pytest.raises(PortfolioStartRejectedError) as raised:
        await state.runtime.start(
            current.portfolio.portfolio_id,
            revision=current.portfolio.revision,
            context=operator(),
        )
    (problem,) = raised.value.problems
    assert (problem.code, problem.strategy_id) == ("warmup_policy_required", records[0].strategy_id)
    assert problem.message.startswith("WARMUP_POLICY_REQUIRED: ")
    assert await state.tagged(current.portfolio.portfolio_id) == ()


def _young_runtime(state: World) -> PortfolioRuntimeService:
    """The world's runtime reading a venue whose markets listed two days ago."""
    return PortfolioRuntimeService(
        portfolios=state.portfolios,
        execution=state.execution,
        strategies=state.strategies,
        publication=state.strategies,
        risk_store=state.risk,
        live_allowed=True,
        audit=state.audit,
        market_data=listed_history(listed_at=datetime.now(UTC) - timedelta(days=2)),
    )


async def test_a_sleeve_on_a_market_younger_than_its_warmup_starts_with_a_warning() -> None:
    """Every sleeve starts; each outcome carries the history warning (ADR 0142)."""
    state = world()
    current, records = await portfolio(state)
    result = await _young_runtime(state).start(
        current.portfolio.portfolio_id, revision=current.portfolio.revision, context=operator()
    )
    assert {item.outcome for item in result.outcomes} == {"started"}
    assert all((item.message or "").startswith("HISTORY_TOO_SHORT: ") for item in result.outcomes)
    assert len(await state.tagged(current.portfolio.portfolio_id)) == len(records)


async def test_the_opt_in_safeguard_refuses_sleeves_on_young_markets() -> None:
    """Under require_market_history, planning refuses every short sleeve; nothing starts."""
    state = world()
    await _enforce(state, require_market_history=True)
    current, records = await portfolio(state)
    with pytest.raises(PortfolioStartRejectedError) as raised:
        await _young_runtime(state).start(
            current.portfolio.portfolio_id,
            revision=current.portfolio.revision,
            context=operator(),
        )
    problems = raised.value.problems
    assert {item.code for item in problems} == {"history_too_short"}
    assert {item.strategy_id for item in problems} == {item.strategy_id for item in records}
    assert problems[0].message.startswith("HISTORY_TOO_SHORT: ")
    assert await state.tagged(current.portfolio.portfolio_id) == ()


async def test_the_risk_policy_is_checked_for_every_sleeve_together() -> None:
    """The paper book admits the first sleeve alone but not both: nothing starts."""
    state = world()
    await state.publish_policy(paper_capital_quote="600")
    current, records = await portfolio(state)
    with pytest.raises(PortfolioStartRejectedError) as raised:
        await state.runtime.start(
            current.portfolio.portfolio_id,
            revision=current.portfolio.revision,
            context=operator(),
        )
    (problem,) = raised.value.problems
    assert problem.code == "risk_policy_refused"
    assert problem.strategy_id == records[1].strategy_id
    assert problem.message.startswith("PAPER_CAPITAL_EXCEEDED")
    assert await state.tagged(current.portfolio.portfolio_id) == ()


async def test_paper_fee_assumptions_apply_to_every_sleeve() -> None:
    """Paper fee rates are validated once and stored on every sleeve book."""
    state = world()
    current, _records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    fees = FeeAssumptions(maker_fee_rate=Decimal("0.0005"), taker_fee_rate=Decimal("0.001"))
    await state.runtime.start(
        pid, revision=current.portfolio.revision, context=operator(), fees=fees
    )
    assert {book.paper_taker_fee_rate for book in await state.tagged(pid)} == {Decimal("0.001")}


async def test_live_start_needs_the_acknowledgement_credentials_and_a_policy() -> None:
    """Live start fails closed without i_understand_live, credentials, or a published policy."""
    state = world()
    current, _records = await portfolio(state, mode="live")
    pid = current.portfolio.portfolio_id
    revision = current.portfolio.revision
    with pytest.raises(PortfolioLiveAcknowledgementError):
        await state.runtime.start(pid, revision=revision, context=operator())
    offline = world(live_allowed=False)
    current_offline, _ = await portfolio(offline, mode="live")
    with pytest.raises(PortfolioConflictError) as raised:
        await offline.runtime.start(
            current_offline.portfolio.portfolio_id,
            revision=current_offline.portfolio.revision,
            context=operator(),
            live_acknowledged=True,
        )
    assert raised.value.code == "live_credentials_missing"
    with pytest.raises(PortfolioStartRejectedError) as rejected:
        await state.runtime.start(
            pid, revision=revision, context=operator(), live_acknowledged=True
        )
    assert {item.message.split(":")[0] for item in rejected.value.problems} == {
        "LIVE_REQUIRES_PUBLISHED_POLICY"
    }


async def test_live_sleeves_allocate_weight_times_capital_as_membership() -> None:
    """With allocations in force for another strategy only, live sleeves still start."""
    state = world()
    current, records = await portfolio(state, mode="live")
    outsider = await portfolio(state, sleeves=(("SOL-USDC", "0.1"),))
    await state.publish_policy(allocations=((outsider[1][0].strategy_id, "100"),))
    pid = current.portfolio.portfolio_id
    result = await state.runtime.start(
        pid, revision=current.portfolio.revision, context=operator(), live_acknowledged=True
    )
    assert [item.outcome for item in result.outcomes] == ["started", "started"]
    books = await state.tagged(pid)
    assert [book.allocated_capital for book in books] == [Decimal("500"), Decimal("300")]
    assert {book.paper_starting_cash for book in books} == {None}
    assert {book.strategy_id for book in books} == {record.strategy_id for record in records}


async def test_pause_resume_and_stop_the_portfolio_or_one_sleeve() -> None:
    """Single-deployment semantics per sleeve: pause, resume, managed stop, flatten."""
    state = world()
    current, records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    paused = await state.runtime.pause(pid, context=operator())
    assert [item.outcome for item in paused.outcomes] == ["paused", "paused"]
    first_sleeve = current.sleeves[0].sleeve.sleeve_id
    resumed = await state.runtime.resume(pid, context=operator(), sleeve_id=first_sleeve)
    assert [item.outcome for item in resumed.outcomes] == ["resumed"]
    second_sleeve = current.sleeves[1].sleeve.sleeve_id
    await state.runtime.stop(pid, context=operator(), sleeve_id=second_sleeve, flatten=True)
    books = {book.strategy_id: book for book in await state.tagged(pid)}
    first, second = books[records[0].strategy_id], books[records[1].strategy_id]
    assert (first.status, first.lifecycle_command) == (
        DeploymentStatus.RUNNING,
        LifecycleCommand.NONE,
    )
    assert (second.status, second.lifecycle_command) == (
        DeploymentStatus.STOPPED,
        LifecycleCommand.FLATTEN,
    )
    stopped = await state.runtime.stop(pid, context=operator())
    assert [item.outcome for item in stopped.outcomes] == ["stopped", "unchanged"]
    with pytest.raises(PortfolioConflictError) as raised:
        await state.runtime.pause(pid, context=operator())
    assert raised.value.code == "portfolio_not_deployed"
    kinds = [
        entry.kind for entry in (await state.portfolios.journal(pid, limit=10, offset=0)).entries
    ]
    assert kinds[:5] == [
        "deployment_stopped",
        "deployment_stopped",
        "deployment_resumed",
        "deployment_paused",
        "deployment_started",
    ]


async def test_live_resume_needs_the_acknowledgement() -> None:
    """Resuming a live portfolio re-arms orders, so it needs i_understand_live."""
    state = world()
    await state.publish_policy()
    current, _records = await portfolio(state, mode="live")
    pid = current.portfolio.portfolio_id
    await state.runtime.start(
        pid, revision=current.portfolio.revision, context=operator(), live_acknowledged=True
    )
    await state.runtime.pause(pid, context=operator())
    with pytest.raises(PortfolioLiveAcknowledgementError):
        await state.runtime.resume(pid, context=operator())
    resumed = await state.runtime.resume(pid, context=operator(), live_acknowledged=True)
    assert [item.outcome for item in resumed.outcomes] == ["resumed", "resumed"]


async def test_a_latched_breaker_blocks_start_and_resume_until_reset() -> None:
    """Only an operator reset clears the latch; it re-baselines and leaves sleeves paused."""
    state = world()
    current, _records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    await state.runtime.pause(pid, context=operator())
    books = await state.tagged(pid)
    await state.mark_equity(books[0].id, pnl="-50")
    runtime = await state.portfolios.runtime_state(pid)
    latched = replace(
        runtime,
        breaker_reason="PORTFOLIO_DRAWDOWN_STOP",
        breaker_detail="Portfolio equity is 5% below its peak.",
        breaker_latched_at=runtime.run_started_at,
    )
    assert await state.portfolios.write_runtime(latched, expected_revision=runtime.revision)
    with pytest.raises(PortfolioConflictError) as raised:
        await state.runtime.resume(pid, context=operator())
    assert raised.value.code == "portfolio_breaker_latched"
    with pytest.raises(PortfolioConflictError):
        await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    cleared = await state.runtime.reset_breaker(pid, context=operator())
    assert cleared.breaker_latched is False
    assert cleared.high_water_mark_equity == cleared.day_open_equity == Decimal("950")
    assert {book.status for book in await state.tagged(pid)} == {DeploymentStatus.PAUSED}
    journal = await state.portfolios.journal(pid, limit=1, offset=0)
    assert journal.entries[0].kind == "breaker_reset"
    assert journal.entries[0].detail.reason_code == "PORTFOLIO_DRAWDOWN_STOP"
    with pytest.raises(PortfolioConflictError) as again:
        await state.runtime.reset_breaker(pid, context=operator())
    assert again.value.code == "portfolio_breaker_not_latched"


async def test_deployed_portfolios_and_sleeves_cannot_be_deleted() -> None:
    """Delete and remove-sleeve wait until the bots are stopped."""
    state = world()
    current, _records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    with pytest.raises(PortfolioConflictError) as deleted:
        await state.portfolios.delete(pid, expected_revision=current.portfolio.revision)
    assert deleted.value.code == "portfolio_deployed"
    with pytest.raises(PortfolioConflictError) as removed:
        await state.portfolios.remove_sleeve(
            pid,
            current.sleeves[0].sleeve.sleeve_id,
            expected_revision=current.portfolio.revision,
            context=operator(),
        )
    assert removed.value.code == "portfolio_sleeve_deployed"
    await state.runtime.stop(pid, context=operator())
    deletion = await state.portfolios.delete(pid, expected_revision=current.portfolio.revision)
    assert deletion.sleeves == 2


async def test_a_weight_change_moves_the_sleeve_target_capital() -> None:
    """The deployment view reports each sleeve's target at the new weight."""
    state = world()
    current, _records = await portfolio(state)
    pid = current.portfolio.portfolio_id
    await state.runtime.start(pid, revision=current.portfolio.revision, context=operator())
    weights = SetWeightsRequest.model_validate(
        {
            "revision": current.portfolio.revision,
            "weights": [
                {"sleeve_id": str(current.sleeves[0].sleeve.sleeve_id), "weight_fraction": "0.4"},
                {"sleeve_id": str(current.sleeves[1].sleeve.sleeve_id), "weight_fraction": "0.4"},
            ],
        }
    )
    await state.portfolios.set_weights(pid, weights, context=operator())
    snapshot = await state.runtime.snapshot(pid)
    assert [book.target_capital for book in snapshot.books.sleeves] == [
        Decimal("400"),
        Decimal("400"),
    ]
    assert snapshot.books.state == "running"
