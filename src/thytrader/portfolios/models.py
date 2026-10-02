"""Portfolio domain records, validated value types, commands, and errors (ADR 0088).

Money and fractions travel as canonical plain decimal strings, exactly like the research
contracts: weights and fractions allow at most four decimal places (0.01%), quote amounts
at most eight. A portfolio's ``mode`` and ``quote_currency`` are fixed at creation. The
manager settings bound what the manager agent's proposals may change without approval
(ADR 0091); no field can grant order authority, because strategies place every trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import re
from typing import TYPE_CHECKING, Annotated, Final, Literal, Self
import unicodedata
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.market_data.products import (
    SpotQuoteCurrency,
    base_currency,
    is_spot_product_id,
    quote_currency,
)
from thytrader.strategies.models import covered_product_ids

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from thytrader.strategies.library import StrategyRecord

PortfolioMode = Literal["paper", "live"]
PORTFOLIO_MODES: Final[tuple[PortfolioMode, ...]] = ("paper", "live")

JournalKind = Literal[
    "created",
    "settings_changed",
    "sleeve_added",
    "sleeve_updated",
    "sleeve_removed",
    "weights_changed",
    "limits_changed",
    "manager_changed",
    "backtest_run",
    "deployment_started",
    "deployment_paused",
    "deployment_resumed",
    "deployment_stopped",
    "breaker_tripped",
    "breaker_reset",
    "proposal_submitted",
    "proposal_approved",
    "proposal_declined",
    "proposal_failed",
]
JOURNAL_KINDS: Final[tuple[JournalKind, ...]] = (
    "created",
    "settings_changed",
    "sleeve_added",
    "sleeve_updated",
    "sleeve_removed",
    "weights_changed",
    "limits_changed",
    "manager_changed",
    "backtest_run",
    "deployment_started",
    "deployment_paused",
    "deployment_resumed",
    "deployment_stopped",
    "breaker_tripped",
    "breaker_reset",
    "proposal_submitted",
    "proposal_approved",
    "proposal_declined",
    "proposal_failed",
)
JournalActor = Literal["operator", "system", "manager"]
"""Who changed the portfolio. ``manager`` is the manager agent acting on a proposal."""
JournalReason = Literal["operator", "strategy_deleted", "manager_proposal", "breaker"]
BreakerReason = Literal["PORTFOLIO_DAILY_LOSS_STOP", "PORTFOLIO_DRAWDOWN_STOP"]
BREAKER_REASONS: Final[tuple[BreakerReason, ...]] = (
    "PORTFOLIO_DAILY_LOSS_STOP",
    "PORTFOLIO_DRAWDOWN_STOP",
)
JOURNAL_ACTORS: Final[tuple[JournalActor, ...]] = ("operator", "system", "manager")
JournalChannel = Literal["browser", "api", "system"]
JOURNAL_CHANNELS: Final[tuple[JournalChannel, ...]] = ("browser", "api", "system")
SleeveIssueCode = Literal["strategy_invalid", "quote_currency_mismatch", "product_unknown"]

PORTFOLIO_BACKTEST_CONTRACT: Final = "thytrader-portfolio-backtest-v1"
PORTFOLIO_BRIEFING_CONTRACT: Final = "thytrader-portfolio-briefing-v1"
MAX_CONCURRENT_PORTFOLIO_BACKTESTS: Final = 1
MAX_SLEEVES: Final = 20
MAX_NAME_LENGTH: Final = 120
MAX_NOTE_LENGTH: Final = 280
MAX_MANDATE_LENGTH: Final = 2000
MAX_SUMMARY_LENGTH: Final = 500
MAX_RATIONALE_LENGTH: Final = 2000
FRACTION_PLACES: Final = 4
QUOTE_PLACES: Final = 8
MAX_CAPITAL_QUOTE: Final = Decimal("1000000000000000")
MANAGER_PERMISSION_KEYS: Final[frozenset[str]] = frozenset(
    {"may_rebalance", "may_pause_sleeves", "may_propose_sleeves"}
)

_DECIMAL_TEXT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_DECIMAL_TEXT_LENGTH = 40
_ZERO = Decimal(0)
_ONE = Decimal(1)
_ALLOWED_MANDATE_CONTROLS = frozenset({"\n", "\t"})


def canonical_decimal_text(value: str, *, places: int, label: str) -> str:
    """Validate one non-negative plain decimal string and return its canonical spelling."""
    if len(value) > _MAX_DECIMAL_TEXT_LENGTH or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError(f"{label} must be a non-negative plain decimal string such as '0.25'")
    whole, _separator, fraction = value.partition(".")
    fraction = fraction.rstrip("0")
    if len(fraction) > places:
        raise ValueError(f"{label} allows at most {places} decimal places")
    whole = whole.lstrip("0") or "0"
    return f"{whole}.{fraction}" if fraction else whole


def _ranged(
    value: str,
    *,
    label: str,
    places: int,
    minimum: Decimal,
    maximum: Decimal,
    minimum_inclusive: bool,
    maximum_inclusive: bool,
) -> str:
    """Canonicalize one decimal and require it inside a documented interval."""
    text = canonical_decimal_text(value, places=places, label=label)
    amount = Decimal(text)
    above = amount >= minimum if minimum_inclusive else amount > minimum
    below = amount <= maximum if maximum_inclusive else amount < maximum
    if not (above and below):
        left = "[" if minimum_inclusive else "("
        right = "]" if maximum_inclusive else ")"
        raise ValueError(f"{label} must be in {left}{minimum}, {maximum}{right}")
    return text


def _weight_fraction(value: str) -> str:
    """A sleeve weight: more than 0 and at most 1."""
    return _ranged(
        value,
        label="weight_fraction",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _reserve_fraction(value: str) -> str:
    """The cash reserve: at least 0 and below 1."""
    return _ranged(
        value,
        label="cash_reserve_fraction",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=True,
        maximum_inclusive=False,
    )


def _limit_fraction(value: str) -> str:
    """An exposure cap: more than 0 and at most 1 of portfolio capital."""
    return _ranged(
        value,
        label="exposure limit",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _drawdown_fraction(value: str) -> str:
    """A drawdown stop: more than 0 and below 1."""
    return _ranged(
        value,
        label="max_drawdown_fraction",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=False,
    )


def _weekly_change_fraction(value: str) -> str:
    """The manager's weekly weight-change budget: more than 0 and at most 1."""
    return _ranged(
        value,
        label="max_weight_change_per_week",
        places=FRACTION_PLACES,
        minimum=_ZERO,
        maximum=_ONE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _quote_amount(value: str) -> str:
    """A positive quote amount up to 1e15 with at most eight decimal places."""
    return _ranged(
        value,
        label="quote amount",
        places=QUOTE_PLACES,
        minimum=_ZERO,
        maximum=MAX_CAPITAL_QUOTE,
        minimum_inclusive=False,
        maximum_inclusive=True,
    )


def _has_disallowed_controls(value: str, *, allowed: frozenset[str] = frozenset()) -> bool:
    """True when text contains control or format characters outside ``allowed``."""
    return any(
        unicodedata.category(character).startswith("C") and character not in allowed
        for character in value
    )


def _portfolio_name(value: str) -> str:
    """Trim a portfolio name and require 1-120 visible characters."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("name must not be blank")
    if len(stripped) > MAX_NAME_LENGTH:
        raise ValueError(f"name allows at most {MAX_NAME_LENGTH} characters")
    if _has_disallowed_controls(stripped):
        raise ValueError("name must not contain control characters")
    return stripped


def _sleeve_note(value: str) -> str:
    """Trim a sleeve note; an empty note means "no note"."""
    stripped = value.strip()
    if len(stripped) > MAX_NOTE_LENGTH:
        raise ValueError(f"note allows at most {MAX_NOTE_LENGTH} characters")
    if _has_disallowed_controls(stripped):
        raise ValueError("note must be one line without control characters")
    return stripped


def _mandate(value: str) -> str:
    """Trim a manager mandate (multi-line plain text, at most 2000 characters)."""
    stripped = value.strip()
    if len(stripped) > MAX_MANDATE_LENGTH:
        raise ValueError(f"mandate allows at most {MAX_MANDATE_LENGTH} characters")
    if _has_disallowed_controls(stripped, allowed=_ALLOWED_MANDATE_CONTROLS):
        raise ValueError("mandate must be plain text")
    return stripped


WeightFractionText = Annotated[str, Field(strict=True), AfterValidator(_weight_fraction)]
ReserveFractionText = Annotated[str, Field(strict=True), AfterValidator(_reserve_fraction)]
LimitFractionText = Annotated[str, Field(strict=True), AfterValidator(_limit_fraction)]
DrawdownFractionText = Annotated[str, Field(strict=True), AfterValidator(_drawdown_fraction)]
WeeklyChangeFractionText = Annotated[
    str, Field(strict=True), AfterValidator(_weekly_change_fraction)
]
QuoteAmountText = Annotated[str, Field(strict=True), AfterValidator(_quote_amount)]
PortfolioNameText = Annotated[str, Field(strict=True), AfterValidator(_portfolio_name)]
SleeveNoteText = Annotated[str, Field(strict=True), AfterValidator(_sleeve_note)]
MandateText = Annotated[str, Field(strict=True), AfterValidator(_mandate)]
RevisionNumber = Annotated[StrictInt, Field(ge=1)]


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PortfolioLimits(_FrozenModel):
    """Shared limits stored on a portfolio (fractions of portfolio capital).

    On a deployed portfolio the exposure caps bind every sleeve's new entries in the risk
    gate, and the optional daily-loss and drawdown stops are portfolio breakers that pause
    every sleeve and latch until an operator reset (ADR 0091). Portfolio backtests do not
    simulate them.
    """

    max_total_exposure_fraction: LimitFractionText = Field(
        default="1", description="Cap on all sleeves' combined position value."
    )
    max_per_asset_fraction: LimitFractionText = Field(
        default="1", description="Cap on position value in any one base asset."
    )
    daily_loss_quote: QuoteAmountText | None = Field(
        default=None, description="Optional UTC-day loss stop in the quote currency."
    )
    max_drawdown_fraction: DrawdownFractionText | None = Field(
        default=None, description="Optional stop on drawdown from the portfolio's peak."
    )


class ManagerPermissions(_FrozenModel):
    """What the manager agent's proposals may do without approval. It never places orders.

    ``may_rebalance`` auto-applies rebalances on a paper portfolio within the rolling
    7-day ``max_weight_change_per_week`` budget (a live rebalance always waits for
    approval); ``may_pause_sleeves`` auto-applies pausing a running sleeve;
    ``may_propose_sleeves`` lets the manager propose new sleeves, which always need
    approval. Strategies place every trade (ADR 0091).
    """

    may_rebalance: StrictBool = False
    max_weight_change_per_week: WeeklyChangeFractionText = Field(
        default="0.1",
        description="Largest total weight a rebalance may move in a rolling week.",
    )
    may_pause_sleeves: StrictBool = False
    may_propose_sleeves: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def reject_unknown_authority(cls, data: object) -> object:
        """Refuse any unknown ``may_*`` permission with the manager's fixed boundary."""
        if isinstance(data, dict):
            for key in data:
                if (
                    isinstance(key, str)
                    and key.startswith("may_")
                    and key not in MANAGER_PERMISSION_KEYS
                ):
                    raise ValueError(
                        f"Unknown manager permission {key!r}: the manager may only rebalance "
                        "weights, pause sleeves, and propose sleeves. It never places orders; "
                        "strategies place every trade (ADR 0088)."
                    )
        return data


class ManagerSettings(_FrozenModel):
    """The manager agent's mandate and permissions (the agent loop runs outside ThyTrader)."""

    mandate: MandateText = ""
    permissions: ManagerPermissions = Field(default_factory=ManagerPermissions)


class JournalChange(_FrozenModel):
    """One field's value before and after a change, rendered as display text."""

    field: str = Field(min_length=1, max_length=64)
    before: str | None = Field(default=None, max_length=2000)
    after: str | None = Field(default=None, max_length=2000)


class JournalDetail(_FrozenModel):
    """Structured facts behind one journal entry.

    Deployment events name the sleeve deployments they touched; breaker events carry the
    tripped ``reason_code``; proposal events name the proposal and quote its rationale.
    """

    sleeve_id: UUID | None = None
    strategy_id: UUID | None = None
    strategy_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    reason: JournalReason | None = None
    changes: tuple[JournalChange, ...] = ()
    job_id: UUID | None = None
    result_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    deployment_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_SLEEVES * 2)
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    proposal_id: UUID | None = None
    rationale: str | None = Field(default=None, max_length=MAX_RATIONALE_LENGTH)
    note: str | None = Field(default=None, max_length=MAX_SUMMARY_LENGTH)


