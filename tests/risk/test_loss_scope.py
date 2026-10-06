"""Account daily-loss scope, drawdown isolation, and optional entry bounds."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.execution.service import reset_breaker_latches
from thytrader.execution_worker.service import _risk_snapshots
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.exposure import daily_loss_snapshots, risk_bearing_snapshots
from thytrader.risk.gate import ProposedEntry, evaluate_new_entry
from thytrader.risk.models import (
    RiskDecision,
    RiskPolicyDefinition,
    RiskReasonCode,
    RiskVerdict,
    canonical_risk_policy_bytes,
    compiled_default_risk_policy,
    risk_policy_fingerprint,
)

_STRATEGY_A = UUID("01978a3e-5f2c-7d10-b3a4-0000000000a1")
_STRATEGY_B = UUID("01978a3e-5f2c-7d10-b3a4-0000000000b2")
_TODAY = datetime(2026, 10, 6, 15, tzinfo=UTC)
_YESTERDAY = datetime(2026, 10, 5, 15, tzinfo=UTC)


def _policy(**updates: str | int) -> RiskPolicyDefinition:
    """Tight daily-loss policy with the compiled drawdown envelope unless overridden."""
    values: dict[str, str | int] = {
        "daily_loss_limit_fraction": "0.001",
        "paper_capital_quote": "10000",
        "quote_currency": "USD",
    }
    values.update(updates)
    return compiled_default_risk_policy().model_copy(update=values)


def _observation(
    *,
    as_of: datetime = _TODAY,
    product_id: str = "BTC-USD",
    price: Decimal = Decimal("100"),
    marks: dict[str, Decimal] | None = None,
) -> EntryObservation:
    """Collar-safe observation on one UTC day."""
    return EntryObservation(
        as_of=as_of,
        proposed_price=price,
        reference_price=price,
        marks=marks if marks is not None else {product_id: price},
    )


def _deployment(
    *,
    strategy_id: UUID | None = _STRATEGY_A,
    product_id: str = "BTC-USD",
    mode: DeploymentMode = DeploymentMode.PAPER,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    created_at: datetime = _TODAY,
    cash: Decimal = Decimal("10000"),
    paper_starting_cash: Decimal | None = Decimal("10000"),
    initial_equity: Decimal | None = None,
    utc_day_open_equity: Decimal | None = None,
    utc_day_open_at: datetime | None = None,
    daily_loss_latched: bool = False,
    drawdown_latched: bool = False,
    performance_capital_quote: Decimal | None = None,
) -> Deployment:
    """One book with explicit baselines so tests do not depend on worker rolls."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=strategy_id,
        kind=DeploymentKind.STRATEGY if strategy_id is not None else DeploymentKind.DISCRETIONARY,
        product_id=product_id,
        mode=mode,
        status=status,
        cash=cash,
        phase=RuntimePhase.FLAT,
        created_at=created_at,
        updated_at=created_at,
        paper_starting_cash=paper_starting_cash,
        initial_equity=initial_equity,
        utc_day_open_equity=utc_day_open_equity,
        utc_day_open_at=utc_day_open_at,
        daily_loss_latched=daily_loss_latched,
        drawdown_latched=drawdown_latched,
        performance_capital_quote=performance_capital_quote,
    )


def _round_trip(
    deployment: Deployment,
    *,
    buy_at: datetime,
    sell_at: datetime,
    sell_price: Decimal = Decimal("50"),
) -> DeploymentSnapshot:
    """Flat book with a buy then a sell. Cash is whatever the deployment already records."""
    buy = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="buy",
        product_id=deployment.product_id,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=buy_at,
        updated_at=buy_at,
        price=Decimal("100"),
        filled_quantity=Decimal("1"),
    )
    sell = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="sell",
        product_id=deployment.product_id,
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=sell_at,
        updated_at=sell_at,
        price=sell_price,
        filled_quantity=Decimal("1"),
    )
    fills = (
        Fill(
            id=uuid4(),
            deployment_id=deployment.id,
            order_id=buy.id,
            venue_fill_id="buy-fill",
            price=Decimal("100"),
            quantity=Decimal("1"),
            fee=Decimal("0"),
            filled_at=buy_at,
            economics_applied_at=buy_at,
        ),
        Fill(
            id=uuid4(),
            deployment_id=deployment.id,
            order_id=sell.id,
            venue_fill_id="sell-fill",
            price=sell_price,
            quantity=Decimal("1"),
            fee=Decimal("0"),
            filled_at=sell_at,
            economics_applied_at=sell_at,
        ),
    )
    return DeploymentSnapshot(deployment=deployment, orders=(buy, sell), fills=fills, position=None)


