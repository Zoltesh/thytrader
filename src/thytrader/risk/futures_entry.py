"""Futures books in the risk gate: start, entry admission, linked breakers (ADR 0129, 0134).

Futures books are their own settlement scope (``CFM-USD``): their capital is the policy's
``futures.paper_capital_usd`` for paper and ``futures.live_capital_usd`` for live, never
``paper_capital_quote``, and their daily loss and drawdown are computed over futures books
only. Within one mode the futures scope and the USD/USDC spot scopes are collateral-linked:
a latched daily-loss breaker in one denies new entries in the other
(``SHARED_COLLATERAL_BREAKER``), naming the latched scope; the loss figures are never added
together. Live futures starts and entries first pass the live gate (``futures_live``, ADR 0134
P2-3); every start surface still refuses live futures until P2-7.

P1-4 admits a paper futures entry when the policy block, the bound contract, the observed
margin and every due funding hour are known, the entry fits the leverage (the lower of the
policy's and the strategy's) and the liquidation buffer, and the scope's breakers, rate
limits, collar and fleet clustering allow it. P1-5 adds the policy caps of §5: contracts per
order, gross futures exposure, the funding-rate cap, an absolute daily loss and the futures
BTC-beta cap.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.products import is_spot_product_id, quote_currency
from thytrader.risk.breakers import (
    evaluate_circuit_breakers,
    evaluate_rate_and_collar,
)
from thytrader.risk.entry_clustering import cluster_verdict
from thytrader.risk.entry_limits import _entry_membership
from thytrader.risk.futures_beta import futures_beta_verdict
from thytrader.risk.futures_live import (
    live_futures_deployment_verdict,
    live_futures_entry_verdict,
    live_futures_opt_in_verdict,
)
from thytrader.risk.gate_common import _allow, _deny
from thytrader.risk.models import RiskDecision, RiskReasonCode
from thytrader.trading.exposure import (
    daily_loss_snapshots,
    product_exposure,
    risk_bearing_snapshots,
)
from thytrader.trading.futures_book import current_futures_book, futures_book_equity
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from thytrader.risk.beta import BetaEvidence
    from thytrader.risk.breakers import EntryObservation
    from thytrader.risk.futures_beta import FuturesLegs
    from thytrader.risk.futures_live import FuturesVenueEvidence, LiveFuturesStart
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
    from thytrader.trading.futures_book import FuturesBookState
    from thytrader.trading.models import Deployment, DeploymentSnapshot

_LINKED_SPOT_QUOTES = frozenset({"USD", "USDC"})
"""Spot quotes in the collateral group with CFM futures (ADR 0129 §1); USDT is not."""


def futures_deployment_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    covered: Sequence[str],
    paper_starting_cash: Decimal | None,
    deployments: Sequence[Deployment],
    live: LiveFuturesStart | None = None,
) -> RiskVerdict:
    """Admit a futures start inside its mode's separate USD futures envelope.

    A live start passes the live gate with ``live`` (its allocation, contract and venue
    evidence); without it the start is denied.
    """
    if mode is DeploymentMode.LIVE:
        verdict = live_futures_deployment_verdict(
            policy, covered=covered, deployments=deployments, start=live
        )
        return _allow() if verdict is None else verdict
    if len(covered) != 1:
        return _deny(
            RiskReasonCode.FUTURES_PAPER_UNSUPPORTED,
            "A paper futures deployment trades exactly one contract.",
        )
    futures = policy.futures
    if futures is None or futures.paper_capital_usd is None:
        return _deny(
            RiskReasonCode.FUTURES_POLICY_UNSET,
            "Set the risk policy's futures.paper_capital_usd before starting paper futures.",
        )
    if paper_starting_cash is None or paper_starting_cash <= 0:
        return _deny(
            RiskReasonCode.FUTURES_PAPER_CAPITAL_EXCEEDED,
            "Paper futures deployments require positive starting cash (USD).",
        )
    committed = sum(
        (
            item.paper_starting_cash
            for item in deployments
            if item.mode is mode
            and occupies_running_slot(item)
            and is_futures_product_id(item.product_id)
            and item.paper_starting_cash is not None
        ),
        start=Decimal(0),
    )
    if committed + paper_starting_cash > Decimal(futures.paper_capital_usd):
        return _deny(
            RiskReasonCode.FUTURES_PAPER_CAPITAL_EXCEEDED,
            "Paper futures starting cash would exceed futures.paper_capital_usd.",
        )
    return _allow()


def futures_capital(policy: RiskPolicyDefinition, mode: DeploymentMode) -> Decimal:
    """The futures scope's USD capital: the paper or live envelope; 0 while it is unset."""
    futures = policy.futures
    if futures is None:
        return Decimal(0)
    envelope = (
        futures.live_capital_usd if mode is DeploymentMode.LIVE else futures.paper_capital_usd
    )
    return Decimal(0) if envelope is None else Decimal(envelope)


