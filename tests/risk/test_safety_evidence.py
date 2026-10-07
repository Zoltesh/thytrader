"""Actual scoped-store regressions for complete economics and qualified UTC openings."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.loop_patching import patch_loop_global
from tests.risk.test_loss_scope import (
    _STRATEGY_A,
    _TODAY,
    _YESTERDAY,
    _deployment,
    _entry,
    _observation,
    _policy,
    _round_trip,
    _verdict,
)
from thytrader.execution.capital import refresh_performance
from thytrader.execution.day_open import DailyOpeningEvidence, MidnightMark
from thytrader.execution.leases import RevisionFencedStore
from thytrader.execution.loop import (
    _apply_circuit_breakers,
    _entry_verdict,
    _persist_performance,
    process_closed_bar,
)
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentSnapshot,
    ExecutionStoreError,
    InstrumentRuntime,
    Position,
    RuntimePhase,
)
from thytrader.execution.overlay import InstrumentScopedStore, overlay_snapshot
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.models import (
    Candle,
    CandleQualityReport,
    CandleRangeReport,
    MarketProduct,
)
from thytrader.market_data.service import MarketDataService
from thytrader.risk.accounting_evidence import accounting_snapshot, risk_market_data_scope
from thytrader.risk.breakers import _daily_pnl
from thytrader.risk.models import RiskDecision, RiskReasonCode
from thytrader.risk.opening_accounting import reconstruct_day_open
from thytrader.strategies.authoring import create_template_strategy

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.market_data.models import CandleInterval, MarketDataPreview

_MIDNIGHT = _TODAY.replace(hour=0)


async def seed_accounting(store: InMemoryExecutionStore, snapshot: DeploymentSnapshot) -> None:
    """Persist full explicit test economics without inventing projection side effects."""
    await store.create_deployment(snapshot.deployment)
    for order in snapshot.orders:
        await store.save_order(order)
    for fill in snapshot.fills:
        await store.save_fill(fill)
    for position in snapshot.positions or (
        () if snapshot.position is None else (snapshot.position,)
    ):
        await store.save_position(position, deployment_id=snapshot.deployment.id)
    for runtime in snapshot.instrument_runtimes:
        await store.save_instrument_runtime(runtime, deployment_id=snapshot.deployment.id)


def sibling_loss(*, live: bool = False, applied: bool = True) -> DeploymentSnapshot:
    """An older shared book with ETH loss and no BTC fills; cash is applied exactly once."""
    initial = Decimal("0") if live else Decimal("10000")
    root = _deployment(
        created_at=_YESTERDAY,
        mode=DeploymentMode.LIVE if live else DeploymentMode.PAPER,
        paper_starting_cash=None if live else initial,
        initial_equity=initial,
        cash=initial - Decimal("50") if applied else initial,
        performance_capital_quote=Decimal("10000"),
        utc_day_open_at=_MIDNIGHT,
        utc_day_open_equity=initial,
    )
    root = replace(root, venue_available_quote=Decimal("10000") if live else None)
    eth = _round_trip(
        replace(root, product_id="ETH-USD"),
        buy_at=_TODAY - timedelta(hours=2),
        sell_at=_TODAY - timedelta(hours=1),
    )
    return replace(
        eth,
        deployment=root,
        fills=eth.fills
        if applied
        else tuple(replace(f, economics_applied_at=None) for f in eth.fills),
        instrument_runtimes=(
            InstrumentRuntime("BTC-USD", RuntimePhase.FLAT),
            InstrumentRuntime("ETH-USD", RuntimePhase.FLAT),
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("live,applied", [(False, True), (True, True), (True, False)])
async def test_actual_scoped_gate_reads_sibling_economics(
    monkeypatch: pytest.MonkeyPatch, live: bool, applied: bool
) -> None:
    """Cached full peers plus a focused current view cannot hide losses or unapplied fills."""
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    full = sibling_loss(live=live, applied=applied)
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    fenced = RevisionFencedStore(store, full.deployment.id, full.deployment.revision)
    scoped = InstrumentScopedStore(fenced, "BTC-USD")
    focused = await scoped.get_deployment(full.deployment.id)
    assert not focused.accounting_complete
    assert focused.fills == ()
    assert (await scoped.get_accounting_snapshot(full.deployment.id)).fills == full.fills
    verdict = await _entry_verdict(
        focused,
        store=scoped,
        product_id="BTC-USD",
        notional=Decimal("1"),
        quantity=Decimal("0.01"),
        risk_policy=_policy(),
        portfolio=(full,),
        observation=_observation(),
    )
    assert verdict.reason_code is (
        RiskReasonCode.DAILY_LOSS_LIMIT if applied else RiskReasonCode.BREAKER_MARK_MISSING
    )
    # Pure gates also reject explicitly incomplete accounting views.
    assert (
        _verdict(
            (focused,),
            _entry(strategy_id=_STRATEGY_A),
            mode=full.deployment.mode,
            live_quote_cash=Decimal("10000") if live else None,
        ).decision
        is RiskDecision.DENY
    )


@pytest.mark.anyio
async def test_cached_portfolio_cannot_overwrite_successive_sibling_fills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh reads see this cycle's later ETH loss despite an earlier cached BTC portfolio."""
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    full = sibling_loss()
    store = InMemoryExecutionStore()
    cached = replace(
        full, deployment=replace(full.deployment, cash=Decimal("10000")), orders=(), fills=()
    )
    await seed_accounting(store, cached)
    scoped = InstrumentScopedStore(store, "BTC-USD")
    stale_current = await scoped.get_deployment(full.deployment.id)
    first = await _entry_verdict(
        stale_current,
        store=scoped,
        product_id="BTC-USD",
        notional=Decimal("1"),
        risk_policy=_policy(),
        portfolio=(cached,),
        observation=_observation(),
    )
    assert first.decision is RiskDecision.ALLOW
    for order in full.orders:
        await store.save_order(order)
    for fill in full.fills:
        await store.save_fill(fill)
    await store.save_deployment(full.deployment)
    second = await _entry_verdict(
        stale_current,
        store=scoped,
        product_id="BTC-USD",
        notional=Decimal("1"),
        risk_policy=_policy(),
        portfolio=(cached,),
        observation=_observation(),
    )
    assert second.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
    paused = await _apply_circuit_breakers(
        await scoped.get_deployment(full.deployment.id),
        candle=_candle(),
        product_id="BTC-USD",
        store=scoped,
        risk_policy=_policy(),
        portfolio=(cached,),
        marks={"BTC-USD": Decimal("100")},
    )
    assert paused.deployment.daily_loss_latched


