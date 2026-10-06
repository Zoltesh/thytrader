"""Review regressions for currency provenance, midnight inventory, and retained evidence."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.risk.test_loss_scope import (
    _STRATEGY_A,
    _STRATEGY_B,
    _TODAY,
    _YESTERDAY,
    _deployment,
    _entry,
    _observation,
    _policy,
    _round_trip,
    _verdict,
)
from thytrader.execution.discretionary import _pause_on_breaker, parse_discretionary_request
from thytrader.execution.loop import _pause_for_breaker
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    InstrumentRuntime,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.risk.breakers import _daily_pnl
from thytrader.risk.daily_accounting import flat_day_fill_pnl
from thytrader.risk.gate import evaluate_new_deployment, evaluate_runtime_breakers
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict

_MIDNIGHT = datetime(2026, 10, 6, tzinfo=UTC)


def test_optional_quote_bounds_require_the_declared_policy_currency() -> None:
    """A new USDC monetary bound cannot be silently labeled USD for a USD entry."""
    for field in ("max_order_notional_quote", "min_available_quote_reserve"):
        policy = _policy(quote_currency="USDC", **{field: "100"})
        verdict = _verdict((), _entry(strategy_id=_STRATEGY_B), policy=policy)
        assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
        assert "policy=USDC" in verdict.detail


def test_daily_pause_scope_keeps_other_quotes_and_deliberate_pauses_unchanged() -> None:
    """A stopped source keeps its latch, without spreading pause to another quote bucket."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        source = _deployment(status=DeploymentStatus.STOPPED)
        sibling = _deployment(strategy_id=_STRATEGY_B)
        foreign = _deployment(product_id="ETH-USDC", strategy_id=_STRATEGY_B)
        deliberate = replace(_deployment(status=DeploymentStatus.PAUSED), mismatch_detail="person")
        for deployment in (source, sibling, foreign, deliberate):
            await store.create_deployment(deployment)
        snapshots = tuple(DeploymentSnapshot(item) for item in (sibling, foreign, deliberate))
        result = await _pause_for_breaker(
            DeploymentSnapshot(source),
            store=store,
            portfolio=snapshots,
            verdict=RiskVerdict(
                decision=RiskDecision.DENY,
                reason_code=RiskReasonCode.DAILY_LOSS_LIMIT,
                detail="test daily loss",
            ),
        )
        assert result.deployment.status is DeploymentStatus.STOPPED
        assert result.deployment.daily_loss_latched
        assert (await store.get_deployment(sibling.id)).deployment.status is DeploymentStatus.PAUSED
        assert (await store.get_deployment(foreign.id)).deployment == foreign
        assert (await store.get_deployment(deliberate.id)).deployment == deliberate

    asyncio.run(scenario())


