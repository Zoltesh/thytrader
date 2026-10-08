"""Portfolio journal entries and the field-by-field change descriptions they carry.

Every entry is stamped with the mutation's actor, channel, and instant. Settings, limits,
and manager changes render as ``field before → after`` text, abbreviated in the summary
while the full values stay in ``detail.changes``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.decimal_text import canonical_decimal
from thytrader.portfolios.allocation import percent_text, quote_text
from thytrader.portfolios.models import (
    JournalChange,
    JournalDetail,
    JournalEntry,
    MutationContext,
    Portfolio,
    PortfolioLimits,
    PortfolioUpdateRequest,
)
from thytrader.trading.ids import uuid7

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.portfolios.models import ManagerSettings
    from thytrader.portfolios.vocabulary import JournalKind


_NONE_TEXT = "none"


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