def _entry(
    *,
    strategy_id: UUID | None,
    product_id: str = "BTC-USD",
    notional: Decimal = Decimal("100"),
    quantity: Decimal | None = None,
) -> ProposedEntry:
    """Sized entry for one strategy or a discretionary book."""
    return ProposedEntry(
        product_id=product_id,
        strategy_id=strategy_id,
        notional=notional,
        quantity=quantity,
    )


def _verdict(
    snapshots: tuple[DeploymentSnapshot, ...],
    proposed: ProposedEntry,
    *,
    mode: DeploymentMode = DeploymentMode.PAPER,
    as_of: datetime = _TODAY,
    policy: RiskPolicyDefinition | None = None,
    live_quote_cash: Decimal | None = None,
    marks: dict[str, Decimal] | None = None,
) -> RiskVerdict:
    """Evaluate one entry against the supplied books."""
    return evaluate_new_entry(
        _policy() if policy is None else policy,
        mode=mode,
        proposed=proposed,
        snapshots=snapshots,
        live_quote_cash=live_quote_cash,
        observation=_observation(as_of=as_of, product_id=proposed.product_id, marks=marks),
    )


def test_unrelated_strategy_drawdown_does_not_block_another_book() -> None:
    """A drawdown latch or breach on strategy A must not deny strategy B."""
    latched = replace(
        _deployment(strategy_id=_STRATEGY_A, product_id="BTC-USD", drawdown_latched=True),
        high_water_mark_equity=Decimal("10000"),
        performance_capital_quote=Decimal("10000"),
        initial_equity=Decimal("10000"),
        cash=Decimal("9000"),
    )
    losing = _round_trip(
        replace(latched, id=uuid4(), drawdown_latched=False),
        buy_at=_TODAY - timedelta(hours=2),
        sell_at=_TODAY - timedelta(hours=1),
    )
    policy = _policy(max_strategy_drawdown_fraction="0.01", daily_loss_limit_fraction="1")
    for snapshots in (
        (DeploymentSnapshot(deployment=latched),),
        (losing,),
    ):
        verdict = _verdict(
            snapshots,
            _entry(strategy_id=_STRATEGY_B, product_id="BTC-USD"),
            policy=policy,
        )
        assert verdict.decision is RiskDecision.ALLOW


def test_matching_strategy_drawdown_blocks_another_product_and_a_stopped_book() -> None:
    """Drawdown follows the strategy, including a stopped book, not the whole mode."""
    stopped = _deployment(
        strategy_id=_STRATEGY_A,
        product_id="BTC-USD",
        status=DeploymentStatus.STOPPED,
        drawdown_latched=True,
        performance_capital_quote=Decimal("10000"),
    )
    verdict = _verdict(
        (DeploymentSnapshot(deployment=stopped),),
        _entry(strategy_id=_STRATEGY_A, product_id="ETH-USD"),
        policy=_policy(daily_loss_limit_fraction="1"),
    )
    assert verdict.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT
    assert "this strategy" in verdict.detail


