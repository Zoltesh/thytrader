"""Operator ``futures-books`` report and the bot-detail futures view (ADR 0129 §4, P1-6).

Read-only. For every paper futures book it shows the contract bound at start, the position
in contracts and base units, the last-bar mark, equity, gross notional and leverage, the
overnight margin (initial and maintenance at the latest observed rates; maintenance equals
initial), the liquidation buffer ``(equity - maintenance) / equity`` against the policy
minimum, the price at which equity would fall to maintenance, the funding ledger and the
reasons new entries are denied. Every amount is USD (the CFM settlement currency) and is
never added to a USDC or USDT amount; ``null`` is unknown, never zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from thytrader import __version__
from thytrader.decimal_text import canonical_decimal
from thytrader.exchanges.futures_models import SHARED_COLLATERAL_NOTE
from thytrader.execution.book_marks import last_bar_marks
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.operator.models import (
    PORTFOLIO_REDACTION,
    ComponentReport,
    OperatorEnvelope,
    ReportStatus,
    _FrozenModel,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.risk.futures_policy import DEFAULT_LIQUIDATION_BUFFER_FRACTION
from thytrader.risk.store import RiskPolicyStoreError, load_effective_policy
from thytrader.trading.futures_book import (
    FuturesBookUnavailableError,
    funding_hours_held,
    funding_overdue,
)
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    ExecutionStoreError,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.futures_paper import FuturesObservationReader
    from thytrader.market_data.futures_observations import FuturesInstrumentObservation
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.trading.futures_book import BoundFuturesContract, FuturesContractStore
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

RECENT_FUNDING_HOURS = 24
"""How many of the newest funding hours the view lists (the totals cover every hour)."""

UnknownEvidence = Literal["binding", "mark", "margin_rates", "funding", "policy"]
EntryBlock = Literal[
    "FUTURES_CONTRACT_UNBOUND",
    "FUTURES_MARGIN_UNKNOWN",
    "FUNDING_HISTORY_MISSING",
    "FUTURES_POLICY_UNSET",
]

_RATIO = Decimal("0.000001")
_CENT = Decimal("0.01")


class FuturesFundingEntryPayload(_FrozenModel):
    """One funding hour applied to the book (USD; negative ``amount`` was paid)."""

    funding_time: datetime
    signed_quantity: str
    mark_price: str
    rate: str
    amount: str


class FuturesBookPayload(_FrozenModel):
    """One paper futures book. Amounts are USD strings; ``null`` is unknown.

    ``unknown`` lists what could not be read (``binding``, ``mark``, ``margin_rates``,
    an overdue ``funding`` hour, an unreadable ``policy``); figures that depend on it are
    ``null``. ``entry_blocks`` are the reason codes that deny the book's next entry today
    (protective and liquidation exits are never blocked). ``liquidation_price`` is the mark
    at which equity would equal maintenance (overnight rate, maintenance = initial); the
    worker liquidates on a closed bar whose adverse extreme crosses it.
    """

    deployment_id: UUID
    strategy_name: str | None
    status: str
    mode: Literal["paper", "live"]
    product_id: str
    currency: Literal["USD"] = "USD"
    contract_kind: Literal["perpetual_future", "dated_future"] | None
    underlying: str | None
    contract_size: str | None
    fee_per_contract: str | None
    maker_fee_rate: str | None
    taker_fee_rate: str | None
    catalog_fingerprint: str | None
    bound_at: datetime | None
    side: Literal["long", "short", "flat"]
    contracts: str | None
    base_quantity: str
    entry_price: str | None
    mark_price: str | None
    marked_at: datetime | None
    paper_starting_cash: str | None
    cash: str
    equity: str | None
    notional: str | None
    leverage: str | None
    policy_max_leverage: str | None
    overnight_long_margin_rate: str | None
    overnight_short_margin_rate: str | None
    margin_observed_at: datetime | None
    initial_margin: str | None
    maintenance_margin: str | None
    liquidation_buffer_fraction: str | None
    min_liquidation_buffer_fraction: str | None
    liquidation_price: str | None
    funding_total: str
    funding_hours: int
    funding_overdue_since: datetime | None
    recent_funding: tuple[FuturesFundingEntryPayload, ...]
    daily_loss_latched: bool
    entry_blocks: tuple[EntryBlock, ...]
    unknown: tuple[UnknownEvidence, ...]


class FuturesBooksPayload(_FrozenModel):
    """Every paper futures book plus the policy envelope that funds them (USD)."""

    paper_capital_usd: str | None
    committed_paper_cash_usd: str
    futures_policy_set: bool | None
    books: tuple[FuturesBookPayload, ...]
    collateral_note: str = SHARED_COLLATERAL_NOTE
    live_supported: Literal[False] = False


class FuturesBooksReport(OperatorEnvelope):
    """Read-only paper futures books."""

    report_kind: Literal["futures_books"] = "futures_books"
    payload: FuturesBooksPayload


_DETAILS: dict[str, str] = {
    "OK": "Every active paper futures book's binding, mark, margin rates and funding are known.",
    "NO_FUTURES_BOOKS": "No paper futures book exists.",
    "STORE_UNAVAILABLE": "Execution storage could not be read.",
    "FUTURES_EVIDENCE_UNKNOWN": (
        "Some active paper futures books have an unreadable binding, policy, no mark yet, no "
        "observed margin rates or an overdue funding hour; their dependent figures are null "
        "and new entries are denied."
    ),
}


async def effective_policy_or_none(store: RiskPolicyStore | None) -> RiskPolicyDefinition | None:
    """The effective policy (compiled default when unpublished), or None when unreadable."""
    try:
        return (await load_effective_policy(store)).definition
    except RiskPolicyStoreError:
        return None


async def futures_book_payload(
    snapshot: DeploymentSnapshot,
    *,
    journal: DecisionJournalStore | None,
    contracts: FuturesContractStore | None,
    observations: FuturesObservationReader | None,
    policy: RiskPolicyDefinition | None,
    now: datetime | None = None,
) -> FuturesBookPayload:
    """Read the binding, mark and margin rates of one futures book and project it.

    ``policy`` None means the policy could not be read (not "unpublished").
    """
    binding = None
    if contracts is not None:
        try:
            binding = await contracts.load_contract(snapshot.deployment.id)
        except FuturesBookUnavailableError:
            binding = None
    observation = None
    if observations is not None:
        try:
            observation = await observations.latest_instrument(snapshot.deployment.product_id)
        except Exception:  # noqa: BLE001 - an unreadable catalog leaves margin unknown.
            observation = None
    marks = {} if journal is None else await last_bar_marks(journal, snapshot, now=now)
    mark = marks.get(snapshot.deployment.product_id)
    return book_payload(
        snapshot,
        binding=binding,
        observation=None if observation is None else observation[0],
        observed_at=None if observation is None else observation[1],
        mark=None if mark is None else (mark.price, mark.bar_closes_at),
        policy=policy,
        now=now or datetime.now(UTC),
    )


def book_payload(
    snapshot: DeploymentSnapshot,
    *,
    binding: BoundFuturesContract | None,
    observation: FuturesInstrumentObservation | None,
    observed_at: datetime | None,
    mark: tuple[Decimal, datetime] | None,
    policy: RiskPolicyDefinition | None,
    now: datetime,
) -> FuturesBookPayload:
    """Project one futures book onto exact USD strings (pure)."""
    deployment = snapshot.deployment
    signed = _signed_quantity(snapshot)
    side: Literal["long", "short", "flat"] = (
        "flat" if signed == 0 else ("short" if signed < 0 else "long")
    )
    quantity = abs(signed)
    long_rate = _rate(None if observation is None else observation.overnight_long_margin_rate)
    short_rate = _rate(None if observation is None else observation.overnight_short_margin_rate)
    price = None if mark is None else mark[0]
    equity = _equity(deployment.cash, signed, price)
    notional = Decimal(0) if quantity == 0 else (None if price is None else quantity * price)
    rate = short_rate if side == "short" else long_rate
    initial = None if notional is None or rate is None else notional * rate
    overdue = _funding_overdue_since(snapshot, binding, now)
    unknown = _unknown(
        binding=binding,
        mark_missing=price is None and quantity != 0,
        rates_known=long_rate is not None and short_rate is not None,
        overdue=overdue,
        policy=policy,
    )
    futures = None if policy is None else policy.futures
    funding = sorted(snapshot.funding, key=lambda flow: flow.funding_time)
    return FuturesBookPayload(
        deployment_id=deployment.id,
        strategy_name=deployment.strategy_name,
        status=deployment.status.value,
        mode=deployment.mode.value,
        product_id=deployment.product_id,
        contract_kind=None if binding is None else binding.contract.kind,
        underlying=None if binding is None else binding.contract.underlying,
        contract_size=None if binding is None else binding.contract.contract_size,
        fee_per_contract=None if binding is None else _text(binding.fee_per_contract),
        maker_fee_rate=_optional(deployment.paper_maker_fee_rate),
        taker_fee_rate=_optional(deployment.paper_taker_fee_rate),
        catalog_fingerprint=None if binding is None else binding.contract.catalog_fingerprint,
        bound_at=None if binding is None else binding.bound_at,
        side=side,
        contracts=(
            None if binding is None else _text(quantity / Decimal(binding.contract.contract_size))
        ),
        base_quantity=_text(quantity),
        entry_price=None if snapshot.position is None else _text(snapshot.position.entry_price),
        mark_price=_optional(price),
        marked_at=None if mark is None else mark[1],
        paper_starting_cash=_optional(deployment.paper_starting_cash),
        cash=_text(deployment.cash),
        equity=_optional(equity),
        notional=_optional(notional),
        leverage=_ratio(notional, equity),
        policy_max_leverage=None if futures is None else futures.max_leverage,
        overnight_long_margin_rate=_optional(long_rate),
        overnight_short_margin_rate=_optional(short_rate),
        margin_observed_at=observed_at,
        initial_margin=_optional(initial),
        maintenance_margin=_optional(initial),
        liquidation_buffer_fraction=(
            None if initial is None or equity is None else _ratio(equity - initial, equity)
        ),
        min_liquidation_buffer_fraction=_min_buffer(policy),
        liquidation_price=_liquidation_price(deployment.cash, signed, rate),
        funding_total=_text(sum((flow.amount for flow in funding), start=Decimal(0))),
        funding_hours=len(funding),
        funding_overdue_since=overdue,
        recent_funding=tuple(
            FuturesFundingEntryPayload(
                funding_time=flow.funding_time,
                signed_quantity=_text(flow.signed_quantity),
                mark_price=_text(flow.mark_price),
                rate=_text(flow.rate),
                amount=_text(flow.amount),
            )
            for flow in reversed(funding[-RECENT_FUNDING_HOURS:])
        ),
        daily_loss_latched=deployment.daily_loss_latched,
        entry_blocks=_entry_blocks(binding, long_rate, short_rate, overdue, policy),
        unknown=unknown,
    )


async def build_futures_books_report(
    *,
    execution: ExecutionStore,
    journal: DecisionJournalStore | None,
    contracts: FuturesContractStore | None,
    observations: FuturesObservationReader | None,
    policy: RiskPolicyDefinition | None,
    now: datetime | None = None,
) -> FuturesBooksReport:
    """List every paper futures book with its margin, buffer, leverage and funding.

    ``policy`` None means the effective policy could not be read.
    """
    generated_at = now or datetime.now(UTC)
    try:
        deployments = await execution.list_deployments()
        snapshots = [
            await execution.get_deployment(item.id)
            for item in deployments
            if is_futures_product_id(item.product_id)
        ]
    except ExecutionStoreError:
        component = _component(ReportStatus.FAILED, "STORE_UNAVAILABLE")
        return _report(generated_at, _payload(policy, ()), (component,))
    books = tuple(
        [
            await futures_book_payload(
                snapshot,
                journal=journal,
                contracts=contracts,
                observations=observations,
                policy=policy,
                now=generated_at,
            )
            for snapshot in snapshots
        ]
    )
    active = [book for book in books if book.status != DeploymentStatus.STOPPED.value]
    if not books:
        component = _component(ReportStatus.HEALTHY, "NO_FUTURES_BOOKS")
    elif any(book.unknown for book in active):
        component = _component(ReportStatus.DEGRADED, "FUTURES_EVIDENCE_UNKNOWN")
    else:
        component = _component(ReportStatus.HEALTHY, "OK")
    return _report(generated_at, _payload(policy, books), (component,))


def _payload(
    policy: RiskPolicyDefinition | None, books: tuple[FuturesBookPayload, ...]
) -> FuturesBooksPayload:
    """Sum the starting cash running and paused paper books draw from the USD envelope."""
    futures = None if policy is None else policy.futures
    committed = sum(
        (
            Decimal(book.paper_starting_cash)
            for book in books
            if book.mode == DeploymentMode.PAPER.value
            and book.status in {DeploymentStatus.RUNNING.value, DeploymentStatus.PAUSED.value}
            and book.paper_starting_cash is not None
        ),
        start=Decimal(0),
    )
    return FuturesBooksPayload(
        paper_capital_usd=None if futures is None else futures.paper_capital_usd,
        committed_paper_cash_usd=_text(committed),
        futures_policy_set=None if policy is None else futures is not None,
        books=books,
    )


def _signed_quantity(snapshot: DeploymentSnapshot) -> Decimal:
    """Signed base quantity of the futures product (negative for shorts)."""
    total = Decimal(0)
    for position in snapshot_positions(snapshot):
        if resolved_product_id(position.product_id, snapshot.deployment) != (
            snapshot.deployment.product_id
        ):
            continue
        total += -position.quantity if position.side is PositionSide.SHORT else position.quantity
    return total


def _equity(cash: Decimal, signed: Decimal, price: Decimal | None) -> Decimal | None:
    """Cash plus signed base quantity at the mark (the spot ledger's equity identity)."""
    if signed == 0:
        return cash
    return None if price is None else cash + signed * price


def _liquidation_price(cash: Decimal, signed: Decimal, rate: Decimal | None) -> str | None:
    """The mark at which equity equals maintenance (= initial margin), or None.

    Long ``q``: ``cash + qP = qPr`` gives ``P = -cash / (q(1 - r))``, rounded up a cent.
    Short ``q``: ``cash - qP = qPr`` gives ``P = cash / (q(1 + r))``, rounded down a cent.
    Rounding moves the price towards the current mark (conservative). A long whose cash
    covers the whole notional has no positive liquidation price (None).
    """
    if signed == 0 or rate is None:
        return None
    quantity = abs(signed)
    if signed > 0:
        if rate >= 1:
            return None
        price = -cash / (quantity * (1 - rate))
        rounded = price.quantize(_CENT, rounding=ROUND_CEILING)
    else:
        price = cash / (quantity * (1 + rate))
        rounded = price.quantize(_CENT, rounding=ROUND_FLOOR)
    return _text(rounded) if rounded > 0 else None


def _funding_overdue_since(
    snapshot: DeploymentSnapshot, binding: BoundFuturesContract | None, now: datetime
) -> datetime | None:
    """The oldest held funding hour of a perp still unapplied past the settle grace."""
    if binding is None or binding.contract.kind != "perpetual_future":
        return None
    due = funding_hours_held(snapshot, snapshot.deployment.product_id, through=now)
    return due[0] if due and funding_overdue(due[0], now) else None


def _unknown(
    *,
    binding: BoundFuturesContract | None,
    mark_missing: bool,
    rates_known: bool,
    overdue: datetime | None,
    policy: RiskPolicyDefinition | None,
) -> tuple[UnknownEvidence, ...]:
    """The evidence that could not be read, in a fixed order."""
    unknown: list[UnknownEvidence] = []
    if binding is None:
        unknown.append("binding")
    if mark_missing:
        unknown.append("mark")
    if not rates_known:
        unknown.append("margin_rates")
    if overdue is not None:
        unknown.append("funding")
    if policy is None:
        unknown.append("policy")
    return tuple(unknown)


def _entry_blocks(
    binding: BoundFuturesContract | None,
    long_rate: Decimal | None,
    short_rate: Decimal | None,
    overdue: datetime | None,
    policy: RiskPolicyDefinition | None,
) -> tuple[EntryBlock, ...]:
    """Evidence reason codes the worker would deny the next entry with (ADR 0129 §4-5)."""
    blocks: list[EntryBlock] = []
    if binding is None:
        blocks.append("FUTURES_CONTRACT_UNBOUND")
    if long_rate is None or short_rate is None:
        blocks.append("FUTURES_MARGIN_UNKNOWN")
    if overdue is not None:
        blocks.append("FUNDING_HISTORY_MISSING")
    if policy is not None and policy.futures is None:
        blocks.append("FUTURES_POLICY_UNSET")
    return tuple(blocks)


def _min_buffer(policy: RiskPolicyDefinition | None) -> str | None:
    """The policy's minimum liquidation buffer (0.5 while unset); None when unreadable."""
    if policy is None:
        return None
    if policy.futures is None:
        return _text(DEFAULT_LIQUIDATION_BUFFER_FRACTION)
    return _text(policy.futures.liquidation_buffer_fraction)


def _rate(raw: str | None) -> Decimal | None:
    """A listed margin rate in (0, 1], or None when unlisted or malformed (as the worker)."""
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and 0 < value <= 1 else None


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> str | None:
    """``numerator / denominator`` to six places, or None when unknown or not positive."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return _text((numerator / denominator).quantize(_RATIO))


def _optional(value: Decimal | None) -> str | None:
    """Canonical decimal text, keeping unknown as None."""
    return None if value is None else _text(value)


def _text(value: Decimal) -> str:
    """Canonical decimal text."""
    return canonical_decimal(value)


def _component(status: ReportStatus, reason_code: str) -> ComponentReport:
    """One component with its fixed detail."""
    return ComponentReport(
        name="futures_books", status=status, reason_code=reason_code, detail=_DETAILS[reason_code]
    )


def _report(
    generated_at: datetime,
    payload: FuturesBooksPayload,
    components: tuple[ComponentReport, ...],
) -> FuturesBooksReport:
    """Wrap the payload; balances are shown, identifiers and secrets never are."""
    return FuturesBooksReport(
        application_version=__version__,
        generated_at=generated_at,
        overall_status=aggregate_status(components),
        components=components,
        redaction=PORTFOLIO_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )
