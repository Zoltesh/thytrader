"""Neutral CFM read composition and catalog units for the staged futures runtime."""

from __future__ import annotations

import contextlib
from decimal import Decimal, DecimalException
from typing import TYPE_CHECKING, Protocol

from thytrader.exchanges.futures_models import FuturesPositionSide
from thytrader.execution.futures_live import FuturesLiveAccount
from thytrader.market_data.cycle_reads import shared_read
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.instruments import MaintenanceWindow
from thytrader.risk.futures_live import LIVE_EVIDENCE_MAX_AGE, FuturesVenueEvidence
from thytrader.trading.ids import utc_now

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from thytrader.exchanges.futures_models import (
        FuturesAccountSnapshotStore,
        FuturesBalanceSummary,
        FuturesPosition,
    )
    from thytrader.exchanges.models import ExchangeOpenOrder
    from thytrader.execution.futures_paper import FuturesObservationReader
    from thytrader.market_data.futures_observations import FuturesInstrumentObservation


class FuturesAccountReader(Protocol):
    """The two read-only account operations needed by every managed live futures cycle."""

    async def balance_summary(self) -> FuturesBalanceSummary:
        """Return USD margin evidence or raise when unreadable."""
        ...

    async def positions(self) -> tuple[FuturesPosition, ...]:
        """Return a complete position read or raise; never a successful partial list."""
        ...


class FuturesOrdersReader(Protocol):
    """Complete account-wide futures order evidence for product exclusivity."""

    async def list_futures_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
        """Return all nonterminal futures orders or raise when completeness is unknown."""
        ...


async def catalog_observation(
    observations: FuturesObservationReader,
    product_id: str,
) -> tuple[FuturesInstrumentObservation, datetime] | None:
    """Share catalog units within a cycle, including broker conversion and admission."""

    async def read() -> tuple[FuturesInstrumentObservation, datetime] | None:
        """Read the exact product through the bound observation store."""
        return await observations.latest_instrument(product_id)

    return await shared_read(("futures_catalog", str(id(observations)), product_id), read)


class _ListedOrder(Protocol):
    """The two fields product exclusivity reads from a venue or mirrored order row."""

    @property
    def product_id(self) -> str | None:
        """The order's product, ``None`` when the venue row did not name one."""
        ...

    @property
    def client_order_id(self) -> str | None:
        """The order's client id, ``None`` when the venue row did not carry one."""
        ...


def external_order_products(
    orders: Iterable[_ListedOrder], managed: frozenset[tuple[str, str]]
) -> frozenset[str] | None:
    """Products with an order no managed book owns by exact (product, client id) match.

    An unclassifiable row (no product, or not a futures product) makes the answer unknown.
    """
    products: set[str] = set()
    for order in orders:
        if order.product_id is None or not is_futures_product_id(order.product_id):
            return None
        # Product ids compare canonically (upper case), as the classification above does.
        product = order.product_id.upper()
        if order.client_order_id is None or (product, order.client_order_id) not in managed:
            products.add(product)
    return frozenset(products)


class CatalogContractSizes:
    """ContractSizeSource backed by the very same catalog that prepares the futures book."""

    def __init__(self, observations: FuturesObservationReader) -> None:
        """Bind a catalog reader, not an exchange mutation capability."""
        self.observations = observations

    async def contract_size(self, product_id: str) -> Decimal | None:
        """Unknown/nonfinite/incorrect-product catalog facts never become one contract."""
        try:
            latest = await catalog_observation(self.observations, product_id)
            if latest is None or latest[0].product_id != product_id:
                return None
            size = Decimal(latest[0].contract_size)
            return size if size.is_finite() and size > 0 else None
        except RuntimeError, ValueError, TypeError, OSError, DecimalException:
            return None


