"""Paper start and closed-bar evaluation for a published 5m strategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from thytrader.execution.loop import process_closed_bar
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import DeploymentMode, RuntimePhase
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import create_deployment
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.strategies.authoring import create_reference_draft
from thytrader.strategies.models import StrategyDefinition, StrategyStatus, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy


class _Catalog:
    """Load one published strategy by fingerprint."""

    def __init__(self, definition: StrategyDefinition) -> None:
        """Bind one immutable publication."""
        fingerprint = strategy_fingerprint(definition)
        self._published = PublishedStrategy(strategy_fingerprint=fingerprint, definition=definition)

    async def load(self, strategy_fingerprint_value: str) -> PublishedStrategy:
        """Return the bound publication or fail closed."""
        if strategy_fingerprint_value != self._published.strategy_fingerprint:
            message = "Published strategy was not found."
            raise RuntimeError(message)
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
