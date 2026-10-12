"""Injected live futures evidence, distinct from approval to wire a production venue.

There is no default source or approval boolean. The process must explicitly provide an
account reader and broker. Production composition remains spot-only pending ADR 0134 probes.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from thytrader.execution.exit_guards import active_orders
from thytrader.execution.futures_venue_check import PositionConfirmations, compare_position
from thytrader.market_data.cycle_reads import shared_read
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.ids import utc_now
from thytrader.trading.models import DeploymentMode, DeploymentStatus, IntentPurpose, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from decimal import Decimal

    from thytrader.risk.futures_live import FuturesVenueEvidence
    from thytrader.trading.models import DeploymentSnapshot


@dataclass(frozen=True, slots=True)
class FuturesLiveAccount:
    """Account evidence in USD; marks are journal/monitor inputs, never execution prices."""

    venue: FuturesVenueEvidence
    marks: Mapping[str, Decimal]
    available_margin_usd: Decimal | None
    liquidation_threshold_usd: Decimal | None

    def __post_init__(self) -> None:
        """Freeze a defensive copy; absent or invalid marks remain unknown."""
        object.__setattr__(
            self,
            "marks",
            MappingProxyType(
                {key: value for key, value in self.marks.items() if value.is_finite() and value > 0}
            ),
        )


class FuturesLiveSource(Protocol):
    """Provider-neutral source of fresh CFM facts and external-order exclusivity evidence."""

    async def read_account(
        self,
        managed_client_ids: frozenset[tuple[str, str]],
        product_ids: frozenset[str] = frozenset(),
    ) -> FuturesLiveAccount:
        """Read balance and positions once; never manufacture successful missing evidence."""
        ...


@dataclass(slots=True)
class FuturesLiveRuntime:
    """Explicit injected source and restart-local I9 counters, not venue-acceptance authority."""

    source: FuturesLiveSource
    confirmations: PositionConfirmations = field(default_factory=PositionConfirmations)


_RUNTIME: ContextVar[FuturesLiveRuntime | None] = ContextVar("live_futures_runtime", default=None)
_ACCOUNT: ContextVar[FuturesLiveAccount | None] = ContextVar("live_futures_account", default=None)


@contextmanager
def live_futures_runtime_scope(runtime: FuturesLiveRuntime | None) -> Iterator[None]:
    """Bind staged runtime dependencies; production supplies None until probes are accepted."""
    token = _RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


@contextmanager
def live_futures_account_scope(account: FuturesLiveAccount | None) -> Iterator[None]:
    """Limit one account observation to the book currently being processed."""
    token = _ACCOUNT.set(account)
    try:
        yield
    finally:
        _ACCOUNT.reset(token)


def current_live_futures_account() -> FuturesLiveAccount | None:
    """The current book's observation; missing never means flat or funded."""
    return _ACCOUNT.get()


def current_live_futures_runtime() -> FuturesLiveRuntime | None:
    """The explicit runtime dependency, absent in the default production worker."""
    return _RUNTIME.get()


def admission_venue(
    account: FuturesLiveAccount | None,
    portfolio: Sequence[DeploymentSnapshot],
) -> FuturesVenueEvidence | None:
    """A newer risk-increasing intent invalidates shared pre-submit buying-power authority.

    We deliberately wait for the next cycle rather than sum currencies or fabricate venue
    reservations. Reductions do not call this admission-only guard.
    """
    if account is None:
        return None
    for book in portfolio:
        if book.deployment.mode is not DeploymentMode.LIVE or not is_futures_product_id(
            book.deployment.product_id
        ):
            continue
        if any(
            intent.purpose is IntentPurpose.ENTRY and intent.created_at >= account.venue.observed_at
            for intent in book.intents
        ):
            return None
    return account.venue


def managed_live_futures(snapshot: DeploymentSnapshot) -> bool:
    """Exclude completed stopped books; retain pending flat verification and orphan orders."""
    deployment = snapshot.deployment
    return (
        deployment.mode is DeploymentMode.LIVE
        and is_futures_product_id(deployment.product_id)
        and (
            deployment.status is not DeploymentStatus.STOPPED
            or snapshot.position is not None
            or bool(active_orders(snapshot))
            or unsettled_fill_evidence(snapshot)
            or bool(unprojected_inventory_products(snapshot))
            or any(
                intent.status in {OrderStatus.PENDING, OrderStatus.UNKNOWN}
                and not any(order.intent_id == intent.id for order in snapshot.orders)
                for intent in snapshot.intents
            )
            or deployment.futures_derisk_pending
            or deployment.futures_flat_confirmation_after is not None
        )
    )


def futures_decisions_deferred(snapshot: DeploymentSnapshot) -> bool:
    """Do not re-rest protection or settle while a durable futures safety operation waits."""
    if (
        snapshot.deployment.futures_derisk_pending
        or snapshot.deployment.futures_flat_confirmation_after is not None
    ):
        return True
    detail = snapshot.deployment.mismatch_detail or ""
    if detail.startswith("FUTURES_POSITION_MISMATCH"):
        account = current_live_futures_account()
        sample = compare_position(
            snapshot, None if account is None else account.venue, now=utc_now()
        )
        return sample is None or not sample.matches
    return False


async def prepare_live_futures_book(
    snapshot: DeploymentSnapshot, *, portfolio: Sequence[DeploymentSnapshot]
) -> FuturesLiveAccount | None:
    """Share successful account reads only while supervising a managed live futures book."""
    runtime = _RUNTIME.get()
    if (
        snapshot.deployment.mode is not DeploymentMode.LIVE
        or not is_futures_product_id(snapshot.deployment.product_id)
        or runtime is None
        or not managed_live_futures(snapshot)
    ):
        return None
    managed = frozenset(
        (order.product_id or book.deployment.product_id, order.client_order_id)
        for book in portfolio
        if book.deployment.mode is DeploymentMode.LIVE
        for order in book.orders
    )

    async def read() -> FuturesLiveAccount:
        """Read this credential generation's account with complete managed identities."""
        products = frozenset(
            book.deployment.product_id
            for book in portfolio
            if is_futures_product_id(book.deployment.product_id)
        )
        return await runtime.source.read_account(managed, products)

    return await shared_read(("cfm_live_account", str(id(runtime.source))), read)