class JournalEntry(_FrozenModel):
    """One append-only portfolio journal event.

    ``revision`` is the portfolio revision the event produced (or, for ``backtest_run``,
    the revision whose composition was simulated).
    """

    entry_id: UUID
    portfolio_id: UUID
    occurred_at: datetime
    kind: JournalKind
    actor: JournalActor
    channel: JournalChannel
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    detail: JournalDetail = Field(default_factory=JournalDetail)
    revision: int = Field(ge=1)

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Keep journal instants timezone-aware UTC."""
        return require_utc(value)

    @field_serializer("occurred_at", when_used="json")
    def serialize_occurred_at(self, value: datetime) -> str:
        """Render the instant with a Z suffix."""
        return utc_text(value)


class PortfolioCreateRequest(_FrozenModel):
    """Create one paper or live portfolio; mode and quote currency are then fixed."""

    name: PortfolioNameText
    mode: PortfolioMode
    quote_currency: SpotQuoteCurrency = "USDC"
    capital_quote: QuoteAmountText
    cash_reserve_fraction: ReserveFractionText = "0"
    limits: PortfolioLimits = Field(default_factory=PortfolioLimits)
    manager: ManagerSettings = Field(default_factory=ManagerSettings)


class PortfolioUpdateRequest(_FrozenModel):
    """Change name, capital, cash reserve, limits, or manager settings in one revision."""

    revision: RevisionNumber
    name: PortfolioNameText | None = None
    capital_quote: QuoteAmountText | None = None
    cash_reserve_fraction: ReserveFractionText | None = None
    limits: PortfolioLimits | None = None
    manager: ManagerSettings | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_fixed_fields(cls, data: object) -> object:
        """Explain that mode and quote currency cannot change after creation."""
        if isinstance(data, dict) and ("mode" in data or "quote_currency" in data):
            raise ValueError(
                "mode and quote_currency are fixed when a portfolio is created; "
                "create a new portfolio instead."
            )
        return data

    @model_validator(mode="after")
    def require_change(self) -> Self:
        """Require at least one field to change."""
        if (
            self.name is None
            and self.capital_quote is None
            and self.cash_reserve_fraction is None
            and self.limits is None
            and self.manager is None
        ):
            raise ValueError(
                "Name at least one of name, capital_quote, cash_reserve_fraction, limits, "
                "or manager."
            )
        return self


class SleeveAddRequest(_FrozenModel):
    """Add one strategy as a sleeve with a capital weight."""

    revision: RevisionNumber
    strategy_id: UUID
    weight_fraction: WeightFractionText
    note: SleeveNoteText | None = None


class SleeveUpdateRequest(_FrozenModel):
    """Change one sleeve's weight and/or note. An empty note clears it."""

    revision: RevisionNumber
    weight_fraction: WeightFractionText | None = None
    note: SleeveNoteText | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        """Require a weight or a note."""
        if self.weight_fraction is None and self.note is None:
            raise ValueError("Name weight_fraction, note, or both.")
        return self


