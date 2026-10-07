"""Operator ``readiness`` report: advisory preflight before risk is armed (ADR 0114).

Read-only. The report answers "if these books run as allocated, what do the venue
balance, the account, and each portfolio actually permit?" It shows allocation
commitments versus the venue quote balance versus the account and portfolio exposure
caps, per-asset caps, remaining entry capacity, paper fee assumptions versus the
account's fee evidence, and both breaker tiers side by side.

It never tightens, publishes, or changes risk policy, allocations, or bot state.
Overcommitment of allocations is reported as an *advisory*: allocations are sizing
limits, not reserved funds, and the entry gate already denies orders at the cap. An
*actual* exposure violation (position cost plus working entries above the effective cap) is
reported as a violation finding. Amounts are exact ``Decimal`` values rendered as
canonical decimal strings; quote currencies are never summed across each other, and
books whose quote differs from the policy quote are disclosed instead of folded into
account totals.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader import __version__
from thytrader.exchanges.read_errors import ExchangeReadError, ExchangeReadFailure
from thytrader.execution.ledger import effective_paper_fee_rates
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderSide,
    OrderStatus,
    PositionSide,
    is_venue_protection,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.execution.protection import book_inventory_reasons
from thytrader.execution.store import DisabledExecutionStore
from thytrader.market_data.products import (
    SPOT_QUOTE_CURRENCIES,
    SpotQuoteCurrency,
    base_currency,
    is_spot_product_id,
    quote_currency,
)
from thytrader.operator.models import (
    PORTFOLIO_REDACTION,
    ComponentReport,
    OperatorEnvelope,
    ReportStatus,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.portfolios.deployment import members
from thytrader.portfolios.models import PortfolioLimits, PortfolioRuntimeState
from thytrader.portfolios.rules import allocation_summary, sleeve_capital
from thytrader.research.indicators import canonical_decimal
from thytrader.risk.exposure import (
    product_exposure,
    risk_bearing_snapshots,
    working_entry_notional,
)
from thytrader.risk.store import load_effective_policy

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.store import ExecutionStore
    from thytrader.portfolio.models import PortfolioAsset
    from thytrader.portfolio.service import PortfolioService
    from thytrader.portfolios.models import PortfolioAggregate, PortfolioPage
    from thytrader.risk.models import ActiveRiskPolicy
    from thytrader.risk.store import RiskPolicyStore

READINESS_NOTE = (
    "Advisory only: this report never tightens or changes the published risk policy, "
    "allocations, or bot state. The entry gate enforces caps at order time."
)
FEE_COMPARISON_NOTE = (
    "Paper books carry documented maker/taker assumptions; live books pay venue-recorded "
    "fees. Backtest and paper suggestions prefill from the account's reported rates; older "
    "runs may assume cheaper fees. Compare with `thytrader-operator fees`."
)
_ZERO = Decimal("0")
_PORTFOLIO_REPORT_LIMIT = 100


class ReadinessSeverity(StrEnum):
    """How hard one readiness finding should press on the operator."""

    INFO = "info"
    ADVISORY = "advisory"
    VIOLATION = "violation"
    UNKNOWN = "unknown"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime | None) -> datetime | None:
    """Keep report timestamps timezone-aware UTC after JSON round-trips."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("readiness timestamps must be timezone-aware UTC")
    return value.astimezone(UTC)


class ReadinessFinding(_FrozenModel):
    """One advisory, violation, or unknown-capacity observation with a stable code."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    severity: ReadinessSeverity
    deployment_id: UUID | None = None
    portfolio_id: UUID | None = None
    detail: str = Field(min_length=1, max_length=500)


class ReadinessVenueQuote(_FrozenModel):
    """One quote currency's venue balance, exactly as observed.

    Balances are disclosed (``balances_omitted=false``) because the report exists to
    compare them against caps. Account identifiers and secrets stay out.
    """

    quote_currency: SpotQuoteCurrency
    venue_available: str | None = None
    venue_hold: str | None = None
    venue_total: str | None = None
    observed_at: datetime | None = None
    demo: bool = False

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep venue observation timestamps timezone-aware UTC."""
        return _require_utc(value)


class ReadinessInventoryEvidence(_FrozenModel):
    """Storage-read completeness and economic completeness are independent evidence."""

    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    expected_books: int | None = Field(default=None, ge=0)
    read_books: int = Field(default=0, ge=0)
    missing_deployment_ids: tuple[UUID, ...] = ()
    unsupported_products: tuple[str, ...] = ()
    unpriced_entry_order_ids: tuple[UUID, ...] = ()
    accounting_status: Literal["complete", "unresolved", "unavailable"] = "unavailable"
    unresolved_deployment_ids: tuple[UUID, ...] = ()


class ReadinessQuoteExposure(_FrozenModel):
    """One book's cost-basis exposure in exactly one quote currency, without FX."""

    quote_currency: SpotQuoteCurrency
    inventory_cost: str | None
    working_entry_reserved: str
    exposure: str | None


class ReadinessProductCapRow(_FrozenModel):
    """Account-wide cost-basis exposure on one product versus the per-product cap."""

    product_id: str
    quote_currency: SpotQuoteCurrency | None = None
    exposure: str | None
    cap: str | None = None
    remaining: str | None = None


