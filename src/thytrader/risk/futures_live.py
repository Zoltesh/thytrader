"""The live CFM futures gate: policy opt-in, product exclusivity and venue evidence.

ADR 0134 §2, slice P2-3. Nothing here is reachable yet: every start surface still refuses a
live futures start with ``FUTURES_LIVE_UNSUPPORTED`` until P2-7, and no worker builds live
futures venue evidence until P2-4. The gate admits starts and risk-increasing entries only;
exits, protection and the margin monitor never reach it.

- **I2 opt-in.** ``futures.live_enabled: true`` (else ``FUTURES_LIVE_DISABLED``),
  ``futures.live_capital_usd`` (else ``FUTURES_LIVE_CAPITAL_UNSET``) and the product on
  ``futures.product_allowlist`` (else ``PRODUCT_NOT_ALLOWLISTED``).
- **I3 exclusivity.** At most one occupied live futures book per product
  (``FUTURES_PRODUCT_OCCUPIED``), and no external CFM position or nonterminal external
  futures order on it (``FUTURES_EXTERNAL_POSITION_ON_PRODUCT``).
- **I4.** Perp-style contracts only (``FUTURES_LIVE_DATED_UNSUPPORTED``).
- **Envelope.** The live allocations of occupied live futures books plus the new one fit in
  ``live_capital_usd`` (``FUTURES_LIVE_CAPITAL_EXCEEDED``).
- **I5.** An order and the resulting position hold at most the effective live
  ``max_order_contracts`` (1 while unset; ``FUTURES_ORDER_CONTRACTS_EXCEEDED``).
- **I8.** Venue evidence at most 180 s old (else ``FUTURES_COLLATERAL_UNKNOWN``): buying
  power covers the proposed initial margin (``FUTURES_BUYING_POWER_SHORT``), the declared L3
  reserve covers (current + proposed initial margin) x haircut
  (``FUTURES_COLLATERAL_RESERVE_SHORT``), the venue killswitch is off
  (``FUTURES_VENUE_KILLSWITCH``) and no recorded maintenance window is pending or in
  progress (``FUTURES_VENUE_MAINTENANCE``).
- **I11.** The bound contract size equals the catalog's (``FUTURES_CONTRACT_DRIFT``).

Amounts are USD. The reserve check is a yes/no threshold under the declared peg (ADR 0129);
USD and USDC are never added (I12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.futures_collateral import MIRROR_STALE_AFTER
from thytrader.risk.gate_common import _deny
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import (
    DeploymentMode,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.evaluation.futures_spec import InstrumentContract
    from thytrader.market_data.instruments import MaintenanceWindow
    from thytrader.risk.futures_policy import FuturesRiskPolicy
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
    from thytrader.trading.futures_book import FuturesBookState
    from thytrader.trading.futures_sizing import FuturesSide
    from thytrader.trading.models import Deployment, DeploymentSnapshot

LIVE_EVIDENCE_MAX_AGE = MIRROR_STALE_AFTER
"""How old venue evidence may be for a live futures admission (180 s, I8)."""


@dataclass(frozen=True, slots=True)
class FuturesVenueEvidence:
    """One venue read of the CFM account plus catalog facts; ``None`` is unknown.

    ``positions`` maps each product with an open CFM position to its signed contract count
    (short is negative); ``None`` means the position read failed. ``external_order_products``
    are products with a nonterminal futures order that carries no managed client id;
    ``None`` means the open-order read failed. ``killswitch_enabled`` is True when the venue
    reports any futures killswitch enabled. ``maintenance`` holds the recorded maintenance
    window of each product that has one, and ``contract_sizes`` the catalog contract size of
    each product the catalog lists. Amounts are USD.
    """

    observed_at: datetime
    buying_power_usd: Decimal | None
    initial_margin_usd: Decimal | None
    positions: Mapping[str, Decimal] | None
    external_order_products: frozenset[str] | None
    killswitch_enabled: bool | None
    maintenance: Mapping[str, MaintenanceWindow]
    contract_sizes: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        """Reject malformed venue facts and freeze defensive copies of read evidence."""
        if self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("Futures venue evidence requires an aware UTC timestamp")
        _validate_venue_state(self.killswitch_enabled, self.maintenance)
        for amount in (self.buying_power_usd, self.initial_margin_usd):
            if amount is not None and (not _finite_decimal(amount) or amount < 0):
                raise ValueError("Futures venue amounts must be finite and nonnegative")
        for size in self.contract_sizes.values():
            if not _finite_decimal(size) or size <= 0:
                raise ValueError("Catalog contract sizes must be finite and positive")
        if self.positions is not None:
            for count in self.positions.values():
                if not _finite_decimal(count) or count != count.to_integral_value():
                    raise ValueError("Venue positions must be whole finite contracts")
            object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))
        if self.external_order_products is not None:
            object.__setattr__(
                self, "external_order_products", frozenset(self.external_order_products)
            )
        object.__setattr__(self, "contract_sizes", MappingProxyType(dict(self.contract_sizes)))
        object.__setattr__(self, "maintenance", MappingProxyType(dict(self.maintenance)))

    def is_fresh(self, as_of: datetime) -> bool:
        """Whether the read is at most :data:`LIVE_EVIDENCE_MAX_AGE` old at ``as_of``."""
        return (
            as_of.utcoffset() == timedelta(0)
            and self.observed_at <= as_of
            and as_of - self.observed_at <= LIVE_EVIDENCE_MAX_AGE
        )


@dataclass(frozen=True, slots=True)
class LiveFuturesStart:
    """What a live futures start states: its USD allocation, contract and venue evidence."""

    allocated_capital_usd: Decimal | None
    contract: InstrumentContract | None
    venue: FuturesVenueEvidence | None
    as_of: datetime
    proposed_initial_margin_usd: Decimal | None = None


def live_futures_opt_in_verdict(
    policy: RiskPolicyDefinition, product_id: str
) -> RiskVerdict | None:
    """I2: live futures enabled, a live USD envelope, and the product allowlisted."""
    futures = policy.futures
    if futures is None or not futures.live_opted_in:
        return _deny(
            RiskReasonCode.FUTURES_LIVE_DISABLED,
            "Live futures are off: publish the risk policy with futures.live_enabled true.",
        )
    if futures.live_capital_usd is None:
        return _deny(
            RiskReasonCode.FUTURES_LIVE_CAPITAL_UNSET,
            "Set futures.live_capital_usd, the USD envelope live futures books draw from.",
        )
    if not futures.allowlists(product_id):
        return _deny(
            RiskReasonCode.PRODUCT_NOT_ALLOWLISTED,
            f"{product_id} is not on futures.product_allowlist; live futures need it listed.",
        )
    return None


def live_futures_deployment_verdict(
    policy: RiskPolicyDefinition,
    *,
    covered: Sequence[str],
    deployments: Sequence[Deployment],
    start: LiveFuturesStart | None,
) -> RiskVerdict | None:
    """Admit a live futures start (I2, I3, I4 and the live envelope), or name the objection.

    ``start`` carries the allocation, the contract and venue evidence; while a caller has
    none (every caller until P2-7), the start is denied, never assumed safe.
    """
    if len(covered) != 1:
        return _deny(
            RiskReasonCode.FUTURES_LIVE_UNSUPPORTED,
            "A live futures deployment trades exactly one contract.",
        )
    product_id = covered[0]
    checks: tuple[Callable[[], RiskVerdict | None], ...] = (
        lambda: live_futures_opt_in_verdict(policy, product_id),
        lambda: _occupied_verdict(product_id, deployments, own=None),
        lambda: _envelope_verdict(policy, deployments, start),
        lambda: _contract_kind_verdict(None if start is None else start.contract),
        lambda: _binding_product_verdict(product_id, None if start is None else start.contract),
        lambda: _start_venue_verdict(policy, product_id, start),
    )
    return _first(checks)


def live_futures_entry_verdict(
    policy: RiskPolicyDefinition,
    *,
    proposed: ProposedEntry,
    book: DeploymentSnapshot,
    state: FuturesBookState,
    snapshots: Sequence[DeploymentSnapshot],
    venue: FuturesVenueEvidence | None,
    as_of: datetime | None,
) -> RiskVerdict | None:
    """Admit one live futures entry by a loaded book (I2-I5, I8, I11), or name the objection.

    The book's binding and margin terms are known (the caller checked them first).
    """
    binding = state.binding
    margin = state.margin
    if binding is None or margin is None:
        return _deny(
            RiskReasonCode.FUTURES_CONTRACT_UNBOUND,
            "The live futures book's contract binding or margin terms are not loaded.",
        )
    contract_size = Decimal(binding.contract.contract_size)
    side: FuturesSide = proposed.side or state.side
    if not proposed.notional.is_finite() or proposed.notional <= 0:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
            "A finite positive notional is required to evaluate proposed initial margin.",
        )
    initial = proposed.notional * margin.initial_rate(side)
    checks: tuple[Callable[[], RiskVerdict | None], ...] = (
        lambda: live_futures_opt_in_verdict(policy, proposed.product_id),
        lambda: _contract_kind_verdict(binding.contract),
        lambda: _binding_product_verdict(proposed.product_id, binding.contract),
        lambda: _occupied_verdict(
            proposed.product_id,
            tuple(item.deployment for item in snapshots),
            own=book.deployment.id,
        ),
        lambda: _evidence_verdict(venue, as_of),
        lambda: _drift_verdict(proposed.product_id, contract_size, venue),
        lambda: _external_entry_verdict(proposed.product_id, book, contract_size, venue),
        lambda: _contracts_verdict(policy, proposed, book, contract_size),
        lambda: _venue_state_verdict(proposed.product_id, venue, as_of),
        lambda: _margin_capacity_verdict(policy, venue, initial),
    )
    return _first(checks)


def _first(checks: Sequence[Callable[[], RiskVerdict | None]]) -> RiskVerdict | None:
    """The first objection of ``checks``, in order."""
    for check in checks:
        verdict = check()
        if verdict is not None:
            return verdict
    return None


def _occupied_verdict(
    product_id: str, deployments: Sequence[Deployment], *, own: UUID | None
) -> RiskVerdict | None:
    """I3: one occupied live futures book per product."""
    for item in deployments:
        if (
            item.id != own
            and item.mode is DeploymentMode.LIVE
            and occupies_running_slot(item)
            and item.product_id == product_id
        ):
            return _deny(
                RiskReasonCode.FUTURES_PRODUCT_OCCUPIED,
                f"Live futures book {item.id} already trades {product_id}; one book per product.",
            )
    return None


def _envelope_verdict(
    policy: RiskPolicyDefinition,
    deployments: Sequence[Deployment],
    start: LiveFuturesStart | None,
) -> RiskVerdict | None:
    """The live allocations, the new one included, fit in ``live_capital_usd``."""
    futures = policy.futures
    allocated = None if start is None else start.allocated_capital_usd
    if futures is None or futures.live_capital_usd is None:
        return None
    if allocated is None or not allocated.is_finite() or allocated <= 0:
        return _deny(
            RiskReasonCode.FUTURES_LIVE_CAPITAL_EXCEEDED,
            "A live futures start needs a positive USD allocation.",
        )
    books = tuple(
        item
        for item in deployments
        if item.mode is DeploymentMode.LIVE
        and occupies_running_slot(item)
        and is_futures_product_id(item.product_id)
    )
    if any(
        item.allocated_capital is None
        or not item.allocated_capital.is_finite()
        or item.allocated_capital <= 0
        for item in books
    ):
        return _deny(
            RiskReasonCode.FUTURES_LIVE_CAPITAL_EXCEEDED,
            "A running live futures book has no recorded allocation; the envelope is unknown.",
        )
    committed = sum((item.allocated_capital or Decimal(0) for item in books), start=Decimal(0))
    capital = Decimal(futures.live_capital_usd)
    if committed + allocated > capital:
        return _deny(
            RiskReasonCode.FUTURES_LIVE_CAPITAL_EXCEEDED,
            f"Live futures allocations {committed} + {allocated} USD would exceed "
            f"futures.live_capital_usd {capital} USD.",
        )
    return None


def _finite_decimal(value: object) -> bool:
    """Only exact finite decimal evidence can enter financial arithmetic."""
    return isinstance(value, Decimal) and value.is_finite()


def _validate_venue_state(
    killswitch: bool | None, maintenance: Mapping[str, MaintenanceWindow]
) -> None:
    """Require explicit boolean state and valid UTC maintenance intervals."""
    if killswitch is not None and not isinstance(killswitch, bool):
        raise ValueError("Venue killswitch must be boolean or unknown")
    for window in maintenance.values():
        if (
            window.starts_at.utcoffset() != timedelta(0)
            or window.ends_at.utcoffset() != timedelta(0)
            or window.ends_at <= window.starts_at
        ):
            raise ValueError("Venue maintenance requires an increasing aware UTC interval")


def _binding_product_verdict(
    product_id: str, contract: InstrumentContract | None
) -> RiskVerdict | None:
    """A contract binding cannot authorize a different product with the same size."""
    if contract is not None and contract.product_id != product_id:
        return _deny(
            RiskReasonCode.FUTURES_CONTRACT_DRIFT,
            "The bound contract does not identify the proposed futures product.",
        )
    return None


def _contract_kind_verdict(contract: InstrumentContract | None) -> RiskVerdict | None:
    """I4: a known perp-style contract."""
    if contract is None:
        return _deny(
            RiskReasonCode.FUTURES_CONTRACT_UNBOUND,
            "The live futures contract is unknown; the start is denied.",
        )
    if contract.kind != "perpetual_future":
        return _deny(
            RiskReasonCode.FUTURES_LIVE_DATED_UNSUPPORTED,
            f"{contract.product_id} is a dated contract; live futures trade perp-style "
            "contracts only until an expiry flatten exists.",
        )
    return None


def _evidence_verdict(
    venue: FuturesVenueEvidence | None, as_of: datetime | None
) -> RiskVerdict | None:
    """I8: venue evidence exists and is at most 180 s old."""
    if venue is None or as_of is None or not venue.is_fresh(as_of):
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
            "Fresh CFM venue evidence (at most 180 s old) is unavailable; live futures "
            "admissions are denied.",
        )
    return None


def _start_venue_verdict(
    policy: RiskPolicyDefinition, product_id: str, start: LiveFuturesStart | None
) -> RiskVerdict | None:
    """I3 at start: fresh evidence with no position or external order on the product."""
    venue = None if start is None else start.venue
    stale = _evidence_verdict(venue, None if start is None else start.as_of)
    if stale is not None or venue is None or start is None or start.contract is None:
        return stale
    contract_size = Decimal(start.contract.contract_size)
    checks: tuple[Callable[[], RiskVerdict | None], ...] = (
        lambda: _external_verdict(product_id, venue, expected_contracts=Decimal(0)),
        lambda: _venue_state_verdict(product_id, venue, start.as_of),
        lambda: _drift_verdict(product_id, contract_size, venue),
        lambda: _margin_capacity_verdict(policy, venue, start.proposed_initial_margin_usd),
    )
    return _first(checks)


def _external_entry_verdict(
    product_id: str,
    book: DeploymentSnapshot,
    contract_size: Decimal,
    venue: FuturesVenueEvidence | None,
) -> RiskVerdict | None:
    """I3 at entry: the venue position equals the book's, and no external order exists."""
    if venue is None:
        return None
    held = _signed_base(book, product_id) / contract_size
    return _external_verdict(product_id, venue, expected_contracts=held)


