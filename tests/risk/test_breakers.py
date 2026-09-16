"""Daily-loss, drawdown, order-rate, and reference-price collar gate tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from thytrader.execution.models import (
    Deployment,
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
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import ProposedEntry, evaluate_new_entry
from thytrader.risk.models import (
    RiskDecision,
    RiskReasonCode,
    compiled_default_risk_policy,
    definition_from_stored_json,
    stored_canonical_fingerprint,
)

_STRATEGY = UUID("01978a3e-5f2c-7d10-b3a4-0000000000b1")
_NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)


def _observation(
    *,
    proposed: Decimal = Decimal("100"),
    reference: Decimal = Decimal("100"),
    marks: dict[str, Decimal] | None = None,
) -> EntryObservation:
    """Observation that satisfies the compiled collar and has a last close."""
    price = reference
    return EntryObservation(
        as_of=_NOW,
        proposed_price=proposed,
        reference_price=reference,
        marks=marks if marks is not None else {"BTC-USD": price},
    )


def _deployment(*, phase: RuntimePhase = RuntimePhase.FLAT) -> Deployment:
    """One occupied paper book."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=_STRATEGY,
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("9000"),
        phase=phase,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal("10000"),
    )


def _order(*, created_at: datetime, status: OrderStatus = OrderStatus.OPEN) -> Order:
    """One venue-visible order in the rolling rate window."""
    return Order(
        id=uuid4(),
        deployment_id=uuid4(),
        intent_id=uuid4(),
        client_order_id="client-1",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.1"),
        status=status,
        created_at=created_at,
        updated_at=created_at,
        price=Decimal("100"),
    )


def _round_trip_loss_snapshot() -> DeploymentSnapshot:
    """A flat book that realized a 500-quote loss on the current UTC day."""
    deployment = _deployment()
    buy = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="buy",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=_NOW - timedelta(hours=1),
        updated_at=_NOW - timedelta(hours=1),
        price=Decimal("100"),
        filled_quantity=Decimal("1"),
    )
    sell = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="sell",
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=_NOW,
        updated_at=_NOW,
        price=Decimal("50"),
        filled_quantity=Decimal("1"),
    )
    buy_fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=buy.id,
        venue_fill_id="buy-fill",
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0"),
        filled_at=_NOW - timedelta(hours=1),
    )
    sell_fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=sell.id,
        venue_fill_id="sell-fill",
        price=Decimal("50"),
        quantity=Decimal("1"),
        fee=Decimal("0"),
        filled_at=_NOW,
    )
    return DeploymentSnapshot(
        deployment=deployment,
        orders=(buy, sell),
        fills=(buy_fill, sell_fill),
        position=None,
    )


def _proposed() -> ProposedEntry:
    """Sized BTC paper entry."""
    return ProposedEntry(
        product_id="BTC-USD",
        strategy_id=_STRATEGY,
        notional=Decimal("100"),
    )


def test_compiled_default_allows_a_marked_flat_entry() -> None:
    """Wide compiled breaker defaults must still admit a normal paper entry."""
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(),
        observation=_observation(),
    )
    assert verdict.decision is RiskDecision.ALLOW


def test_daily_loss_limit_denies_after_utc_day_loss() -> None:
    """Mode-wide UTC-day realized loss at the fraction cap must deny the next entry."""
    policy = compiled_default_risk_policy().model_copy(
        update={"daily_loss_limit_fraction": "0.001", "paper_capital_quote": "10000"}
    )
    snapshot = _round_trip_loss_snapshot()
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(snapshot,),
        observation=_observation(marks={"BTC-USD": Decimal("50")}),
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT


def test_drawdown_limit_denies_when_fill_ledger_drawdown_reaches_cap() -> None:
    """Per-strategy fill-ledger drawdown at the policy cap must deny."""
    policy = compiled_default_risk_policy().model_copy(
        update={"max_strategy_drawdown_fraction": "0.01"}
    )
    snapshot = _round_trip_loss_snapshot()
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(snapshot,),
        observation=_observation(marks={"BTC-USD": Decimal("50")}),
    )
    assert verdict.decision is RiskDecision.DENY
    assert verdict.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT


