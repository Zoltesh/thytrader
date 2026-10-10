"""Paper futures books: whole contracts, per-contract fees, funding, liquidation (ADR 0129 §4)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from thytrader.evaluation.futures_spec import InstrumentContract
from thytrader.exchanges.coinbase_futures_catalog import parse_futures_row
from thytrader.execution.futures_liquidation import paper_liquidation_if_due
from thytrader.execution.futures_paper import (
    FuturesRuntime,
    futures_runtime_scope,
    prepare_futures_book,
)
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.futures_observations import (
    FundingRateRecord,
    FuturesInstrumentObservation,
    instrument_observation,
)
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.trading.futures_book import (
    BoundFuturesContract,
    FuturesBookState,
    InMemoryFuturesContractStore,
    futures_book_scope,
)
from thytrader.trading.futures_sizing import FuturesMarginTerms
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.market_data.service import MarketDataService
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot

_PERP = "BIP-20DEC30-CDE"
_START = datetime(2026, 10, 10, tzinfo=UTC)
_LISTING = Path("tests/exchanges/fixtures/coinbase_futures_listing.json")
_CONTRACT = InstrumentContract(
    product_id=_PERP,
    kind="perpetual_future",
    underlying="BTC",
    contract_size="0.01",
    listed_expiry=date(2030, 12, 20),
    catalog_fingerprint="sha256:" + "c" * 64,
)
_MARGIN = FuturesMarginTerms(
    contract_size=Decimal("0.01"),
    long_rate=Decimal("0.2"),
    short_rate=Decimal("0.25"),
    maintenance_fraction=Decimal(1),
    min_buffer_fraction=Decimal("0.5"),
    max_leverage=Decimal(2),
    fee_per_contract=Decimal("0.15"),
)


def _product() -> MarketProduct:
    """The BIP perp in base units: whole contracts of 0.01 BTC."""
    return MarketProduct(
        product_id=_PERP,
        base_currency="BTC",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.01"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.01"),
        quote_min_size=Decimal("0.01"),
        trading_enabled=True,
    )


def _strategy() -> StrategyDefinition:
    """The 1h template on the perp, entering on every bar."""
    draft = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["instrument"] = {
        "product_id": _PERP,
        "base_currency": "BTC",
        "quote_currency": "USD",
        "kind": "future",
    }
    payload["derivatives"] = {"max_leverage": "2"}
    payload["entry"]["when"] = {
        "all": [
            {
                "left": {"literal": "1"},
                "operator": "greater_than_or_equal",
                "right": {"literal": "0"},
            }
        ]
    }
    payload["execution"]["max_entry_wait_bars"] = 2
    payload["sizing"] = {
        **payload["sizing"],
        "risk_fraction": "0.25",
        "max_quote_notional": "1000000",
    }
    payload["portfolio_limits"] = {
        **payload["portfolio_limits"],
        "max_strategy_exposure_fraction": "1",
    }
    return StrategyDefinition.model_validate(payload)


def _policy() -> RiskPolicyDefinition:
    """The compiled policy with a paper futures envelope."""
    return compiled_default_risk_policy().model_copy(
        update={"futures": FuturesRiskPolicy(paper_capital_usd="100000")}
    )


def _candles(count: int, *, low_offset: Decimal = Decimal("0.01")) -> tuple[Candle, ...]:
    """A rising 1h series from the start instant."""
    return tuple(
        Candle(
            starts_at=_START + timedelta(hours=index),
            open=Decimal(100 + index),
            high=Decimal(100 + index) + 2,
            low=Decimal(100 + index) - low_offset,
            close=Decimal(100 + index),
            volume=Decimal(10),
        )
        for index in range(count)
    )


async def _book(store: InMemoryExecutionStore, strategy: StrategyDefinition) -> DeploymentSnapshot:
    """Insert one running paper futures book with 10000 USD and explicit fees."""
    deployment = Deployment(
        id=uuid7(_START),
        strategy_fingerprint=strategy_fingerprint(strategy),
        strategy_id=strategy.strategy_id,
        product_id=_PERP,
        timeframe="1h",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal(10000),
        paper_maker_fee_rate=Decimal(0),
        paper_taker_fee_rate=Decimal("0.0005"),
        cash=Decimal(10000),
        initial_equity=Decimal(10000),
        phase=RuntimePhase.FLAT,
        created_at=_START,
        updated_at=_START,
    )
    await store.create_deployment(deployment)
    return await store.get_deployment(deployment.id)


def _binding(snapshot: DeploymentSnapshot) -> BoundFuturesContract:
    """The book's bound perp with a 0.15 USD per-contract fee."""
    return BoundFuturesContract(
        deployment_id=snapshot.deployment.id,
        contract=_CONTRACT,
        fee_per_contract=Decimal("0.15"),
        bound_at=_START,
    )


