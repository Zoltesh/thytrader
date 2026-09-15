"""Paper start and closed-bar evaluation for a published 5m strategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentMode, ExecutionConflictError, RuntimePhase
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import create_deployment
from thytrader.execution.signals import evaluate_latest_entry
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy, StrategyPublicationError


class _Catalog:
    """Load-only StrategyPublicationStore double for one published strategy."""

    def __init__(self, definition: StrategyDefinition) -> None:
        """Bind one immutable publication."""
        fingerprint = strategy_fingerprint(definition)
        self._published = PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition)

    async def publish(self, definition: StrategyDefinition) -> PublishedStrategy:
        """Refuse extra publications; this fixture only serves load()."""
        del definition
        raise StrategyPublicationError("Catalog fixture is load-only.")

    async def publish_draft(
        self, definition: StrategyDefinition, *, expected_revision: int
    ) -> PublishedStrategy:
        """Refuse draft publication; this fixture only serves load()."""
        del definition, expected_revision
        raise StrategyPublicationError("Catalog fixture is load-only.")

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return the bound publication or fail closed."""
        if strategy_fingerprint_value != self._published.strategy_fingerprint:
            raise StrategyPublicationError("Published strategy was not found.")
        return self._published


def _product() -> MarketProduct:
    """Return BTC-USD venue increments."""
    return MarketProduct(
        product_id="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        price_increment=Decimal("0.01"),
        base_increment=Decimal("0.00000001"),
        quote_increment=Decimal("0.01"),
        base_min_size=Decimal("0.0001"),
        quote_min_size=Decimal("1"),
        trading_enabled=True,
    )


def _always_entry_five_minute() -> StrategyDefinition:
    """Published 5m reference strategy whose entry condition is always true."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["status"] = StrategyStatus.PUBLISHED.value
    payload["timeframe"] = "5m"
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
    payload["execution"]["on_unfilled_entry"] = "cancel"
    return StrategyDefinition.model_validate(payload)


def _five_minute_candles(count: int, *, low_offset: Decimal = Decimal("1")) -> tuple[Candle, ...]:
    """Build a rising 5m series with a configurable low relative to close."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles: list[Candle] = []
    for index in range(count):
        close = Decimal("100") + Decimal(index)
        candles.append(
            Candle(
                starts_at=start + timedelta(minutes=5 * index),
                open=close,
                high=close + Decimal("2"),
                low=close - low_offset,
                close=close,
                volume=Decimal("10"),
            )
        )
    return tuple(candles)


@pytest.mark.anyio
async def test_create_deployment_starts_five_minute_paper_and_fills_on_five_minute_bars() -> None:
    """The same published 5m strategy can paper-start and fill on closed 5m bars."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_five_minute()
    catalog = _Catalog(strategy)
    created = await create_deployment(
        store=store,
        publication_store=catalog,
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    snapshot = await store.get_deployment(created.id)
    warmup = _five_minute_candles(30, low_offset=Decimal("0.01"))
    pending = await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=_product(),
        candles=warmup,
        broker=PaperBroker(),
        store=store,
    )
    assert pending.deployment.phase is RuntimePhase.PENDING_ENTRY
    last = warmup[-1]
    continuation = Candle(
        starts_at=last.starts_at + timedelta(minutes=5),
        open=last.close,
        high=last.close + Decimal("1"),
        low=last.close - Decimal("0.5"),
        close=last.close,
        volume=Decimal("10"),
    )
    filled = await process_closed_bar(
        pending,
        strategy=strategy,
        product=_product(),
        candles=(*warmup, continuation),
        broker=PaperBroker(),
        store=store,
    )
    assert filled.position is not None
    assert filled.fills
    assert filled.position.entered_bar == continuation.starts_at
    assert continuation.starts_at.minute % 5 == 0


def _published_with_htf_filter() -> StrategyDefinition:
    """Published 1h reference strategy with a 6h HTF trend filter."""
    draft = create_reference_draft(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python")
    payload["status"] = StrategyStatus.PUBLISHED.value
    payload["htf_filter"] = {
        "timeframe": "6h",
        "data_requirements": {
            "warmup_bars": 50,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {"id": "htf_ema_fast", "kind": "ema", "input": "close", "parameters": {"period": 20}},
            {"id": "htf_ema_slow", "kind": "ema", "input": "close", "parameters": {"period": 50}},
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_ema_fast"},
                    "operator": "greater_than",
                    "right": {"indicator": "htf_ema_slow"},
                }
            ]
        },
    }
    return StrategyDefinition.model_validate(payload)


@pytest.mark.anyio
async def test_create_deployment_starts_five_minute_live_when_allowed() -> None:
    """A published 5m strategy can arm live when credentials are allowed."""
    store = InMemoryExecutionStore()
    strategy = _always_entry_five_minute()
    catalog = _Catalog(strategy)
    created = await create_deployment(
        store=store,
        publication_store=catalog,
        strategy_fingerprint=strategy_fingerprint(strategy),
        mode=DeploymentMode.LIVE,
        paper_starting_cash=None,
        live_allowed=True,
    )
    assert created.mode is DeploymentMode.LIVE
    assert created.status.value == "running"


@pytest.mark.anyio
async def test_create_deployment_rejects_htf_filter_paper_and_live() -> None:
    """Paper and live must not start HTF-filter strategies until those runtimes bind HTF candles."""
    strategy = _published_with_htf_filter()
    catalog = _Catalog(strategy)
    with pytest.raises(ExecutionConflictError, match="HTF-filter"):
        await create_deployment(
            store=InMemoryExecutionStore(),
            publication_store=catalog,
            strategy_fingerprint=strategy_fingerprint(strategy),
            mode=DeploymentMode.PAPER,
            paper_starting_cash=Decimal("10000"),
            live_allowed=False,
        )
    with pytest.raises(ExecutionConflictError, match="HTF-filter"):
        await create_deployment(
            store=InMemoryExecutionStore(),
            publication_store=catalog,
            strategy_fingerprint=strategy_fingerprint(strategy),
            mode=DeploymentMode.LIVE,
            paper_starting_cash=None,
            live_allowed=True,
        )


def test_evaluate_latest_entry_rejects_htf_filter() -> None:
    """The paper/live signal helper must not ignore an HTF filter."""
    with pytest.raises(ValueError, match="HTF-filter"):
        evaluate_latest_entry(_published_with_htf_filter(), _five_minute_candles(30))
