"""Paper futures books in the risk gate: start, entry admission, linked breakers (ADR 0129).

Futures books are their own settlement scope (``CFM-USD``): their capital is the policy's
``futures.paper_capital_usd``, never ``paper_capital_quote``, and their daily loss and
drawdown are computed over futures books only. Within one mode the futures scope and the
USD/USDC spot scopes are collateral-linked: a latched daily-loss breaker in one denies new
entries in the other (``SHARED_COLLATERAL_BREAKER``), naming the latched scope; the loss
figures are never added together. Live futures stay unsupported (P2).

P1-4 admits a paper futures entry when the policy block, the bound contract, the observed
margin and every due funding hour are known, the entry fits the strategy's leverage and the
liquidation buffer, and the scope's breakers, rate limits, collar and fleet clustering
allow it. P1-5 adds the remaining policy caps.
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
from thytrader.risk.gate_common import _allow, _deny
from thytrader.risk.models import RiskDecision, RiskReasonCode
from thytrader.trading.exposure import daily_loss_snapshots, risk_bearing_snapshots
from thytrader.trading.futures_book import current_futures_book
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.risk.breakers import EntryObservation
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
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
) -> RiskVerdict:
    """Admit a paper futures start inside the separate USD futures envelope."""
    if mode is DeploymentMode.LIVE:
        return _deny(
            RiskReasonCode.FUTURES_LIVE_UNSUPPORTED,
            "Live futures deployments are not supported; there is no futures order path.",
        )
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
    """The futures scope's capital: the paper envelope; live has none in P1."""
    futures = policy.futures
    if mode is not DeploymentMode.PAPER or futures is None or futures.paper_capital_usd is None:
        return Decimal(0)
    return Decimal(futures.paper_capital_usd)


def evaluate_futures_entry(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None,
) -> RiskVerdict:
    """Admit one paper futures entry, or name what is unknown or exceeded."""
    if mode is DeploymentMode.LIVE:
        return _deny(
            RiskReasonCode.FUTURES_LIVE_UNSUPPORTED,
            "Live futures entries are not supported; there is no futures order path.",
        )
    if futures_capital(policy, mode) <= 0:
        return _deny(
            RiskReasonCode.FUTURES_POLICY_UNSET,
            "The risk policy has no futures.paper_capital_usd; futures entries are denied.",
        )
    book = _proposing_book(proposed, snapshots)
    known = _known_book_verdict(book)
    if known is not None:
        return known
    occupied = tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and occupies_running_slot(item.deployment)
    )
    membership = _entry_membership(policy, mode=mode, proposed=proposed, occupied=occupied)
    if membership.decision is RiskDecision.DENY:
        return membership
    linked = linked_breaker_verdict(mode=mode, product_id=proposed.product_id, snapshots=snapshots)
    if linked is not None:
        return linked
    margin = _margin_verdict(proposed, book, observation)
    if margin is not None:
        return margin
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
        capital=futures_capital(policy, mode),
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
    clustered = cluster_verdict(
        policy, mode=mode, proposed=proposed, snapshots=snapshots, observation=observation
    )
    return _allow() if clustered is None else clustered


def linked_breaker_verdict(
    *, mode: DeploymentMode, product_id: str, snapshots: Sequence[DeploymentSnapshot]
) -> RiskVerdict | None:
    """Deny when a latched daily-loss breaker sits in a collateral-linked scope (§7).

    A paper futures entry is denied by a latched USD or USDC spot book, and a USD/USDC
    spot entry by a latched futures book, in the same mode. Live futures are unmanaged in
    P1, so live spot is never linked this way. The two losses are never summed.
    """
    if mode is not DeploymentMode.PAPER:
        return None
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
            "The paper futures book's contract binding is not loaded.",
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
    ledger = ledger_from_snapshot(book, marks=marks)
    equity = ledger.equity
    if equity is None or not ledger.mark_complete or equity <= 0:
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
