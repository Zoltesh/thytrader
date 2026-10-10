"""Faster cycles decide exactly as before on a fixture fleet (ADR 0131).

The cycle now reloads the entry gate's fleet evidence with one batched read and shares
identical product, preview and fee-tier reads within a cycle. These tests run the same
fleet twice from identical state, once through the original per-book reload with no
shared reads and once through the new path, and require identical decisions, orders,
intents and book state, with fewer reads.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.decision_support import strategy
from tests.worker_patching import patch_worker_global
from thytrader.exchanges.fees import FeeProfile
from thytrader.execution.decision_journal import decision_journal_scope
from thytrader.execution.decision_store import InMemoryDecisionJournalStore
from thytrader.execution.paper import PaperBroker
from thytrader.execution.service import create_deployment
from thytrader.execution_worker.live_sizing import _live_fee_profile
from thytrader.execution_worker.service import _risk_snapshots, _run_cycle
from thytrader.market_data.cycle_reads import CycleReads, cycle_reads_scope
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.service import MarketDataService
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotError
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode, DeploymentStatus, with_runtime

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from uuid import UUID

    from thytrader.execution.decisions import BarDecision
    from thytrader.market_data.models import MarketDataPreview
    from thytrader.trading.models import DeploymentSnapshot

pytestmark = pytest.mark.anyio

_ALWAYS = {
    "all": [
        {"left": {"literal": "1"}, "operator": "greater_than_or_equal", "right": {"literal": "0"}}
    ]
}


class _Catalog:
    """Load-only snapshot store for several published strategies."""

    def __init__(self, definitions: Sequence[StrategyDefinition]) -> None:
        """Index each definition by fingerprint."""
        self._published = {
            strategy_fingerprint(item): StrategySnapshot(
                strategy_fingerprint=strategy_fingerprint(item), definition=item
            )
            for item in definitions
        }

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Return one publication or fail closed."""
        found = self._published.get(strategy_fingerprint_value)
        if found is None:
            raise StrategySnapshotError("Published strategy was not found.")
        return found

    async def record_snapshot(self, definition: StrategyDefinition) -> StrategySnapshot:
        """Refuse new snapshots."""
        del definition
        raise StrategySnapshotError("Read-only test catalog.")


class _BatchStore(InMemoryExecutionStore):
    """In-memory store that also offers the batched fleet read, counting its calls."""

    def __init__(self) -> None:
        """Start empty with zero batched reads."""
        super().__init__()
        self.batch_reads = 0

    async def get_deployments(
        self, deployment_ids: Sequence[UUID]
    ) -> tuple[DeploymentSnapshot, ...]:
        """Return the same snapshots ``get_deployment`` returns, in order."""
        self.batch_reads += 1
        return tuple([await self.get_deployment(item) for item in deployment_ids])


