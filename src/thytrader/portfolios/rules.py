"""Pure portfolio rules: the mutation plans every portfolio change goes through (ADR 0088).

Every mutation is planned here from the current aggregate and a validated command. A
plan holds the next portfolio row (creation at revision 1, edits at revision + 1), the sleeve
set, and the journal entries the change appends. Stores persist plans inside one transaction after
re-checking the revision under a row lock, so the rules hold under concurrency:

* sleeve weights plus the cash reserve never exceed 1 (exact decimal arithmetic);
* a strategy appears at most once per portfolio;
* every sleeve's strategy is quoted in the portfolio's quote currency;
* a stale ``revision`` is a conflict, never an overwrite.

The allocation arithmetic lives in ``portfolios.allocation`` and the journal entry builders
in ``portfolios.journal_changes``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from thytrader.portfolios.allocation import percent_text, quote_text, require_allocation
from thytrader.portfolios.errors import (
    PortfolioRevisionConflictError,
    PortfolioSleeveExistsError,
    PortfolioValidationError,
)
from thytrader.portfolios.journal_changes import (
    _describe_changes,
    _limits_changes,
    _manager_changes,
    _settings_changes,
    journal_entry,
)
from thytrader.portfolios.models import (
    JournalChange,
    JournalDetail,
    JournalEntry,
    MutationContext,
    Portfolio,
    PortfolioAggregate,
    PortfolioCreateRequest,
    PortfolioUpdateRequest,
    SetWeightsRequest,
    Sleeve,
    SleeveAddRequest,
    SleevesAddRequest,
    SleeveStrategy,
    SleeveUpdateRequest,
    SleeveView,
)
from thytrader.portfolios.vocabulary import MAX_SLEEVES, JournalKind

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.portfolios.models import SleeveBatchItem


@dataclass(frozen=True, slots=True)
class MutationPlan:
    """The next portfolio row, the complete next sleeve set, and the journal to append."""

    portfolio: Portfolio
    sleeves: tuple[Sleeve, ...]
    journal: tuple[JournalEntry, ...]


def require_revision(portfolio: Portfolio, expected: int) -> None:
    """Refuse a mutation planned against a stale revision."""
    if portfolio.revision != expected:
        raise PortfolioRevisionConflictError(portfolio.revision)


def plan_create(
    request: PortfolioCreateRequest,
    *,
    portfolio_id: UUID,
    context: MutationContext,
    strategies: Sequence[SleeveStrategy] = (),
    sleeve_ids: Sequence[UUID] = (),
) -> MutationPlan:
    """Plan a complete portfolio and its journal at revision 1, before any writes."""
    now = context.occurred_at
    portfolio = Portfolio(
        portfolio_id=portfolio_id,
        name=request.name,
        mode=request.mode,
        quote_currency=request.quote_currency,
        capital_quote=request.capital_quote,
        cash_reserve_fraction=request.cash_reserve_fraction,
        limits=request.limits,
        manager=request.manager,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    mode = "live" if request.mode == "live" else "paper"
    summary = (
        f"Created {mode} portfolio “{request.name}” with "
        f"{quote_text(request.capital_quote, request.quote_currency)} and a "
        f"{percent_text(request.cash_reserve_fraction)} cash reserve."
    )
    changes = (
        JournalChange(field="mode", after=request.mode),
        JournalChange(field="quote_currency", after=request.quote_currency),
        JournalChange(field="capital_quote", after=request.capital_quote),
        JournalChange(field="cash_reserve_fraction", after=request.cash_reserve_fraction),
    )
    entry = journal_entry(
        portfolio_id,
        kind="created",
        context=context,
        summary=summary,
        revision=1,
        detail=JournalDetail(reason="operator", changes=changes),
    )
    sleeves = _plan_sleeves(
        portfolio, (), strategies, request.sleeves, sleeve_ids=sleeve_ids, context=context
    )
    return replace(sleeves, journal=(entry, *sleeves.journal))


def plan_update(
    current: PortfolioAggregate, request: PortfolioUpdateRequest, *, context: MutationContext
) -> MutationPlan | None:
    """Plan a settings, limits, and/or manager change; None when nothing differs."""
    portfolio = current.portfolio
    require_revision(portfolio, request.revision)
    settings = _settings_changes(portfolio, request)
    limits = _limits_changes(portfolio.limits, request.limits)
    manager = _manager_changes(portfolio.manager, request.manager)
    if not (settings or limits or manager):
        return None
    reserve = request.cash_reserve_fraction or portfolio.cash_reserve_fraction
    require_allocation((view.sleeve.weight_fraction for view in current.sleeves), reserve)
    revision = portfolio.revision + 1
    next_portfolio = replace(
        portfolio,
        name=request.name or portfolio.name,
        capital_quote=request.capital_quote or portfolio.capital_quote,
        cash_reserve_fraction=reserve,
        limits=request.limits or portfolio.limits,
        manager=request.manager or portfolio.manager,
        revision=revision,
        updated_at=context.occurred_at,
    )
    sections: tuple[tuple[JournalKind, tuple[JournalChange, ...], str], ...] = (
        ("settings_changed", settings, "settings"),
        ("limits_changed", limits, "limits"),
        ("manager_changed", manager, "manager settings"),
    )
    entries = tuple(
        journal_entry(
            portfolio.portfolio_id,
            kind=kind,
            context=context,
            summary=f"Changed {label}: {_describe_changes(changes)}.",
            revision=revision,
            detail=JournalDetail(reason="operator", changes=changes),
        )
        for kind, changes, label in sections
        if changes
    )
    return MutationPlan(
        portfolio=next_portfolio,
        sleeves=tuple(view.sleeve for view in current.sleeves),
        journal=entries,
    )


def plan_add_sleeve(
    current: PortfolioAggregate,
    strategy: SleeveStrategy,
    request: SleeveAddRequest,
    *,
    sleeve_id: UUID,
    context: MutationContext,
) -> MutationPlan:
    """Plan one new sleeve after the quote, uniqueness, count, and allocation checks."""
    portfolio = current.portfolio
    require_revision(portfolio, request.revision)
    for view in current.sleeves:
        if view.sleeve.strategy_id == strategy.strategy_id:
            raise PortfolioSleeveExistsError(view.sleeve.sleeve_id)
    if len(current.sleeves) >= MAX_SLEEVES:
        raise PortfolioValidationError(
            "portfolio_sleeve_limit", f"A portfolio holds at most {MAX_SLEEVES} sleeves."
        )
    _require_strategy_quote(strategy, portfolio)
    require_allocation(
        (*(view.sleeve.weight_fraction for view in current.sleeves), request.weight_fraction),
        portfolio.cash_reserve_fraction,
    )
    now = context.occurred_at
    sleeve = Sleeve(
        sleeve_id=sleeve_id,
        portfolio_id=portfolio.portfolio_id,
        strategy_id=strategy.strategy_id,
        weight_fraction=request.weight_fraction,
        note=request.note or None,
        created_at=now,
        updated_at=now,
    )
    revision = portfolio.revision + 1
    market = strategy.product_id or "unknown market"
    clock = f" · {strategy.timeframe}" if strategy.timeframe else ""
    entry = journal_entry(
        portfolio.portfolio_id,
        kind="sleeve_added",
        context=context,
        summary=(
            f"Added sleeve “{strategy.name}” ({market}{clock}) at "
            f"{percent_text(request.weight_fraction)}."
        ),
        revision=revision,
        detail=JournalDetail(
            sleeve_id=sleeve_id,
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.name,
            reason="operator",
            changes=(JournalChange(field="weight", after=percent_text(request.weight_fraction)),),
        ),
    )
    return MutationPlan(
        portfolio=replace(portfolio, revision=revision, updated_at=now),
        sleeves=(*(view.sleeve for view in current.sleeves), sleeve),
        journal=(entry,),
    )


def plan_add_sleeves(
    current: PortfolioAggregate,
    strategies: Sequence[SleeveStrategy],
    request: SleevesAddRequest,
    *,
    sleeve_ids: Sequence[UUID],
    context: MutationContext,
) -> MutationPlan:
    """Plan several new sleeves as one revision, with one ``sleeve_added`` entry each.

    Every check of :func:`plan_add_sleeve` applies to the whole batch (one sleeve per
    strategy, the sleeve cap, the quote currency, and weights plus reserve at most 1), so
    the batch is applied completely or not at all.
    """
    portfolio = current.portfolio
    require_revision(portfolio, request.revision)
    return _plan_sleeves(
        replace(portfolio, revision=portfolio.revision + 1, updated_at=context.occurred_at),
        current.sleeves,
        strategies,
        request.sleeves,
        sleeve_ids=sleeve_ids,
        context=context,
    )


def _plan_sleeves(
    portfolio: Portfolio,
    current: Sequence[SleeveView],
    strategies: Sequence[SleeveStrategy],
    items: Sequence[SleeveBatchItem],
    *,
    sleeve_ids: Sequence[UUID],
    context: MutationContext,
) -> MutationPlan:
    """Validate and build a sleeve batch at the supplied portfolio's target revision."""
    existing = {view.sleeve.strategy_id: view.sleeve.sleeve_id for view in current}
    for strategy in strategies:
        if strategy.strategy_id in existing:
            raise PortfolioSleeveExistsError(existing[strategy.strategy_id])
    if len(current) + len(items) > MAX_SLEEVES:
        raise PortfolioValidationError(
            "portfolio_sleeve_limit",
            f"A portfolio holds at most {MAX_SLEEVES} sleeves; it has "
            f"{len(current)} and the batch adds {len(items)}.",
        )
    for strategy in strategies:
        _require_strategy_quote(strategy, portfolio)
    require_allocation(
        (
            *(view.sleeve.weight_fraction for view in current),
            *(item.weight_fraction for item in items),
        ),
        portfolio.cash_reserve_fraction,
    )
    now = context.occurred_at
    added: list[Sleeve] = []
    entries: list[JournalEntry] = []
    for strategy, item, sleeve_id in zip(strategies, items, sleeve_ids, strict=True):
        added.append(
            Sleeve(
                sleeve_id=sleeve_id,
                portfolio_id=portfolio.portfolio_id,
                strategy_id=strategy.strategy_id,
                weight_fraction=item.weight_fraction,
                note=item.note or None,
                created_at=now,
                updated_at=now,
            )
        )
        entries.append(
            _sleeve_added_entry(
                portfolio,
                strategy,
                item.weight_fraction,
                sleeve_id,
                revision=portfolio.revision,
                context=context,
            )
        )
    return MutationPlan(
        portfolio=portfolio,
        sleeves=(*(view.sleeve for view in current), *added),
        journal=tuple(entries),
    )