def test_discretionary_drawdown_does_not_inherit_a_strategy_book() -> None:
    """A strategy latch on the same product does not block a discretionary entry."""
    latched = _deployment(strategy_id=_STRATEGY_A, drawdown_latched=True)
    verdict = _verdict(
        (DeploymentSnapshot(deployment=latched),),
        _entry(strategy_id=None, product_id="BTC-USD"),
        policy=_policy(daily_loss_limit_fraction="1"),
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_discretionary_drawdown_latch_matches_only_that_product() -> None:
    """Discretionary drawdown stays on the same product, not every discretionary book."""
    btc = _deployment(strategy_id=None, product_id="BTC-USD", drawdown_latched=True)
    eth = _verdict(
        (DeploymentSnapshot(deployment=btc),),
        _entry(strategy_id=None, product_id="ETH-USD"),
        policy=_policy(daily_loss_limit_fraction="1"),
    )
    same = _verdict(
        (DeploymentSnapshot(deployment=btc),),
        _entry(strategy_id=None, product_id="BTC-USD"),
        policy=_policy(daily_loss_limit_fraction="1"),
    )
    assert eth.decision is RiskDecision.ALLOW
    assert same.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT


def test_stopped_flat_loss_is_retained_for_a_replacement_book() -> None:
    """Stopping a flat book must not drop its same-day realized loss."""
    stopped = _deployment(
        status=DeploymentStatus.STOPPED,
        created_at=_TODAY,
        cash=Decimal("9000"),
        initial_equity=Decimal("10000"),
    )
    snapshot = _round_trip(
        stopped, buy_at=_TODAY - timedelta(hours=1), sell_at=_TODAY - timedelta(minutes=5)
    )
    assert risk_bearing_snapshots((snapshot,), DeploymentMode.PAPER) == ()
    assert daily_loss_snapshots((snapshot,), DeploymentMode.PAPER) == (snapshot,)
    verdict = _verdict((snapshot,), _entry(strategy_id=_STRATEGY_B, product_id="ETH-USD"))
    assert verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
    assert "latched" not in verdict.detail


def test_stopped_flat_orders_do_not_consume_the_entry_rate_cap() -> None:
    """Occupancy for rate limits stays separate from daily-loss evidence."""
    stopped = _deployment(status=DeploymentStatus.STOPPED)
    order = Order(
        id=uuid4(),
        deployment_id=stopped.id,
        intent_id=uuid4(),
        client_order_id="old-entry",
        product_id="BTC-USD",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.CANCELED,
        filled_quantity=Decimal("0"),
        created_at=_TODAY - timedelta(seconds=5),
        updated_at=_TODAY - timedelta(seconds=5),
        price=Decimal("100"),
    )
    snapshot = DeploymentSnapshot(deployment=stopped, orders=(order,))
    policy = _policy(max_entry_orders_per_minute=1, daily_loss_limit_fraction="1")
    verdict = _verdict((snapshot,), _entry(strategy_id=_STRATEGY_B), policy=policy)
    assert verdict.decision is RiskDecision.ALLOW


def test_daily_latch_survives_stop_and_is_not_an_implicit_reset() -> None:
    """A stopped flat book's daily-loss latch still denies until explicit reset."""
    stopped = _deployment(
        status=DeploymentStatus.STOPPED,
        daily_loss_latched=True,
        cash=Decimal("10000"),
        utc_day_open_equity=Decimal("10000"),
        utc_day_open_at=_TODAY,
    )
    verdict = _verdict(
        (DeploymentSnapshot(deployment=stopped),),
        _entry(strategy_id=_STRATEGY_B, product_id="ETH-USD"),
        policy=_policy(daily_loss_limit_fraction="1"),
    )
    assert verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
    assert "explicit operator reset" in verdict.detail
    assert stopped.daily_loss_latched is True


def test_late_fill_on_a_stopped_flat_book_blocks_when_cash_is_stale() -> None:
    """Contradictory applied fills and stale cash deny instead of establishing a false baseline."""
    stopped = _deployment(
        status=DeploymentStatus.STOPPED,
        created_at=_YESTERDAY,
        cash=Decimal("10000"),
        paper_starting_cash=Decimal("10000"),
        utc_day_open_equity=Decimal("10000"),
        utc_day_open_at=_YESTERDAY,
    )
    snapshot = _round_trip(
        stopped,
        buy_at=_TODAY - timedelta(hours=1),
        sell_at=_TODAY - timedelta(minutes=1),
        sell_price=Decimal("50"),
    )
    verdict = _verdict((snapshot,), _entry(strategy_id=_STRATEGY_B))
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING


def test_utc_day_rollover_does_not_carry_yesterday_loss() -> None:
    """Complete flat history proves today's zero movement without trusting legacy stamps."""
    stopped = _deployment(
        status=DeploymentStatus.STOPPED,
        created_at=_YESTERDAY,
        cash=Decimal("9950"),
        paper_starting_cash=Decimal("10000"),
        utc_day_open_equity=Decimal("10000"),
        utc_day_open_at=_YESTERDAY,
    )
    yesterday = _round_trip(
        stopped,
        buy_at=_YESTERDAY - timedelta(hours=2),
        sell_at=_YESTERDAY - timedelta(hours=1),
    )
    rolled = _verdict((yesterday,), _entry(strategy_id=_STRATEGY_B), as_of=_TODAY)
    assert rolled.decision is RiskDecision.ALLOW
    same_day = replace(
        stopped,
        utc_day_open_at=_TODAY.replace(hour=0),
        utc_day_open_equity=Decimal("10000"),
        created_at=_YESTERDAY,
    )
    still_today = _verdict(
        (replace(yesterday, deployment=same_day),),
        _entry(strategy_id=_STRATEGY_B),
        as_of=_TODAY,
    )
    assert still_today.decision is RiskDecision.ALLOW


def test_open_book_without_a_same_day_baseline_fails_closed() -> None:
    """Do not treat yesterday's open equity as today's baseline for open inventory."""
    deployment = replace(
        _deployment(
            created_at=_YESTERDAY,
            utc_day_open_equity=Decimal("10000"),
            utc_day_open_at=_YESTERDAY,
            cash=Decimal("9000"),
        ),
        phase=RuntimePhase.OPEN,
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        position=Position(
            deployment_id=deployment.id,
            product_id="BTC-USD",
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_YESTERDAY,
            updated_at=_YESTERDAY,
        ),
    )
    verdict = _verdict(
        (snapshot,),
        _entry(strategy_id=_STRATEGY_B),
        marks={"BTC-USD": Decimal("100")},
    )
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert "same-UTC-day" in verdict.detail


def test_legacy_same_day_book_uses_opening_equity_without_a_day_open() -> None:
    """Books created today with no UTC day-open keep the opening-equity fallback."""
    deployment = _deployment(created_at=_TODAY, cash=Decimal("9000"), initial_equity=None)
    verdict = _verdict(
        (DeploymentSnapshot(deployment=deployment),),
        _entry(strategy_id=_STRATEGY_B),
    )
    assert verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT


def test_same_quote_losses_sum_and_different_quotes_do_not() -> None:
    """USD and USDC losses are separate buckets; neither is converted into the other."""
    usd = _deployment(
        strategy_id=_STRATEGY_A,
        product_id="BTC-USD",
        status=DeploymentStatus.STOPPED,
        created_at=_TODAY,
        cash=Decimal("9000"),
        initial_equity=Decimal("10000"),
    )
    usdc_entry = _verdict(
        (DeploymentSnapshot(deployment=usd),),
        _entry(strategy_id=_STRATEGY_B, product_id="ETH-USDC"),
    )
    usd_entry = _verdict(
        (DeploymentSnapshot(deployment=usd),),
        _entry(strategy_id=_STRATEGY_B, product_id="ETH-USD"),
    )
    assert usdc_entry.decision is RiskDecision.ALLOW
    assert usd_entry.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT


def test_mixed_quote_book_fails_closed_instead_of_summing() -> None:
    """A book that traded two quote currencies cannot contribute a single loss number."""
    deployment = _deployment(product_id="BTC-USD", created_at=_TODAY, cash=Decimal("10000"))
    other = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="usdc",
        product_id="ETH-USDC",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.CANCELED,
        created_at=_TODAY,
        updated_at=_TODAY,
        price=Decimal("10"),
    )
    snapshot = DeploymentSnapshot(deployment=deployment, orders=(other,))
    verdict = _verdict((snapshot,), _entry(strategy_id=_STRATEGY_B, product_id="BTC-USD"))
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert "mixed quote" in verdict.detail


