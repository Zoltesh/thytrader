"""HTTP views of a deployed portfolio and its proposals (ADR 0091).

Shared by the API routes, the ``thytrader-portfolio`` and ``thytrader-runtime`` CLIs
(which validate responses with these models), and the manager briefing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from thytrader.decimal_text import canonical_decimal
from thytrader.execution.book_marks import recorded_position_entry_fees, unrealized_pnl
from thytrader.market_data.products import is_spot_product_id, quote_currency
from thytrader.portfolios.deployment import (
    PortfolioDeploymentState,
    daily_pnl,
    drawdown_fraction,
    net_pnl,
    roll_baselines,
    run_members,
)
from thytrader.portfolios.models import sleeve_issues, utc_text
from thytrader.portfolios.proposals import Proposal
from thytrader.portfolios.vocabulary import PortfolioMode, SleeveIssueCode
from thytrader.trading.exposure import product_exposure, risk_bearing_snapshots
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.lifecycle import occupies_running_slot
from thytrader.trading.models import (
    DeploymentMode,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.trading.protection import (
    book_inventory_reasons,
    book_position_state,
    book_protection_evidence,
    deployment_position_state,
    protection_evidence_response,
)
from thytrader.trading.protection_models import PositionState, ProtectionEvidenceResponse

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.execution.book_marks import BookMark
    from thytrader.market_data.products import SpotQuoteCurrency
    from thytrader.portfolios.deployment import SleeveBook
    from thytrader.portfolios.runtime import PortfolioDeploymentSnapshot
    from thytrader.portfolios.runtime_outcomes import PortfolioActionResult
    from thytrader.trading.models import Deployment, DeploymentSnapshot

_ZERO = Decimal(0)
_FRACTION = Decimal("0.000001")


class SleeveOpenBookResponse(BaseModel):
    """One open book of a sleeve bot, compactly (ADR 0098)."""

    product_id: str
    side: str
    quantity: str
    entry_price: str
    stop_price: str
    target_price: str | None = Field(description="Null when the strategy declares none.")
    entered_bar: str = Field(description="UTC start of the bar the book was entered on.")
    position_state: str = Field(
        description="open_protected, open_unprotected, open_unverified, or exiting (ADR 0097)."
    )
    protection: ProtectionEvidenceResponse = Field(
        description=(
            "Quantitative stop cover (ADR 0112). Paper is worker-dependent, not a "
            "venue-resting stop. A take-profit alone is not cover."
        ),
    )
    mark_price: str | None = Field(
        default=None,
        description=(
            "Close of the newest bar the bot evaluated for this product; null without a "
            "journaled close or on responses that do not mark books."
        ),
    )
    marked_at: str | None = Field(default=None, description="UTC close of that bar.")
    unrealized_pnl: str | None = Field(
        default=None,
        description=(
            "Gross PnL of this projected row at mark_price, not complete inventory PnL; "
            "before exit fees and null without a mark."
        ),
    )
    entry_fees: str | None = Field(
        default=None,
        description="Paid entry fees allocated to held quantity; null without verified evidence.",
    )
    unrealized_pnl_net: str | None = Field(
        default=None,
        description="Gross PnL minus entry_fees; future exit fees excluded; null without evidence.",
    )


class SleeveDeploymentResponse(BaseModel):
    """One sleeve's bot as the portfolio sees it (open it at ``/deployments/{id}``)."""

    deployment_id: UUID
    strategy_id: UUID | None
    strategy_name: str | None
    status: str
    phase: str = Field(
        description="Raw worker phase; pending_exit includes resting TP/SL protection."
    )
    position_state: str | None = Field(
        default=None,
        description=(
            "flat, entering, open_protected, open_unprotected, open_unverified, or exiting "
            "(ADR 0097); null when the sleeve's book was not read."
        ),
    )
    exit_in_flight: bool | None = Field(
        default=None, description="True only while an exit is being sent (ADR 0097)."
    )
    lifecycle_command: str
    mismatch_detail: str | None
    allocated_capital: str | None
    paper_starting_cash: str | None
    performance_equity: str | None
    accounting_complete: bool = Field(description="Full economic evidence, not a venue audit.")
    net_pnl: str | None = Field(
        description="Persisted bar-close equity minus starting equity; null when unresolved."
    )
    return_fraction: str | None = Field(
        description="net_pnl over the sleeve's capital (starting cash or allocation)."
    )
    drawdown_fraction: str | None = Field(
        description="Drawdown of the sleeve's equity from its own peak this run."
    )
    exposure_quote: str | None
    open_books: int | None = Field(
        description="Projected positions read, not full occupancy proof."
    )
    books: tuple[SleeveOpenBookResponse, ...] = Field(
        default=(),
        description=(
            "Each open book with entry, stop, target, entry bar, and state; the deployment "
            "read also marks them at the last evaluated bar (ADR 0098)."
        ),
    )
    strategy_fingerprint: str | None
    running_current_rules: bool | None = Field(
        description="False when the bot runs an earlier edit of its strategy."
    )
    created_at: str
    updated_at: str