def _sleeve_added_entry(
    portfolio: Portfolio,
    strategy: SleeveStrategy,
    weight_fraction: str,
    sleeve_id: UUID,
    *,
    revision: int,
    context: MutationContext,
) -> JournalEntry:
    """The ``sleeve_added`` journal entry for one new sleeve."""
    market = strategy.product_id or "unknown market"
    clock = f" · {strategy.timeframe}" if strategy.timeframe else ""
    return journal_entry(
        portfolio.portfolio_id,
        kind="sleeve_added",
        context=context,
        summary=(
            f"Added sleeve “{strategy.name}” ({market}{clock}) at {percent_text(weight_fraction)}."
        ),
        revision=revision,
        detail=JournalDetail(
            sleeve_id=sleeve_id,
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.name,
            reason="operator",
            changes=(JournalChange(field="weight", after=percent_text(weight_fraction)),),
        ),
    )


def _require_strategy_quote(strategy: SleeveStrategy, portfolio: Portfolio) -> None:
    """Refuse a strategy whose market is unknown or quoted in another currency."""
    quote = strategy.quote_currency
    if quote is None:
        raise PortfolioValidationError(
            "portfolio_sleeve_product_unknown",
            f"Strategy “{strategy.name}” has no readable market yet; fix and save it first.",
        )
    if quote != portfolio.quote_currency:
        raise PortfolioValidationError(
            "portfolio_sleeve_quote_mismatch",
            f"Strategy “{strategy.name}” trades in {quote}; this portfolio holds "
            f"{portfolio.quote_currency}. Every sleeve must use the portfolio's quote currency.",
        )