class WeightAssignment(_FrozenModel):
    """One sleeve's new weight."""

    sleeve_id: UUID
    weight_fraction: WeightFractionText


class SetWeightsRequest(_FrozenModel):
    """Replace every sleeve weight (and optionally the cash reserve) atomically."""

    revision: RevisionNumber
    weights: tuple[WeightAssignment, ...] = Field(min_length=1, max_length=MAX_SLEEVES)
    cash_reserve_fraction: ReserveFractionText | None = None


@dataclass(frozen=True, slots=True)
class MutationContext:
    """Who made a change, through which channel, and when (UTC)."""

    actor: JournalActor
    channel: JournalChannel
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class Portfolio:
    """One portfolio row. Decimal fields hold canonical decimal text."""

    portfolio_id: UUID
    name: str
    mode: PortfolioMode
    quote_currency: SpotQuoteCurrency
    capital_quote: str
    cash_reserve_fraction: str
    limits: PortfolioLimits
    manager: ManagerSettings
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Sleeve:
    """One strategy held in a portfolio with a capital weight."""

    sleeve_id: UUID
    portfolio_id: UUID
    strategy_id: UUID
    weight_fraction: str
    note: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SleeveStrategy:
    """Current facts about a sleeve's strategy, read with the sleeve.

    Strategies stay mutable, so these facts can drift after a sleeve is added (an edit can
    invalidate the rules or change the market). Reads report that drift as sleeve issues.
    """

    strategy_id: UUID
    name: str
    product_id: str | None
    covered_product_ids: tuple[str, ...]
    timeframe: str | None
    valid: bool
    current_fingerprint: str | None

    @property
    def quote_currency(self) -> SpotQuoteCurrency | None:
        """Quote currency of the primary product, or None when it is not parseable."""
        if self.product_id is None or not is_spot_product_id(self.product_id):
            return None
        return quote_currency(self.product_id)