class SleeveBookResponse(BaseModel):
    """One sleeve with its target capital and its current bot (null before a start)."""

    sleeve_id: UUID
    strategy_id: UUID
    strategy_name: str
    product_id: str | None
    timeframe: str | None
    weight_fraction: str
    target_capital_quote: str
    issues: tuple[SleeveIssueCode, ...]
    deployment: SleeveDeploymentResponse | None


class PortfolioBreakerResponse(BaseModel):
    """The portfolio breakers: their limits, the latch, and the baselines they watch."""

    latched: bool
    reason_code: str | None
    detail: str | None
    latched_at: str | None
    daily_loss_quote: str | None
    max_drawdown_fraction: str | None
    run_started_at: str | None
    accounting_complete: bool
    unresolved_deployment_ids: tuple[UUID, ...] = ()
    equity: str | None = Field(description="Capital plus run PnL; null with unresolved economics.")
    day_open_equity: str | None
    daily_pnl: str | None
    high_water_mark_equity: str | None
    drawdown_fraction: str | None
    evaluated_at: str | None = Field(description="When the worker last recorded equity.")


class AssetExposureResponse(BaseModel):
    """Exposure in one base asset against the per-asset cap."""

    asset: str
    exposure_quote: str | None
    fraction_of_capital: str | None
    cap_quote: str


class PortfolioExposureResponse(BaseModel):
    """Exposure as the entry gate counts it, against the portfolio's caps."""

    accounting_complete: bool
    unresolved_deployment_ids: tuple[UUID, ...] = ()
    total_quote: str | None
    fraction_of_capital: str | None
    cap_quote: str
    asset_cap_quote: str
    assets: tuple[AssetExposureResponse, ...]


class PortfolioDeploymentResponse(BaseModel):
    """A portfolio's deployment: state, each sleeve's bot, breakers, and exposure."""

    portfolio_id: UUID
    name: str
    mode: PortfolioMode
    quote_currency: str
    capital_quote: str
    revision: int
    state: PortfolioDeploymentState
    sleeves: tuple[SleeveBookResponse, ...]
    detached: tuple[SleeveDeploymentResponse, ...] = Field(
        description="Running or paused bots of sleeves that were removed afterwards."
    )
    breaker: PortfolioBreakerResponse
    exposure: PortfolioExposureResponse
    pending_proposals: int


class SleeveOutcomeResponse(BaseModel):
    """What one action did to one sleeve."""

    sleeve_id: UUID | None
    strategy_id: UUID | None
    strategy_name: str
    outcome: Literal["started", "attached", "paused", "resumed", "stopped", "unchanged", "failed"]
    deployment_id: UUID | None
    message: str | None


class PortfolioActionResponse(BaseModel):
    """One start, pause, resume, or stop with per-sleeve outcomes and the new state."""

    action: Literal["start", "pause", "resume", "stop"]
    outcomes: tuple[SleeveOutcomeResponse, ...]
    deployment: PortfolioDeploymentResponse


class ProposalListResponse(BaseModel):
    """One newest-first page of proposals."""

    proposals: tuple[Proposal, ...]
    limit: int
    returned: int
    total: int
    has_more: bool
    next_cursor: str | None = None


class ProposalResponse(BaseModel):
    """One proposal after a submit or decision, with the portfolio revision it left."""

    proposal: Proposal
    portfolio_revision: int