def plan_update_sleeve(
    current: PortfolioAggregate,
    sleeve_id: UUID,
    request: SleeveUpdateRequest,
    *,
    context: MutationContext,
) -> MutationPlan | None:
    """Plan a weight and/or note change on one sleeve; None when nothing differs."""
    portfolio = current.portfolio
    require_revision(portfolio, request.revision)
    view = current.sleeve(sleeve_id)
    sleeve = view.sleeve
    weight = request.weight_fraction or sleeve.weight_fraction
    note = sleeve.note if request.note is None else (request.note or None)
    if weight == sleeve.weight_fraction and note == sleeve.note:
        return None
    if weight != sleeve.weight_fraction:
        require_allocation(
            (
                weight if item.sleeve.sleeve_id == sleeve_id else item.sleeve.weight_fraction
                for item in current.sleeves
            ),
            portfolio.cash_reserve_fraction,
        )
    revision = portfolio.revision + 1
    entries = _sleeve_update_entries(
        view, weight=weight, note=note, revision=revision, context=context
    )
    now = context.occurred_at
    updated = replace(sleeve, weight_fraction=weight, note=note, updated_at=now)
    return MutationPlan(
        portfolio=replace(portfolio, revision=revision, updated_at=now),
        sleeves=tuple(
            updated if item.sleeve.sleeve_id == sleeve_id else item.sleeve
            for item in current.sleeves
        ),
        journal=entries,
    )