def evaluate_futures_entry(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None,
    beta: BetaEvidence | None = None,
    legs: FuturesLegs | None = None,
    venue: FuturesVenueEvidence | None = None,
) -> RiskVerdict:
    """Admit one futures entry, or name what is unknown or exceeded.

    Checks run in order and the first objection wins: the live opt-in (live), the envelope,
    known binding, margin and funding, the live gate (live: exclusivity, contract drift,
    contract cap and fresh ``venue`` evidence), membership, the linked breaker, leverage and
    the liquidation buffer, the policy caps (order contracts, gross exposure, funding rate),
    the futures BTC-beta cap, then the scope's loss breakers, rate limits, collar and fleet
    clustering.
    """
    if mode is DeploymentMode.LIVE:
        opted = live_futures_opt_in_verdict(policy, proposed.product_id)
        if opted is not None:
            return opted
    capital = futures_capital(policy, mode)
    if capital <= 0:
        return _deny(
            RiskReasonCode.FUTURES_POLICY_UNSET,
            "The risk policy has no futures.paper_capital_usd; futures entries are denied.",
        )
    book = _proposing_book(proposed, snapshots)
    checks: tuple[Callable[[], RiskVerdict | None], ...] = (
        lambda: _known_book_verdict(book),
        lambda: _live_gate_verdict(
            policy,
            mode=mode,
            proposed=proposed,
            book=book,
            snapshots=snapshots,
            venue=venue,
            as_of=None if observation is None else observation.as_of,
        ),
        lambda: _membership_verdict(policy, mode=mode, proposed=proposed, snapshots=snapshots),
        lambda: linked_breaker_verdict(
            mode=mode, product_id=proposed.product_id, snapshots=snapshots
        ),
        lambda: _margin_verdict(proposed, book, observation),
        lambda: _policy_caps_verdict(
            policy, mode=mode, proposed=proposed, snapshots=snapshots, capital=capital
        ),
        lambda: futures_beta_verdict(
            policy,
            mode=mode,
            proposed=proposed,
            proposing_underlying=_proposing_underlying(book),
            snapshots=snapshots,
            legs=legs,
            beta=beta,
            capital=capital,
            as_of=None if observation is None else observation.as_of,
        ),
        lambda: _scope_breaker_verdict(
            policy,
            mode=mode,
            proposed=proposed,
            snapshots=snapshots,
            observation=observation,
            capital=capital,
        ),
    )
    for check in checks:
        verdict = check()
        if verdict is not None:
            return verdict
    return _allow()


def _live_gate_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    book: DeploymentSnapshot | None,
    snapshots: Sequence[DeploymentSnapshot],
    venue: FuturesVenueEvidence | None,
    as_of: datetime | None,
) -> RiskVerdict | None:
    """The live futures gate for a loaded live book; paper books skip it."""
    state = None if book is None else current_futures_book(book.deployment.id)
    if mode is not DeploymentMode.LIVE or book is None or state is None:
        return None
    return live_futures_entry_verdict(
        policy,
        proposed=proposed,
        book=book,
        state=state,
        snapshots=snapshots,
        venue=venue,
        as_of=as_of,
    )