def _external_verdict(
    product_id: str, venue: FuturesVenueEvidence, *, expected_contracts: Decimal
) -> RiskVerdict | None:
    """Deny an unexplained venue position or an external open order on the product."""
    if venue.positions is None or venue.external_order_products is None:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
            "The CFM position or open-order read failed; product exclusivity is unknown.",
        )
    venue_contracts = venue.positions.get(product_id, Decimal(0))
    if venue_contracts != expected_contracts:
        return _deny(
            RiskReasonCode.FUTURES_EXTERNAL_POSITION_ON_PRODUCT,
            f"The venue holds {venue_contracts} contracts of {product_id} but the managed "
            f"book holds {expected_contracts}; an external position shares the product.",
        )
    if product_id in venue.external_order_products:
        return _deny(
            RiskReasonCode.FUTURES_EXTERNAL_POSITION_ON_PRODUCT,
            f"An open futures order on {product_id} is not managed by ThyTrader.",
        )
    return None


def _drift_verdict(
    product_id: str, contract_size: Decimal, venue: FuturesVenueEvidence | None
) -> RiskVerdict | None:
    """I11: the bound contract size equals the catalog's."""
    catalog = None if venue is None else venue.contract_sizes.get(product_id)
    if catalog is None:
        return _deny(
            RiskReasonCode.FUTURES_CONTRACT_DRIFT,
            f"The catalog contract size of {product_id} is unknown; entries are denied.",
        )
    if catalog != contract_size:
        return _deny(
            RiskReasonCode.FUTURES_CONTRACT_DRIFT,
            f"The bound contract size {contract_size} differs from the catalog's {catalog}; "
            "entries are denied (exits use the catalog size).",
        )
    return None