def deployment_response(
    snapshot: PortfolioDeploymentSnapshot,
    *,
    pending_proposals: int,
    marks: Mapping[UUID, Mapping[str, BookMark]] | None = None,
) -> PortfolioDeploymentResponse:
    """Project a portfolio's deployment snapshot.

    ``marks`` (per deployment id, then product) price each sleeve's open books at the last
    evaluated bar; without them books carry no mark or unrealized PnL.
    """
    aggregate = snapshot.aggregate
    portfolio = aggregate.portfolio
    by_id = {item.deployment.id: item for item in snapshot.snapshots}
    marked = marks or {}
    return PortfolioDeploymentResponse(
        portfolio_id=portfolio.portfolio_id,
        name=portfolio.name,
        mode=portfolio.mode,
        quote_currency=portfolio.quote_currency,
        capital_quote=portfolio.capital_quote,
        revision=portfolio.revision,
        state=snapshot.books.state,
        sleeves=tuple(
            _sleeve_book(book, by_id, quote=portfolio.quote_currency, marks=marked)
            for book in snapshot.books.sleeves
        ),
        detached=tuple(
            sleeve_deployment(
                item,
                by_id.get(item.id),
                current_fingerprint=None,
                marks=marked.get(item.id),
                quote=portfolio.quote_currency,
            )
            for item in snapshot.books.detached
        ),
        breaker=_breaker(snapshot),
        exposure=_exposure(snapshot),
        pending_proposals=pending_proposals,
    )


def action_response(
    result: PortfolioActionResult,
    snapshot: PortfolioDeploymentSnapshot,
    *,
    pending_proposals: int,
) -> PortfolioActionResponse:
    """Project one action's outcomes plus the deployment state after it."""
    return PortfolioActionResponse(
        action=result.action,
        outcomes=tuple(
            SleeveOutcomeResponse(
                sleeve_id=item.sleeve_id,
                strategy_id=item.strategy_id,
                strategy_name=item.strategy_name,
                outcome=item.outcome,
                deployment_id=item.deployment_id,
                message=item.message,
            )
            for item in result.outcomes
        ),
        deployment=deployment_response(snapshot, pending_proposals=pending_proposals),
    )


def _sleeve_book(
    book: SleeveBook,
    by_id: dict[UUID, DeploymentSnapshot],
    *,
    quote: SpotQuoteCurrency,
    marks: Mapping[UUID, Mapping[str, BookMark]],
) -> SleeveBookResponse:
    """Project one sleeve and its bot."""
    view = book.view
    deployment = book.deployment
    return SleeveBookResponse(
        sleeve_id=view.sleeve.sleeve_id,
        strategy_id=view.sleeve.strategy_id,
        strategy_name=view.strategy.name,
        product_id=view.strategy.product_id,
        timeframe=view.strategy.timeframe,
        weight_fraction=view.sleeve.weight_fraction,
        target_capital_quote=canonical_decimal(book.target_capital),
        issues=sleeve_issues(view, quote),
        deployment=(
            None
            if deployment is None
            else sleeve_deployment(
                deployment,
                by_id.get(deployment.id),
                current_fingerprint=view.strategy.current_fingerprint,
                marks=marks.get(deployment.id),
                quote=quote,
            )
        ),
    )


def sleeve_capital_base(deployment: Deployment) -> Decimal | None:
    """The capital a sleeve's return is measured on: paper starting cash, else allocation."""
    if deployment.mode is DeploymentMode.PAPER and deployment.paper_starting_cash is not None:
        return deployment.paper_starting_cash
    return deployment.allocated_capital


def sleeve_drawdown(deployment: Deployment) -> Decimal | None:
    """Drawdown of a sleeve's capital-plus-PnL from its own peak this run."""
    base = sleeve_capital_base(deployment)
    if base is None or base <= 0:
        return None
    pnl = net_pnl(deployment)
    peak_pnl = pnl
    if deployment.high_water_mark_equity is not None:
        starting = deployment.initial_equity or deployment.paper_starting_cash or _ZERO
        peak_pnl = max(pnl, deployment.high_water_mark_equity - starting)
    peak = base + max(peak_pnl, _ZERO)
    equity = base + pnl
    if peak <= 0:
        return None
    return max(_ZERO, (peak - equity) / peak)