def _sleeve_update_entries(
    view: SleeveView,
    *,
    weight: str,
    note: str | None,
    revision: int,
    context: MutationContext,
) -> tuple[JournalEntry, ...]:
    """Journal a weight change as ``weights_changed`` and a note change as ``sleeve_updated``."""
    sleeve = view.sleeve
    entries: list[JournalEntry] = []
    if weight != sleeve.weight_fraction:
        change = JournalChange(
            field=view.strategy.name[:64],
            before=percent_text(sleeve.weight_fraction),
            after=percent_text(weight),
        )
        entries.append(
            journal_entry(
                sleeve.portfolio_id,
                kind="weights_changed",
                context=context,
                summary=f"Changed weights: {_describe_changes((change,))}.",
                revision=revision,
                detail=_sleeve_detail(view, change),
            )
        )
    if note != sleeve.note:
        change = JournalChange(field="note", before=sleeve.note, after=note)
        entries.append(
            journal_entry(
                sleeve.portfolio_id,
                kind="sleeve_updated",
                context=context,
                summary=f"Updated the note on “{view.strategy.name}”.",
                revision=revision,
                detail=_sleeve_detail(view, change),
            )
        )
    return tuple(entries)


def _sleeve_detail(view: SleeveView, change: JournalChange) -> JournalDetail:
    """Journal detail naming one sleeve, its strategy, and one change."""
    return JournalDetail(
        sleeve_id=view.sleeve.sleeve_id,
        strategy_id=view.sleeve.strategy_id,
        strategy_name=view.strategy.name,
        reason="operator",
        changes=(change,),
    )


def plan_remove_sleeve(
    current: PortfolioAggregate,
    sleeve_id: UUID,
    *,
    expected_revision: int | None,
    context: MutationContext,
    strategy_deleted: bool = False,
) -> MutationPlan:
    """Plan removing one sleeve; a strategy deletion removes it without a revision guard."""
    portfolio = current.portfolio
    if expected_revision is not None:
        require_revision(portfolio, expected_revision)
    view = current.sleeve(sleeve_id)
    revision = portfolio.revision + 1
    weight = percent_text(view.sleeve.weight_fraction)
    summary = f"Removed sleeve “{view.strategy.name}” ({weight})"
    summary += " because its strategy was deleted." if strategy_deleted else "."
    entry = journal_entry(
        portfolio.portfolio_id,
        kind="sleeve_removed",
        context=context,
        summary=summary,
        revision=revision,
        detail=JournalDetail(
            sleeve_id=sleeve_id,
            strategy_id=view.sleeve.strategy_id,
            strategy_name=view.strategy.name,
            reason="strategy_deleted" if strategy_deleted else "operator",
            changes=(JournalChange(field="weight", before=weight, after=None),),
        ),
    )
    now = context.occurred_at
    return MutationPlan(
        portfolio=replace(portfolio, revision=revision, updated_at=now),
        sleeves=tuple(
            item.sleeve for item in current.sleeves if item.sleeve.sleeve_id != sleeve_id
        ),
        journal=(entry,),
    )


def plan_set_weights(
    current: PortfolioAggregate, request: SetWeightsRequest, *, context: MutationContext
) -> MutationPlan | None:
    """Plan replacing every sleeve weight (and optionally the reserve); None when unchanged."""
    portfolio = current.portfolio
    require_revision(portfolio, request.revision)
    assigned = {item.sleeve_id: item.weight_fraction for item in request.weights}
    existing = {view.sleeve.sleeve_id for view in current.sleeves}
    if len(assigned) != len(request.weights) or set(assigned) != existing:
        raise PortfolioValidationError(
            "portfolio_weights_incomplete",
            "Send exactly one weight for every sleeve in the portfolio (remove a sleeve to "
            "drop it).",
        )
    reserve = request.cash_reserve_fraction or portfolio.cash_reserve_fraction
    require_allocation(assigned.values(), reserve)
    changes = [
        JournalChange(
            field=view.strategy.name[:64],
            before=percent_text(view.sleeve.weight_fraction),
            after=percent_text(assigned[view.sleeve.sleeve_id]),
        )
        for view in current.sleeves
        if assigned[view.sleeve.sleeve_id] != view.sleeve.weight_fraction
    ]
    if reserve != portfolio.cash_reserve_fraction:
        changes.append(
            JournalChange(
                field="cash reserve",
                before=percent_text(portfolio.cash_reserve_fraction),
                after=percent_text(reserve),
            )
        )
    if not changes:
        return None
    revision = portfolio.revision + 1
    now = context.occurred_at
    entry = journal_entry(
        portfolio.portfolio_id,
        kind="weights_changed",
        context=context,
        summary=f"Changed weights: {_describe_changes(tuple(changes))}.",
        revision=revision,
        detail=JournalDetail(reason="operator", changes=tuple(changes)),
    )
    sleeves = tuple(
        replace(view.sleeve, weight_fraction=assigned[view.sleeve.sleeve_id], updated_at=now)
        if assigned[view.sleeve.sleeve_id] != view.sleeve.weight_fraction
        else view.sleeve
        for view in current.sleeves
    )
    return MutationPlan(
        portfolio=replace(
            portfolio, cash_reserve_fraction=reserve, revision=revision, updated_at=now
        ),
        sleeves=sleeves,
        journal=(entry,),
    )