class ReadinessAccountCaps(_FrozenModel):
    """Account-level capacity in the policy's quote currency (ADR 0106 scope).

    ``capital_base`` is observed venue available quote plus managed long inventory cost
    and working buy-entry reservations — never a bot allocation or duplicated ledger
    cash. Caps and capacities are ``None`` when the venue balance is unknown; they are
    never guessed. Books quoted in another currency are excluded and disclosed.
    """

    quote_currency: SpotQuoteCurrency
    policy_source: Literal["compiled_default", "published"]
    policy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    enforcement: Literal["advisory_only"] = "advisory_only"
    capital_base: str | None = None
    venue_available_quote: str | None = None
    inventory: ReadinessInventoryEvidence
    excluded_products: tuple[str, ...] = ()
    exposure_basis: Literal["position_cost_plus_working_entries"] = (
        "position_cost_plus_working_entries"
    )
    managed_long_inventory_cost: str | None = None
    working_buy_entry_reserved: str | None = None
    current_exposure: str | None = None
    exposure_fraction: str
    absolute_exposure_cap: str | None = None
    effective_exposure_cap: str | None = None
    remaining_entry_capacity: str | None = None
    per_product_fraction: str
    product_caps: tuple[ReadinessProductCapRow, ...] = ()
    daily_loss_fraction: str
    daily_loss_quote_cap: str | None = None
    effective_daily_loss_cap: str | None = None
    drawdown_fraction: str
    venue_read_failure: ExchangeReadFailure | None = None


class ReadinessDeploymentRow(_FrozenModel):
    """One in-scope book's allocation and capacity facts (no order payloads)."""

    deployment_id: UUID
    mode: Literal["paper", "live"]
    status: str
    kind: str
    strategy_name: str | None = None
    product_id: str
    quote_currency: SpotQuoteCurrency | None = None
    portfolio_id: UUID | None = None
    allocated_capital: str | None = None
    allocation_basis: Literal["stored", "portfolio_weight", "paper_starting_cash", "none"] = "none"
    quote_exposures: tuple[ReadinessQuoteExposure, ...] = ()
    unsupported_products: tuple[str, ...] = ()
    unpriced_entry_order_ids: tuple[UUID, ...] = ()
    inventory_cost: str | None = None
    working_entry_reserved: str | None = None
    exposure: str | None = None
    allocation_remaining: str | None = None
    daily_loss_latched: bool = False
    drawdown_latched: bool = False
    paper_maker_fee_rate: str | None = None
    paper_taker_fee_rate: str | None = None


class ReadinessAssetCapRow(_FrozenModel):
    """One base asset's exposure inside one portfolio versus its per-asset cap."""

    asset: str
    exposure: str
    cap: str
    remaining: str


class ReadinessPortfolioSection(_FrozenModel):
    """One portfolio's caps, exposure, and both breaker tiers side by side.

    ``tighter_daily_breaker`` only *names* which daily-loss stop binds first; nothing
    here tightens either breaker. Exposure caps are fractions of the portfolio's
    configured ``capital_quote`` exactly as the entry gate applies them (ADR 0091).
    """

    portfolio_id: UUID
    name: str
    mode: Literal["paper", "live"]
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    allocated_quote: str
    limits: PortfolioLimits
    total_exposure_cap: str
    per_asset_cap: str
    inventory: ReadinessInventoryEvidence
    excluded_products: tuple[str, ...] = ()
    current_total_exposure: str | None = None
    remaining_total_capacity: str | None = None
    asset_caps: tuple[ReadinessAssetCapRow, ...] = ()
    runtime_available: bool = False
    breaker_latched: bool | None = None
    breaker_reason_code: str | None = None
    daily_loss_quote_stop: str | None = None
    drawdown_fraction_stop: str | None = None
    drawdown_stop_loss_allowance: str | None = None
    account_daily_loss_cap: str | None = None
    account_breaker_comparable: bool = False
    tighter_daily_breaker: Literal[
        "portfolio", "account", "neither_set", "unknown", "not_comparable"
    ] = "unknown"


class ReadinessFeeGapRow(_FrozenModel):
    """One paper book's assumed fees versus the account's reported fee evidence.

    A gap is ``account rate - assumed rate``: positive means the paper book assumes
    cheaper fees than the account reports, so its results read more optimistic.
    """

    deployment_id: UUID
    product_id: str
    assumed_maker_fee_rate: str
    assumed_taker_fee_rate: str
    account_maker_fee_rate: str | None = None
    account_taker_fee_rate: str | None = None
    maker_gap: str | None = None
    taker_gap: str | None = None
    more_optimistic: bool = False


class ReadinessFeeEvidence(_FrozenModel):
    """Account fee evidence and every scoped paper book's assumption against it.

    ``unavailable_reason`` is set when the account rates could not be read; assumed
    paper rates are still listed, but no optimism conclusion is drawn from invented
    numbers. Demo data is labeled and never compared against real expectations.
    """

    account_maker_fee_rate: str | None = None
    account_taker_fee_rate: str | None = None
    account_fee_tier: str | None = None
    account_as_of: datetime | None = None
    demo: bool = False
    unavailable_reason: Literal["demo_or_missing_credentials", "read_failure"] | None = None
    read_failure: ExchangeReadFailure | None = None
    paper_books_compared: int = Field(default=0, ge=0)
    paper_books_defaulting_rates: int = Field(default=0, ge=0)
    optimistic_books: tuple[ReadinessFeeGapRow, ...] = ()
    comparison_note: str = FEE_COMPARISON_NOTE

    @field_validator("account_as_of")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        """Keep fee-evidence timestamps timezone-aware UTC."""
        return _require_utc(value)


class ReadinessPaperSection(_FrozenModel):
    """Paper-book capacity in the policy quote currency (advisory, rehearsal money)."""

    quote_currency: SpotQuoteCurrency
    paper_capital_quote: str
    committed_starting_cash: str
    books: int = Field(ge=0)


