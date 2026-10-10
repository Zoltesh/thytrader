"""CFM futures account mirror loop (ADR 0127): independent reads, failure evidence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.exchanges import cfm_fixtures
from thytrader.config import Settings
from thytrader.exchanges.coinbase import CoinbasePaginationError
from thytrader.exchanges.coinbase_cfm import (
    BALANCE_SUMMARY_PATH,
    MARGIN_SETTING_PATH,
    MARGIN_WINDOW_PATH,
    POSITIONS_PATH,
    CoinbaseCfmAccount,
)
from thytrader.exchanges.futures_models import (
    FuturesAccountObservation,
    FuturesAccountStoreUnavailableError,
    FuturesEnablement,
    SpotCollateralBalances,
)
from thytrader.exchanges.models import ExchangeBalance
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailure,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.worker.cli import _ReloadingFuturesReader
from thytrader.worker.futures_mirror import observe_futures_account, run_futures_mirror

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_NOW = datetime(2026, 10, 10, 2, tzinfo=UTC)


class _Transport:
    """GET-only transport double; listed paths fail with an HTTP status."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        """Choose which paths fail."""
        self.failing = failing
        self.calls = 0

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Answer one GET."""
        del params
        self.calls += 1
        if path in self.failing:
            raise CoinbaseHttpStatusError(503, None)
        factories: dict[str, Callable[[], dict[str, Any]]] = {
            BALANCE_SUMMARY_PATH: cfm_fixtures.balance_summary,
            POSITIONS_PATH: cfm_fixtures.positions,
            MARGIN_SETTING_PATH: cfm_fixtures.margin_setting,
            MARGIN_WINDOW_PATH: cfm_fixtures.margin_window,
        }
        return factories[path]()


class _Store:
    """Collect observations, or fail like an unreachable database."""

    def __init__(self, *, fail: bool = False) -> None:
        """Choose success or failure."""
        self.fail = fail
        self.records: list[FuturesAccountObservation] = []
        self.recorded = asyncio.Event()

    async def record(self, observation: FuturesAccountObservation) -> None:
        """Append one observation."""
        self.recorded.set()
        if self.fail:
            raise FuturesAccountStoreUnavailableError("down")
        self.records.append(observation)

    async def latest(self) -> FuturesAccountObservation | None:
        """Newest observation."""
        return self.records[-1] if self.records else None


def test_full_read_is_enabled_with_every_value() -> None:
    """All four reads succeed: enabled, positions known, no failures."""
    observation = asyncio.run(observe_futures_account(CoinbaseCfmAccount(_Transport()), _NOW))
    assert observation.enablement is FuturesEnablement.ENABLED
    assert observation.balance is not None
    assert observation.positions is not None
    assert len(observation.positions) == 1
    assert observation.read_failures == ()


def test_each_failed_read_is_unknown_and_recorded() -> None:
    """A failed balance read makes enablement unknown; a failed position read is not ``()``."""
    failing = frozenset({BALANCE_SUMMARY_PATH, POSITIONS_PATH})
    observation = asyncio.run(
        observe_futures_account(CoinbaseCfmAccount(_Transport(failing)), _NOW)
    )
    assert observation.enablement is FuturesEnablement.UNKNOWN
    assert observation.balance is None
    assert observation.positions is None
    assert observation.read_failures == ("balance_summary:http_503", "positions:http_503")
    assert observation.margin_window is not None


def test_loop_records_each_cycle_and_survives_store_failures() -> None:
    """The mirror records, keeps running on a storage failure, and idles without a reader."""

    async def exercise() -> None:
        """Run one recorded cycle, one failing cycle, and one demo cycle."""
        transport = _Transport()
        reader = CoinbaseCfmAccount(transport)
        for store in (_Store(), _Store(fail=True)):
            stop = asyncio.Event()
            task = asyncio.create_task(
                run_futures_mirror(
                    stop,
                    reader=lambda: reader,
                    store=store,
                    interval_seconds=60,
                    now_factory=lambda: _NOW,
                )
            )
            await store.recorded.wait()
            stop.set()
            await task
        idle_store = _Store()
        stop = asyncio.Event()
        stop.set()
        await run_futures_mirror(stop, reader=lambda: None, store=idle_store)
        assert idle_store.records == []
        assert transport.calls == 8

    asyncio.run(exercise())


class _Spot:
    """Spot listing double: balances, or one of the adapter's redacted failures."""

    def __init__(
        self, balances: tuple[ExchangeBalance, ...] = (), *, error: Exception | None = None
    ) -> None:
        """Hold the canned answer."""
        self.balances = balances
        self.error = error
        self.calls = 0

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Answer one listing."""
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.balances


def _balance(currency: str, available: str, hold: str) -> ExchangeBalance:
    """One spot balance row."""
    return ExchangeBalance(
        currency=currency, name=currency, available=Decimal(available), hold=Decimal(hold)
    )


def test_spot_listing_records_usdc_and_usd_beside_the_cfm_reads() -> None:
    """USDC and USD rows are kept per currency; other assets are ignored; absent is zero."""
    spot = _Spot(
        (
            _balance("USDC", "999.99", "1.5"),
            _balance("BTC", "0.01", "0"),
            _balance("USDC", "10", "0"),
        )
    )
    observation = asyncio.run(observe_futures_account(CoinbaseCfmAccount(_Transport()), _NOW, spot))
    assert observation.spot_balances == SpotCollateralBalances(
        usdc_available=Decimal("1009.99"),
        usdc_hold=Decimal("1.5"),
        usd_available=Decimal(0),
        usd_hold=Decimal(0),
    )
    assert observation.read_failures == ()


@pytest.mark.parametrize(
    ("error", "token"),
    [
        (
            ExchangeReadError(
                ExchangeReadFailure(
                    operation=ExchangeReadOperation.BALANCES,
                    kind=ExchangeReadFailureKind.HTTP,
                    http_status=503,
                )
            ),
            "spot_balances:http_503",
        ),
        (
            ExchangeReadError(
                ExchangeReadFailure(
                    operation=ExchangeReadOperation.BALANCES,
                    kind=ExchangeReadFailureKind.TIMEOUT,
                )
            ),
            "spot_balances:timeout",
        ),
        (CoinbasePaginationError("repeated cursor"), "spot_balances:pagination"),
    ],
)
def test_failed_spot_listing_is_unknown_and_named(error: Exception, token: str) -> None:
    """A failed listing leaves spot balances unknown (never zero) and records a token."""
    observation = asyncio.run(
        observe_futures_account(CoinbaseCfmAccount(_Transport()), _NOW, _Spot(error=error))
    )
    assert observation.spot_balances is None
    assert observation.read_failures == (token,)
    assert observation.enablement is FuturesEnablement.ENABLED


def test_loop_reads_the_spot_listing_each_cycle() -> None:
    """The spot reader is resolved per cycle like the CFM reader."""

    async def exercise() -> None:
        """Run one recorded cycle with a spot reader."""
        store = _Store()
        spot = _Spot((_balance("USDC", "100", "0"),))
        stop = asyncio.Event()
        reader = CoinbaseCfmAccount(_Transport())
        task = asyncio.create_task(
            run_futures_mirror(
                stop,
                reader=lambda: reader,
                store=store,
                spot_reader=lambda: spot,
                interval_seconds=60,
                now_factory=lambda: _NOW,
            )
        )
        await store.recorded.wait()
        stop.set()
        await task
        assert spot.calls == 1
        assert store.records[0].spot_balances is not None
        assert store.records[0].spot_balances.usdc_available == Decimal(100)

    asyncio.run(exercise())


def test_worker_builds_no_readers_without_credentials() -> None:
    """Demo mode has neither the CFM reader nor the spot listing, so nothing is mirrored."""
    readers = _ReloadingFuturesReader(Settings(_env_file=None))
    assert (readers.current(), readers.spot()) == (None, None)