def _contracts_verdict(
    policy: RiskPolicyDefinition,
    proposed: ProposedEntry,
    book: DeploymentSnapshot,
    contract_size: Decimal,
) -> RiskVerdict | None:
    """I5: the order and the resulting position hold at most the live contract cap."""
    futures: FuturesRiskPolicy | None = policy.futures
    cap = 1 if futures is None else futures.effective_live_max_order_contracts
    if proposed.quantity is None or not proposed.quantity.is_finite() or proposed.quantity <= 0:
        return _deny(
            RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED,
            "The live futures entry has no quantity; its contracts cannot be checked.",
        )
    ordered = proposed.quantity / contract_size
    held = abs(_signed_base(book, proposed.product_id)) / contract_size
    if ordered != ordered.to_integral_value() or ordered > cap or held + ordered > cap:
        return _deny(
            RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED,
            f"{ordered} contracts on a position of {held} exceed the live cap of {cap} "
            "(futures.max_order_contracts, 1 while unset).",
        )
    return None


def _venue_state_verdict(
    product_id: str, venue: FuturesVenueEvidence | None, as_of: datetime | None
) -> RiskVerdict | None:
    """I8: the venue killswitch is off and no maintenance window is pending or current."""
    if venue is None or as_of is None:
        return None
    if venue.killswitch_enabled is None:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
            "The venue futures killswitch state is unknown; entries are denied.",
        )
    if venue.killswitch_enabled:
        return _deny(
            RiskReasonCode.FUTURES_VENUE_KILLSWITCH,
            "The venue reports a futures killswitch enabled; entries wait until it is off.",
        )
    window = venue.maintenance.get(product_id)
    if window is not None and as_of < window.ends_at:
        return _deny(
            RiskReasonCode.FUTURES_VENUE_MAINTENANCE,
            f"A venue maintenance window for {product_id} runs "
            f"{_utc(window.starts_at)} to {_utc(window.ends_at)}; entries wait until it ends.",
        )
    return None