@dataclass(frozen=True, slots=True)
class SleeveView:
    """A sleeve plus its strategy's current facts."""

    sleeve: Sleeve
    strategy: SleeveStrategy


@dataclass(frozen=True, slots=True)
class PortfolioAggregate:
    """A portfolio with every sleeve (in creation order)."""

    portfolio: Portfolio
    sleeves: tuple[SleeveView, ...]

    def sleeve(self, sleeve_id: UUID) -> SleeveView:
        """Return one sleeve or raise :class:`PortfolioSleeveNotFoundError`."""
        for view in self.sleeves:
            if view.sleeve.sleeve_id == sleeve_id:
                return view
        raise PortfolioSleeveNotFoundError("Sleeve was not found in this portfolio.")


@dataclass(frozen=True, slots=True)
class PortfolioPage:
    """One page of portfolios (oldest first) plus the total count."""

    portfolios: tuple[PortfolioAggregate, ...]
    total: int


@dataclass(frozen=True, slots=True)
class JournalPage:
    """One newest-first page of journal entries plus the total count."""

    entries: tuple[JournalEntry, ...]
    total: int


@dataclass(frozen=True, slots=True)
class PortfolioDeletion:
    """What deleting one portfolio removed."""

    portfolio_id: UUID
    name: str
    sleeves: int
    journal_entries: int
    backtests: int
    backtest_jobs: int