def test_discretionary_daily_trip_latches_a_persisted_peer_not_the_unsaved_candidate() -> None:
    """Denied first admission keeps a durable latch after stop/restart even before book creation."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        stopped = _deployment(status=DeploymentStatus.STOPPED)
        foreign = _deployment(product_id="ETH-USDC", strategy_id=_STRATEGY_B)
        for deployment in (stopped, foreign):
            await store.create_deployment(deployment)
        candidate = DeploymentSnapshot(_deployment(strategy_id=None))
        request = parse_discretionary_request(
            mode="paper",
            product_id="BTC-USD",
            entry_kind="post_only_limit",
            stop_price="90",
            take_profit_price="120",
            origin="agent",
            idempotency_key="test-only-denial",
            quantity="1",
            limit_price="100",
            paper_starting_cash="10000",
        )
        await _pause_on_breaker(
            store=store,
            request=request,
            snapshot=candidate,
            deployments=(stopped, foreign),
            peers=(DeploymentSnapshot(stopped), DeploymentSnapshot(foreign)),
            verdict=RiskVerdict(
                decision=RiskDecision.DENY,
                reason_code=RiskReasonCode.DAILY_LOSS_LIMIT,
                detail="test daily loss",
            ),
        )
        recovered = await store.get_deployment(stopped.id)
        assert recovered.deployment.status is DeploymentStatus.STOPPED
        assert recovered.deployment.daily_loss_latched
        assert (await store.get_deployment(foreign.id)).deployment == foreign
        assert _verdict((recovered,), _entry(strategy_id=_STRATEGY_B)).reason_code is (
            RiskReasonCode.DAILY_LOSS_LIMIT
        )

    asyncio.run(scenario())


def test_stale_flat_overlay_cannot_hide_working_entry_exposure() -> None:
    """An active remainder still occupies exposure before runtime phase catches up."""
    book = _working_buy(venue_id="venue-confirmed", status=OrderStatus.OPEN)
    book = replace(book, instrument_runtimes=(InstrumentRuntime("BTC-USD", RuntimePhase.FLAT),))
    verdict = _verdict(
        (book,),
        _entry(strategy_id=_STRATEGY_B, notional=Decimal("21")),
        mode=DeploymentMode.LIVE,
        live_quote_cash=Decimal("100"),
        policy=_policy(daily_loss_limit_fraction="1", max_portfolio_exposure_fraction="0.5"),
    )
    assert verdict.reason_code is RiskReasonCode.PORTFOLIO_EXPOSURE_EXCEEDED
    assert "existing=60.0" in verdict.detail
    assert "capital=160.0" in verdict.detail


@pytest.mark.parametrize("foreign_product", ["BTC-USDC", "BTC-USDT"])
@pytest.mark.parametrize("status", [DeploymentStatus.RUNNING, DeploymentStatus.PAUSED])
def test_paper_admission_cannot_sum_two_quote_currencies(
    foreign_product: str, status: DeploymentStatus
) -> None:
    """One published paper envelope cannot be reused independently in every currency."""
    policy = _policy().model_copy(update={"product_allowlist": ("ETH-USD",)})
    verdict = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="ETH-USD",
        strategy_id=_STRATEGY_B,
        paper_starting_cash=Decimal("10000"),
        deployments=(_deployment(product_id=foreign_product, status=status),),
    )
    assert verdict.reason_code is RiskReasonCode.PAPER_CAPITAL_EXCEEDED
    assert "quote currencies" in verdict.detail


def test_stopped_flat_foreign_paper_evidence_does_not_consume_funding() -> None:
    """Retained flat loss evidence is not an occupied foreign funding commitment."""
    policy = _policy().model_copy(update={"product_allowlist": ("ETH-USD",)})
    verdict = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="ETH-USD",
        strategy_id=_STRATEGY_B,
        paper_starting_cash=Decimal("10000"),
        deployments=(_deployment(product_id="BTC-USDC", status=DeploymentStatus.STOPPED),),
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_same_quote_paper_funding_still_consumes_the_single_envelope() -> None:
    """Independent strategy identities do not each acquire the whole paper budget."""
    policy = _policy().model_copy(update={"product_allowlist": ("BTC-USD",)})
    verdict = evaluate_new_deployment(
        policy,
        mode=DeploymentMode.PAPER,
        product_id="BTC-USD",
        strategy_id=_STRATEGY_B,
        paper_starting_cash=Decimal("1"),
        deployments=(_deployment(product_id="BTC-USD"),),
    )
    assert verdict.reason_code is RiskReasonCode.PAPER_CAPITAL_EXCEEDED
    assert "paper_capital_quote" in verdict.detail


def _old_flat(*, mode: DeploymentMode = DeploymentMode.PAPER) -> DeploymentSnapshot:
    """Flat stopped book with stale baseline and today's round trip."""
    return _round_trip(
        _deployment(
            mode=mode,
            status=DeploymentStatus.STOPPED,
            created_at=_YESTERDAY,
            utc_day_open_at=_YESTERDAY,
            utc_day_open_equity=Decimal("10000"),
        ),
        buy_at=_TODAY - timedelta(hours=2),
        sell_at=_TODAY - timedelta(hours=1),
    )