def _membership_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
) -> RiskVerdict | None:
    """Allowlist, allocation and open-position slots, as for spot."""
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and occupies_running_slot(item.deployment)
    )
    membership = _entry_membership(policy, mode=mode, proposed=proposed, occupied=occupied)
    return membership if membership.decision is RiskDecision.DENY else None


def _policy_caps_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
    capital: Decimal,
) -> RiskVerdict | None:
    """Per-order contracts, gross futures exposure and the funding-rate cap (§5)."""
    futures = policy.futures
    state = current_futures_book()
    if futures is None or state is None or state.binding is None:
        return None
    contract_size = Decimal(state.binding.contract.contract_size)
    if futures.max_order_contracts is not None and proposed.quantity is not None:
        contracts = proposed.quantity / contract_size
        if contracts > futures.max_order_contracts:
            return _deny(
                RiskReasonCode.FUTURES_ORDER_CONTRACTS_EXCEEDED,
                f"{contracts} contracts exceed futures.max_order_contracts "
                f"{futures.max_order_contracts}.",
            )
    if futures.max_exposure_fraction is not None:
        existing = sum(
            (
                abs(product_exposure(book, book.deployment.product_id))
                for book in risk_bearing_snapshots(snapshots, mode)
                if is_futures_product_id(book.deployment.product_id)
            ),
            start=Decimal(0),
        )
        cap = capital * Decimal(futures.max_exposure_fraction)
        if existing + abs(proposed.notional) > cap:
            return _deny(
                RiskReasonCode.FUTURES_EXPOSURE_EXCEEDED,
                f"Gross futures notional {existing + abs(proposed.notional)} USD would exceed "
                f"{cap} USD ({futures.max_exposure_fraction} x futures capital {capital} USD).",
            )
    return _funding_rate_verdict(futures.max_hourly_funding_rate_abs, state)


def _funding_rate_verdict(cap: str | None, state: FuturesBookState) -> RiskVerdict | None:
    """Deny while a perp's latest settled hourly rate is unknown or above the cap."""
    if cap is None or state.binding is None or state.binding.contract.kind != "perpetual_future":
        return None
    rate = state.latest_funding_rate
    if rate is None:
        return _deny(
            RiskReasonCode.FUNDING_HISTORY_MISSING,
            "The latest settled funding rate is unknown; the funding-rate cap cannot be checked.",
        )
    if abs(rate) > Decimal(cap):
        return _deny(
            RiskReasonCode.FUTURES_FUNDING_RATE_EXCEEDED,
            f"The latest settled hourly funding rate {rate} exceeds "
            f"futures.max_hourly_funding_rate_abs {cap}.",
        )
    return None


def _proposing_underlying(book: DeploymentSnapshot | None) -> str:
    """The proposing book's bound underlying (known once the earlier checks passed)."""
    state = None if book is None else current_futures_book(book.deployment.id)
    if state is None or state.binding is None:
        return ""
    return state.binding.contract.underlying


def _scope_breaker_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None,
    capital: Decimal,
) -> RiskVerdict | None:
    """The futures scope's loss breakers, then rate limits, collar and fleet clustering."""
    if observation is None:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Futures daily loss cannot be evaluated without a bar observation.",
        )
    tripped = evaluate_circuit_breakers(
        policy,
        mode=mode,
        proposed_product_id=proposed.product_id,
        proposed_strategy_id=proposed.strategy_id,
        snapshots=snapshots,
        observation=observation,
        capital=capital,
    )
    if tripped is not None:
        return tripped
    protected = evaluate_rate_and_collar(
        policy,
        mode=mode,
        snapshots=risk_bearing_snapshots(snapshots, mode),
        observation=observation,
    )
    if protected is not None:
        return protected
    return cluster_verdict(
        policy, mode=mode, proposed=proposed, snapshots=snapshots, observation=observation
    )


