"""Live spot collateral gate beside manual CFM futures (ADR 0129 sections 2-3, slice P1-2a).

Coinbase counts the USDC spot balance as CFM futures collateral (ADR 0127 §8), so futures
margin and USDC/USD spot books share one pool. The newest futures account mirror snapshot
classifies the account:

- ``absent``: no snapshot, futures not enabled, or no mirror bound (no credentials or
  database).
- ``idle``: enabled, snapshot at most 180 s old, positions read as none, no initial margin
  and no open-order hold.
- ``in_use``: enabled with a position, a positive initial margin or a positive hold.
- ``unknown``: enabled with a stale snapshot or a failed balance or position read.

For new risk-increasing **live** entries in USDC or USD spot books: ``unknown`` denies
(L1); ``in_use`` denies (L2) unless the policy declares
``futures.live_spot_collateral_reserve_quote`` (L3), which must cover the initial margin
times ``futures.peg_haircut`` and is then withheld from the venue available quote the
capital base uses. ``idle`` and ``absent`` change nothing (L4). Paper, USDT books and
in-kind adoption are not affected; protective exits never reach this gate.

USD and USDC are never added: the reserve check is a yes/no threshold under a declared peg,
and the withheld reserve is a same-currency subtraction.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from thytrader.exchanges.futures_models import (
    FuturesAccountStoreUnavailableError,
    FuturesEnablement,
)
from thytrader.market_data.products import is_spot_product_id, quote_currency
from thytrader.risk.gate_common import _deny
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from thytrader.exchanges.futures_models import (
        FuturesAccountObservation,
        FuturesAccountSnapshotStore,
    )
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict

MIRROR_STALE_AFTER = timedelta(seconds=180)
COLLATERAL_LINKED_QUOTES = frozenset({"USD", "USDC"})
_ZERO = Decimal(0)
_STORE: ContextVar[FuturesAccountSnapshotStore | None] = ContextVar(
    "risk_futures_account", default=None
)


class FuturesCollateralState(StrEnum):
    """How the CFM futures account uses the shared collateral pool right now."""

    ABSENT = "absent"
    IDLE = "idle"
    IN_USE = "in_use"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FuturesCollateralEvidence:
    """The classified futures account state the gate reads (amounts in USD)."""

    state: FuturesCollateralState
    observed_at: datetime | None
    initial_margin_usd: Decimal | None
    open_orders_hold_usd: Decimal | None
    position_count: int | None
    cause: str


@contextmanager
def risk_futures_account_scope(store: FuturesAccountSnapshotStore | None) -> Iterator[None]:
    """Bind the futures account mirror store for live entry admission in this context."""
    token = _STORE.set(store)
    try:
        yield
    finally:
        _STORE.reset(token)


def bound_futures_account_store() -> FuturesAccountSnapshotStore | None:
    """Return the store bound by ``risk_futures_account_scope``, or ``None``."""
    return _STORE.get()


async def load_futures_collateral(
    store: FuturesAccountSnapshotStore | None, *, mode: DeploymentMode, as_of: datetime
) -> FuturesCollateralEvidence | None:
    """Read and classify the newest snapshot for a live entry; ``None`` for paper.

    No bound store means no mirror on this install (``absent``). Unreadable storage is
    ``unknown``: the pool's use cannot be proved.
    """
    if mode is not DeploymentMode.LIVE:
        return None
    if store is None:
        return _evidence(FuturesCollateralState.ABSENT, None, "No futures mirror is configured.")
    try:
        latest = await store.latest()
    except FuturesAccountStoreUnavailableError:
        return _evidence(
            FuturesCollateralState.UNKNOWN, None, "Futures account storage could not be read."
        )
    return classify_futures_collateral(latest, as_of=as_of)


def classify_futures_collateral(
    latest: FuturesAccountObservation | None, *, as_of: datetime
) -> FuturesCollateralEvidence:
    """Classify one snapshot (pure; ADR 0129 §2 table)."""
    if latest is None:
        return _evidence(FuturesCollateralState.ABSENT, None, "No futures snapshot exists.")
    if latest.enablement is FuturesEnablement.NOT_ENABLED:
        return _evidence(FuturesCollateralState.ABSENT, latest, "Futures are not enabled.")
    balance = latest.balance
    positions = latest.positions
    if (
        latest.enablement is FuturesEnablement.UNKNOWN
        or balance is None
        or positions is None
        or as_of - latest.observed_at > MIRROR_STALE_AFTER
    ):
        return _evidence(
            FuturesCollateralState.UNKNOWN,
            latest,
            "The futures snapshot is stale or a balance or position read failed.",
        )
    margin = balance.initial_margin
    hold = balance.total_open_orders_hold_amount
    in_use = (
        bool(positions) or (margin is not None and margin > 0) or (hold is not None and hold > 0)
    )
    if in_use:
        return _evidence(
            FuturesCollateralState.IN_USE,
            latest,
            f"CFM futures hold {len(positions)} position(s), initial margin "
            f"{_text(margin)} USD and open-order hold {_text(hold)} USD.",
        )
    return _evidence(FuturesCollateralState.IDLE, latest, "CFM futures are flat.")


def collateral_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    evidence: FuturesCollateralEvidence | None,
    live_quote_cash: Decimal | None,
) -> tuple[RiskVerdict | None, Decimal | None]:
    """Return a denial (or ``None``) and the venue quote cash the rest of the gate uses.

    Only live, non-in-kind entries in USD or USDC spot books are affected. With the L3
    reserve, the returned cash is the venue available quote minus the reserve.
    """
    if (
        mode is not DeploymentMode.LIVE
        or evidence is None
        or proposed.in_kind
        or not is_spot_product_id(proposed.product_id)
        or quote_currency(proposed.product_id) not in COLLATERAL_LINKED_QUOTES
    ):
        return None, live_quote_cash
    if evidence.state is FuturesCollateralState.UNKNOWN:
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
                f"{evidence.cause} USDC and USD are CFM collateral, so their use by futures "
                "is unknown; live spot entries are paused.",
            ),
            live_quote_cash,
        )
    if evidence.state is not FuturesCollateralState.IN_USE:
        return None, live_quote_cash
    futures = policy.futures
    reserve_text = None if futures is None else futures.live_spot_collateral_reserve_quote
    if futures is None or reserve_text is None:
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_IN_USE,
                f"{evidence.cause} They draw on the USDC collateral spot books use; set "
                "futures.live_spot_collateral_reserve_quote to trade spot beside them.",
            ),
            live_quote_cash,
        )
    return _reserve_verdict(
        Decimal(reserve_text), Decimal(futures.peg_haircut), evidence, live_quote_cash, policy
    )


def _reserve_verdict(
    reserve: Decimal,
    haircut: Decimal,
    evidence: FuturesCollateralEvidence,
    live_quote_cash: Decimal | None,
    policy: RiskPolicyDefinition,
) -> tuple[RiskVerdict | None, Decimal | None]:
    """L3: the declared reserve must cover margin x haircut and is withheld from cash."""
    quote = policy.quote_currency
    margin = evidence.initial_margin_usd
    if margin is None:
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
                "CFM futures are in use but their initial margin is unknown; the reserve "
                "cannot be checked.",
            ),
            live_quote_cash,
        )
    required_usd = margin * haircut
    if reserve < required_usd:
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT,
                f"Declared reserve {reserve} {quote} is below CFM initial margin {margin} USD "
                f"x haircut {haircut} = {required_usd} USD (threshold under a declared peg; "
                "the currencies are not added).",
            ),
            live_quote_cash,
        )
    if live_quote_cash is None:
        return None, None
    remaining = live_quote_cash - reserve
    if remaining < _ZERO:
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_RESERVE_SHORT,
                f"Available {live_quote_cash} {quote} is below the declared futures reserve "
                f"{reserve} {quote}; spot capital would be negative.",
            ),
            live_quote_cash,
        )
    return None, remaining


def _evidence(
    state: FuturesCollateralState, latest: FuturesAccountObservation | None, cause: str
) -> FuturesCollateralEvidence:
    """Build evidence from one optional snapshot."""
    balance = None if latest is None else latest.balance
    positions = None if latest is None else latest.positions
    return FuturesCollateralEvidence(
        state=state,
        observed_at=None if latest is None else latest.observed_at,
        initial_margin_usd=None if balance is None else balance.initial_margin,
        open_orders_hold_usd=None if balance is None else balance.total_open_orders_hold_amount,
        position_count=None if positions is None else len(positions),
        cause=cause,
    )


def _text(value: Decimal | None) -> str:
    """Exact decimal or ``unknown``."""
    return "unknown" if value is None else format(value, "f")