class ReadinessPayload(_FrozenModel):
    """Everything the preflight compares, with its scope made explicit."""

    scope: Literal["deployment", "portfolio", "fleet"]
    deployment_id: UUID | None = None
    portfolio_id: UUID | None = None
    modes_in_scope: tuple[Literal["paper", "live"], ...] = ()
    inventory: ReadinessInventoryEvidence = Field(default_factory=ReadinessInventoryEvidence)
    portfolio_scope_complete: bool = False
    note: str = READINESS_NOTE
    venue_quotes: tuple[ReadinessVenueQuote, ...] = ()
    account: ReadinessAccountCaps | None = None
    paper: ReadinessPaperSection | None = None
    deployments: tuple[ReadinessDeploymentRow, ...] = ()
    portfolios: tuple[ReadinessPortfolioSection, ...] = ()
    fee_evidence: ReadinessFeeEvidence = Field(default_factory=ReadinessFeeEvidence)
    findings: tuple[ReadinessFinding, ...] = ()


class ReadinessReport(OperatorEnvelope):
    """Advisory readiness preflight; never mutates policy, allocations, or bots."""

    report_kind: Literal["readiness"] = "readiness"
    payload: ReadinessPayload


@dataclass(frozen=True, slots=True)
class _VenueRead:
    """Venue observation outcome shared by the readiness sections."""

    rows: tuple[ReadinessVenueQuote, ...]
    available: Mapping[str, Decimal]
    failure: ExchangeReadFailure | None
    demo: bool
    complete: bool


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


class _ReadinessUnavailableError(RuntimeError):
    """The report cannot be built; carry the failed component."""

    def __init__(self, component: ComponentReport) -> None:
        """Store the component for the failed envelope."""
        self.component = component
        super().__init__(component.detail)


async def build_readiness_report(
    *,
    portfolio: PortfolioService,
    execution: ExecutionStore | None,
    risk_policies: RiskPolicyStore | None,
    portfolios: ReadinessPortfolioDirectory | None,
    deployment_id: UUID | None = None,
    portfolio_id: UUID | None = None,
) -> ReadinessReport:
    """Build the advisory preflight for one deployment, one portfolio, or the fleet."""
    now = datetime.now(UTC)
    warnings: list[str] = []
    findings: list[ReadinessFinding] = []
    try:
        deployments = await _list_deployments(execution)
        scope, scoped, portfolio_ids = await _resolve_scope(
            deployments, portfolios, deployment_id, portfolio_id, warnings
        )
    except _ReadinessUnavailableError as error:
        return _failed_report(now, error.component)
    all_snapshots = await _load_snapshots(execution, deployments, findings)
    snapshots = {item.id: all_snapshots[item.id] for item in scoped if item.id in all_snapshots}
    live_deployments = tuple(item for item in deployments if item.mode is DeploymentMode.LIVE)
    live_snapshots = {
        item.id: all_snapshots[item.id] for item in live_deployments if item.id in all_snapshots
    }
    live_bearing = risk_bearing_snapshots(tuple(live_snapshots.values()), DeploymentMode.LIVE)
    policy = await _load_policy(risk_policies, findings)
    venue = await _read_venue(portfolio, policy, all_snapshots, findings)
    account = (
        None
        if policy is None
        else _account_section(
            policy,
            live_bearing,
            venue,
            findings,
            inventory_evidence=_inventory_evidence(
                live_deployments, live_snapshots, quote=policy.definition.quote_currency
            ),
        )
    )
    aggregates = await _load_portfolios(portfolios, portfolio_ids, warnings)
    rows = tuple(
        _deployment_row(snapshot, _aggregate_for(aggregates, snapshot.deployment.portfolio_id))
        for snapshot in snapshots.values()
    )
    portfolio_sections = [
        await _portfolio_section(
            portfolios,
            aggregate,
            snapshots=all_snapshots,
            deployments=deployments,
            account=account,
            warnings=warnings,
        )
        for aggregate in aggregates
    ]
    fee_evidence = await _fee_evidence(portfolio, snapshots, findings)
    paper = _paper_section(policy, scoped, warnings)
    if warnings:
        findings.append(
            ReadinessFinding(
                reason_code="READINESS_SCOPE_INCOMPLETE",
                severity=ReadinessSeverity.UNKNOWN,
                detail=(
                    "Portfolio, runtime, or quote scope is incomplete; see partial_result_warnings."
                ),
            )
        )
    _scope_findings(rows, account, portfolio_sections, paper, findings)
    components = _components(findings, venue=venue, fee_evidence=fee_evidence, warnings=warnings)
    modes = tuple(mode for mode in ("live", "paper") if any(row.mode == mode for row in rows))
    return ReadinessReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=PORTFOLIO_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=ReadinessPayload(
            scope=scope,
            deployment_id=deployment_id,
            portfolio_id=portfolio_id,
            modes_in_scope=modes,
            inventory=_inventory_evidence(scoped, snapshots),
            portfolio_scope_complete=not warnings,
            venue_quotes=venue.rows,
            account=account,
            paper=paper,
            deployments=rows,
            portfolios=tuple(portfolio_sections),
            fee_evidence=fee_evidence,
            findings=tuple(findings),
        ),
    )


def _failed_report(now: datetime, component: ComponentReport) -> ReadinessReport:
    """Assemble the failed envelope without guessing any capacity."""
    return ReadinessReport(
        application_version=__version__,
        generated_at=now,
        overall_status=ReportStatus.FAILED,
        components=(component,),
        redaction=PORTFOLIO_REDACTION,
        recommended_next_action=recommend_next_action((component,)),
        payload=ReadinessPayload(scope="fleet"),
    )


async def _list_deployments(execution: ExecutionStore | None) -> tuple[Deployment, ...]:
    """List every deployment, failing the report when storage errors."""
    if execution is None or isinstance(execution, DisabledExecutionStore):
        raise _ReadinessUnavailableError(
            ComponentReport(
                name="readiness",
                status=ReportStatus.FAILED,
                reason_code="EXECUTION_UNAVAILABLE",
                detail="No execution store is attached; the managed fleet is unknown, not empty.",
            )
        )
    try:
        return await execution.list_deployments()
    except Exception as error:
        raise _ReadinessUnavailableError(
            ComponentReport(
                name="readiness",
                status=ReportStatus.FAILED,
                reason_code="EXECUTION_UNAVAILABLE",
                detail="Execution storage could not be read; readiness is unavailable.",
            )
        ) from error