def linked_breaker_verdict(
    *, mode: DeploymentMode, product_id: str, snapshots: Sequence[DeploymentSnapshot]
) -> RiskVerdict | None:
    """Deny when a latched daily-loss breaker sits in a collateral-linked scope (§7).

    A futures entry is denied by a latched USD or USDC spot book, and a USD/USDC spot entry
    by a latched futures book, in the same mode. In live the futures side is always a
    managed live futures book (ADR 0134 §3): manual CFM positions have no book and are
    gated by the collateral rules instead. The two losses are never summed.
    """
    entry_scope = _scope(product_id)
    if entry_scope is None:
        return None
    for item in daily_loss_snapshots(snapshots, mode):
        if not item.deployment.daily_loss_latched:
            continue
        book_scope = _scope(item.deployment.product_id)
        if book_scope is None or book_scope == entry_scope:
            continue
        if "futures" not in (book_scope, entry_scope):
            continue
        return _deny(
            RiskReasonCode.SHARED_COLLATERAL_BREAKER,
            f"The {book_scope} daily-loss breaker is latched (deployment "
            f"{item.deployment.id}); it shares collateral with {entry_scope} entries.",
        )
    return None


def _scope(product_id: str) -> str | None:
    """``futures`` for CFM books, the quote for linked spot books, else None."""
    if is_futures_product_id(product_id):
        return "futures"
    if is_spot_product_id(product_id) and quote_currency(product_id) in _LINKED_SPOT_QUOTES:
        return f"spot {quote_currency(product_id)}"
    return None


def _proposing_book(
    proposed: ProposedEntry, snapshots: Sequence[DeploymentSnapshot]
) -> DeploymentSnapshot | None:
    """The proposing futures book, found through the bound cycle state."""
    state = current_futures_book()
    if state is None or state.product_id != proposed.product_id:
        return None
    return next(
        (item for item in snapshots if item.deployment.id == state.deployment_id),
        None,
    )


def _known_book_verdict(book: DeploymentSnapshot | None) -> RiskVerdict | None:
    """Deny an entry whose binding, margin or funding evidence is unknown."""
    state = None if book is None else current_futures_book(book.deployment.id)
    if book is None or state is None:
        return _deny(
            RiskReasonCode.FUTURES_CONTRACT_UNBOUND,
            "The futures book's contract binding is not loaded.",
        )
    blocked = state.entry_block()
    if blocked is None:
        return None
    code, detail = blocked
    return _deny(RiskReasonCode(code), detail)


def _margin_verdict(
    proposed: ProposedEntry,
    book: DeploymentSnapshot | None,
    observation: EntryObservation | None,
) -> RiskVerdict | None:
    """The entry must fit the strategy's leverage and leave the liquidation buffer."""
    state = None if book is None else current_futures_book(book.deployment.id)
    if book is None or state is None or state.margin is None:
        return _deny(
            RiskReasonCode.FUTURES_MARGIN_UNKNOWN,
            "Futures margin terms are unknown; the entry is denied.",
        )
    marks = {} if observation is None else dict(observation.marks)
    equity = futures_book_equity(book, marks)
    if equity is None or equity <= 0:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Futures book equity is unknown or not positive; the entry is denied.",
        )
    margin = state.margin
    if proposed.notional > margin.max_leverage * equity:
        return _deny(
            RiskReasonCode.FUTURES_LEVERAGE_EXCEEDED,
            f"Entry notional {proposed.notional} USD exceeds max_leverage "
            f"{margin.max_leverage} x equity {equity} USD.",
        )
    quantity = proposed.quantity
    price = proposed.notional / quantity if quantity else Decimal(0)
    maintenance = margin.maintenance(quantity or Decimal(0), price, state.side)
    if maintenance > (Decimal(1) - margin.min_buffer_fraction) * equity:
        return _deny(
            RiskReasonCode.FUTURES_LIQUIDATION_BUFFER,
            f"Maintenance {maintenance} USD would leave less than the "
            f"{margin.min_buffer_fraction} liquidation buffer of equity {equity} USD.",
        )
    return None


def is_futures_entry(product_id: str) -> bool:
    """Whether an entry belongs to the futures scope (admitted by this module)."""
    return is_futures_product_id(product_id)
