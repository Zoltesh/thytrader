"""Start a live strategy bot that adopts coins already held at Coinbase (ADR 0124).

The bot's deployment row and the adoption commit in one transaction, so a failed adoption
can never leave a FLAT running bot that would buy with its allocation. v1 covers
single-instrument, long-side strategies only.

The mark is the newest bar of the deploy-anchored closed window the worker evaluates
(ADR 0113). The initial stop and target come from the strategy's exits at that mark, with
the initial-stop ATR of that same window, exactly as the worker sizes an entry. The target
is None when the strategy declares no take-profit. Performance capital is pinned to
max(allocation, adopted notional) (ADR 0107).

Admission runs every start check, then the entry gate with in-kind funding. The fleet latch
refuses the start like any live start. The worker's next cycle places the protection and
then manages the position by the strategy's exits.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.adoption import (
    ADOPTION_MARK_UNAVAILABLE,
    adopted_quantity,
    journal_adoption,
    live_accounting_books,
    venue_minimum_refusal,
    venue_rows,
)
from thytrader.execution.closed_windows import _closed_window
from thytrader.execution.freshness import entry_prerequisites
from thytrader.execution.service import prepare_deployment
from thytrader.execution.signals import latest_atr
from thytrader.market_data.window_state import WindowCacheWarmingError
from thytrader.memory.trade_reason_scope import strategy_trade_reason_scope, trade_reason_scope
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import evaluate_new_entry
from thytrader.risk.gate_common import ProposedEntry
from thytrader.risk.models import RiskDecision
from thytrader.risk.store import load_effective_policy
from thytrader.strategies.models import covered_product_ids, reward_risk_multiple
from thytrader.trading.adoption_write import AdoptionRefusedError, AdoptionWrite
from thytrader.trading.exposure import counts_for_daily_loss
from thytrader.trading.geometry import EntryLevels, EntrySkipReason, base_currency, entry_levels
from thytrader.trading.ids import utc_now
from thytrader.trading.inventory_claims import base_availability, managed_base_claims
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    ExecutionConflictError,
    IntentOrigin,
    PositionSide,
)
from thytrader.trading.sizing import quantize_to_increment

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.service import ReferenceWatchlist
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshotReader
    from thytrader.trading.adoption_write import BalanceReader
    from thytrader.trading.models import Deployment
    from thytrader.trading.store import ExecutionStore, InventoryAdoptionStore

ADOPTION_STRATEGY_UNSUPPORTED = "ADOPTION_STRATEGY_UNSUPPORTED"
ADOPTION_LEVELS_UNAVAILABLE = "ADOPTION_LEVELS_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class StrategyAdoptionVenue:
    """The live venue reads and stores a strategy adoption needs."""

    adoption_store: InventoryAdoptionStore
    market_data: MarketDataService
    read_balances: BalanceReader
    live_quote_cash: Decimal | None
    memory_store: ExperientialMemoryStore | None = None


async def start_with_adoption(
    *,
    store: ExecutionStore,
    publication_store: StrategySnapshotReader,
    strategy_fingerprint: str,
    quantity: Decimal | None,
    live_allowed: bool,
    venue: StrategyAdoptionVenue,
    risk_store: RiskPolicyStore | None = None,
    reference_watches: ReferenceWatchlist | None = None,
    origin: IntentOrigin = IntentOrigin.HUMAN,
) -> DeploymentSnapshot:
    """Start the strategy live with ``quantity`` (None: all) held coins already adopted.

    Raises:
        ExecutionConflictError: A start check, the entry gate, the fleet latch, or an
            adoption check refuses (``AdoptionRefusedError`` names the code).
    """
    prepared = await prepare_deployment(
        store=store,
        publication_store=publication_store,
        strategy_fingerprint=strategy_fingerprint,
        mode=DeploymentMode.LIVE,
        paper_starting_cash=None,
        live_allowed=live_allowed,
        risk_store=risk_store,
        reference_watches=reference_watches,
    )
    definition = prepared.definition
    require_adoptable_strategy(definition)
    book = prepared.deployment
    product, candle, levels = await _mark_and_levels(venue.market_data, definition, book)
    books = await live_accounting_books(store, prepared.deployments)
    availability = base_availability(
        await venue_rows(venue.read_balances),
        managed_base_claims(books, base_currency(book.product_id)),
        base_increment=product.base_increment,
    )
    adopted = adopted_quantity(quantity, availability=availability, product=product)
    refusal = venue_minimum_refusal(product, quantity=adopted, mark=candle.close)
    if refusal is not None:
        raise refusal
    notional = adopted * candle.close
    book = replace(
        book,
        venue_available_quote=venue.live_quote_cash,
        performance_capital_quote=max(book.allocated_capital or Decimal(0), notional),
    )
    active = await load_effective_policy(risk_store)
    verdict = evaluate_new_entry(
        active.definition,
        mode=DeploymentMode.LIVE,
        proposed=ProposedEntry(
            product_id=book.product_id,
            strategy_id=definition.strategy_id,
            notional=notional,
            quantity=adopted,
            funding="in_kind",
        ),
        snapshots=(
            *(item for item in books if counts_for_daily_loss(item.deployment.status)),
            DeploymentSnapshot(deployment=book, position=None),
        ),
        live_quote_cash=venue.live_quote_cash,
        observation=EntryObservation(
            as_of=utc_now(),
            proposed_price=candle.close,
            reference_price=candle.close,
            marks={book.product_id: candle.close},
        ),
    )
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(f"{verdict.reason_code.value}: {verdict.detail}")
    write = AdoptionWrite(
        deployment_id=book.id,
        product_id=book.product_id,
        quantity=adopted,
        mark=candle.close,
        mark_bar_starts_at=candle.starts_at,
        now=utc_now(),
        origin=origin,
        stop_price=levels.stop_price,
        target_price=levels.target_price,
        base_increment=product.base_increment,
        new_deployment=book,
        respect_entry_latch=True,
    )
    scope = strategy_trade_reason_scope(
        venue.memory_store, deployment=book, strategy=definition, policy=active.definition
    )
    if scope is not None:
        scope.remember_risk(verdict)
    with trade_reason_scope(scope):
        commit = await venue.adoption_store.adopt_inventory(
            write, read_balances=venue.read_balances
        )
        await journal_adoption(
            commit,
            action="strategy_start",
            mark=candle.close,
            timeframe=definition.timeframe,
            candle_starts_at=candle.starts_at,
            product_id=book.product_id,
        )
    return commit.snapshot


def require_adoptable_strategy(definition: StrategyDefinition) -> None:
    """v1 adopts into single-instrument, long-side strategies only.

    Raises:
        AdoptionRefusedError: ``ADOPTION_STRATEGY_UNSUPPORTED``.
    """
    if len(covered_product_ids(definition)) != 1:
        raise AdoptionRefusedError(
            ADOPTION_STRATEGY_UNSUPPORTED,
            "Only single-instrument strategies can start with adopted holdings.",
        )
    if PositionSide(definition.entry.side) is not PositionSide.LONG:
        raise AdoptionRefusedError(
            ADOPTION_STRATEGY_UNSUPPORTED,
            "Only long-side strategies can start with adopted holdings.",
        )


def adoption_levels(
    definition: StrategyDefinition, product: MarketProduct, candles: Sequence[Candle]
) -> EntryLevels | EntrySkipReason | None:
    """Initial stop and target at the newest close, as the worker sizes an entry.

    None means the initial-stop ATR has no value on the window.
    """
    atr = latest_atr(definition, candles)
    if atr is None:
        return None
    return entry_levels(
        side=PositionSide.LONG,
        entry_price=quantize_to_increment(candles[-1].close, product.price_increment),
        stop_distance=atr * Decimal(definition.exits.initial_stop.multiple),
        reward_multiple=reward_risk_multiple(definition.exits),
        price_increment=product.price_increment,
    )


async def _mark_and_levels(
    market_data: MarketDataService, definition: StrategyDefinition, book: Deployment
) -> tuple[MarketProduct, Candle, EntryLevels]:
    """The fresh newest bar of the anchored window, and the levels at its close."""
    try:
        product, candles, expected_last = await _closed_window(
            market_data, definition, deploy_anchor=book.created_at
        )
    except WindowCacheWarmingError as error:
        raise AdoptionRefusedError(
            ADOPTION_MARK_UNAVAILABLE, "Market data is still warming; retry the start."
        ) from error
    if not candles or candles[-1].starts_at != expected_last:
        raise AdoptionRefusedError(
            ADOPTION_MARK_UNAVAILABLE, "The newest closed bar of the strategy clock is missing."
        )
    candle = candles[-1]
    fresh = entry_prerequisites(
        product=product, candle=candle, now=utc_now(), timeframe=definition.timeframe
    )
    if fresh.decision is RiskDecision.DENY:
        raise AdoptionRefusedError(ADOPTION_MARK_UNAVAILABLE, fresh.detail)
    levels = adoption_levels(definition, product, candles)
    if levels is None or isinstance(levels, EntrySkipReason):
        reason = "the initial-stop ATR is undefined" if levels is None else levels.value
        raise AdoptionRefusedError(
            ADOPTION_LEVELS_UNAVAILABLE, f"No initial stop at the mark: {reason}."
        )
    return product, candle, levels