async def _resolve_scope(
    deployments: tuple[Deployment, ...],
    portfolios: ReadinessPortfolioDirectory | None,
    deployment_id: UUID | None,
    portfolio_id: UUID | None,
    warnings: list[str],
) -> tuple[Literal["deployment", "portfolio", "fleet"], tuple[Deployment, ...], tuple[UUID, ...]]:
    """Pick the scoped books and the portfolio sections the scope needs."""
    if deployment_id is not None:
        match = next((item for item in deployments if item.id == deployment_id), None)
        if match is None:
            raise _ReadinessUnavailableError(
                ComponentReport(
                    name="readiness",
                    status=ReportStatus.FAILED,
                    reason_code="DEPLOYMENT_NOT_FOUND",
                    detail="No deployment exists for that id.",
                )
            )
        portfolio_ids = () if match.portfolio_id is None else (match.portfolio_id,)
        return "deployment", (match,), portfolio_ids
    if portfolio_id is not None:
        if portfolios is None:
            raise _ReadinessUnavailableError(
                ComponentReport(
                    name="readiness",
                    status=ReportStatus.FAILED,
                    reason_code="PORTFOLIO_STORAGE_UNAVAILABLE",
                    detail="Portfolio scope requires portfolio storage (PostgreSQL).",
                )
            )
        return "portfolio", members(deployments, portfolio_id), (portfolio_id,)
    portfolio_ids = await _fleet_portfolio_ids(portfolios, warnings)
    return "fleet", deployments, portfolio_ids


async def _fleet_portfolio_ids(
    portfolios: ReadinessPortfolioDirectory | None, warnings: list[str]
) -> tuple[UUID, ...]:
    """List fleet portfolio ids, warning when storage or the page bound hides some."""
    if portfolios is None:
        warnings.append("Portfolio storage is unavailable; portfolio caps and scope are unknown.")
        return ()
    try:
        page = await portfolios.list_page(limit=_PORTFOLIO_REPORT_LIMIT, offset=0)
    except Exception:  # noqa: BLE001 - portfolio listing degrades to books only.
        warnings.append("Portfolios could not be listed; portfolio caps are omitted.")
        return ()
    if page.total > len(page.portfolios):
        warnings.append(f"Showing the first {len(page.portfolios)} of {page.total} portfolios.")
    return tuple(item.portfolio.portfolio_id for item in page.portfolios)


def _record_unreadable_snapshots(
    deployments: tuple[Deployment, ...], findings: list[ReadinessFinding]
) -> None:
    """Record every book as unknown when no execution store is attached."""
    findings.extend(_unreadable_snapshot(deployment.id) for deployment in deployments)


def _unreadable_snapshot(deployment_id: UUID) -> ReadinessFinding:
    """One unknown finding for a book whose inventory could not be read."""
    return ReadinessFinding(
        reason_code="SNAPSHOT_UNAVAILABLE",
        severity=ReadinessSeverity.UNKNOWN,
        deployment_id=deployment_id,
        detail=(
            "Inventory and working orders could not be read; this book's "
            "exposure is unknown, not zero."
        ),
    )


async def _load_snapshots(
    execution: ExecutionStore | None,
    deployments: tuple[Deployment, ...],
    findings: list[ReadinessFinding],
) -> dict[UUID, DeploymentSnapshot]:
    """Load full snapshots, recording an unknown finding for each unreadable book."""
    if execution is None:
        _record_unreadable_snapshots(deployments, findings)
        return {}
    snapshots: dict[UUID, DeploymentSnapshot] = {}
    for deployment in deployments:
        try:
            snapshot = await execution.get_accounting_snapshot(deployment.id)
            snapshots[deployment.id] = snapshot
            if _inventory_unresolved(snapshot):
                findings.append(
                    ReadinessFinding(
                        reason_code="BOOK_ACCOUNTING_UNRESOLVED",
                        severity=ReadinessSeverity.UNKNOWN,
                        deployment_id=deployment.id,
                        detail=(
                            "Retained fills/executions, occupied runtimes without positions, or "
                            "incomplete accounting scope leave managed inventory unresolved; "
                            "no flatness or exact capacity is proved."
                        ),
                    )
                )
        except Exception:  # noqa: BLE001 - one unreadable book must not sink the report.
            findings.append(_unreadable_snapshot(deployment.id))
    return snapshots


def _inventory_evidence(
    deployments: Sequence[Deployment],
    snapshots: Mapping[UUID, DeploymentSnapshot],
    *,
    quote: SpotQuoteCurrency | None = None,
) -> ReadinessInventoryEvidence:
    """Describe expected reads, including unreadable stopped books with unknown residuals."""
    missing = tuple(item.id for item in deployments if item.id not in snapshots)
    read = tuple(snapshots[item.id] for item in deployments if item.id in snapshots)
    unsupported = tuple(
        product for product in _book_products(read) if not is_spot_product_id(product)
    )
    unpriced = _unpriced_entries(read)
    unresolved = tuple(item.deployment.id for item in read if _inventory_unresolved(item, quote))
    return ReadinessInventoryEvidence(
        status="partial" if missing or unsupported or unpriced else "complete",
        unpriced_entry_order_ids=unpriced,
        expected_books=len(deployments),
        read_books=len(read),
        missing_deployment_ids=missing,
        unsupported_products=unsupported,
        accounting_status=(
            "unresolved" if missing or unsupported or unpriced or unresolved else "complete"
        ),
        unresolved_deployment_ids=unresolved,
    )