class PortfolioError(RuntimeError):
    """Base class for redacted portfolio failures."""


class PortfolioStorageUnavailableError(PortfolioError):
    """Durable portfolio storage could not complete the request."""


class PortfolioNotFoundError(PortfolioError):
    """No portfolio exists for the requested identity."""


class PortfolioSleeveNotFoundError(PortfolioError):
    """No sleeve with the requested identity exists in the portfolio."""


class PortfolioStrategyNotFoundError(PortfolioError):
    """A sleeve names a strategy that does not exist."""


class PortfolioRevisionConflictError(PortfolioError):
    """A mutation named a revision that is no longer current."""

    def __init__(self, current_revision: int) -> None:
        """Record the durable revision the caller must reload."""
        super().__init__("Portfolio was changed by another update; reload it and try again.")
        self.current_revision = current_revision


class PortfolioValidationError(PortfolioError):
    """A mutation would break a portfolio rule; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        """Keep a stable machine code beside the human message."""
        super().__init__(message)
        self.code = code


class PortfolioConflictError(PortfolioValidationError):
    """The portfolio's deployment or proposal state refuses this action (HTTP 409)."""


class PortfolioLiveAcknowledgementError(PortfolioError):
    """A live start, resume, or approval arrived without ``i_understand_live`` (HTTP 428)."""


@dataclass(frozen=True, slots=True)
class StartProblem:
    """One sleeve problem that blocked a portfolio start (nothing was started)."""

    code: str
    message: str
    sleeve_id: UUID | None = None
    strategy_id: UUID | None = None
    strategy_name: str | None = None


class PortfolioStartRejectedError(PortfolioValidationError):
    """Every problem that blocked starting a portfolio, so nothing started (HTTP 422)."""

    def __init__(self, problems: tuple[StartProblem, ...]) -> None:
        """Keep each sleeve problem for the response body."""
        count = len(problems)
        noun = "problem" if count == 1 else "problems"
        super().__init__(
            "portfolio_start_rejected",
            f"The portfolio was not started: {count} sleeve {noun}. Nothing was started.",
        )
        self.problems = problems


