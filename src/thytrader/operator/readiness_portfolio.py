"""Portfolio-scope readiness: deployment rows, portfolio sections, scope findings."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

from thytrader.execution.ledger import effective_paper_fee_rates
from thytrader.execution.models import Deployment, DeploymentMode, DeploymentSnapshot
from thytrader.market_data.products import SpotQuoteCurrency, base_currency
from thytrader.operator.readiness_account import (
    _ZERO,
    _book_products,
    _inventory_evidence,
    _product_quote,
    _quote_rows,
    _unpriced_entries,
)
from thytrader.operator.readiness_models import (
    ReadinessAccountCaps,
    ReadinessAssetCapRow,
    ReadinessDeploymentRow,
    ReadinessFinding,
    ReadinessPaperSection,
    ReadinessPortfolioSection,
    ReadinessSeverity,
)
from thytrader.portfolios.rules import allocation_summary, sleeve_capital
from thytrader.research.indicators import canonical_decimal
from thytrader.risk.exposure import product_exposure, risk_bearing_snapshots

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from thytrader.portfolios.models import PortfolioAggregate, PortfolioPage, PortfolioRuntimeState

_ACTIVE_STATUSES = frozenset({"running", "paused"})


class ReadinessPortfolioDirectory(Protocol):
    """Portfolio reads the readiness preflight needs, decoupled from full storage."""

    async def list_page(self, *, limit: int, offset: int) -> PortfolioPage:
        """Return one page of portfolio aggregates."""
        ...

    async def get(self, portfolio_id: UUID) -> PortfolioAggregate:
        """Return one portfolio aggregate."""
        ...

    async def runtime_state(self, portfolio_id: UUID) -> PortfolioRuntimeState:
        """Return one portfolio's breaker runtime state."""
        ...


def _deployment_row(
    snapshot: DeploymentSnapshot,
    aggregate: PortfolioAggregate | None,
) -> ReadinessDeploymentRow:
    """Project one book's allocation, capacity, latches, and fee assumptions."""
    deployment = snapshot.deployment
    unpriced = _unpriced_entries((snapshot,))
    quotes = () if unpriced else _quote_rows(snapshot)
    unsupported = tuple(p for p in _book_products((snapshot,)) if _product_quote(p) is None)
    single = quotes[0] if len(quotes) == 1 and not unsupported else None
    allocated, basis = _allocation_of(deployment, aggregate)
    if single is None:
        allocated, basis = None, "none"
    remaining = (
        None
        if allocated is None or single is None or single.exposure is None
        else max(_ZERO, allocated - Decimal(single.exposure))
    )
    maker = taker = None
    if deployment.mode is DeploymentMode.PAPER:
        maker, taker = effective_paper_fee_rates(
            deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
        )
    product = deployment.product_id
    return ReadinessDeploymentRow(
        deployment_id=deployment.id,
        mode=deployment.mode.value,
        status=deployment.status.value,
        kind=deployment.kind.value,
        strategy_name=deployment.strategy_name,
        product_id=product,
        quote_currency=None if single is None else single.quote_currency,
        portfolio_id=deployment.portfolio_id,
        allocated_capital=None if allocated is None else canonical_decimal(allocated),
        allocation_basis=basis,
        quote_exposures=quotes,
        unsupported_products=unsupported,
        unpriced_entry_order_ids=unpriced,
        inventory_cost=None if single is None else single.inventory_cost,
        working_entry_reserved=None if single is None else single.working_entry_reserved,
        exposure=None if single is None else single.exposure,
        allocation_remaining=None if remaining is None else canonical_decimal(remaining),
        daily_loss_latched=deployment.daily_loss_latched,
        drawdown_latched=deployment.drawdown_latched,
        paper_maker_fee_rate=None if maker is None else canonical_decimal(maker),
        paper_taker_fee_rate=None if taker is None else canonical_decimal(taker),
    )


def _allocation_of(
    deployment: Deployment,
    aggregate: PortfolioAggregate | None,
) -> tuple[Decimal | None, Literal["stored", "portfolio_weight", "paper_starting_cash", "none"]]:
    """Resolve one book's advisory allocation and where it came from."""
    if deployment.allocated_capital is not None:
        return deployment.allocated_capital, "stored"
    if deployment.mode is DeploymentMode.PAPER and deployment.paper_starting_cash is not None:
        return deployment.paper_starting_cash, "paper_starting_cash"
    if aggregate is not None and deployment.portfolio_id is not None:
        for view in aggregate.sleeves:
            if view.sleeve.strategy_id == deployment.strategy_id:
                return (
                    Decimal(
                        sleeve_capital(
                            aggregate.portfolio.capital_quote, view.sleeve.weight_fraction
                        )
                    ),
                    "portfolio_weight",
                )
    return None, "none"