def sleeve_deployment(
    deployment: Deployment,
    snapshot: DeploymentSnapshot | None,
    *,
    current_fingerprint: str | None,
    marks: Mapping[str, BookMark] | None = None,
    quote: SpotQuoteCurrency | None = None,
) -> SleeveDeploymentResponse:
    """Project one sleeve bot (and its open books, marked when ``marks`` has them)."""
    report_quote = (
        quote
        if quote is not None
        else (
            quote_currency(deployment.product_id)
            if is_spot_product_id(deployment.product_id)
            else None
        )
    )
    complete = (
        snapshot is not None
        and report_quote is not None
        and _performance_complete(snapshot, report_quote)
    )
    pnl = net_pnl(deployment) if complete else None
    base = sleeve_capital_base(deployment)
    exposure = (
        None
        if not complete or snapshot is None
        else sum(
            (
                product_exposure(snapshot, product)
                for product in _products(snapshot)
                if is_spot_product_id(product) and quote_currency(product) == report_quote
            ),
            _ZERO,
        )
    )
    drawdown = sleeve_drawdown(deployment) if complete else None
    state = None if snapshot is None else deployment_position_state(snapshot)
    return SleeveDeploymentResponse(
        deployment_id=deployment.id,
        strategy_id=deployment.strategy_id,
        strategy_name=deployment.strategy_name,
        status=deployment.status.value,
        phase=deployment.phase.value,
        position_state=None if state is None else state.value,
        exit_in_flight=None if state is None else state is PositionState.EXITING,
        lifecycle_command=deployment.lifecycle_command.value,
        mismatch_detail=deployment.mismatch_detail,
        allocated_capital=_optional(deployment.allocated_capital),
        paper_starting_cash=_optional(deployment.paper_starting_cash),
        performance_equity=_optional(deployment.performance_equity) if complete else None,
        accounting_complete=complete,
        net_pnl=_optional(pnl),
        return_fraction=None if pnl is None or base is None or base <= 0 else _fraction(pnl / base),
        drawdown_fraction=None if drawdown is None else _fraction(drawdown),
        exposure_quote=_optional(exposure),
        open_books=None if snapshot is None else len(snapshot_positions(snapshot)),
        books=() if snapshot is None else open_books(snapshot, marks or {}),
        strategy_fingerprint=deployment.strategy_fingerprint,
        running_current_rules=(
            None
            if current_fingerprint is None
            else deployment.strategy_fingerprint == current_fingerprint
        ),
        created_at=utc_text(deployment.created_at),
        updated_at=utc_text(deployment.updated_at),
    )


def _breaker(snapshot: PortfolioDeploymentSnapshot) -> PortfolioBreakerResponse:
    """The breakers with today's equity overlaid on the recorded baselines."""
    runtime = snapshot.runtime
    limits = snapshot.aggregate.portfolio.limits
    books = run_members(_portfolio_members(snapshot), runtime)
    by_id = {item.deployment.id: item for item in snapshot.snapshots}
    unresolved = _unresolved_deployments(
        books, by_id, quote=snapshot.aggregate.portfolio.quote_currency
    )
    equity = (
        None
        if unresolved
        else Decimal(snapshot.aggregate.portfolio.capital_quote)
        + sum((net_pnl(by_id[book.id].deployment) for book in books), _ZERO)
    )
    live = runtime
    if equity is not None and runtime.run_started_at is not None:
        evaluated_at = runtime.last_evaluated_at or runtime.run_started_at
        live = roll_baselines(runtime, equity=equity, now=_latest(evaluated_at))
    evaluated = equity is not None and runtime.run_started_at is not None
    change = daily_pnl(live) if evaluated else None
    drawdown = drawdown_fraction(live) if evaluated else None
    return PortfolioBreakerResponse(
        latched=runtime.breaker_latched,
        reason_code=runtime.breaker_reason,
        detail=runtime.breaker_detail,
        latched_at=_optional_time(runtime.breaker_latched_at),
        daily_loss_quote=limits.daily_loss_quote,
        max_drawdown_fraction=limits.max_drawdown_fraction,
        run_started_at=_optional_time(runtime.run_started_at),
        accounting_complete=not unresolved,
        unresolved_deployment_ids=unresolved,
        equity=_optional(equity),
        day_open_equity=_optional(live.day_open_equity),
        daily_pnl=None if change is None else canonical_decimal(change),
        high_water_mark_equity=_optional(live.high_water_mark_equity),
        drawdown_fraction=None if drawdown is None else _fraction(drawdown),
        evaluated_at=_optional_time(runtime.last_evaluated_at),
    )