def _state(snapshot: DeploymentSnapshot, **changes: Any) -> FuturesBookState:
    """A fully known long book state."""
    state = FuturesBookState(
        deployment_id=snapshot.deployment.id,
        product_id=_PERP,
        side="long",
        binding=_binding(snapshot),
        margin=_MARGIN,
    )
    return replace(state, **changes)


async def _filled_book(store: InMemoryExecutionStore) -> DeploymentSnapshot:
    """Rest an entry on bar 30 and fill it on bar 31."""
    strategy = _strategy()
    snapshot = await _book(store, strategy)
    with futures_book_scope(_state(snapshot)):
        rested = await process_closed_bar(
            snapshot,
            strategy=strategy,
            product=_product(),
            candles=_candles(30),
            broker=PaperBroker(),
            store=store,
            risk_policy=_policy(),
        )
        assert rested.deployment.phase is RuntimePhase.PENDING_ENTRY
        return await process_closed_bar(
            rested,
            strategy=strategy,
            product=_product(),
            candles=_candles(31, low_offset=Decimal(5)),
            broker=PaperBroker(),
            store=store,
            risk_policy=_policy(),
        )


@pytest.mark.anyio
async def test_entry_is_whole_contracts_and_fills_pay_the_per_contract_fee() -> None:
    """The entry is a multiple of 0.01 BTC within 2x leverage; each contract pays 0.15 USD."""
    store = InMemoryExecutionStore()
    filled = await _filled_book(store)

    position = filled.position
    assert position is not None
    contracts = position.quantity / Decimal("0.01")
    assert contracts == contracts.to_integral_value()
    assert contracts >= 1
    (fill,) = filled.fills
    assert fill.fee == contracts * Decimal("0.15")
    assert fill.price * fill.quantity <= 2 * Decimal(10000)
    assert filled.deployment.cash == Decimal(10000) - fill.price * fill.quantity - fill.fee