class _CountingDemo(DemoMarketData):
    """Demo candles that count preview reads."""

    def __init__(self) -> None:
        """Start with zero reads."""
        super().__init__()
        self.previews = 0

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Count, then delegate."""
        self.previews += 1
        return await super().get_recent_preview(product_id, interval, now)


def _always_entry(product_id: str, timeframe: str, number: int) -> StrategyDefinition:
    """Strategy ``number``: the template on one product whose entry rule always matches."""
    payload = strategy(when=_ALWAYS).model_dump(mode="python", by_alias=True)
    payload["strategy_id"] = uuid7(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=number))
    payload["sizing"]["max_quote_notional"] = "500"
    payload["timeframe"] = timeframe
    payload["instrument"] = {
        "product_id": product_id,
        "base_currency": product_id.split("-", maxsplit=1)[0],
        "quote_currency": "USD",
    }
    return StrategyDefinition.model_validate(payload)


async def _fleet(
    store: InMemoryExecutionStore, definitions: Sequence[StrategyDefinition]
) -> list[UUID]:
    """Start one book per strategy, then pause one and stop another."""
    catalog = _Catalog(definitions)
    ids: list[UUID] = []
    for definition in definitions:
        created = await create_deployment(
            store=store,
            publication_store=catalog,
            strategy_fingerprint=strategy_fingerprint(definition),
            mode=DeploymentMode.PAPER,
            paper_starting_cash=Decimal("10000"),
            live_allowed=False,
        )
        ids.append(created.id)
    for deployment_id, status in (
        (ids[1], DeploymentStatus.PAUSED),
        (ids[2], DeploymentStatus.STOPPED),
    ):
        current = (await store.get_deployment(deployment_id)).deployment
        await store.save_deployment(
            with_runtime(current, updated_at=current.updated_at, status=status)
        )
    return ids


async def _policy() -> InMemoryRiskPolicyStore:
    """A published policy whose open-position cap binds inside one cycle."""
    store = InMemoryRiskPolicyStore()
    await store.publish(
        compiled_default_risk_policy().model_copy(
            update={"version": 2, "max_concurrent_open_positions": 2}
        )
    )
    return store


@contextmanager
def _legacy_scope(reads: CycleReads) -> Iterator[None]:
    """Stand-in for ``cycle_reads_scope`` that shares nothing, like the cycle before ADR 0131."""
    del reads
    yield


async def _run(
    store: InMemoryExecutionStore,
    definitions: Sequence[StrategyDefinition],
    provider: _CountingDemo,
    journal: InMemoryDecisionJournalStore,
) -> None:
    """Run two worker cycles on deterministic demo candles."""
    risk = await _policy()
    for _cycle in range(2):
        with decision_journal_scope(journal):
            await _run_cycle(
                store=store,
                publication_store=_Catalog(definitions),
                market_data=MarketDataService(provider),
                paper_broker=PaperBroker(),
                live_broker=None,
                quote_reader=None,
                risk_store=risk,
            )


def _book_state(snapshot: DeploymentSnapshot) -> tuple[object, ...]:
    """Identity-free trading facts of one book."""
    deployment = snapshot.deployment
    return (
        deployment.status,
        deployment.phase,
        deployment.cash,
        deployment.last_evaluated_bar,
        deployment.last_signal,
        deployment.mismatch_detail,
        tuple(
            sorted(
                (intent.purpose.value, str(intent.quantity), str(intent.price))
                for intent in snapshot.intents
            )
        ),
        tuple(
            sorted(
                (order.side.value, order.status.value, str(order.price), str(order.quantity))
                for order in snapshot.orders
            )
        ),
        tuple(sorted((str(fill.quantity), str(fill.price)) for fill in snapshot.fills)),
        tuple(sorted((p.product_id, str(p.quantity)) for p in snapshot.positions)),
    )


def _decision(row: BarDecision) -> tuple[object, ...]:
    """Identity- and clock-free content of one journal row."""
    dumped = row.model_dump(
        mode="json",
        exclude={"deployment_id", "evaluated_at", "intent_id", "order_ids", "orders", "fills"},
    )
    return (row.bar_starts_at, row.product_id, repr(sorted(dumped.items())))


async def test_new_cycle_decides_exactly_like_the_original_on_a_fixture_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same decisions, orders, intents and book state; fewer fleet reloads and previews."""
    definitions = (
        _always_entry("BTC-USD", "1h", 1),
        _always_entry("ETH-USD", "1h", 2),
        _always_entry("BTC-USD", "4h", 3),
        _always_entry("BTC-USD", "1h", 4),
        _always_entry("ETH-USD", "1h", 5),
        _always_entry("SOL-USD", "1h", 6),
    )
    original = InMemoryExecutionStore()
    ids = await _fleet(original, definitions)
    batched = _BatchStore()
    for name in ("deployments", "intents", "orders", "fills", "positions", "instrument_runtimes"):
        setattr(batched, name, copy.deepcopy(getattr(original, name)))

    legacy_provider = _CountingDemo()
    legacy_journal = InMemoryDecisionJournalStore()
    with monkeypatch.context() as patch:
        patch_worker_global(patch, "cycle_reads_scope", _legacy_scope)
        await _run(original, definitions, legacy_provider, legacy_journal)

    new_provider = _CountingDemo()
    new_journal = InMemoryDecisionJournalStore()
    await _run(batched, definitions, new_provider, new_journal)

    legacy_books = [_book_state(await original.get_deployment(item)) for item in ids]
    new_books = [_book_state(await batched.get_deployment(item)) for item in ids]
    assert new_books == legacy_books
    entered = [state for state in legacy_books if state[6]]
    assert 0 < len(entered) < len(ids), "the open-position cap must bind inside the cycle"
    legacy_rows = sorted(
        (ids.index(row.deployment_id), _decision(row)) for row in legacy_journal.rows()
    )
    new_rows = sorted((ids.index(row.deployment_id), _decision(row)) for row in new_journal.rows())
    assert new_rows == legacy_rows
    assert legacy_rows
    visited = len(ids)
    assert batched.batch_reads == 2 * (visited + 1)
    assert new_provider.previews < legacy_provider.previews