def test_order_rate_limit_denies_without_requiring_a_pause_code() -> None:
    """Exhausting the rolling-minute order cap blocks risk-increasing entries."""
    policy = compiled_default_risk_policy().model_copy(update={"max_entry_orders_per_minute": 1})
    snapshot = DeploymentSnapshot(
        deployment=_deployment(),
        orders=(_order(created_at=_NOW - timedelta(seconds=10)),),
        fills=(),
        position=None,
    )
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(snapshot,),
        observation=_observation(),
    )
    assert verdict.reason_code is RiskReasonCode.ORDER_RATE_LIMIT


def test_cancel_rate_limit_denies_further_entries() -> None:
    """Exhausting the rolling-minute cancel cap blocks new risk-increasing entries."""
    policy = compiled_default_risk_policy().model_copy(
        update={"max_cancellations_per_minute": 1}
    )
    snapshot = DeploymentSnapshot(
        deployment=_deployment(),
        orders=(
            _order(
                created_at=_NOW - timedelta(seconds=30),
                status=OrderStatus.CANCELED,
            ),
        ),
        fills=(),
        position=None,
    )
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(snapshot,),
        observation=_observation(),
    )
    assert verdict.reason_code is RiskReasonCode.CANCEL_RATE_LIMIT


def test_reference_price_collar_denies_fat_finger_limits() -> None:
    """A limit twice last close exceeds the compiled 0.5 collar."""
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(),
        observation=_observation(proposed=Decimal("200"), reference=Decimal("100")),
    )
    assert verdict.reason_code is RiskReasonCode.REFERENCE_PRICE_COLLAR


def test_missing_reference_price_fails_closed() -> None:
    """Risk-increasing orders without a last close must not proceed."""
    verdict = evaluate_new_entry(
        compiled_default_risk_policy(),
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(),
        observation=EntryObservation(
            as_of=_NOW,
            proposed_price=Decimal("100"),
            reference_price=None,
            marks={},
        ),
    )
    assert verdict.reason_code is RiskReasonCode.REFERENCE_PRICE_UNAVAILABLE


def test_missing_mark_on_open_inventory_fails_closed() -> None:
    """Daily-loss cannot invent equity; open inventory without a mark denies."""
    deployment = _deployment(phase=RuntimePhase.OPEN)
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        orders=(),
        fills=(),
        position=Position(
            deployment_id=deployment.id,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_NOW,
            updated_at=_NOW,
        ),
    )
    policy = compiled_default_risk_policy().model_copy(
        update={"daily_loss_limit_fraction": "0.01"}
    )
    verdict = evaluate_new_entry(
        policy,
        mode=DeploymentMode.PAPER,
        proposed=_proposed(),
        snapshots=(snapshot,),
        observation=EntryObservation(
            as_of=_NOW,
            proposed_price=Decimal("100"),
            reference_price=Decimal("100"),
            marks={},
        ),
    )
    assert verdict.reason_code is RiskReasonCode.BREAKER_MARK_MISSING


def test_legacy_stored_json_overlays_breaker_defaults() -> None:
    """Phase 10 documents without breaker keys must load the compiled defaults."""
    raw = (
        '{"allocations":[],"max_concurrent_open_positions":8,'
        '"max_concurrent_running_deployments":8,'
        '"max_portfolio_exposure_fraction":"1",'
        '"paper_capital_quote":"100000",'
        '"per_product_max_exposure_fraction":"1",'
        '"policy_id":"01978a3e-5f2c-7d10-b3a4-0000000000aa",'
        '"product_allowlist":[],"quote_currency":"USD",'
        '"schema_version":"thytrader-risk-policy-v1","version":1}'
    )
    definition = definition_from_stored_json(raw)
    assert definition.daily_loss_limit_fraction == "1"
    assert definition.max_entry_orders_per_minute == 60
    assert definition.reference_price_collar_fraction == "0.5"
    assert stored_canonical_fingerprint(raw).startswith("sha256:")