def _foreign_inventory(product: str, cost: Decimal) -> DeploymentSnapshot:
    """Live sibling with known marks, zero trading PnL, and quote cost in its own currency."""
    deployment = _deployment(
        mode=DeploymentMode.LIVE,
        product_id=product,
        cash=-cost,
        paper_starting_cash=None,
        initial_equity=Decimal("0"),
        utc_day_open_equity=Decimal("0"),
        utc_day_open_at=_TODAY,
    )
    position = Position(
        deployment_id=deployment.id,
        product_id=product,
        quantity=cost / Decimal("100"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_TODAY,
        updated_at=_TODAY,
    )
    return DeploymentSnapshot(
        deployment=replace(deployment, phase=RuntimePhase.OPEN), position=position
    )


def test_interleaved_products_replay_exact_day_cash_and_fees() -> None:
    """Independent BTC/ETH lots, including a late ETH exit, cannot share an average price."""
    btc = _old_flat()
    eth = _round_trip(
        replace(btc.deployment, product_id="ETH-USD"),
        buy_at=_TODAY - timedelta(hours=1, minutes=30),
        sell_at=_TODAY - timedelta(minutes=10),
        sell_price=Decimal("150"),
    )
    # BTC loses 50, ETH gains 50. Four recorded fees leave an exact four-quote loss.
    merged = replace(
        btc,
        orders=(*btc.orders, *eth.orders),
        fills=tuple(replace(fill, fee=Decimal("1")) for fill in (*eth.fills, *btc.fills)),
    )
    assert flat_day_fill_pnl(merged, since=_MIDNIGHT) == Decimal("-4")
    assert _daily_pnl(merged, marks={}, as_of=_TODAY) == Decimal("-4")
    assert (
        _verdict(
            (merged,),
            _entry(strategy_id=_STRATEGY_B),
            policy=_policy(daily_loss_limit_fraction="0.0003"),
        ).reason_code
        is RiskReasonCode.DAILY_LOSS_LIMIT
    )


def test_overnight_closure_without_opening_mark_is_unknown_not_lifetime_pnl() -> None:
    """A previous day's mark movement must not be charged to today's equity change."""
    stopped = _old_flat()
    overnight = _round_trip(
        stopped.deployment,
        buy_at=_YESTERDAY,
        sell_at=_TODAY,
        sell_price=Decimal("200"),
    )
    assert flat_day_fill_pnl(overnight, since=_MIDNIGHT) is None
    denied = _verdict((overnight,), _entry(strategy_id=_STRATEGY_B))
    assert denied.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert "overnight inventory" in denied.detail


def test_overnight_trade_uses_recorded_day_equity_when_available() -> None:
    """An actual opening mark resolves the day even though a trade began yesterday."""
    overnight = _round_trip(
        _deployment(
            status=DeploymentStatus.STOPPED,
            created_at=_YESTERDAY,
            cash=Decimal("10100"),
            utc_day_open_at=_MIDNIGHT,
            utc_day_open_equity=Decimal("10200"),
        ),
        buy_at=_YESTERDAY,
        sell_at=_TODAY,
        sell_price=Decimal("200"),
    )
    # Lifetime gain is 100, but today's disclosed opening equity shows a 100 loss.
    assert _daily_pnl(overnight, marks={}, as_of=_TODAY) == Decimal("-100")


def test_distinct_midnight_assets_cannot_cancel_one_another() -> None:
    """A BTC long and an ETH short are not flat at midnight just because quantities sum zero."""
    btc = _round_trip(_old_flat().deployment, buy_at=_YESTERDAY, sell_at=_TODAY)
    eth = _round_trip(
        replace(btc.deployment, product_id="ETH-USD"), buy_at=_TODAY, sell_at=_YESTERDAY
    )
    both = replace(btc, orders=(*btc.orders, *eth.orders), fills=(*btc.fills, *eth.fills))
    assert flat_day_fill_pnl(both, since=_MIDNIGHT) is None


def test_unmatched_fill_and_false_flat_projection_fail_closed() -> None:
    """Orphan fill evidence and unprojected inventory cannot be counted as flat day PnL."""
    book = _old_flat()
    missing_order = replace(book, orders=(book.orders[0],))
    missing_exit = replace(book, fills=(book.fills[0],))
    for corrupt in (missing_order, missing_exit):
        assert flat_day_fill_pnl(corrupt, since=_MIDNIGHT) is None
        assert _verdict((corrupt,), _entry(strategy_id=_STRATEGY_B)).reason_code is (
            RiskReasonCode.BREAKER_MARK_MISSING
        )


def test_day_rollover_is_utc_even_for_non_utc_observation() -> None:
    """A fill exactly at midnight UTC belongs to the new day, not the local calendar day."""
    book = _round_trip(
        _old_flat().deployment,
        buy_at=_MIDNIGHT,
        sell_at=_MIDNIGHT + timedelta(minutes=1),
    )
    local = (_MIDNIGHT + timedelta(minutes=2)).astimezone(timezone(timedelta(hours=-7)))
    assert local.day == 5
    assert _daily_pnl(book, marks={}, as_of=local) == Decimal("-50")
    tomorrow = _TODAY + timedelta(days=1)
    assert _daily_pnl(book, marks={}, as_of=tomorrow) == Decimal("0")


def test_same_day_live_late_fill_blocks_until_economics_are_applied() -> None:
    """A fill on a stopped row cannot disappear behind same-day unchanged cash."""
    book = _old_flat(mode=DeploymentMode.LIVE)
    book = replace(
        book,
        deployment=replace(
            book.deployment,
            utc_day_open_at=_MIDNIGHT,
            utc_day_open_equity=Decimal("10000"),
        ),
        fills=tuple(replace(fill, economics_applied_at=None) for fill in book.fills),
    )
    assert (
        _verdict(
            (book,),
            _entry(strategy_id=_STRATEGY_B),
            mode=DeploymentMode.LIVE,
            live_quote_cash=Decimal("10000"),
        ).reason_code
        is RiskReasonCode.BREAKER_MARK_MISSING
    )
    applied = replace(
        book,
        deployment=replace(book.deployment, cash=Decimal("9950")),
        fills=tuple(replace(fill, economics_applied_at=_TODAY) for fill in book.fills),
    )
    assert (
        _verdict(
            (applied,),
            _entry(strategy_id=_STRATEGY_B),
            mode=DeploymentMode.LIVE,
            live_quote_cash=Decimal("10000"),
        ).reason_code
        is RiskReasonCode.DAILY_LOSS_LIMIT
    )


@pytest.mark.parametrize("quote", ["USDC", "USDT"])
def test_foreign_quote_inventory_cannot_enlarge_usd_loss_budget(quote: str) -> None:
    """Both admission and runtime loss denominator must omit foreign quote holdings."""
    loss = _deployment(
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.STOPPED,
        cash=Decimal("-5"),
        paper_starting_cash=None,
        initial_equity=Decimal("0"),
    )
    foreign = _foreign_inventory(f"ETH-{quote}", Decimal("10000"))
    books = (DeploymentSnapshot(deployment=loss), foreign)
    policy = _policy(daily_loss_limit_fraction="0.01")
    entry = _entry(strategy_id=_STRATEGY_B, notional=Decimal("1"))
    assert (
        _verdict(
            books,
            entry,
            mode=DeploymentMode.LIVE,
            policy=policy,
            live_quote_cash=Decimal("100"),
        ).reason_code
        is RiskReasonCode.DAILY_LOSS_LIMIT
    )
    current = DeploymentSnapshot(
        deployment=_deployment(
            mode=DeploymentMode.LIVE,
            strategy_id=_STRATEGY_B,
            cash=Decimal("0"),
            paper_starting_cash=None,
            initial_equity=Decimal("0"),
            performance_capital_quote=Decimal("100"),
        )
    )
    assert (
        evaluate_runtime_breakers(
            policy,
            mode=DeploymentMode.LIVE,
            snapshot=current,
            snapshots=(*books, current),
            live_quote_cash=Decimal("100"),
            observation=_observation(),
        ).reason_code
        is RiskReasonCode.DAILY_LOSS_LIMIT
    )


def test_same_quote_inventory_still_contributes_to_capital() -> None:
    """Quote partitioning keeps ADR 0106 cost capital when the currencies actually agree."""
    loss = _deployment(
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.STOPPED,
        cash=Decimal("-5"),
        paper_starting_cash=None,
        initial_equity=Decimal("0"),
    )
    held = _foreign_inventory("ETH-USD", Decimal("10000"))
    verdict = _verdict(
        (DeploymentSnapshot(deployment=loss), held),
        _entry(strategy_id=_STRATEGY_B, notional=Decimal("1")),
        mode=DeploymentMode.LIVE,
        policy=_policy(daily_loss_limit_fraction="0.01"),
        live_quote_cash=Decimal("100"),
        marks={"BTC-USD": Decimal("100"), "ETH-USD": Decimal("100")},
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_deleted_strategy_drawdown_never_becomes_discretionary_drawdown() -> None:
    """A null strategy FK after deletion is not evidence the book was discretionary."""
    deleted = replace(
        _deployment(drawdown_latched=True, status=DeploymentStatus.STOPPED),
        strategy_id=None,
        kind=DeploymentKind.STRATEGY,
    )
    assert deleted.strategy_deleted
    assert (
        _verdict(
            (DeploymentSnapshot(deployment=deleted),),
            _entry(strategy_id=None),
            policy=_policy(daily_loss_limit_fraction="1"),
        ).decision
        is RiskDecision.ALLOW
    )
    # Account daily loss still follows the detached book's quote.
    deleted = replace(deleted, daily_loss_latched=True)
    assert (
        _verdict((DeploymentSnapshot(deployment=deleted),), _entry(strategy_id=None)).reason_code
        is RiskReasonCode.DAILY_LOSS_LIMIT
    )


def _working_buy(*, venue_id: str | None, status: OrderStatus) -> DeploymentSnapshot:
    """A one-quote-currency book with a working buy remainder."""
    deployment = _deployment(mode=DeploymentMode.LIVE)
    order = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="reserve-buy",
        product_id="BTC-USD",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        filled_quantity=Decimal("0.4"),
        status=status,
        venue_order_id=venue_id,
        created_at=_TODAY,
        updated_at=_TODAY,
    )
    return DeploymentSnapshot(deployment=deployment, orders=(order,))


def test_live_reserve_accounts_for_local_holds_without_double_counting_venue_holds() -> None:
    """Available quote already excludes confirmed venue holds; unsent local buys do not."""
    policy = _policy(daily_loss_limit_fraction="1", min_available_quote_reserve="50")
    proposed = _entry(strategy_id=_STRATEGY_A, notional=Decimal("10"))
    venue = _working_buy(venue_id="venue-confirmed", status=OrderStatus.OPEN)
    local = _working_buy(venue_id=None, status=OrderStatus.OPEN)
    assert (
        _verdict(
            (venue,),
            proposed,
            mode=DeploymentMode.LIVE,
            policy=policy,
            live_quote_cash=Decimal("100"),
        ).decision
        is RiskDecision.ALLOW
    )
    assert (
        _verdict(
            (local,),
            proposed,
            mode=DeploymentMode.LIVE,
            policy=policy,
            live_quote_cash=Decimal("100"),
        ).reason_code
        is RiskReasonCode.BALANCE_RESERVE
    )
    for status in (OrderStatus.PENDING, OrderStatus.UNKNOWN):
        uncertain = _working_buy(venue_id="maybe-held", status=status)
        assert (
            _verdict(
                (uncertain,),
                proposed,
                mode=DeploymentMode.LIVE,
                policy=policy,
                live_quote_cash=Decimal("100"),
            ).reason_code
            is RiskReasonCode.BALANCE_RESERVE
        )


def test_paper_reserve_includes_recorded_cash_losses_and_modeled_entry_fees() -> None:
    """Notional-only exposure cannot hide previous fees/loss or the next paper fee."""
    book = DeploymentSnapshot(deployment=_deployment(cash=Decimal("9900")))
    policy = _policy(daily_loss_limit_fraction="1", min_available_quote_reserve="9800")
    denied = _verdict(
        (book,), _entry(strategy_id=_STRATEGY_B, notional=Decimal("100")), policy=policy
    )
    assert denied.reason_code is RiskReasonCode.BALANCE_RESERVE
    allowed = _verdict(
        (book,), _entry(strategy_id=_STRATEGY_B, notional=Decimal("99")), policy=policy
    )
    assert allowed.decision is RiskDecision.ALLOW


def test_live_optional_reserve_ignores_foreign_quote_local_reservations() -> None:
    """A pending USDC reservation cannot consume USD admission headroom."""
    foreign = _working_buy(venue_id=None, status=OrderStatus.UNKNOWN)
    foreign = replace(
        foreign,
        deployment=replace(foreign.deployment, product_id="BTC-USDC"),
        orders=tuple(replace(order, product_id="BTC-USDC") for order in foreign.orders),
    )
    assert (
        _verdict(
            (foreign,),
            _entry(strategy_id=_STRATEGY_B, notional=Decimal("10")),
            mode=DeploymentMode.LIVE,
            live_quote_cash=Decimal("100"),
            policy=_policy(daily_loss_limit_fraction="1", min_available_quote_reserve="50"),
        ).decision
        is RiskDecision.ALLOW
    )