class RecordedFuturesSource:
    """Fresh balances/positions plus read-only mirror controls and complete external orders.

    Constructed only by the worker's futures venue seam (``futures_venue.py``). Two CFM GETs
    are needed; the separate complete futures open-order listing is also required for I3.
    Killswitch facts reuse the persisted mirror and are never renewed by these two GETs.
    """

    def __init__(
        self,
        account: FuturesAccountReader,
        orders: FuturesOrdersReader,
        observations: FuturesObservationReader,
        *,
        mirror: FuturesAccountSnapshotStore | None,
    ) -> None:
        """Keep provider-neutral reads separate from any order-submission authority."""
        self.account = account
        self.orders = orders
        self.observations = observations
        self.mirror = mirror

    async def read_account(
        self,
        managed_client_ids: frozenset[tuple[str, str]],
        product_ids: frozenset[str] = frozenset(),
    ) -> FuturesLiveAccount:
        """Preserve failed components as unknown and use the oldest read's receipt time."""
        balance = None
        positions = None
        with contextlib.suppress(RuntimeError, ValueError, TypeError, OSError, DecimalException):
            balance = await self.account.balance_summary()
        observed_at = utc_now()
        with contextlib.suppress(RuntimeError, ValueError, TypeError, OSError, DecimalException):
            positions = await self.account.positions()
        signed, marks = _positions(positions)
        products = product_ids | frozenset(() if signed is None else signed)
        sizes, maintenance = await self._catalog(products)
        killswitch, killswitch_at = await self._killswitch()
        venue = FuturesVenueEvidence(
            observed_at=observed_at,
            buying_power_usd=_nonnegative(
                None if balance is None else balance.futures_buying_power
            ),
            initial_margin_usd=_nonnegative(None if balance is None else balance.initial_margin),
            positions=signed,
            external_order_products=await self._external(managed_client_ids),
            killswitch_enabled=killswitch,
            killswitch_observed_at=killswitch_at,
            maintenance=maintenance,
            contract_sizes=sizes,
        )
        return FuturesLiveAccount(
            venue=venue,
            marks=marks,
            available_margin_usd=None if balance is None else balance.available_margin,
            liquidation_threshold_usd=None if balance is None else balance.liquidation_threshold,
        )

    async def _external(self, managed: frozenset[tuple[str, str]]) -> frozenset[str] | None:
        """Never infer ownership from a client-id prefix; require an exact durable match."""
        try:
            orders = await self.orders.list_futures_open_orders()
        except RuntimeError, ValueError, TypeError, OSError:
            return None
        return external_order_products(orders, managed)

    async def _killswitch(self) -> tuple[bool | None, datetime | None]:
        """The mirror's killswitch facts and when they were observed; unknown is (None, None).

        An absent/stale/failed mirror window does not prove a disabled venue killswitch. The
        observation time travels with the evidence so admission re-checks its age. The row is
        judged at a time taken after it was read (ADR 0115), so a mirror refresh that lands
        between the balance read and this read is not discarded as future-dated.
        """
        if self.mirror is None:
            return None, None
        try:
            row = await self.mirror.latest()
        except RuntimeError, ValueError, TypeError, OSError:
            return None, None
        now = utc_now()
        if (
            row is None
            or row.observed_at > now
            or now - row.observed_at > LIVE_EVIDENCE_MAX_AGE
            or row.margin_window is None
        ):
            return None, None
        flags = (
            row.margin_window.intraday_killswitch_enabled,
            row.margin_window.enrollment_killswitch_enabled,
        )
        if any(flag is True for flag in flags):
            return True, row.observed_at
        return (False, row.observed_at) if all(flag is False for flag in flags) else (None, None)

    async def _catalog(
        self, products: frozenset[str]
    ) -> tuple[dict[str, Decimal], dict[str, MaintenanceWindow]]:
        """Load exact current sizes and recorded maintenance intervals without a fallback."""
        return await catalog_facts(self.observations, products)


async def catalog_facts(
    observations: FuturesObservationReader, products: frozenset[str]
) -> tuple[dict[str, Decimal], dict[str, MaintenanceWindow]]:
    """Exact contract sizes and recorded maintenance windows, shared by the worker and mirror."""
    sizes: dict[str, Decimal] = {}
    maintenance: dict[str, MaintenanceWindow] = {}
    source = CatalogContractSizes(observations)
    for product in products:
        size = await source.contract_size(product)
        if size is None:
            continue
        sizes[product] = size
        latest = await catalog_observation(observations, product)
        if latest is None:
            continue
        row = latest[0]
        if row.maintenance_starts_at is not None and row.maintenance_ends_at is not None:
            maintenance[product] = MaintenanceWindow(
                starts_at=row.maintenance_starts_at, ends_at=row.maintenance_ends_at
            )
    return sizes, maintenance


def _nonnegative(value: Decimal | None) -> Decimal | None:
    """Invalid USD buying-power/margin evidence is unknown, not zero."""
    return value if value is not None and value.is_finite() and value >= 0 else None


def _positions(
    rows: tuple[FuturesPosition, ...] | None,
) -> tuple[dict[str, Decimal] | None, dict[str, Decimal]]:
    """Whole signed contracts and positive marks; any ambiguous position invalidates the list."""
    if rows is None:
        return None, {}
    signed: dict[str, Decimal] = {}
    marks: dict[str, Decimal] = {}
    for row in rows:
        count = row.number_of_contracts
        if (
            row.product_id in signed
            or not is_futures_product_id(row.product_id)
            or not count.is_finite()
            or count < 0
            or count != count.to_integral_value()
            or row.side not in {FuturesPositionSide.LONG, FuturesPositionSide.SHORT}
        ):
            return None, {}
        signed[row.product_id] = (
            count.copy_negate() if row.side is FuturesPositionSide.SHORT else count
        )
        if (
            row.current_price is not None
            and row.current_price.is_finite()
            and row.current_price > 0
        ):
            marks[row.product_id] = row.current_price
    return signed, marks