class _BrokenAccountingStore(InMemoryExecutionStore):
    """An unavailable authoritative read must not fall back to cached observation."""

    async def get_accounting_snapshot(self, deployment_id: UUID) -> DeploymentSnapshot:
        """Simulate incomplete durable accounting despite available runtime overlays."""
        del deployment_id
        raise ExecutionStoreError("test-only incomplete evidence")


@pytest.mark.anyio
async def test_missing_authoritative_read_denies_without_cached_fallback() -> None:
    """Even a healthy-looking cached snapshot cannot replace unavailable economics."""
    store = _BrokenAccountingStore()
    full = sibling_loss()
    await seed_accounting(store, full)
    verdict = await _entry_verdict(
        full,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("1"),
        risk_policy=_policy(),
        portfolio=(full,),
        observation=_observation(),
    )
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
    assert "Fresh complete" in verdict.detail


def overnight_long() -> DeploymentSnapshot:
    """One applied overnight buy with a misleading legacy same-day observation stamp."""
    root = _deployment(
        created_at=_YESTERDAY,
        cash=Decimal("9900"),
        initial_equity=Decimal("10000"),
        utc_day_open_at=_MIDNIGHT,
        utc_day_open_equity=Decimal("9950"),
    )
    trip = _round_trip(root, buy_at=_YESTERDAY, sell_at=_TODAY)
    position = Position(
        deployment_id=root.id,
        product_id="BTC-USD",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("40"),
        target_price=Decimal("120"),
        entered_bar=_YESTERDAY,
        updated_at=_TODAY,
    )
    return replace(
        trip,
        position=position,
        orders=(trip.orders[0],),
        fills=(trip.fills[0],),
        deployment=replace(root, phase=RuntimePhase.OPEN),
    )