@pytest.mark.anyio
async def test_unknown_margin_or_overdue_funding_rests_no_entry() -> None:
    """Missing evidence blocks the entry instead of falling back to spot sizing."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _book(store, strategy)
    for state in (
        _state(snapshot, margin=None),
        _state(snapshot, funding_overdue=_START),
        _state(snapshot, binding=None),
    ):
        with futures_book_scope(state):
            result = await process_closed_bar(
                snapshot,
                strategy=strategy,
                product=_product(),
                candles=_candles(30),
                broker=PaperBroker(),
                store=store,
                risk_policy=_policy(),
            )
        assert result.deployment.phase is RuntimePhase.FLAT
        assert result.orders == ()


@pytest.mark.anyio
async def test_a_crash_below_maintenance_sends_a_liquidation_exit() -> None:
    """Equity at the bar low below maintenance closes the book with a LIQUIDATION intent."""
    store = InMemoryExecutionStore()
    filled = await _filled_book(store)
    position = filled.position
    assert position is not None
    crash = Candle(
        starts_at=_START + timedelta(hours=31),
        open=Decimal(130),
        high=Decimal(130),
        low=Decimal(1),
        close=Decimal(2),
        volume=Decimal(10),
    )
    with futures_book_scope(_state(filled)):
        liquidated = await paper_liquidation_if_due(
            filled,
            strategy=_strategy(),
            candle=crash,
            product=_product(),
            broker=PaperBroker(),
            store=store,
            position=position,
        )
    assert liquidated is not None
    assert liquidated.position is None
    purposes = {intent.purpose for intent in liquidated.intents}
    assert IntentPurpose.LIQUIDATION in purposes
    exit_order = next(
        order
        for order in liquidated.orders
        if order.status is OrderStatus.FILLED and order.price == Decimal(1)
    )
    assert exit_order.quantity == position.quantity


@pytest.mark.anyio
async def test_a_book_above_maintenance_is_not_liquidated() -> None:
    """A normal bar never liquidates."""
    store = InMemoryExecutionStore()
    filled = await _filled_book(store)
    position = filled.position
    assert position is not None
    with futures_book_scope(_state(filled)):
        kept = await paper_liquidation_if_due(
            filled,
            strategy=_strategy(),
            candle=_candles(32)[-1],
            product=_product(),
            broker=PaperBroker(),
            store=store,
            position=position,
        )
    assert kept is None


def _observation() -> FuturesInstrumentObservation:
    """The recorded BIP perp facts from the listing fixture."""
    payload: dict[str, Any] = json.loads(_LISTING.read_text())
    row = next(item for item in payload["products"] if item["product_id"] == _PERP)
    product = parse_futures_row(row)
    assert product is not None
    return instrument_observation(product)


def _record(hour: datetime, rate: str) -> FundingRateRecord:
    """One settled funding row."""
    return FundingRateRecord(
        product_id=_PERP,
        funding_time=hour,
        rate=Decimal(rate),
        interval_seconds=3600,
        first_observed_at=hour,
        last_observed_at=hour,
        observation_count=1,
        revision_count=0,
        settled=True,
        settled_at=hour + timedelta(hours=1),
        conflict_count=0,
        last_conflict_rate=None,
        last_conflict_at=None,
    )


class _Observations:
    """The latest observation plus a fixed set of settled funding rows."""

    def __init__(self, records: tuple[FundingRateRecord, ...]) -> None:
        self.records = records

    async def latest_instrument(
        self, product_id: str
    ) -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Return the fixture observation."""
        del product_id
        return _observation(), _START

    async def funding_rates(
        self, *, product_id: str | None, starts_at: datetime, ends_at: datetime
    ) -> tuple[FundingRateRecord, ...]:
        """Return rows in ``[starts_at, ends_at)``."""
        del product_id
        return tuple(r for r in self.records if starts_at <= r.funding_time < ends_at)


@dataclass(frozen=True)
class _Quality:
    candles: tuple[Candle, ...]


@dataclass(frozen=True)
class _Report:
    quality: _Quality


class _MarketData:
    """Hourly candles for the funding marks."""

    async def get_range(
        self,
        product_id: str,
        interval: object,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> _Report:
        """Return the series' candles inside the range."""
        del product_id, interval, now
        candles = tuple(c for c in _candles(60) if starts_at <= c.starts_at < ends_at)
        return _Report(quality=_Quality(candles=candles))


@pytest.mark.anyio
async def test_funding_is_applied_once_per_held_hour_at_the_bar_close() -> None:
    """Filled on the 30:00 bar, hours 31-33 are charged at closes 130-132, never twice."""
    store = InMemoryExecutionStore()
    filled = await _filled_book(store)
    position = filled.position
    assert position is not None
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(filled))
    hours = tuple(_START + timedelta(hours=offset) for offset in (31, 32, 33))
    runtime = FuturesRuntime(
        contracts=contracts,
        observations=_Observations(tuple(_record(hour, "0.0001") for hour in hours)),
    )
    now = _START + timedelta(hours=33, minutes=5)
    with futures_runtime_scope(runtime):
        state, charged = await prepare_futures_book(
            filled,
            strategy=_strategy(),
            store=store,
            market_data=cast("MarketDataService", _MarketData()),
            now=now,
        )
        _again, replayed = await prepare_futures_book(
            charged,
            strategy=_strategy(),
            store=store,
            market_data=cast("MarketDataService", _MarketData()),
            now=now,
        )

    assert state is not None
    assert state.margin is not None
    assert state.margin.long_rate == Decimal("0.21025")
    assert state.funding_overdue is None
    expected = -position.quantity * Decimal(130 + 131 + 132) * Decimal("0.0001")
    assert [flow.funding_time for flow in charged.funding] == list(hours)
    assert charged.deployment.cash == filled.deployment.cash + expected
    assert replayed.deployment.cash == charged.deployment.cash
    assert len(replayed.funding) == 3


