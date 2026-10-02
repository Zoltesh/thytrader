"""Pure portfolio rules: allocation checks, mutation plans, and journal entries (ADR 0088).

Every mutation is planned here from the current aggregate and a validated command. A
plan holds the next portfolio row (revision + 1), the complete next sleeve set, and the
journal entries the change appends. Stores persist plans inside one transaction after
re-checking the revision under a row lock, so the rules hold under concurrency:

* sleeve weights plus the cash reserve never exceed 1 (exact decimal arithmetic);
* a strategy appears at most once per portfolio;
* every sleeve's strategy is quoted in the portfolio's quote currency;
* a stale ``revision`` is a conflict, never an overwrite.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.ids import uuid7
from thytrader.portfolios.models import (
    MAX_SLEEVES,
    JournalChange,
    JournalDetail,
    JournalEntry,
    JournalKind,
    MutationContext,
    Portfolio,
    PortfolioAggregate,
    PortfolioCreateRequest,
    PortfolioLimits,
    PortfolioRevisionConflictError,
    PortfolioSleeveExistsError,
    PortfolioUpdateRequest,
    PortfolioValidationError,
    SetWeightsRequest,
    Sleeve,
    SleeveAddRequest,
    SleeveStrategy,
    SleeveUpdateRequest,
    SleeveView,
    asset_of,
)
from thytrader.research.indicators import canonical_decimal

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from thytrader.portfolios.models import ManagerSettings

_ONE = Decimal(1)
_HUNDRED = Decimal(100)
_NONE_TEXT = "none"


@dataclass(frozen=True, slots=True)
class MutationPlan:
    """The next portfolio row, the complete next sleeve set, and the journal to append."""

    portfolio: Portfolio
    sleeves: tuple[Sleeve, ...]
    journal: tuple[JournalEntry, ...]


@dataclass(frozen=True, slots=True)
class AssetAllocation:
    """Weight held in one base asset (a multi-product sleeve counts toward each asset)."""

    asset: str
    weight_fraction: str
    sleeve_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AllocationSummary:
    """How capital splits between sleeves, the cash reserve, and unallocated cash."""

    allocated_fraction: str
    cash_reserve_fraction: str
    unallocated_fraction: str
    allocated_quote: str
    cash_reserve_quote: str
    unallocated_quote: str
    assets: tuple[AssetAllocation, ...]
    largest_asset: AssetAllocation | None
    largest_asset_within_limit: bool | None


def percent_text(fraction: str | Decimal) -> str:
    """Render a fraction as a percent without trailing zeros (``0.3333`` → ``33.33%``)."""
    return f"{canonical_decimal(Decimal(fraction) * _HUNDRED)}%"


def quote_text(amount: str | Decimal, currency: str) -> str:
    """Render a quote amount with grouped digits and its currency."""
    text = canonical_decimal(Decimal(amount))
    whole, separator, fraction = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    digits = whole.removeprefix("-")
    grouped = f"{int(digits):,}"
    return f"{sign}{grouped}{separator}{fraction} {currency}"


def sleeve_capital(capital_quote: str, weight_fraction: str) -> str:
    """Return the exact quote capital one weight represents."""
    return canonical_decimal(Decimal(capital_quote) * Decimal(weight_fraction))


def allocated_fraction(weights: Iterable[str]) -> Decimal:
    """Exact sum of sleeve weights."""
    return sum((Decimal(weight) for weight in weights), start=Decimal(0))


def require_allocation(weights: Iterable[str], cash_reserve_fraction: str) -> None:
    """Refuse weights plus the cash reserve above 100% of capital."""
    allocated = allocated_fraction(weights)
    reserve = Decimal(cash_reserve_fraction)
    total = allocated + reserve
    if total > _ONE:
        raise PortfolioValidationError(
            "portfolio_allocation_exceeded",
            f"Sleeve weights ({percent_text(allocated)}) plus the cash reserve "
            f"({percent_text(reserve)}) come to {percent_text(total)}; together they must "
            "not exceed 100%.",
        )


def require_revision(portfolio: Portfolio, expected: int) -> None:
    """Refuse a mutation planned against a stale revision."""
    if portfolio.revision != expected:
        raise PortfolioRevisionConflictError(portfolio.revision)


def allocation_summary(aggregate: PortfolioAggregate) -> AllocationSummary:
    """Summarize allocation by sleeve, reserve, unallocated cash, and base asset."""
    portfolio = aggregate.portfolio
    weights = [view.sleeve.weight_fraction for view in aggregate.sleeves]
    allocated = allocated_fraction(weights)
    reserve = Decimal(portfolio.cash_reserve_fraction)
    unallocated = max(Decimal(0), _ONE - allocated - reserve)
    capital = Decimal(portfolio.capital_quote)
    assets = _asset_allocations(aggregate.sleeves)
    largest = assets[0] if assets else None
    within: bool | None = None
    if largest is not None:
        within = Decimal(largest.weight_fraction) <= Decimal(
            portfolio.limits.max_per_asset_fraction
        )
    return AllocationSummary(
        allocated_fraction=canonical_decimal(allocated),
        cash_reserve_fraction=canonical_decimal(reserve),
        unallocated_fraction=canonical_decimal(unallocated),
        allocated_quote=canonical_decimal(capital * allocated),
        cash_reserve_quote=canonical_decimal(capital * reserve),
        unallocated_quote=canonical_decimal(capital * unallocated),
        assets=assets,
        largest_asset=largest,
        largest_asset_within_limit=within,
    )


def _asset_allocations(sleeves: tuple[SleeveView, ...]) -> tuple[AssetAllocation, ...]:
    """Group sleeve weights by base asset, largest first (ties by asset name)."""
    weights: dict[str, Decimal] = {}
    members: dict[str, list[UUID]] = {}
    for view in sleeves:
        assets = dict.fromkeys(asset_of(product) for product in view.strategy.covered_product_ids)
        for asset in assets:
            weights[asset] = weights.get(asset, Decimal(0)) + Decimal(view.sleeve.weight_fraction)
            members.setdefault(asset, []).append(view.sleeve.sleeve_id)
    ordered = sorted(weights, key=lambda asset: (-weights[asset], asset))
    return tuple(
        AssetAllocation(
            asset=asset,
            weight_fraction=canonical_decimal(weights[asset]),
            sleeve_ids=tuple(members[asset]),
        )
        for asset in ordered
    )


def journal_entry(
    portfolio_id: UUID,
    *,
    kind: JournalKind,
    context: MutationContext,
    summary: str,
    revision: int,
    detail: JournalDetail | None = None,
) -> JournalEntry:
    """Build one journal entry stamped with the mutation's actor, channel, and instant."""
    return JournalEntry(
        entry_id=uuid7(context.occurred_at),
        portfolio_id=portfolio_id,
        occurred_at=context.occurred_at,
        kind=kind,
        actor=context.actor,
        channel=context.channel,
        summary=summary[:500],
        detail=detail or JournalDetail(),
        revision=revision,
    )