def _margin_capacity_verdict(
    policy: RiskPolicyDefinition, venue: FuturesVenueEvidence | None, initial: Decimal | None
) -> RiskVerdict | None:
    """I8: buying power covers the entry and the L3 reserve covers the projected margin."""
    if venue is None:
        return None
    if initial is None or not initial.is_finite() or initial <= 0:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
            "A finite positive proposed initial margin is required for admission.",
        )
    buying_power = venue.buying_power_usd
    current = venue.initial_margin_usd
    if buying_power is None or current is None:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
            "CFM buying power or initial margin is unknown; entries are denied.",
        )
    if buying_power < initial:
        return _deny(
            RiskReasonCode.FUTURES_BUYING_POWER_SHORT,
            f"Futures buying power {buying_power} USD is below the entry's initial margin "
            f"{initial} USD.",
        )
    if policy.quote_currency not in {"USD", "USDC"}:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
            "Live futures require a declared USD/USDC collateral reserve; "
            "other policy quotes cannot cover CFM USD margin.",
        )
    futures = policy.futures
    reserve = None if futures is None else futures.live_spot_collateral_reserve_quote
    if futures is None or reserve is None:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT,
            "Declare futures.live_spot_collateral_reserve_quote: live futures margin shares "
            "the USDC pool, and the reserve must cover it.",
        )
    haircut = Decimal(futures.peg_haircut)
    required = (current + initial) * haircut
    if Decimal(reserve) < required:
        return _deny(
            RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT,
            f"Declared reserve {reserve} {policy.quote_currency} is below projected initial "
            f"margin ({current} + {initial}) USD x haircut {haircut} = {required} USD "
            "(threshold under a declared peg; the currencies are not added).",
        )
    return None


def _signed_base(book: DeploymentSnapshot, product_id: str) -> Decimal:
    """The book's signed base quantity on ``product_id`` (short is negative)."""
    return sum(
        (
            -position.quantity if position.side is PositionSide.SHORT else position.quantity
            for position in snapshot_positions(book)
            if resolved_product_id(position.product_id, book.deployment) == product_id
        ),
        start=Decimal(0),
    )


def _utc(value: datetime) -> str:
    """Canonical ``Z`` instant text."""
    return value.isoformat().replace("+00:00", "Z")
