"""In-transaction portfolio row operations shared by PostgreSQL stores (ADR 0088).

Kept free of backtest imports so the strategy-deletion transaction
(:mod:`thytrader.persistence.postgres_strategies`) can journal and remove a deleted
strategy's sleeves without an import cycle. Callers own the transaction and the row locks:
lock the strategy row before any portfolio row, then portfolio rows in ``portfolio_id``
order, so concurrent deletions and sleeve additions cannot deadlock.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - runtime casts of database values.
import json
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import Select, delete, func, insert, select, update

from thytrader.market_data.products import SpotQuoteCurrency  # noqa: TC001 - runtime cast.
from thytrader.persistence.schema import (
    portfolio_journal_entries,
    portfolio_sleeves,
    portfolios,
    strategies,
)
from thytrader.portfolios.errors import PortfolioNotFoundError, PortfolioRevisionConflictError
from thytrader.portfolios.models import (
    JournalDetail,
    JournalEntry,
    ManagerPermissions,
    ManagerSettings,
    MutationContext,
    Portfolio,
    PortfolioAggregate,
    PortfolioLimits,
    Sleeve,
    SleeveStrategy,
    SleeveView,
    document_product_ids,
)
from thytrader.portfolios.rules import MutationPlan, plan_remove_sleeve

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.portfolios.vocabulary import (
        JournalActor,
        JournalChannel,
        JournalKind,
        PortfolioMode,
    )


def portfolio_values(portfolio: Portfolio) -> dict[str, object]:
    """Column values for one portfolio row."""
    limits = portfolio.limits
    manager = portfolio.manager
    permissions = manager.permissions
    return {
        "portfolio_id": str(portfolio.portfolio_id),
        "name": portfolio.name,
        "mode": portfolio.mode,
        "quote_currency": portfolio.quote_currency,
        "capital_quote": portfolio.capital_quote,
        "cash_reserve_fraction": portfolio.cash_reserve_fraction,
        "max_total_exposure_fraction": limits.max_total_exposure_fraction,
        "max_per_asset_fraction": limits.max_per_asset_fraction,
        "daily_loss_quote": limits.daily_loss_quote,
        "max_drawdown_fraction": limits.max_drawdown_fraction,
        "manager_mandate": manager.mandate,
        "manager_may_rebalance": permissions.may_rebalance,
        "manager_max_weight_change_per_week": permissions.max_weight_change_per_week,
        "manager_may_pause_sleeves": permissions.may_pause_sleeves,
        "manager_may_propose_sleeves": permissions.may_propose_sleeves,
        "revision": portfolio.revision,
        "created_at": portfolio.created_at,
        "updated_at": portfolio.updated_at,
    }


def portfolio_from_row(row: RowMapping) -> Portfolio:
    """Rebuild one portfolio from its row (values revalidate through the domain models)."""
    return Portfolio(
        portfolio_id=UUID(cast("str", row["portfolio_id"])),
        name=cast("str", row["name"]),
        mode=cast("PortfolioMode", row["mode"]),
        quote_currency=cast("SpotQuoteCurrency", row["quote_currency"]),
        capital_quote=cast("str", row["capital_quote"]),
        cash_reserve_fraction=cast("str", row["cash_reserve_fraction"]),
        limits=PortfolioLimits(
            max_total_exposure_fraction=cast("str", row["max_total_exposure_fraction"]),
            max_per_asset_fraction=cast("str", row["max_per_asset_fraction"]),
            daily_loss_quote=cast("str | None", row["daily_loss_quote"]),
            max_drawdown_fraction=cast("str | None", row["max_drawdown_fraction"]),
        ),
        manager=ManagerSettings(
            mandate=cast("str", row["manager_mandate"]),
            permissions=ManagerPermissions(
                may_rebalance=bool(row["manager_may_rebalance"]),
                max_weight_change_per_week=cast("str", row["manager_max_weight_change_per_week"]),
                may_pause_sleeves=bool(row["manager_may_pause_sleeves"]),
                may_propose_sleeves=bool(row["manager_may_propose_sleeves"]),
            ),
        ),
        revision=int(cast("int", row["revision"])),
        created_at=cast("datetime", row["created_at"]),
        updated_at=cast("datetime", row["updated_at"]),
    )


def _sleeve_statement(portfolio_ids: Sequence[str]) -> Select[tuple[object, ...]]:
    """Sleeves of the given portfolios joined with their strategy's current facts."""
    return (
        select(
            portfolio_sleeves,
            strategies.c.name.label("strategy_name"),
            strategies.c.product_id.label("strategy_product_id"),
            strategies.c.timeframe.label("strategy_timeframe"),
            strategies.c.is_valid.label("strategy_valid"),
            strategies.c.current_fingerprint.label("strategy_current_fingerprint"),
            strategies.c.document.label("strategy_document"),
        )
        .select_from(
            portfolio_sleeves.join(
                strategies, portfolio_sleeves.c.strategy_id == strategies.c.strategy_id
            )
        )
        .where(portfolio_sleeves.c.portfolio_id.in_(list(portfolio_ids)))
        .order_by(
            portfolio_sleeves.c.portfolio_id,
            portfolio_sleeves.c.created_at,
            portfolio_sleeves.c.sleeve_id,
        )
    )