async def _load_portfolios(
    portfolios: ReadinessPortfolioDirectory | None,
    portfolio_ids: tuple[UUID, ...],
    warnings: list[str],
) -> tuple[PortfolioAggregate, ...]:
    """Load the scoped portfolio aggregates; unreadable ones degrade to a warning."""
    if not portfolio_ids:
        return ()
    if portfolios is None:
        warnings.append("Portfolio storage is unavailable; requested portfolio caps are omitted.")
        return ()
    aggregates: list[PortfolioAggregate] = []
    for portfolio_id in portfolio_ids:
        try:
            aggregates.append(await portfolios.get(portfolio_id))
        except Exception:  # noqa: BLE001 - one unreadable portfolio degrades the report.
            warnings.append(f"Portfolio {portfolio_id} could not be read; its caps are omitted.")
    return tuple(aggregates)


def _aggregate_for(
    aggregates: tuple[PortfolioAggregate, ...], portfolio_id: UUID | None
) -> PortfolioAggregate | None:
    """Return the aggregate of one portfolio, or None."""
    if portfolio_id is None:
        return None
    return next((item for item in aggregates if item.portfolio.portfolio_id == portfolio_id), None)


async def _portfolio_section(
    portfolios: ReadinessPortfolioDirectory | None,
    aggregate: PortfolioAggregate,
    *,
    snapshots: Mapping[UUID, DeploymentSnapshot],
    deployments: Sequence[Deployment],
    account: ReadinessAccountCaps | None,
    warnings: list[str],
) -> ReadinessPortfolioSection:
    """One portfolio's caps, exposure, and both breaker tiers (disclosure only)."""
    portfolio = aggregate.portfolio
    mode = DeploymentMode.PAPER if portfolio.mode == "paper" else DeploymentMode.LIVE
    members = tuple(
        item
        for item in snapshots.values()
        if item.deployment.portfolio_id == portfolio.portfolio_id and item.deployment.mode is mode
    )
    expected = tuple(
        item
        for item in deployments
        if item.portfolio_id == portfolio.portfolio_id and item.mode is mode
    )
    inventory = _inventory_evidence(expected, snapshots, quote=portfolio.quote_currency)
    runtime = await _runtime_state(portfolios, portfolio.portfolio_id, warnings)
    capital = Decimal(portfolio.capital_quote)
    limits = portfolio.limits
    bearing = risk_bearing_snapshots(members, mode)
    excluded = tuple(
        p for p in _book_products(bearing) if _product_quote(p) != portfolio.quote_currency
    )
    complete = (
        inventory.status == "complete"
        and inventory.accounting_status == "complete"
        and not excluded
    )
    if not complete:
        warnings.append(
            f"Portfolio {portfolio.portfolio_id} inventory scope is incomplete or mixed-quote."
        )
    exposure, assets = _portfolio_quote_exposure(bearing, portfolio.quote_currency)
    total_cap = capital * Decimal(limits.max_total_exposure_fraction)
    asset_cap = capital * Decimal(limits.max_per_asset_fraction)
    asset_rows = tuple(
        ReadinessAssetCapRow(
            asset=asset,
            exposure=canonical_decimal(held),
            cap=canonical_decimal(asset_cap),
            remaining=canonical_decimal(asset_cap - held),
        )
        for asset, held in sorted(assets.items())
        if complete and held > 0
    )
    comparable = (
        portfolio.mode == "live"
        and account is not None
        and portfolio.quote_currency == account.quote_currency
    )
    account_daily_cap = (
        Decimal(account.effective_daily_loss_cap)
        if comparable and account is not None and account.effective_daily_loss_cap is not None
        else None
    )
    daily_stop = limits.daily_loss_quote
    drawdown_stop = limits.max_drawdown_fraction
    peak = None if runtime is None else runtime.high_water_mark_equity
    allowance = None if peak is None or drawdown_stop is None else peak * Decimal(drawdown_stop)
    return ReadinessPortfolioSection(
        portfolio_id=portfolio.portfolio_id,
        name=portfolio.name,
        mode=portfolio.mode,
        quote_currency=portfolio.quote_currency,
        capital_quote=portfolio.capital_quote,
        cash_reserve_fraction=portfolio.cash_reserve_fraction,
        allocated_quote=allocation_summary(aggregate).allocated_quote,
        limits=limits,
        total_exposure_cap=canonical_decimal(total_cap),
        per_asset_cap=canonical_decimal(asset_cap),
        inventory=inventory.model_copy(update={"status": "partial"}) if excluded else inventory,
        excluded_products=excluded,
        current_total_exposure=canonical_decimal(exposure) if complete else None,
        remaining_total_capacity=canonical_decimal(total_cap - exposure) if complete else None,
        asset_caps=asset_rows,
        runtime_available=runtime is not None,
        breaker_latched=None if runtime is None else runtime.breaker_latched,
        breaker_reason_code=None if runtime is None else runtime.breaker_reason,
        daily_loss_quote_stop=daily_stop,
        drawdown_fraction_stop=drawdown_stop,
        drawdown_stop_loss_allowance=(None if allowance is None else canonical_decimal(allowance)),
        account_daily_loss_cap=(
            None if account_daily_cap is None else canonical_decimal(account_daily_cap)
        ),
        account_breaker_comparable=comparable,
        tighter_daily_breaker=(
            "not_comparable"
            if not comparable
            else "unknown"
            if account_daily_cap is None or not complete
            else _tighter_daily_breaker(
                portfolio_stop=None if daily_stop is None else Decimal(daily_stop),
                account_stop=account_daily_cap,
            )
        ),
    )