def _candle() -> Candle:
    """One complete current candle that does not hit the overnight long's stop."""
    return Candle(
        _TODAY - timedelta(hours=1),
        Decimal("50"),
        Decimal("55"),
        Decimal("45"),
        Decimal("50"),
        Decimal("1"),
    )


class MidnightProvider:
    """A hermetic historical provider with an actual explicit closed midnight candle."""

    def __init__(self, *, complete: bool = True) -> None:
        """Choose whether the requested range genuinely supplies the opening evidence."""
        self.complete = complete
        self.requests = 0

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """No catalog reads are needed to recover already identified product evidence."""
        return ()

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """A latest close must never be used as midnight evidence."""
        del product_id, interval, now
        raise AssertionError("Midnight recovery must request an explicit historical range.")

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return one actual midnight close, with completeness controlled by the test."""
        del product_id, now
        self.requests += 1
        assert ends_at == _MIDNIGHT and starts_at + interval.duration == ends_at
        candle = Candle(
            starts_at, Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("1")
        )
        quality = CandleQualityReport((candle,), 1, 0, 0, ends_at, True)
        return CandleRangeReport(starts_at, ends_at, 1, quality, self.complete)


@pytest.mark.anyio
@pytest.mark.parametrize("complete", [False, True])
async def test_late_restart_same_bar_maintenance_and_consecutive_cycles(
    monkeypatch: pytest.MonkeyPatch, complete: bool
) -> None:
    """Maintenance preserves raw evidence and cannot turn unknown midnight into zero."""
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    full = overnight_long()
    candle = _candle()
    full = replace(full, deployment=replace(full.deployment, last_evaluated_bar=candle.starts_at))
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    product = _product()
    provider = MidnightProvider(complete=complete)
    with risk_market_data_scope(MarketDataService(provider)):
        for _ in range(2):
            current = await store.get_deployment(full.deployment.id)
            await process_closed_bar(
                current,
                strategy=create_template_strategy(),
                product=product,
                candles=(candle,),
                broker=PaperBroker(),
                store=store,
            )
        recovered = await store.get_accounting_snapshot(full.deployment.id)
        pnl = _daily_pnl(recovered, marks={"BTC-USD": Decimal("50")}, as_of=_TODAY)
        assert pnl == (Decimal("-50") if complete else None)
        verdict = await _entry_verdict(
            recovered,
            store=store,
            product_id="BTC-USD",
            notional=Decimal("1"),
            risk_policy=_policy(),
            portfolio=(full,),
            observation=_observation(marks={"BTC-USD": Decimal("50")}),
        )
        assert verdict.reason_code is (
            RiskReasonCode.DAILY_LOSS_LIMIT if complete else RiskReasonCode.BREAKER_MARK_MISSING
        )
    assert recovered.deployment.utc_day_open_at == full.deployment.utc_day_open_at
    assert recovered.deployment.utc_day_open_equity == Decimal("9950")
    assert recovered.deployment.cash == Decimal("9900")
    assert recovered.fills == full.fills
    assert provider.requests == (1 if complete else 3)


def _product() -> MarketProduct:
    """Exact executable product metadata for the in-memory maintenance path."""
    return MarketProduct(
        "BTC-USD",
        "BTC",
        "USD",
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("1"),
        True,
    )


def test_refresh_does_not_promote_a_legacy_midnight_stamp() -> None:
    """Current equity is not opening evidence, even with an old same-day stamp."""
    full = overnight_long()
    refreshed = refresh_performance(full, marks={"BTC-USD": Decimal("50")}, now=_TODAY)
    assert refreshed.risk_day_open_evidence is None
    assert refreshed.utc_day_open_equity == full.deployment.utc_day_open_equity
    assert (
        _daily_pnl(
            replace(full, deployment=refreshed), marks={"BTC-USD": Decimal("50")}, as_of=_TODAY
        )
        is None
    )


def test_flat_midnight_reconstruction_and_strict_evidence_reload() -> None:
    """Flat day cash requires no mark; serialized qualified evidence retains exact decimals."""
    full = sibling_loss()
    evidence = reconstruct_day_open(full, as_of=_TODAY)
    assert evidence is not None and evidence.equity == Decimal("10000")
    assert evidence.marks == ()
    recovered = DailyOpeningEvidence.model_validate_json(evidence.model_dump_json())
    assert recovered == evidence
    assert _daily_pnl(
        replace(full, deployment=replace(full.deployment, risk_day_open_evidence=recovered)),
        marks={},
        as_of=_TODAY,
    ) == Decimal("-50")


def test_overnight_closure_uses_actual_midnight_mark_not_lifetime_pnl() -> None:
    """Yesterday's gain is not today's gain; closed overnight inventory still needs its mark."""
    full = _round_trip(
        _deployment(created_at=_YESTERDAY, initial_equity=Decimal("10000"), cash=Decimal("10100")),
        buy_at=_YESTERDAY,
        sell_at=_TODAY,
        sell_price=Decimal("200"),
    )
    mark = MidnightMark(product_id="BTC-USD", closes_at=_MIDNIGHT, price=Decimal("300"))
    evidence = reconstruct_day_open(full, as_of=_TODAY, marks=(mark,))
    assert evidence is not None and evidence.equity == Decimal("10200")
    assert _daily_pnl(
        replace(full, deployment=replace(full.deployment, risk_day_open_evidence=evidence)),
        marks={},
        as_of=_TODAY,
    ) == Decimal("-100")


@pytest.mark.anyio
async def test_late_applied_fill_invalidates_cached_opening_economics() -> None:
    """Fresh economics change derived opening evidence without rewriting old cash or fee records."""
    full = sibling_loss()
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    before = await accounting_snapshot(store, full.deployment.id, as_of=_TODAY)
    evidence = before.deployment.risk_day_open_evidence
    assert evidence is not None
    # A newly discovered, not-yet-projected fill must deny even with yesterday's cached proof.
    await store.save_deployment(before.deployment)
    pending = replace(
        full.fills[0], id=uuid4(), venue_fill_id="late-extra", economics_applied_at=None
    )
    await store.save_fill(pending)
    after = await accounting_snapshot(store, full.deployment.id, as_of=_TODAY)
    assert _daily_pnl(after, marks={}, as_of=_TODAY) is None
    assert after.deployment.risk_day_open_evidence == evidence


@pytest.mark.anyio
async def test_stale_performance_and_pause_views_never_rewrite_sibling_cash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Performance metadata and breaker flags cannot reapply an earlier runtime cash copy."""
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    full = sibling_loss()
    full = replace(full, deployment=replace(full.deployment, drawdown_latched=True))
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    scoped = InstrumentScopedStore(store, "BTC-USD")
    stale = overlay_snapshot(
        replace(full, deployment=replace(full.deployment, cash=Decimal("10000"))), "BTC-USD"
    )
    updated = await _persist_performance(
        stale,
        store=scoped,
        mark_price=Decimal("100"),
        product_id="BTC-USD",
    )
    assert updated.deployment.cash == Decimal("9950")
    assert updated.deployment.drawdown_latched
    paused = await _apply_circuit_breakers(
        stale,
        candle=_candle(),
        product_id="BTC-USD",
        store=scoped,
        risk_policy=_policy(),
        portfolio=(stale,),
        marks={},
    )
    assert paused.deployment.cash == Decimal("9950")
    assert paused.deployment.drawdown_latched
    assert (await store.get_accounting_snapshot(full.deployment.id)).fills == full.fills