def _inventory_unresolved(
    snapshot: DeploymentSnapshot, quote: SpotQuoteCurrency | None = None
) -> bool:
    """Product economics and runtime/inventory consistency, not display text, prove completeness."""
    return not snapshot.accounting_complete or any(
        book_inventory_reasons(snapshot, product_id=product)
        for product in _book_products((snapshot,))
        if quote is None or _product_quote(product) == quote
    )


def _unpriced_entries(snapshots: Sequence[DeploymentSnapshot]) -> tuple[UUID, ...]:
    """Unresolved entries without notional evidence make capacity unknown, not free."""
    ids: list[UUID] = []
    for snapshot in snapshots:
        purposes = {intent.id: intent.purpose for intent in snapshot.intents}
        ids.extend(
            order.id
            for order in snapshot.orders
            if order.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
            and order.quantity > order.filled_quantity
            and order.price is None
            and not is_venue_protection(order.kind)
            and purposes.get(order.intent_id, IntentPurpose.ENTRY) is IntentPurpose.ENTRY
        )
    return tuple(ids)


async def _load_policy(
    risk_policies: RiskPolicyStore | None, findings: list[ReadinessFinding]
) -> ActiveRiskPolicy | None:
    """Load the effective policy, degrading to unknown caps when the store fails."""
    try:
        return await load_effective_policy(risk_policies)
    except Exception:  # noqa: BLE001 - policy store failures degrade the report.
        findings.append(
            ReadinessFinding(
                reason_code="POLICY_UNAVAILABLE",
                severity=ReadinessSeverity.UNKNOWN,
                detail="The effective risk policy could not be loaded; caps are unknown.",
            )
        )
        return None


async def _read_venue(
    portfolio: PortfolioService,
    policy: ActiveRiskPolicy | None,
    snapshots: Mapping[UUID, DeploymentSnapshot],
    findings: list[ReadinessFinding],
) -> _VenueRead:
    """Observe venue quote balances for every quote currency the scope references."""
    currencies: set[SpotQuoteCurrency] = set()
    if policy is not None:
        currencies.add(policy.definition.quote_currency)
    for product in _book_products(tuple(snapshots.values())):
        if is_spot_product_id(product):
            currencies.add(quote_currency(product))
    try:
        observed = await portfolio.get_portfolio()
    except ExchangeReadError as error:
        _venue_unknown_finding(snapshots, findings, error.failure)
        return _VenueRead(rows=(), available={}, failure=error.failure, demo=False, complete=False)
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        _venue_unknown_finding(snapshots, findings, None)
        return _VenueRead(rows=(), available={}, failure=None, demo=False, complete=False)
    summed = _sum_quote_balances(observed.assets, currencies)
    rows = [
        ReadinessVenueQuote(
            quote_currency=quote,
            venue_available=canonical_decimal(parts[0]),
            venue_hold=canonical_decimal(parts[1]),
            venue_total=canonical_decimal(parts[2]),
            observed_at=observed.as_of,
            demo=observed.demo,
        )
        for quote, parts in sorted(summed.items())
    ]
    rows.extend(
        ReadinessVenueQuote(
            quote_currency=missing,
            venue_available="0",
            venue_hold="0",
            venue_total="0",
            observed_at=observed.as_of,
            demo=observed.demo,
        )
        for missing in sorted(currencies - set(summed))
    )
    return _VenueRead(
        rows=tuple(rows),
        available={quote: parts[0] for quote, parts in summed.items()},
        failure=None,
        demo=observed.demo,
        complete=True,
    )


def _sum_quote_balances(
    assets: Sequence[PortfolioAsset], currencies: set[SpotQuoteCurrency]
) -> dict[SpotQuoteCurrency, tuple[Decimal, Decimal, Decimal]]:
    """Sum available, hold, and total for each requested quote, including duplicates."""
    summed: dict[SpotQuoteCurrency, tuple[Decimal, Decimal, Decimal]] = {}
    for asset in assets:
        quote = _as_spot_quote(asset.currency)
        if quote is None or quote not in currencies:
            continue
        previous = summed.get(quote, (_ZERO, _ZERO, _ZERO))
        summed[quote] = (
            previous[0] + asset.available,
            previous[1] + asset.hold,
            previous[2] + asset.total,
        )
    return summed


def _as_spot_quote(currency: str) -> SpotQuoteCurrency | None:
    """Narrow one currency text to the spot quote union, or None."""
    for candidate in SPOT_QUOTE_CURRENCIES:
        if candidate == currency:
            return candidate
    return None


def _venue_unknown_finding(
    snapshots: Mapping[UUID, DeploymentSnapshot],
    findings: list[ReadinessFinding],
    failure: ExchangeReadFailure | None,
) -> None:
    """Record that the venue quote is unknown; entries already fail closed there."""
    live = any(item.deployment.mode is DeploymentMode.LIVE for item in snapshots.values())
    reason = (
        "The venue quote balance could not be observed, so account caps and remaining "
        "capacity are unknown (never guessed)."
        if failure is None
        else f"The venue quote balance could not be observed ({failure.summary()}); "
        "account caps and remaining capacity are unknown (never guessed)."
    )
    findings.append(
        ReadinessFinding(
            reason_code="VENUE_BALANCE_UNKNOWN",
            severity=ReadinessSeverity.UNKNOWN if live else ReadinessSeverity.INFO,
            detail=reason,
        )
    )