def test_live_and_paper_losses_do_not_cross_modes() -> None:
    """A stopped live loss does not deny a paper entry, and the reverse is also true."""
    live = _deployment(
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.STOPPED,
        created_at=_TODAY,
        cash=Decimal("0"),
        paper_starting_cash=None,
        initial_equity=Decimal("0"),
    )
    live = replace(live, cash=Decimal("-500"))
    paper = _verdict(
        (DeploymentSnapshot(deployment=live),),
        _entry(strategy_id=_STRATEGY_B),
        mode=DeploymentMode.PAPER,
    )
    assert paper.decision is RiskDecision.ALLOW


def test_absent_deleted_book_is_not_invented() -> None:
    """Already-absent legacy evidence cannot be invented; new deletion retains the rows."""
    verdict = _verdict((), _entry(strategy_id=_STRATEGY_B))
    assert verdict.decision is RiskDecision.ALLOW


def test_reset_clears_the_latch_without_erasing_same_day_loss() -> None:
    """Explicit reset is auditable and is not a stop. Continuing loss can trip again."""

    async def _scenario() -> None:
        store = InMemoryExecutionStore()
        deployment = replace(
            _deployment(
                status=DeploymentStatus.STOPPED,
                daily_loss_latched=True,
                created_at=_TODAY,
                cash=Decimal("9000"),
                initial_equity=Decimal("10000"),
                performance_capital_quote=Decimal("10000"),
            ),
            high_water_mark_equity=Decimal("10000"),
        )
        await store.create_deployment(deployment)
        before = _verdict(
            (DeploymentSnapshot(deployment=deployment),),
            _entry(strategy_id=_STRATEGY_B),
            policy=_policy(daily_loss_limit_fraction="1"),
        )
        assert before.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
        assert "explicit operator reset" in before.detail
        reset = await reset_breaker_latches(store=store, deployment_id=deployment.id)
        assert reset.deployment.daily_loss_latched is False
        assert reset.deployment.status is DeploymentStatus.STOPPED
        assert reset.deployment.performance_capital_quote == Decimal("10000")
        assert reset.deployment.high_water_mark_equity == Decimal("10000")
        after = _verdict(
            (DeploymentSnapshot(deployment=reset.deployment),),
            _entry(strategy_id=_STRATEGY_B),
        )
        assert after.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
        assert "reached the risk-policy limit" in after.detail

    asyncio.run(_scenario())