@dataclass(frozen=True, slots=True)
class PortfolioRuntimeState:
    """Durable runtime state of one deployed portfolio (ADR 0091).

    ``run_started_at`` marks the current run: sleeve deployments created since then make
    up its equity. The breaker latches with a reason until an operator reset, and the UTC
    day-open and high-water-mark equity are the baselines the daily-loss and drawdown
    stops measure against. A portfolio that was never deployed reads as the empty state.
    """

    portfolio_id: UUID
    run_started_at: datetime | None = None
    breaker_reason: BreakerReason | None = None
    breaker_detail: str | None = None
    breaker_latched_at: datetime | None = None
    day_open_equity: Decimal | None = None
    day_open_at: datetime | None = None
    high_water_mark_equity: Decimal | None = None
    last_equity: Decimal | None = None
    last_evaluated_at: datetime | None = None
    revision: int = 0
    """Compare-and-set counter: 0 until the first write, then +1 per write."""

    @property
    def breaker_latched(self) -> bool:
        """True while a tripped portfolio breaker waits for an operator reset."""
        return self.breaker_reason is not None


@dataclass(frozen=True, slots=True)
class PortfolioRuntimeView:
    """A portfolio with its sleeves and its runtime state, read together."""

    aggregate: PortfolioAggregate
    runtime: PortfolioRuntimeState


class PortfolioProposalNotFoundError(PortfolioError):
    """No proposal with the requested identity exists for this portfolio."""


class PortfolioSleeveExistsError(PortfolioValidationError):
    """The strategy is already a sleeve of this portfolio."""

    def __init__(self, sleeve_id: UUID) -> None:
        """Name the existing sleeve."""
        super().__init__(
            "portfolio_sleeve_exists",
            "This strategy is already a sleeve of the portfolio; change its weight instead.",
        )
        self.sleeve_id = sleeve_id


def require_utc(value: datetime) -> datetime:
    """Require a timezone-aware UTC instant and normalize its timezone object."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(UTC)


def utc_text(value: datetime) -> str:
    """Render one UTC instant with a Z suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utc_millisecond(value: datetime) -> datetime:
    """Truncate a UTC instant to the millisecond a UUIDv7 can encode."""
    normalized = value.astimezone(UTC)
    return normalized.replace(microsecond=(normalized.microsecond // 1_000) * 1_000)


def document_product_ids(document: Mapping[str, JsonValue], primary: str | None) -> tuple[str, ...]:
    """Read covered spot product ids from a (possibly invalid) strategy document."""
    products: list[str] = [primary] if primary is not None else []
    raw = document.get("additional_instruments")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            product = item.get("product_id")
            if isinstance(product, str) and is_spot_product_id(product) and product not in products:
                products.append(product)
    return tuple(products)


def sleeve_strategy_from_record(record: StrategyRecord) -> SleeveStrategy:
    """Project one strategy record into the facts a sleeve needs."""
    if record.definition is not None:
        covered = covered_product_ids(record.definition)
    else:
        covered = document_product_ids(record.document, record.product_id)
    return SleeveStrategy(
        strategy_id=record.strategy_id,
        name=record.name,
        product_id=record.product_id,
        covered_product_ids=covered,
        timeframe=record.timeframe,
        valid=record.validation.valid,
        current_fingerprint=record.current_fingerprint,
    )


def sleeve_issues(view: SleeveView, quote: SpotQuoteCurrency) -> tuple[SleeveIssueCode, ...]:
    """Return what keeps one sleeve from being backtested or (later) deployed."""
    issues: list[SleeveIssueCode] = []
    strategy_quote = view.strategy.quote_currency
    if strategy_quote is None:
        issues.append("product_unknown")
    elif strategy_quote != quote:
        issues.append("quote_currency_mismatch")
    if not view.strategy.valid:
        issues.append("strategy_invalid")
    return tuple(issues)


def asset_of(product_id: str) -> str:
    """Return the base asset of one spot product id."""
    return base_currency(product_id)