def _account_section(
    policy: ActiveRiskPolicy,
    live_bearing: Sequence[DeploymentSnapshot],
    venue: _VenueRead,
    findings: list[ReadinessFinding],
    *,
    inventory_evidence: ReadinessInventoryEvidence,
) -> ReadinessAccountCaps:
    """Compute account capacity in the policy quote currency (ADR 0106 scope)."""
    definition = policy.definition
    quote = definition.quote_currency
    venue_available = None if not venue.complete else venue.available.get(quote, _ZERO)
    complete = (
        inventory_evidence.status == "complete"
        and inventory_evidence.accounting_status == "complete"
    )
    inventory = sum(
        (
            position.quantity * position.entry_price
            for snapshot in live_bearing
            for position in snapshot_positions(snapshot)
            if position.side is PositionSide.LONG
            and _product_quote(resolved_product_id(position.product_id, snapshot.deployment))
            == quote
        ),
        _ZERO,
    )
    buy_reserved = sum((_buy_entry_reserved(item, quote) for item in live_bearing), _ZERO)
    capital_base = (
        None
        if venue_available is None or not complete
        else venue_available + inventory + buy_reserved
    )
    exposure = sum((_quote_exposure(item, quote) for item in live_bearing), _ZERO)
    excluded = tuple(
        product for product in _book_products(live_bearing) if _product_quote(product) != quote
    )
    if excluded:
        findings.append(
            ReadinessFinding(
                reason_code="QUOTE_CURRENCY_MISMATCH",
                severity=ReadinessSeverity.INFO,
                detail=f"Products outside {quote} account scope are excluded, not converted: "
                + ", ".join(excluded)
                + ".",
            )
        )
    if not complete:
        findings.append(
            ReadinessFinding(
                reason_code="ACCOUNT_INVENTORY_INCOMPLETE",
                severity=ReadinessSeverity.UNKNOWN,
                detail=(
                    "Managed live inventory is incomplete; account totals and capacity are unknown."
                ),
            )
        )
    fraction_cap = (
        None
        if capital_base is None
        else capital_base * Decimal(definition.max_portfolio_exposure_fraction)
    )
    absolute = (
        None
        if definition.max_portfolio_exposure_quote is None
        else Decimal(definition.max_portfolio_exposure_quote)
    )
    effective = fraction_cap
    if effective is not None and absolute is not None:
        effective = min(effective, absolute)
    remaining = None if effective is None else effective - exposure
    daily_fraction_cap = (
        None
        if capital_base is None
        else capital_base * Decimal(definition.daily_loss_limit_fraction)
    )
    daily_absolute = (
        None
        if definition.max_daily_loss_quote is None
        else Decimal(definition.max_daily_loss_quote)
    )
    effective_daily = daily_fraction_cap
    if effective_daily is not None and daily_absolute is not None:
        effective_daily = min(effective_daily, daily_absolute)
    product_caps = _product_cap_rows(
        live_bearing, capital_base, definition.per_product_max_exposure_fraction, quote, complete
    )
    if effective is not None and exposure > effective:
        findings.append(
            ReadinessFinding(
                reason_code="ACCOUNT_EXPOSURE_CAP_EXCEEDED",
                severity=ReadinessSeverity.VIOLATION,
                detail=(
                    f"Account exposure {canonical_decimal(exposure)} {quote} exceeds the "
                    f"effective cap {canonical_decimal(effective)} {quote}. The entry gate "
                    "already denies new risk-increasing orders; inspect books before resuming."
                ),
            )
        )
    findings.extend(_product_cap_findings(product_caps, quote))
    return ReadinessAccountCaps(
        quote_currency=quote,
        policy_source=policy.source.value,
        policy_fingerprint=policy.policy_fingerprint,
        capital_base=None if capital_base is None else canonical_decimal(capital_base),
        venue_available_quote=(
            None if venue_available is None else canonical_decimal(venue_available)
        ),
        inventory=inventory_evidence,
        excluded_products=excluded,
        managed_long_inventory_cost=canonical_decimal(inventory) if complete else None,
        working_buy_entry_reserved=(
            canonical_decimal(buy_reserved) if inventory_evidence.status == "complete" else None
        ),
        current_exposure=canonical_decimal(exposure) if complete else None,
        exposure_fraction=definition.max_portfolio_exposure_fraction,
        absolute_exposure_cap=definition.max_portfolio_exposure_quote,
        effective_exposure_cap=None if effective is None else canonical_decimal(effective),
        remaining_entry_capacity=None if remaining is None else canonical_decimal(remaining),
        per_product_fraction=definition.per_product_max_exposure_fraction,
        product_caps=product_caps,
        daily_loss_fraction=definition.daily_loss_limit_fraction,
        daily_loss_quote_cap=definition.max_daily_loss_quote,
        effective_daily_loss_cap=(
            None if effective_daily is None else canonical_decimal(effective_daily)
        ),
        drawdown_fraction=definition.max_strategy_drawdown_fraction,
        venue_read_failure=venue.failure,
    )


def _product_cap_findings(
    product_caps: tuple[ReadinessProductCapRow, ...], quote: SpotQuoteCurrency
) -> tuple[ReadinessFinding, ...]:
    """Violation findings for products whose cost-basis exposure exceeds the account cap."""
    return tuple(
        ReadinessFinding(
            reason_code="PRODUCT_EXPOSURE_CAP_EXCEEDED",
            severity=ReadinessSeverity.VIOLATION,
            detail=(
                f"Account exposure on {row.product_id} is {row.exposure} {quote} "
                f"above its cap {row.cap} {quote}."
            ),
        )
        for row in product_caps
        if row.cap is not None
        and row.exposure is not None
        and Decimal(row.exposure) > Decimal(row.cap)
    )