def plan_create(
    request: PortfolioCreateRequest, *, portfolio_id: UUID, context: MutationContext
) -> MutationPlan:
    """Plan a new portfolio at revision 1 with its ``created`` journal entry."""
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
    return MutationPlan(portfolio=portfolio, sleeves=(), journal=(entry,))


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


def _settings_changes(
    portfolio: Portfolio, request: PortfolioUpdateRequest
) -> tuple[JournalChange, ...]:
    """Name, capital, and reserve changes, rendered for the journal."""
    changes: list[JournalChange] = []
    if request.name is not None and request.name != portfolio.name:
        changes.append(JournalChange(field="name", before=portfolio.name, after=request.name))
    if request.capital_quote is not None and request.capital_quote != portfolio.capital_quote:
        changes.append(
            JournalChange(
                field="capital",
                before=quote_text(portfolio.capital_quote, portfolio.quote_currency),
                after=quote_text(request.capital_quote, portfolio.quote_currency),
            )
        )
    if (
        request.cash_reserve_fraction is not None
        and request.cash_reserve_fraction != portfolio.cash_reserve_fraction
    ):
        changes.append(
            JournalChange(
                field="cash reserve",
                before=percent_text(portfolio.cash_reserve_fraction),
                after=percent_text(request.cash_reserve_fraction),
            )
        )
    return tuple(changes)


def _limits_changes(
    current: PortfolioLimits, proposed: PortfolioLimits | None
) -> tuple[JournalChange, ...]:
    """Field-by-field limit changes, rendered for the journal."""
    if proposed is None or proposed == current:
        return ()
    pairs = (
        (
            "max total exposure",
            current.max_total_exposure_fraction,
            proposed.max_total_exposure_fraction,
            True,
        ),
        (
            "max per asset",
            current.max_per_asset_fraction,
            proposed.max_per_asset_fraction,
            True,
        ),
        ("daily loss stop", current.daily_loss_quote, proposed.daily_loss_quote, False),
        (
            "max drawdown stop",
            current.max_drawdown_fraction,
            proposed.max_drawdown_fraction,
            True,
        ),
    )
    return tuple(
        JournalChange(
            field=label,
            before=_limit_text(before, as_percent=as_percent),
            after=_limit_text(after, as_percent=as_percent),
        )
        for label, before, after, as_percent in pairs
        if before != after
    )


def _limit_text(value: str | None, *, as_percent: bool) -> str:
    """Render one optional limit value."""
    if value is None:
        return _NONE_TEXT
    return percent_text(value) if as_percent else canonical_decimal(Decimal(value))


def _manager_changes(
    current: ManagerSettings, proposed: ManagerSettings | None
) -> tuple[JournalChange, ...]:
    """Mandate and permission changes, rendered for the journal."""
    if proposed is None or proposed == current:
        return ()
    changes: list[JournalChange] = []
    if proposed.mandate != current.mandate:
        changes.append(
            JournalChange(
                field="mandate",
                before=current.mandate or _NONE_TEXT,
                after=proposed.mandate or _NONE_TEXT,
            )
        )
    before, after = current.permissions, proposed.permissions
    for label, old, new in (
        ("may rebalance", before.may_rebalance, after.may_rebalance),
        ("may pause sleeves", before.may_pause_sleeves, after.may_pause_sleeves),
        ("may propose sleeves", before.may_propose_sleeves, after.may_propose_sleeves),
    ):
        if old != new:
            changes.append(JournalChange(field=label, before=_on_off(old), after=_on_off(new)))
    if after.max_weight_change_per_week != before.max_weight_change_per_week:
        changes.append(
            JournalChange(
                field="max weight change per week",
                before=percent_text(before.max_weight_change_per_week),
                after=percent_text(after.max_weight_change_per_week),
            )
        )
    return tuple(changes)


def _on_off(value: bool) -> str:
    """Render one permission flag."""
    return "on" if value else "off"


def _describe_changes(changes: tuple[JournalChange, ...]) -> str:
    """Join changes as ``field before → after`` phrases (long text abbreviated)."""
    return "; ".join(
        f"{change.field} {_short(change.before)} → {_short(change.after)}" for change in changes
    )


def _short(value: str | None) -> str:
    """Abbreviate long journal values (the full text stays in ``detail.changes``)."""
    text = _NONE_TEXT if value is None else value.replace("\n", " ")
    return text if len(text) <= 60 else f"{text[:57]}…"


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