def test_closed_order_without_its_fills_is_not_complete_flat_history() -> None:
    """A known filled order cannot be interpreted as no trading just because fills are absent."""
    full = sibling_loss()
    missing = replace(full, deployment=replace(full.deployment, cash=Decimal("10000")), fills=())
    assert reconstruct_day_open(missing, as_of=_TODAY) is None
    assert _daily_pnl(missing, marks={}, as_of=_TODAY) is None


def test_equal_and_opposite_midnight_products_need_separate_actual_marks() -> None:
    """A BTC long and ETH short are not flat at midnight even when base amounts net to zero."""
    root = _deployment(created_at=_YESTERDAY, cash=Decimal("9900"), initial_equity=Decimal("10000"))
    btc = _round_trip(root, buy_at=_YESTERDAY, sell_at=_TODAY)
    eth = _round_trip(replace(root, product_id="ETH-USD"), buy_at=_TODAY, sell_at=_YESTERDAY)
    full = replace(
        btc,
        orders=(*btc.orders, *eth.orders),
        fills=(
            *btc.fills,
            *(replace(fill, venue_fill_id=f"eth-{fill.venue_fill_id}") for fill in eth.fills),
        ),
    )
    assert reconstruct_day_open(full, as_of=_TODAY) is None
    marks = (
        MidnightMark(product_id="BTC-USD", closes_at=_MIDNIGHT, price=Decimal("200")),
        MidnightMark(product_id="ETH-USD", closes_at=_MIDNIGHT, price=Decimal("100")),
    )
    evidence = reconstruct_day_open(full, as_of=_TODAY, marks=marks)
    assert evidence is not None and evidence.equity == Decimal("10050")
    qualified = replace(full, deployment=replace(root, risk_day_open_evidence=evidence))
    assert _daily_pnl(qualified, marks={}, as_of=_TODAY) == Decimal("-150")