def _portfolio_quote_exposure(
    snapshots: Sequence[DeploymentSnapshot], quote: SpotQuoteCurrency
) -> tuple[Decimal, dict[str, Decimal]]:
    """Aggregate product cost exposure within one portfolio quote, without FX."""
    assets: dict[str, Decimal] = {}
    for product in _book_products(snapshots):
        if _product_quote(product) != quote:
            continue
        held = sum((product_exposure(snapshot, product) for snapshot in snapshots), _ZERO)
        asset = base_currency(product)
        assets[asset] = assets.get(asset, _ZERO) + held
    return sum(assets.values(), _ZERO), assets


async def _runtime_state(
    portfolios: ReadinessPortfolioDirectory | None,
    portfolio_id: UUID,
    warnings: list[str],
) -> PortfolioRuntimeState | None:
    """Read durable runtime state; unavailable is unknown, never a fresh unlatched state."""
    if portfolios is not None:
        try:
            return await portfolios.runtime_state(portfolio_id)
        except Exception:  # noqa: BLE001 - unavailable state is disclosed, not fabricated.
            warnings.append(f"Runtime state of portfolio {portfolio_id} is unavailable.")
            return None
    warnings.append(f"Runtime state of portfolio {portfolio_id} is unavailable.")
    return None


def _tighter_daily_breaker(
    *,
    portfolio_stop: Decimal | None,
    account_stop: Decimal | None,
) -> Literal["portfolio", "account", "neither_set", "unknown"]:
    """Name which daily-loss stop binds first; never tighten either breaker."""
    if portfolio_stop is not None and account_stop is not None:
        return "portfolio" if portfolio_stop <= account_stop else "account"
    if portfolio_stop is not None:
        return "portfolio"
    if account_stop is not None:
        return "account"
    return "neither_set"


def _scope_findings(
    rows: tuple[ReadinessDeploymentRow, ...],
    account: ReadinessAccountCaps | None,
    portfolio_sections: Sequence[ReadinessPortfolioSection],
    paper: ReadinessPaperSection | None,
    findings: list[ReadinessFinding],
) -> None:
    """Derive advisory and violation findings from the assembled payload."""
    if account is not None:
        _allocation_findings(rows, account, findings)
    for section in portfolio_sections:
        _portfolio_findings(section, findings)
    if paper is not None and Decimal(paper.committed_starting_cash) > Decimal(
        paper.paper_capital_quote
    ):
        findings.append(
            ReadinessFinding(
                reason_code="PAPER_CAPITAL_OVERCOMMITTED",
                severity=ReadinessSeverity.ADVISORY,
                detail=(
                    f"Scoped paper books commit {paper.committed_starting_cash} of starting "
                    f"cash against paper_capital_quote {paper.paper_capital_quote}. New paper "
                    "starts are already denied; this is a disclosure, not a new limit."
                ),
            )
        )
    for row in rows:
        if row.daily_loss_latched or row.drawdown_latched:
            latches = []
            if row.daily_loss_latched:
                latches.append("daily loss")
            if row.drawdown_latched:
                latches.append("drawdown")
            findings.append(
                ReadinessFinding(
                    reason_code="BREAKER_LATCHED",
                    severity=ReadinessSeverity.ADVISORY,
                    deployment_id=row.deployment_id,
                    detail=(
                        f"This book's {' and '.join(latches)} breaker is latched; entries "
                        "stay blocked until an operator resets it."
                    ),
                )
            )