def test_reset_allows_a_new_day_after_the_latch_clears() -> None:
    """After reset, complete applied history proves yesterday's loss is not today's loss."""
    deployment = _deployment(
        status=DeploymentStatus.STOPPED,
        daily_loss_latched=False,
        created_at=_YESTERDAY,
        cash=Decimal("9950"),
        paper_starting_cash=Decimal("10000"),
        utc_day_open_equity=Decimal("10000"),
        utc_day_open_at=_YESTERDAY,
    )
    history = _round_trip(
        deployment,
        buy_at=_YESTERDAY - timedelta(hours=1),
        sell_at=_YESTERDAY,
    )
    verdict = _verdict((history,), _entry(strategy_id=_STRATEGY_B))
    assert verdict.decision is RiskDecision.ALLOW


def test_optional_entry_bounds_do_not_change_compiled_bytes_or_unset_entries() -> None:
    """Unset quantity, notional, and reserve fields stay out of the canonical policy."""
    default = compiled_default_risk_policy()
    explicit = default.model_copy(
        update={
            "max_order_quantity": None,
            "max_order_notional_quote": None,
            "min_available_quote_reserve": None,
        }
    )
    assert risk_policy_fingerprint(default) == risk_policy_fingerprint(explicit)
    assert b"max_order_quantity" not in canonical_risk_policy_bytes(default)
    verdict = _verdict(
        (),
        _entry(strategy_id=_STRATEGY_A, quantity=Decimal("5"), notional=Decimal("500")),
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_optional_entry_bounds_deny_only_when_set() -> None:
    """Published optional caps bind the entry without changing exposure fractions."""
    quantity = _policy(
        daily_loss_limit_fraction="1",
        max_order_quantity="0.5",
    )
    denied_qty = _verdict(
        (),
        _entry(strategy_id=_STRATEGY_A, quantity=Decimal("1")),
        policy=quantity,
    )
    missing_qty = _verdict((), _entry(strategy_id=_STRATEGY_A), policy=quantity)
    notional = _policy(daily_loss_limit_fraction="1", max_order_notional_quote="50")
    denied_notional = _verdict(
        (),
        _entry(strategy_id=_STRATEGY_A, notional=Decimal("80")),
        policy=notional,
    )
    reserve = _policy(daily_loss_limit_fraction="1", min_available_quote_reserve="9900")
    denied_reserve = _verdict(
        (),
        _entry(strategy_id=_STRATEGY_A, notional=Decimal("200")),
        policy=reserve,
    )
    live_reserve = _policy(daily_loss_limit_fraction="1", min_available_quote_reserve="50")
    denied_live = _verdict(
        (),
        _entry(strategy_id=_STRATEGY_A, notional=Decimal("80")),
        mode=DeploymentMode.LIVE,
        live_quote_cash=Decimal("100"),
        policy=live_reserve,
    )
    assert denied_qty.reason_code is RiskReasonCode.MAX_ORDER_QUANTITY
    assert missing_qty.reason_code is RiskReasonCode.MAX_ORDER_QUANTITY
    assert denied_notional.reason_code is RiskReasonCode.MAX_ORDER_NOTIONAL
    assert denied_reserve.reason_code is RiskReasonCode.BALANCE_RESERVE
    assert denied_live.reason_code is RiskReasonCode.BALANCE_RESERVE


def test_worker_risk_snapshots_keep_stopped_flat_books() -> None:
    """The execution worker must hand stopped flat rows to the gate."""

    async def _scenario() -> None:
        store = InMemoryExecutionStore()
        stopped = _deployment(status=DeploymentStatus.STOPPED, mode=DeploymentMode.LIVE)
        running = _deployment(status=DeploymentStatus.RUNNING, mode=DeploymentMode.LIVE)
        await store.create_deployment(stopped)
        await store.create_deployment(running)
        loaded = await _risk_snapshots(store, (stopped, running))
        ids = {item.deployment.id for item in loaded}
        assert stopped.id in ids
        assert running.id in ids

    asyncio.run(_scenario())