def _sleeve_view(row: RowMapping) -> SleeveView:
    """Build one sleeve and its strategy facts from a joined row."""
    product_id = cast("str | None", row["strategy_product_id"])
    return SleeveView(
        sleeve=Sleeve(
            sleeve_id=UUID(cast("str", row["sleeve_id"])),
            portfolio_id=UUID(cast("str", row["portfolio_id"])),
            strategy_id=UUID(cast("str", row["strategy_id"])),
            weight_fraction=cast("str", row["weight_fraction"]),
            note=cast("str | None", row["note"]),
            created_at=cast("datetime", row["created_at"]),
            updated_at=cast("datetime", row["updated_at"]),
        ),
        strategy=SleeveStrategy(
            strategy_id=UUID(cast("str", row["strategy_id"])),
            name=cast("str", row["strategy_name"]),
            product_id=product_id,
            covered_product_ids=covered_products(cast("str", row["strategy_document"]), product_id),
            timeframe=cast("str | None", row["strategy_timeframe"]),
            valid=bool(row["strategy_valid"]),
            current_fingerprint=cast("str | None", row["strategy_current_fingerprint"]),
        ),
    )


def covered_products(document_text: str, product_id: str | None) -> tuple[str, ...]:
    """Covered products from the stored strategy document (primary only when unreadable)."""
    try:
        document: object = json.loads(document_text)
    except json.JSONDecodeError:
        return (product_id,) if product_id is not None else ()
    if not isinstance(document, dict):
        return (product_id,) if product_id is not None else ()
    return document_product_ids(cast("dict[str, JsonValue]", document), product_id)


async def load_aggregate(
    connection: AsyncConnection, portfolio_id: UUID, *, lock: bool
) -> PortfolioAggregate:
    """Read one portfolio (row-locked when ``lock``) with its sleeves."""
    statement = select(portfolios).where(portfolios.c.portfolio_id == str(portfolio_id))
    if lock:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise PortfolioNotFoundError("Portfolio was not found.")
    aggregates = await aggregates_for(connection, (row,))
    return aggregates[0]


async def aggregates_for(
    connection: AsyncConnection, rows: Sequence[RowMapping]
) -> tuple[PortfolioAggregate, ...]:
    """Attach sleeves to already-read portfolio rows (one sleeve query)."""
    built = [portfolio_from_row(row) for row in rows]
    if not built:
        return ()
    sleeve_rows = (
        (await connection.execute(_sleeve_statement([str(item.portfolio_id) for item in built])))
        .mappings()
        .all()
    )
    grouped: dict[UUID, list[SleeveView]] = {}
    for sleeve_row in sleeve_rows:
        view = _sleeve_view(sleeve_row)
        grouped.setdefault(view.sleeve.portfolio_id, []).append(view)
    return tuple(
        PortfolioAggregate(portfolio=item, sleeves=tuple(grouped.get(item.portfolio_id, ())))
        for item in built
    )