def _product_cap_rows(
    live_bearing: Sequence[DeploymentSnapshot],
    capital_base: Decimal | None,
    per_product_fraction: str,
    quote: SpotQuoteCurrency,
    complete: bool,
) -> tuple[ReadinessProductCapRow, ...]:
    """Cost-basis exposure per product within the live policy-quote scope."""
    rows: list[ReadinessProductCapRow] = []
    for product in _book_products(live_bearing):
        if _product_quote(product) != quote:
            continue
        exposure = sum((product_exposure(item, product) for item in live_bearing), _ZERO)
        if exposure <= 0:
            continue
        cap = None if capital_base is None else capital_base * Decimal(per_product_fraction)
        rows.append(
            ReadinessProductCapRow(
                product_id=product,
                quote_currency=quote_currency(product) if is_spot_product_id(product) else None,
                exposure=canonical_decimal(exposure) if complete else None,
                cap=None if cap is None else canonical_decimal(cap),
                remaining=None if cap is None else canonical_decimal(cap - exposure),
            )
        )
    return tuple(rows)


def _book_products(snapshots: Sequence[DeploymentSnapshot]) -> tuple[str, ...]:
    """Every product the given books hold, work, or run, sorted."""
    products: set[str] = set()
    for snapshot in snapshots:
        products.add(snapshot.deployment.product_id)
        products.update(
            resolved_product_id(position.product_id, snapshot.deployment)
            for position in snapshot_positions(snapshot)
        )
        products.update(
            resolved_product_id(order.product_id, snapshot.deployment) for order in snapshot.orders
        )
        products.update(
            runtime.product_id for runtime in snapshot.instrument_runtimes if runtime.product_id
        )
    return tuple(sorted(product for product in products if product))


def _buy_entry_reserved(snapshot: DeploymentSnapshot, quote: SpotQuoteCurrency) -> Decimal:
    """Working buy-entry quote for one book; mirrors the risk gate's held capital.

    Sells and verified exit intents hold base units or reduce risk, so only buy
    entries reserve quote here (ADR 0106).
    """
    buys = replace(
        snapshot, orders=tuple(order for order in snapshot.orders if order.side is OrderSide.BUY)
    )
    return sum(
        (
            working_entry_notional(buys, product)
            for product in _book_products((snapshot,))
            if _product_quote(product) == quote
        ),
        _ZERO,
    )


def _product_quote(product: str) -> SpotQuoteCurrency | None:
    """Resolve a spot product's quote without inferring FX for unsupported products."""
    return quote_currency(product) if is_spot_product_id(product) else None


def _quote_exposure(snapshot: DeploymentSnapshot, quote: SpotQuoteCurrency) -> Decimal:
    """Cost-basis positions plus working entry remainders on actual same-quote products."""
    return sum(
        (
            product_exposure(snapshot, product)
            for product in _book_products((snapshot,))
            if _product_quote(product) == quote
        ),
        _ZERO,
    )


def _quote_rows(snapshot: DeploymentSnapshot) -> tuple[ReadinessQuoteExposure, ...]:
    """Split mixed-product books into exact per-quote rows; never label a cross-quote sum."""
    rows: list[ReadinessQuoteExposure] = []
    for quote in sorted({q for p in _book_products((snapshot,)) if (q := _product_quote(p))}):
        cost = sum(
            (
                position.quantity * position.entry_price
                for position in snapshot_positions(snapshot)
                if _product_quote(resolved_product_id(position.product_id, snapshot.deployment))
                == quote
            ),
            _ZERO,
        )
        exposure = _quote_exposure(snapshot, quote)
        rows.append(
            ReadinessQuoteExposure(
                quote_currency=quote,
                inventory_cost=(
                    None if _inventory_unresolved(snapshot, quote) else canonical_decimal(cost)
                ),
                working_entry_reserved=canonical_decimal(exposure - cost),
                exposure=(
                    None if _inventory_unresolved(snapshot, quote) else canonical_decimal(exposure)
                ),
            )
        )
    return tuple(rows)


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


async def _account_fee_profile(
    portfolio: PortfolioService,
) -> tuple[FeeProfile | None, ExchangeReadFailure | None]:
    """Read account fee evidence, or return a failure without inventing rates."""
    try:
        return await portfolio.get_fee_profile(), None
    except ExchangeReadError as error:
        return None, error.failure
    except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
        return None, None


def _paper_fee_gap(
    deployment: Deployment, profile: FeeProfile | None, *, demo: bool
) -> ReadinessFeeGapRow | None:
    """Compare one paper book's assumptions with account evidence, if any."""
    if deployment.mode is not DeploymentMode.PAPER:
        return None
    maker, taker = effective_paper_fee_rates(
        deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
    )
    maker_gap = taker_gap = None
    optimistic = False
    if profile is not None and not demo:
        maker_gap = profile.maker_fee_rate - maker
        taker_gap = profile.taker_fee_rate - taker
        optimistic = maker_gap > 0 or taker_gap > 0
    return ReadinessFeeGapRow(
        deployment_id=deployment.id,
        product_id=deployment.product_id,
        assumed_maker_fee_rate=canonical_decimal(maker),
        assumed_taker_fee_rate=canonical_decimal(taker),
        account_maker_fee_rate=(
            None if profile is None else canonical_decimal(profile.maker_fee_rate)
        ),
        account_taker_fee_rate=(
            None if profile is None else canonical_decimal(profile.taker_fee_rate)
        ),
        maker_gap=None if maker_gap is None else canonical_decimal(maker_gap),
        taker_gap=None if taker_gap is None else canonical_decimal(taker_gap),
        more_optimistic=optimistic,
    )