async def test_risk_snapshots_batched_read_equals_per_book_reads() -> None:
    """The batched fleet read returns exactly the per-book snapshots, in order."""
    definitions = (
        _always_entry("BTC-USD", "1h", 1),
        _always_entry("ETH-USD", "1h", 2),
        _always_entry("SOL-USD", "1h", 3),
    )
    store = _BatchStore()
    await _fleet(store, definitions)
    deployments = await store.list_deployments()
    batched = await _risk_snapshots(store, deployments)
    per_book = await _risk_snapshots(_PlainView(store), deployments)
    assert batched == per_book
    assert store.batch_reads == 1


class _PlainView(InMemoryExecutionStore):
    """Expose another store's records without the batched read."""

    def __init__(self, source: InMemoryExecutionStore) -> None:
        """Share the source's record maps."""
        super().__init__()
        self.deployments = source.deployments
        self.intents = source.intents
        self.orders = source.orders
        self.fills = source.fills
        self.positions = source.positions
        self.instrument_runtimes = source.instrument_runtimes


async def test_shared_reads_never_store_failures_and_key_previews_by_closed_bar() -> None:
    """A failed read is retried by the next caller; a new closed bar is a new key."""
    reads = CycleReads()
    calls = 0

    async def flaky() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("venue down")
        return calls

    with cycle_reads_scope(reads):
        with pytest.raises(OSError, match="venue down"):
            await reads.read(("k",), flaky)
        assert await reads.read(("k",), flaky) == 2
        assert await reads.read(("k",), flaky) == 2
    assert calls == 2
    assert reads.hits == 1
    provider = _CountingDemo()
    service = MarketDataService(provider)
    with cycle_reads_scope(CycleReads()):
        first = await service.get_preview("BTC-USD", CandleInterval.ONE_HOUR)
        second = await service.get_preview("BTC-USD", CandleInterval.ONE_HOUR)
        await service.get_preview("ETH-USD", CandleInterval.ONE_HOUR)
    assert first is second
    assert provider.previews == 2
    await service.get_preview("BTC-USD", CandleInterval.ONE_HOUR)
    assert provider.previews == 3


class _FeeReader:
    """Quote reader double that counts fee-tier reads and fails the first one."""

    def __init__(self) -> None:
        """Start with zero reads."""
        self.reads = 0

    async def list_balances(self) -> tuple[()]:
        """No balances are needed here."""
        return ()

    async def get_fee_profile(self) -> FeeProfile:
        """Fail once like a venue blip, then return one fixed tier."""
        self.reads += 1
        if self.reads == 1:
            raise OSError("venue down")
        return FeeProfile(
            taker_fee_rate=Decimal("0.006"),
            maker_fee_rate=Decimal("0.004"),
            usd_volume_30d=Decimal("0"),
            fee_tier="Intro 1",
            as_of=datetime(2026, 10, 10, tzinfo=UTC),
        )


async def test_fee_tier_is_read_once_per_cycle_and_failures_are_retried() -> None:
    """A failed read still yields None for that book and the next book reads again."""
    reader = _FeeReader()
    with cycle_reads_scope(CycleReads()):
        assert await _live_fee_profile(reader) is None
        first = await _live_fee_profile(reader)
        second = await _live_fee_profile(reader)
    assert first is not None
    assert first is second
    assert reader.reads == 2
    assert await _live_fee_profile(reader) is not None
    assert reader.reads == 3