@pytest.mark.anyio
async def test_a_missing_settled_hour_past_the_grace_blocks_entries() -> None:
    """No rate for hour 31 long after it settled: nothing is charged and entries wait."""
    store = InMemoryExecutionStore()
    filled = await _filled_book(store)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(filled))
    runtime = FuturesRuntime(contracts=contracts, observations=_Observations(()))
    with futures_runtime_scope(runtime):
        state, after = await prepare_futures_book(
            filled,
            strategy=_strategy(),
            store=store,
            market_data=cast("MarketDataService", _MarketData()),
            now=_START + timedelta(hours=33, minutes=30),
        )
    assert state is not None
    assert state.funding_overdue == _START + timedelta(hours=31)
    blocked = state.entry_block()
    assert blocked is not None
    assert blocked[0] == "FUNDING_HISTORY_MISSING"
    assert after.deployment.cash == filled.deployment.cash


@pytest.mark.anyio
async def test_the_closed_bar_loop_liquidates_before_the_protective_stop() -> None:
    """On a crash bar the loop sends LIQUIDATION, not the stop the bar also traded through."""
    store = InMemoryExecutionStore()
    filled = await _filled_book(store)
    crash = Candle(
        starts_at=_START + timedelta(hours=31),
        open=Decimal(130),
        high=Decimal(130),
        low=Decimal(1),
        close=Decimal(2),
        volume=Decimal(10),
    )
    with futures_book_scope(_state(filled)):
        after = await process_closed_bar(
            filled,
            strategy=_strategy(),
            product=_product(),
            candles=(*_candles(31), crash),
            broker=PaperBroker(),
            store=store,
            risk_policy=_policy(),
        )
    assert after.position is None
    purposes = {intent.purpose for intent in after.intents}
    assert IntentPurpose.LIQUIDATION in purposes
    assert IntentPurpose.STOP not in purposes


@pytest.mark.anyio
async def test_policy_leverage_buffer_and_latest_funding_rate_reach_the_book_state() -> None:
    """The lower leverage and the policy buffer shape the margin terms (ADR 0129 §5)."""
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _book(store, strategy)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    records = (
        _record(_START + timedelta(hours=1), "0.00002"),
        _record(_START + timedelta(hours=2), "-0.00003"),
    )
    runtime = FuturesRuntime(contracts=contracts, observations=_Observations(records))
    policy = compiled_default_risk_policy().model_copy(
        update={
            "futures": FuturesRiskPolicy(
                paper_capital_usd="100000",
                max_leverage="1.5",
                min_liquidation_buffer_fraction="0.3",
            )
        }
    )
    with futures_runtime_scope(runtime):
        state, _same = await prepare_futures_book(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=cast("MarketDataService", _MarketData()),
            now=_START + timedelta(hours=3),
            policy=policy,
        )
    assert state is not None
    assert state.margin is not None
    assert state.margin.max_leverage == Decimal("1.5")
    assert state.margin.min_buffer_fraction == Decimal("0.3")
    assert state.latest_funding_rate == Decimal("-0.00003")