def _allocation_findings(
    rows: tuple[ReadinessDeploymentRow, ...],
    account: ReadinessAccountCaps,
    findings: list[ReadinessFinding],
) -> None:
    """Advisory allocation overcommitment and quote-mismatch disclosures.

    Only running and paused books count: a stopped book never sizes another entry.
    """
    quote = account.quote_currency
    matching = [
        row
        for row in rows
        if row.mode == "live"
        and row.status in _ACTIVE_STATUSES
        and row.quote_currency == quote
        and row.allocated_capital
    ]
    mismatched = [
        row for row in rows if row.mode == "live" and row.quote_currency not in {quote, None}
    ]
    allocated_total = sum((Decimal(row.allocated_capital or "0") for row in matching), _ZERO)
    cap = account.effective_exposure_cap
    if cap is not None and allocated_total > Decimal(cap) and matching:
        findings.append(
            ReadinessFinding(
                reason_code="ALLOCATION_OVERCOMMITMENT",
                severity=ReadinessSeverity.ADVISORY,
                detail=(
                    f"{len(matching)} live book(s) commit {canonical_decimal(allocated_total)} "
                    f"{quote} of allocations, but the effective account exposure cap is "
                    f"{cap} {quote}"
                    + (
                        f" (venue available {account.venue_available_quote} {quote})."
                        if account.venue_available_quote is not None
                        else "."
                    )
                    + " Allocations are sizing limits, not reserved funds; the entry gate "
                    "denies orders at the cap. Current exposure is "
                    f"{account.current_exposure} {quote}."
                ),
            )
        )
    if mismatched:
        currencies = sorted({row.quote_currency for row in mismatched if row.quote_currency})
        findings.append(
            ReadinessFinding(
                reason_code="QUOTE_CURRENCY_MISMATCH",
                severity=ReadinessSeverity.INFO,
                detail=(
                    f"{len(mismatched)} live book(s) are quoted in {', '.join(currencies)} "
                    f"while the policy quote is {quote}; they are excluded from account "
                    "totals instead of being summed across currencies."
                ),
            )
        )


def _portfolio_findings(
    section: ReadinessPortfolioSection, findings: list[ReadinessFinding]
) -> None:
    """Violation findings for portfolio caps and an advisory for a latched breaker."""
    if section.current_total_exposure is not None and Decimal(
        section.current_total_exposure
    ) > Decimal(section.total_exposure_cap):
        findings.append(
            ReadinessFinding(
                reason_code="PORTFOLIO_EXPOSURE_CAP_EXCEEDED",
                severity=ReadinessSeverity.VIOLATION,
                portfolio_id=section.portfolio_id,
                detail=(
                    f"Portfolio {section.name} exposure "
                    f"{section.current_total_exposure} {section.quote_currency} exceeds its "
                    f"cap {section.total_exposure_cap} {section.quote_currency} "
                    "(max_total_exposure_fraction times capital)."
                ),
            )
        )
    findings.extend(
        ReadinessFinding(
            reason_code="PORTFOLIO_ASSET_EXPOSURE_CAP_EXCEEDED",
            severity=ReadinessSeverity.VIOLATION,
            portfolio_id=section.portfolio_id,
            detail=(
                f"Portfolio {section.name} exposure in {row.asset} is {row.exposure} "
                f"{section.quote_currency} above its per-asset cap {row.cap} "
                f"{section.quote_currency}."
            ),
        )
        for row in section.asset_caps
        if Decimal(row.exposure) > Decimal(row.cap)
    )
    if section.breaker_latched:
        findings.append(
            ReadinessFinding(
                reason_code="PORTFOLIO_BREAKER_LATCHED",
                severity=ReadinessSeverity.ADVISORY,
                portfolio_id=section.portfolio_id,
                detail=(
                    f"Portfolio {section.name} breaker {section.breaker_reason_code} is "
                    "latched; sleeves stay paused until an operator resets it."
                ),
            )
        )