def _record_fee_findings(
    findings: list[ReadinessFinding],
    *,
    compared: int,
    profile: FeeProfile | None,
    optimistic: Sequence[ReadinessFeeGapRow],
) -> None:
    """Disclose missing fee evidence or paper assumptions cheaper than the account."""
    if profile is None and compared:
        findings.append(
            ReadinessFinding(
                reason_code="FEE_EVIDENCE_UNAVAILABLE",
                severity=ReadinessSeverity.UNKNOWN,
                detail=(
                    "Account fee evidence is unavailable, so paper fee assumptions were "
                    "listed but not judged; no comparison was invented."
                ),
            )
        )
    if not optimistic:
        return
    worst = max(optimistic, key=lambda item: Decimal(item.maker_gap or "0"))
    findings.append(
        ReadinessFinding(
            reason_code="PAPER_FEE_ASSUMPTION_MORE_OPTIMISTIC",
            severity=ReadinessSeverity.ADVISORY,
            deployment_id=worst.deployment_id,
            detail=(
                f"{len(optimistic)} paper book(s) assume cheaper fees than the account "
                f"reports (e.g. maker {worst.assumed_maker_fee_rate} vs "
                f"{worst.account_maker_fee_rate}, taker {worst.assumed_taker_fee_rate} vs "
                f"{worst.account_taker_fee_rate}). Paper and backtest results read more "
                "optimistic than live fills; this disclosure changes no policy."
            ),
        )
    )


async def _fee_evidence(
    portfolio: PortfolioService,
    snapshots: Mapping[UUID, DeploymentSnapshot],
    findings: list[ReadinessFinding],
) -> ReadinessFeeEvidence:
    """Compare scoped paper fee assumptions with the account's reported rates."""
    profile, failure = await _account_fee_profile(portfolio)
    gaps = tuple(
        row
        for snapshot in snapshots.values()
        if (row := _paper_fee_gap(snapshot.deployment, profile, demo=portfolio.demo)) is not None
    )
    optimistic = tuple(row for row in gaps if row.more_optimistic)
    defaulting = sum(
        1
        for snapshot in snapshots.values()
        if snapshot.deployment.mode is DeploymentMode.PAPER
        and (
            snapshot.deployment.paper_maker_fee_rate is None
            or snapshot.deployment.paper_taker_fee_rate is None
        )
    )
    _record_fee_findings(findings, compared=len(gaps), profile=profile, optimistic=optimistic)
    return ReadinessFeeEvidence(
        account_maker_fee_rate=(
            None if profile is None else canonical_decimal(profile.maker_fee_rate)
        ),
        account_taker_fee_rate=(
            None if profile is None else canonical_decimal(profile.taker_fee_rate)
        ),
        account_fee_tier=None if profile is None else profile.fee_tier,
        account_as_of=None if profile is None else profile.as_of,
        demo=portfolio.demo,
        unavailable_reason=None if profile is not None else "read_failure",
        read_failure=failure,
        paper_books_compared=len(gaps),
        paper_books_defaulting_rates=defaulting,
        optimistic_books=optimistic,
    )


def _paper_section(
    policy: ActiveRiskPolicy | None,
    deployments: Sequence[Deployment],
    warnings: list[str],
) -> ReadinessPaperSection | None:
    """Paper-book committed starting cash versus the policy's paper capital."""
    if policy is None:
        return None
    paper = [item for item in deployments if item.mode is DeploymentMode.PAPER]
    matching = [
        item
        for item in paper
        if _product_quote(item.product_id) == policy.definition.quote_currency
    ]
    if len(matching) != len(paper):
        warnings.append(
            "Paper starting-cash totals exclude other/unknown quote books; no FX assumed."
        )
    committed = sum((item.paper_starting_cash or _ZERO for item in matching), _ZERO)
    return ReadinessPaperSection(
        quote_currency=policy.definition.quote_currency,
        paper_capital_quote=policy.definition.paper_capital_quote,
        committed_starting_cash=canonical_decimal(committed),
        books=len(matching),
    )


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
    """Advisory allocation overcommitment and quote-mismatch disclosures."""
    quote = account.quote_currency
    matching = [
        row
        for row in rows
        if row.mode == "live" and row.quote_currency == quote and row.allocated_capital
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


def _components(
    findings: Sequence[ReadinessFinding],
    *,
    venue: _VenueRead,
    fee_evidence: ReadinessFeeEvidence,
    warnings: Sequence[str],
) -> list[ComponentReport]:
    """Grade the report from finding severities plus venue and fee evidence."""
    severities = {finding.severity for finding in findings}
    if ReadinessSeverity.VIOLATION in severities:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.DEGRADED,
            reason_code="READINESS_VIOLATION",
            detail="Current exposure exceeds at least one cap; inspect before new risk.",
        )
    elif ReadinessSeverity.UNKNOWN in severities or warnings:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.DEGRADED,
            reason_code="READINESS_UNKNOWN",
            detail="At least one capacity input is unknown; nothing was guessed.",
        )
    elif ReadinessSeverity.ADVISORY in severities:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.DEGRADED,
            reason_code="READINESS_ADVISORY",
            detail=(
                "Advisory findings only (allocation overcommitment, optimistic fee "
                "assumptions, or latched breakers); no policy was changed."
            ),
        )
    else:
        main = ComponentReport(
            name="readiness",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail="No readiness findings; caps and capacities are advisory disclosure.",
        )
    components = [main]
    if not venue.complete:
        detail = (
            "The exchange account could not be queried."
            if venue.failure is None
            else venue.failure.summary()
        )
        components.append(
            ComponentReport(
                name="venue",
                status=ReportStatus.DEGRADED,
                reason_code="EXCHANGE_UNAVAILABLE",
                detail=detail,
            )
        )
    elif venue.demo:
        components.append(
            ComponentReport(
                name="venue",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE",
                detail="Venue balances are demo data, not account evidence.",
            )
        )
    if fee_evidence.unavailable_reason == "read_failure":
        components.append(
            ComponentReport(
                name="fees",
                status=ReportStatus.DEGRADED,
                reason_code="FEE_EVIDENCE_UNAVAILABLE",
                detail="Account fee evidence could not be read; assumptions were not judged.",
            )
        )
    elif fee_evidence.demo:
        components.append(
            ComponentReport(
                name="fees",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE",
                detail="Fee evidence is demo data; paper assumptions were not judged.",
            )
        )
    return components