def _latest(moment: datetime) -> datetime:
    """Now, or the recorded instant when the clock reads earlier (never roll backwards)."""
    now = datetime.now(UTC)
    return now if now >= moment else moment


def _exposure(snapshot: PortfolioDeploymentSnapshot) -> PortfolioExposureResponse:
    """Exposure against the total and per-asset caps."""
    portfolio = snapshot.aggregate.portfolio
    capital = Decimal(portfolio.capital_quote)
    mode = DeploymentMode.LIVE if portfolio.mode == "live" else DeploymentMode.PAPER
    by_id = {item.deployment.id: item for item in snapshot.snapshots}
    books = tuple(
        book
        for book in _portfolio_members(snapshot)
        if book.id in by_id
        or occupies_running_slot(book)
        or book.phase in {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}
    )
    bearing = risk_bearing_snapshots(
        tuple(by_id[book.id] for book in books if book.id in by_id), mode
    )
    amounts: dict[str, Decimal] = {}
    for book in bearing:
        for product in _products(book):
            if is_spot_product_id(product) and quote_currency(product) == portfolio.quote_currency:
                asset = product.split("-", 1)[0]
                amounts[asset] = amounts.get(asset, _ZERO) + product_exposure(book, product)
    unresolved, unknown = _exposure_uncertainty(books, by_id, portfolio.quote_currency)
    total = None if unresolved else sum(amounts.values(), _ZERO)
    asset_cap = capital * Decimal(portfolio.limits.max_per_asset_fraction)
    return PortfolioExposureResponse(
        accounting_complete=not unresolved,
        unresolved_deployment_ids=unresolved,
        total_quote=_optional(total),
        fraction_of_capital=None
        if total is None
        else _fraction(total / capital)
        if capital > 0
        else "0",
        cap_quote=canonical_decimal(
            capital * Decimal(portfolio.limits.max_total_exposure_fraction)
        ),
        asset_cap_quote=canonical_decimal(asset_cap),
        assets=tuple(
            AssetExposureResponse(
                asset=asset,
                exposure_quote=(
                    None if unknown is None or asset in unknown else canonical_decimal(amount)
                ),
                fraction_of_capital=(
                    None
                    if unknown is None or asset in unknown
                    else _fraction(amount / capital)
                    if capital > 0
                    else "0"
                ),
                cap_quote=canonical_decimal(asset_cap),
            )
            for asset, amount in sorted(
                (amounts | {asset: amounts.get(asset, _ZERO) for asset in unknown or ()}).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
    )


def _portfolio_members(snapshot: PortfolioDeploymentSnapshot) -> tuple[Deployment, ...]:
    """Keep reporting in this portfolio's mode and membership, never other accounts' books."""
    portfolio = snapshot.aggregate.portfolio
    return tuple(
        book
        for book in snapshot.tagged
        if book.portfolio_id == portfolio.portfolio_id and book.mode.value == portfolio.mode
    )


def _unresolved_deployments(
    books: Sequence[Deployment],
    by_id: Mapping[UUID, DeploymentSnapshot],
    *,
    quote: SpotQuoteCurrency,
) -> tuple[UUID, ...]:
    """Missing full reads or unresolved ledgers cannot certify current run performance."""
    return tuple(
        book.id
        for book in books
        if book.id not in by_id or not _performance_complete(by_id[book.id], quote)
    )


def _performance_complete(snapshot: DeploymentSnapshot, quote: SpotQuoteCurrency) -> bool:
    """A complete ledger does not define aggregate PnL across different quote currencies."""
    return ledger_from_snapshot(snapshot).accounting_complete and all(
        is_spot_product_id(product) and quote_currency(product) == quote
        for product in _products(snapshot)
    )


def _products(snapshot: DeploymentSnapshot) -> tuple[str, ...]:
    """Name actual inventory/runtime/order products without reconstructing quantities."""
    return tuple(
        sorted(
            {
                snapshot.deployment.product_id,
                *(
                    resolved_product_id(row.product_id, snapshot.deployment)
                    for row in snapshot_positions(snapshot)
                ),
                *(
                    resolved_product_id(row.product_id, snapshot.deployment)
                    for row in snapshot.orders
                ),
                *(
                    resolved_product_id(row.product_id, snapshot.deployment)
                    for row in snapshot.instrument_runtimes
                ),
            }
        )
    )


def _exposure_uncertainty(
    books: Sequence[Deployment], by_id: Mapping[UUID, DeploymentSnapshot], quote: SpotQuoteCurrency
) -> tuple[tuple[UUID, ...], set[str] | None]:
    """Qualify quote-scoped exposure; independent resolved assets can remain exact."""
    unresolved: list[UUID] = []
    unknown: set[str] = set()
    unassignable = False
    for book in books:
        snapshot = by_id.get(book.id)
        if snapshot is None or not snapshot.accounting_complete:
            unresolved.append(book.id)
            unassignable = True
            continue
        order_ids = {order.id for order in snapshot.orders}
        if any(
            fill.order_id not in order_ids and fill.economics_applied_at is None
            for fill in snapshot.fills
        ):
            unresolved.append(book.id)
            unassignable = True
            continue
        products = tuple(
            product
            for product in _products(snapshot)
            if (not is_spot_product_id(product) or quote_currency(product) == quote)
            and book_inventory_reasons(snapshot, product_id=product)
        )
        if products:
            unresolved.append(book.id)
            unknown.update(
                product.split("-", 1)[0] for product in products if is_spot_product_id(product)
            )
            unassignable |= any(not is_spot_product_id(product) for product in products)
    return tuple(unresolved), None if unassignable else unknown


def _fraction(value: Decimal) -> str:
    """A fraction rounded to six places, canonical."""
    return canonical_decimal(value.quantize(_FRACTION))


def _optional(value: Decimal | None) -> str | None:
    """Canonical text for an optional decimal."""
    return None if value is None else canonical_decimal(value)


def _optional_time(value: datetime | None) -> str | None:
    """UTC text for an optional instant."""
    return None if value is None else utc_text(value)


def open_books(
    snapshot: DeploymentSnapshot, marks: Mapping[str, BookMark]
) -> tuple[SleeveOpenBookResponse, ...]:
    """Project every open book of one sleeve bot, priced at its last-bar mark if known."""
    rows: list[SleeveOpenBookResponse] = []
    for position in snapshot_positions(snapshot):
        product_id = resolved_product_id(position.product_id, snapshot.deployment)
        mark = marks.get(product_id)
        resolved = not book_inventory_reasons(snapshot, product_id=product_id)
        fees = (
            None
            if mark is None or not resolved
            else recorded_position_entry_fees(snapshot, position)
        )
        evidence = book_protection_evidence(snapshot, product_id=product_id, position=position)
        state = book_position_state(
            snapshot,
            product_id=product_id,
            position=position,
            phase=RuntimePhase.OPEN,
            evidence=evidence,
        )
        rows.append(
            SleeveOpenBookResponse(
                product_id=product_id,
                side=position.side.value,
                quantity=canonical_decimal(position.quantity),
                entry_price=canonical_decimal(position.entry_price),
                stop_price=canonical_decimal(position.stop_price),
                target_price=_optional(position.target_price),
                entered_bar=utc_text(position.entered_bar),
                position_state=state.value,
                protection=protection_evidence_response(evidence),
                mark_price=None if mark is None else canonical_decimal(mark.price),
                marked_at=None if mark is None else utc_text(mark.bar_closes_at),
                unrealized_pnl=(
                    None
                    if mark is None
                    else canonical_decimal(unrealized_pnl(position, mark.price))
                ),
                entry_fees=_optional(fees),
                unrealized_pnl_net=(
                    None
                    if mark is None or fees is None
                    else canonical_decimal(unrealized_pnl(position, mark.price) - fees)
                ),
            )
        )
    return tuple(rows)