@pytest.mark.anyio
async def test_multiple_retained_books_are_fresh_not_only_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer peer's fills bind an otherwise clean candidate despite a stale peer cache."""
    patch_loop_global(monkeypatch, "utc_now", lambda: _TODAY)
    full = sibling_loss()
    peer_cached = replace(
        full, deployment=replace(full.deployment, cash=Decimal("10000")), orders=(), fills=()
    )
    candidate = DeploymentSnapshot(_deployment(created_at=_YESTERDAY))
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    await seed_accounting(store, candidate)
    verdict = await _entry_verdict(
        candidate,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("1"),
        risk_policy=_policy(),
        portfolio=(peer_cached,),
        observation=_observation(),
    )
    assert verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT


@pytest.mark.anyio
async def test_absent_observation_cannot_skip_daily_evidence() -> None:
    """A caller omitting marks still gets an unknown-risk denial, never disabled breakers."""
    full = overnight_long()
    store = InMemoryExecutionStore()
    await seed_accounting(store, full)
    verdict = await _entry_verdict(
        full,
        store=store,
        product_id="BTC-USD",
        notional=Decimal("1"),
        risk_policy=_policy(),
        portfolio=(full,),
    )
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING


def test_consecutive_utc_days_require_new_opening_marks_without_resetting_latches() -> None:
    """Preserved prior-day proof cannot value tomorrow; real new midnight prices can."""
    full = overnight_long()
    today = reconstruct_day_open(
        full,
        as_of=_TODAY,
        marks=(MidnightMark(product_id="BTC-USD", closes_at=_MIDNIGHT, price=Decimal("100")),),
    )
    assert today is not None
    full = replace(
        full,
        deployment=replace(
            full.deployment,
            risk_day_open_evidence=today,
            daily_loss_latched=True,
            drawdown_latched=True,
        ),
    )
    tomorrow = _TODAY + timedelta(days=1)
    unknown = refresh_performance(full, marks={"BTC-USD": Decimal("50")}, now=tomorrow)
    assert unknown.risk_day_open_evidence == today
    assert unknown.daily_loss_latched and unknown.drawdown_latched
    assert (
        _daily_pnl(
            replace(full, deployment=unknown), marks={"BTC-USD": Decimal("50")}, as_of=tomorrow
        )
        is None
    )
    next_opening = reconstruct_day_open(
        full,
        as_of=tomorrow,
        marks=(
            MidnightMark(
                product_id="BTC-USD", closes_at=_MIDNIGHT + timedelta(days=1), price=Decimal("80")
            ),
        ),
    )
    assert next_opening is not None and next_opening.equity == Decimal("9980")
    recovered = replace(full, deployment=replace(unknown, risk_day_open_evidence=next_opening))
    assert _daily_pnl(recovered, marks={"BTC-USD": Decimal("50")}, as_of=tomorrow) == Decimal("-30")
