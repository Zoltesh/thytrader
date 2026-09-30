"""Execution-worker venue bindings follow shared Coinbase credentials without restart."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from thytrader.config import Settings
from thytrader.credentials.reload import CoinbaseCredentialReloadStore
from thytrader.credentials.service import settings_with_coinbase
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.paper import PaperBroker
from thytrader.execution.user_feed_state import (
    InMemoryUserOrderFeedStateStore,
    UserOrderFeedStateStore,
)
from thytrader.execution_worker.service import run_execution_worker
from thytrader.execution_worker.venue import (
    ExecutionVenue,
    ExecutionVenueRuntime,
    build_execution_venue,
    credential_identity,
)
from thytrader.execution_worker.venue_feed import run_venue_user_order_feed
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from thytrader.persistence.audit_events import AuditEventStore

_KEY = "organizations/test/apiKeys/one"
_PEM = "-----BEGIN EC PRIVATE KEY-----\nfake\n-----END EC PRIVATE KEY-----"


def _settings(tmp_path: Path) -> Settings:
    """Return settings without Coinbase secrets rooted at a temp credentials dir."""
    return Settings(
        credentials_dir=tmp_path,
        coinbase_api_key_name=None,
        coinbase_api_private_key=None,
    )


def _fake_builder(calls: list[int]) -> Callable[[Settings, int], ExecutionVenue]:
    """Build demo-shaped venues that mark live when secrets exist, recording generations."""

    def build(settings: Settings, generation: int) -> ExecutionVenue:
        calls.append(generation)
        live = settings.coinbase_api_key_name is not None
        return ExecutionVenue(
            market_data=MarketDataService(DemoMarketData()),
            live_broker=PaperBroker() if live else None,
            quote_reader=None,
            jwt_provider=(lambda: "jwt") if live else None,
            source="coinbase" if live else "demo",
            generation=generation,
        )

    return build


def test_demo_venue_has_no_live_broker_without_credentials(tmp_path: Path) -> None:
    """No secrets means demo candles, no live broker, and no user-feed JWT."""
    venue = build_execution_venue(_settings(tmp_path), 0)
    assert venue.source == "demo"
    assert venue.live_broker is None
    assert venue.quote_reader is None
    assert venue.jwt_provider is None
    assert venue.live_enabled is False


def test_coinbase_venue_binds_live_broker_with_credentials(tmp_path: Path) -> None:
    """Secrets bind the Coinbase broker, market data, quote reader, and a JWT provider."""
    settings = settings_with_coinbase(_settings(tmp_path), key_name=_KEY, private_key=_PEM)
    venue = build_execution_venue(settings, 3)
    assert venue.source == "coinbase"
    assert venue.live_broker is not None
    assert venue.quote_reader is not None
    assert venue.jwt_provider is not None
    assert venue.generation == 3


def test_replace_rebuilds_only_when_secret_pair_changes(tmp_path: Path) -> None:
    """Unrelated reloads keep the generation; set and clear each bump it."""
    calls: list[int] = []
    base = _settings(tmp_path)
    runtime = ExecutionVenueRuntime(base, builder=_fake_builder(calls))
    assert runtime.current().generation == 0
    runtime.replace(base)
    assert calls == [0]
    with_keys = settings_with_coinbase(base, key_name=_KEY, private_key=_PEM)
    runtime.replace(with_keys)
    assert runtime.current().generation == 1
    assert runtime.current().live_enabled is True
    runtime.replace(with_keys)
    assert calls == [0, 1]
    runtime.replace(settings_with_coinbase(base, key_name=None, private_key=None))
    cleared = runtime.current()
    assert cleared.generation == 2
    assert cleared.live_broker is None
    assert cleared.source == "demo"


def test_credential_identity_never_equals_secret_values(tmp_path: Path) -> None:
    """The comparison identity is a digest, not the key or PEM text."""
    settings = settings_with_coinbase(_settings(tmp_path), key_name=_KEY, private_key=_PEM)
    identity = credential_identity(settings)
    assert identity is not None
    assert _KEY not in identity
    assert "PRIVATE" not in identity
    assert credential_identity(_settings(tmp_path)) is None


def test_shared_dotenv_write_hot_swaps_execution_venue(tmp_path: Path) -> None:
    """A credentials-volume write (API set/clear) reaches the execution venue on reload."""
    calls: list[int] = []
    base = _settings(tmp_path)
    runtime = ExecutionVenueRuntime(base, builder=_fake_builder(calls))
    env_path = tmp_path / ".env"
    reload_store = CoinbaseCredentialReloadStore(
        env_path=env_path, base_settings=base, on_reload=runtime.replace
    )
    assert runtime.current().live_enabled is False
    env_path.write_text(
        f'THYTRADER_COINBASE_API_KEY_NAME={_KEY}\nTHYTRADER_COINBASE_API_PRIVATE_KEY="fake"\n',
        encoding="utf-8",
    )
    _ = reload_store.current
    assert runtime.current().live_enabled is True
    env_path.write_text(
        "THYTRADER_COINBASE_API_KEY_NAME=\nTHYTRADER_COINBASE_API_PRIVATE_KEY=\n",
        encoding="utf-8",
    )
    _ = reload_store.current
    assert runtime.current().live_enabled is False
    assert runtime.current().generation == 2


class _RecordingRunner:
    """Record each feed generation start and wait for its stop event."""

    def __init__(self) -> None:
        """Start with no recorded generations."""
        self.started: list[tuple[bool, str | None]] = []
        self.stopped = 0

    async def __call__(
        self,
        stop_requested: asyncio.Event,
        *,
        enabled: bool,
        feed_store: UserOrderFeedStateStore,
        jwt_provider: Callable[[], str] | None = None,
        audit_store: AuditEventStore | None = None,
        wake_requested: asyncio.Event | None = None,
    ) -> None:
        """Wait until this generation is stopped."""
        del feed_store, audit_store, wake_requested
        self.started.append((enabled, jwt_provider() if jwt_provider is not None else None))
        await stop_requested.wait()
        self.stopped += 1


@pytest.mark.anyio
async def test_user_feed_restarts_on_credential_generation_change(tmp_path: Path) -> None:
    """Setting then clearing credentials restarts the feed enabled, then disabled, with audit."""
    calls: list[int] = []
    base = _settings(tmp_path)
    runtime = ExecutionVenueRuntime(base, builder=_fake_builder(calls))
    runner = _RecordingRunner()
    audit = InMemoryAuditEventStore()
    stop = asyncio.Event()
    wake = asyncio.Event()
    task = asyncio.create_task(
        run_venue_user_order_feed(
            stop,
            venue_provider=runtime.current,
            feed_store=InMemoryUserOrderFeedStateStore(),
            audit_store=audit,
            wake_requested=wake,
            runner=runner,
            poll_seconds=0.01,
        )
    )
    await asyncio.sleep(0.05)
    runtime.replace(settings_with_coinbase(base, key_name=_KEY, private_key=_PEM))
    await asyncio.sleep(0.05)
    assert wake.is_set()
    runtime.replace(settings_with_coinbase(base, key_name=None, private_key=None))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert runner.started == [(False, None), (True, "jwt"), (False, None)]
    assert runner.stopped == 3
    actions = [event.action for event in await audit.list_recent(limit=10)]
    assert actions.count("execution_venue_reloaded") == 2
    assert "execution_venue_bound" in actions
    details = " ".join(event.detail for event in await audit.list_recent(limit=10))
    assert _KEY not in details
    assert "synthetic demo candles" in details


@pytest.mark.anyio
async def test_execution_worker_reads_the_venue_once_per_cycle(tmp_path: Path) -> None:
    """Each cycle reads one venue generation, so a swap lands between cycles."""
    calls: list[int] = []
    base = _settings(tmp_path)
    runtime = ExecutionVenueRuntime(base, builder=_fake_builder(calls))
    stop = asyncio.Event()
    wake = asyncio.Event()
    seen: list[int] = []

    def provider() -> ExecutionVenue:
        venue = runtime.current()
        seen.append(venue.generation)
        if len(seen) == 1:
            runtime.replace(settings_with_coinbase(base, key_name=_KEY, private_key=_PEM))
            wake.set()
        else:
            stop.set()
        return venue

    initial = runtime.current()
    await asyncio.wait_for(
        run_execution_worker(
            stop,
            store=InMemoryExecutionStore(),
            publication_store=DisabledStrategySnapshotStore(),
            market_data=initial.market_data,
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            interval_seconds=1,
            wake_requested=wake,
            venue_provider=provider,
        ),
        timeout=5,
    )
    assert seen == [0, 1]
