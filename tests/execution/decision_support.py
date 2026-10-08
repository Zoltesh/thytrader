"""Shared builders for per-bar decision journal tests (ADR 0087)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.decision_journal import (
    decision_journal_scope,
    observe_bar,
    record_bar_decision,
)
from thytrader.execution.loop import process_closed_bar
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import create_deployment
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotError
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.execution.decision_store import InMemoryDecisionJournalStore
    from thytrader.execution.decisions import BarDecision
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.models import DeploymentSnapshot

START = datetime(2026, 3, 2, tzinfo=UTC)
HOUR = timedelta(hours=1)
RSI_AT_LEAST_50: dict[str, object] = {
    "all": [
        {
            "left": {"indicator": "rsi"},
            "operator": "greater_than_or_equal",
            "right": {"literal": "50"},
        }
    ]
}


class Catalog:
    """Load-only StrategySnapshotStore double for one published strategy."""

    def __init__(self, definition: StrategyDefinition) -> None:
        """Bind one immutable publication."""
        fingerprint = strategy_fingerprint(definition)
        self._published = StrategySnapshot(strategy_fingerprint=fingerprint, definition=definition)

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Return the bound publication or fail closed."""
        if strategy_fingerprint_value != self._published.strategy_fingerprint:
            raise StrategySnapshotError("Published strategy was not found.")
        return self._published

    async def record_snapshot(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Refuse new snapshots: this catalog is read-only."""
        del definition
        raise StrategySnapshotError("Read-only test catalog cannot record snapshots.")


def strategy(
    *,
    when: Mapping[str, object] | None = None,
    max_bars_held: int = 96,
    cooldown_bars: int = 3,
    extra_indicators: Sequence[Mapping[str, object]] = (),
) -> StrategyDefinition:
    """Template 1h BTC-USD strategy with one replaceable entry rule."""
    draft = create_template_strategy(now=datetime(2026, 1, 1, tzinfo=UTC))
    payload = draft.model_dump(mode="python", by_alias=True)
    payload["indicators"] = [*payload["indicators"], *(dict(item) for item in extra_indicators)]
    payload["entry"]["when"] = dict(when or RSI_AT_LEAST_50)
    payload["entry"]["cooldown_bars"] = cooldown_bars
    payload["exits"]["time_exit"] = {"max_bars_held": max_bars_held}
    return StrategyDefinition.model_validate(payload)


def product() -> MarketProduct:
    """BTC-USD venue increments."""
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


def candles(count: int, *, step: str = "1", base: str = "100") -> tuple[Candle, ...]:
    """A steady trend of 1h bars: positive ``step`` rises, negative falls."""
    delta = Decimal(step)
    rows: list[Candle] = []
    for index in range(count):
        close = Decimal(base) + delta * index
        rows.append(
            Candle(
                starts_at=START + HOUR * index,
                open=close - delta,
                high=max(close, close - delta) + Decimal("0.5"),
                low=min(close, close - delta) - Decimal("0.5"),
                close=close,
                volume=Decimal("10"),
            )
        )
    return tuple(rows)


def next_candle(
    previous: Candle, *, close: str, high: str | None = None, low: str | None = None
) -> Candle:
    """One more 1h bar after ``previous`` with explicit geometry."""
    value = Decimal(close)
    return Candle(
        starts_at=previous.starts_at + HOUR,
        open=previous.close,
        high=Decimal(high) if high is not None else max(value, previous.close) + Decimal("0.5"),
        low=Decimal(low) if low is not None else min(value, previous.close) - Decimal("0.5"),
        close=value,
        volume=Decimal("10"),
    )


async def paper_book(
    definition: StrategyDefinition,
) -> tuple[InMemoryExecutionStore, DeploymentSnapshot]:
    """Create one running paper deployment for the strategy."""
    store = InMemoryExecutionStore()
    created = await create_deployment(
        store=store,
        publication_store=Catalog(definition),
        strategy_fingerprint=strategy_fingerprint(definition),
        mode=DeploymentMode.PAPER,
        paper_starting_cash=Decimal("10000"),
        live_allowed=False,
    )
    return store, await store.get_deployment(created.id)


async def journaled_bar(
    snapshot: DeploymentSnapshot,
    *,
    definition: StrategyDefinition,
    window: Sequence[Candle],
    store: InMemoryExecutionStore,
    journal: InMemoryDecisionJournalStore,
    risk_policy: RiskPolicyDefinition | None = None,
    allow_new_entries: bool = True,
) -> tuple[DeploymentSnapshot, BarDecision]:
    """Process one closed bar exactly like the worker and return its journal row."""
    candle = window[-1]
    with decision_journal_scope(journal), observe_bar() as observations:
        after = await process_closed_bar(
            snapshot,
            strategy=definition,
            product=product(),
            candles=window,
            broker=PaperBroker(),
            store=store,
            risk_policy=risk_policy,
            allow_new_entries=allow_new_entries,
        )
        await record_bar_decision(
            strategy=definition,
            product_id="BTC-USD",
            candle=candle,
            before=snapshot,
            after=after,
            observations=observations,
            allow_new_entries=allow_new_entries,
        )
    decision = next(
        row
        for row in journal.rows()
        if row.deployment_id == snapshot.deployment.id and row.bar_starts_at == candle.starts_at
    )
    return after, decision