async def apply_plan(
    connection: AsyncConnection, plan: MutationPlan, *, previous: PortfolioAggregate | None
) -> None:
    """Write one plan: the portfolio row (revision-guarded), the sleeve diff, the journal."""
    values = portfolio_values(plan.portfolio)
    if previous is None:
        await connection.execute(insert(portfolios).values(**values))
    else:
        result = await connection.execute(
            update(portfolios)
            .where(
                portfolios.c.portfolio_id == str(plan.portfolio.portfolio_id),
                portfolios.c.revision == previous.portfolio.revision,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise PortfolioRevisionConflictError(previous.portfolio.revision)
    await _write_sleeve_diff(
        connection, () if previous is None else tuple(v.sleeve for v in previous.sleeves), plan
    )
    await insert_journal(connection, plan.journal)


async def _write_sleeve_diff(
    connection: AsyncConnection, before: tuple[Sleeve, ...], plan: MutationPlan
) -> None:
    """Delete, insert, and update sleeves so the table matches the plan."""
    old = {sleeve.sleeve_id: sleeve for sleeve in before}
    new = {sleeve.sleeve_id: sleeve for sleeve in plan.sleeves}
    removed = [str(sleeve_id) for sleeve_id in old if sleeve_id not in new]
    if removed:
        await connection.execute(
            delete(portfolio_sleeves).where(portfolio_sleeves.c.sleeve_id.in_(removed))
        )
    for sleeve_id, sleeve in new.items():
        existing = old.get(sleeve_id)
        if existing is None:
            await connection.execute(insert(portfolio_sleeves).values(**_sleeve_values(sleeve)))
        elif existing != sleeve:
            await connection.execute(
                update(portfolio_sleeves)
                .where(portfolio_sleeves.c.sleeve_id == str(sleeve_id))
                .values(
                    weight_fraction=sleeve.weight_fraction,
                    note=sleeve.note,
                    updated_at=sleeve.updated_at,
                )
            )


def _sleeve_values(sleeve: Sleeve) -> dict[str, object]:
    """Column values for one sleeve row."""
    return {
        "sleeve_id": str(sleeve.sleeve_id),
        "portfolio_id": str(sleeve.portfolio_id),
        "strategy_id": str(sleeve.strategy_id),
        "weight_fraction": sleeve.weight_fraction,
        "note": sleeve.note,
        "created_at": sleeve.created_at,
        "updated_at": sleeve.updated_at,
    }


async def insert_journal(connection: AsyncConnection, entries: Sequence[JournalEntry]) -> None:
    """Append journal entries in order (the sequence column records append order)."""
    for entry in entries:
        await connection.execute(
            insert(portfolio_journal_entries).values(
                entry_id=entry.entry_id,
                portfolio_id=str(entry.portfolio_id),
                occurred_at=entry.occurred_at,
                kind=entry.kind,
                actor=entry.actor,
                channel=entry.channel,
                summary=entry.summary,
                detail=entry.detail.model_dump_json(exclude_none=True),
                revision=entry.revision,
            )
        )


def journal_from_row(row: RowMapping) -> JournalEntry:
    """Rebuild one journal entry from its row."""
    return JournalEntry(
        entry_id=cast("UUID", row["entry_id"]),
        portfolio_id=UUID(cast("str", row["portfolio_id"])),
        occurred_at=cast("datetime", row["occurred_at"]),
        kind=cast("JournalKind", row["kind"]),
        actor=cast("JournalActor", row["actor"]),
        channel=cast("JournalChannel", row["channel"]),
        summary=cast("str", row["summary"]),
        detail=JournalDetail.model_validate_json(cast("str", row["detail"])),
        revision=int(cast("int", row["revision"])),
    )


async def count_strategy_sleeves(connection: AsyncConnection, strategy_id: str) -> int:
    """How many sleeves hold the strategy (what deleting it would remove)."""
    statement = (
        select(func.count())
        .select_from(portfolio_sleeves)
        .where(portfolio_sleeves.c.strategy_id == strategy_id)
    )
    return int((await connection.execute(statement)).scalar_one())


async def remove_strategy_sleeves_in(
    connection: AsyncConnection, strategy_id: str, *, occurred_at: datetime
) -> int:
    """Journal and remove every sleeve of a strategy being deleted (caller locked the strategy).

    Each affected portfolio is locked in ``portfolio_id`` order, gets a ``sleeve_removed``
    entry (actor ``system``, reason ``strategy_deleted``), and advances its revision.
    """
    rows = (
        await connection.execute(
            select(portfolio_sleeves.c.sleeve_id, portfolio_sleeves.c.portfolio_id)
            .where(portfolio_sleeves.c.strategy_id == strategy_id)
            .order_by(portfolio_sleeves.c.portfolio_id)
        )
    ).all()
    context = MutationContext(actor="system", channel="system", occurred_at=occurred_at)
    for sleeve_id, portfolio_id in rows:
        current = await load_aggregate(connection, UUID(str(portfolio_id)), lock=True)
        plan = plan_remove_sleeve(
            current,
            UUID(str(sleeve_id)),
            expected_revision=None,
            context=context,
            strategy_deleted=True,
        )
        await apply_plan(connection, plan, previous=current)
    return len(rows)
