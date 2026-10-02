"""Canonical immutable specifications for reproducible research runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.market_data.models import CandleInterval, DatasetTimeframe, parse_candle_interval
from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN, SpotQuoteCurrency

_FINGERPRINT_PREFIX = "sha256:"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DECIMAL_TEXT_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_DECIMAL_TEXT_LENGTH = 64
_MAX_INITIAL_QUOTE_BALANCE = Decimal("1000000000000000000")
_MAX_FEE_RATE = Decimal("0.1")
_MAX_SLIPPAGE_BPS = Decimal("1000")
_MAX_SPREAD_BPS = Decimal("1000")

BACKTEST_ENGINE: Final = "thytrader-backtest"
"""The single unversioned backtest engine identity bound into run, trace, and result bytes.

ADR 0083 defines its semantics. A future semantic change needs a superseding ADR and a new
identity string so earlier fingerprints never acquire new meaning.
"""

SIMULATION_SEMANTICS: Final = "2026-10-02"
"""Date of the latest amendment to the unified model's simulated fills (ADR 0083).

Part of the submission dedupe key only, so a request made after an amendment re-simulates
instead of reusing a result computed under the earlier rule. Published run, trace, and
result bytes are untouched, so earlier fingerprints keep their meaning. This is not an
engine version: there is one engine, and nothing user-facing shows this value.
"""

BacktestEngine = Literal["thytrader-backtest"]


def removed_engine_selection_message(name: str = "engine_contract_version") -> str:
    """Return the caller-facing rejection for a request that still selects an engine."""
    return (
        f"{name} was removed: ThyTrader has one backtest model (ADR 0083). "
        "Omit it; set spread_bps only for optional constant spread stress."
    )


def reject_removed_engine_selection(data: object) -> object:
    """Fail loudly when an untrusted request payload still carries an engine selector.

    Used as a ``mode="before"`` validator on HTTP, CLI, study, and job request models so a
    stale client gets an explicit migration message instead of a generic extra-field error.
    """
    if isinstance(data, dict) and "engine_contract_version" in data:
        raise ValueError(removed_engine_selection_message())
    return data


def _decimal_text(value: str) -> str:
    """Validate and lexically normalize one non-negative finite plain decimal string."""
    if len(value) > _MAX_DECIMAL_TEXT_LENGTH or not _DECIMAL_TEXT_PATTERN.fullmatch(value):
        raise ValueError("financial assumptions must be non-negative plain decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("financial assumptions must be valid decimal strings") from error
    if not parsed.is_finite():
        raise ValueError("financial assumptions must be finite")
    whole, separator, fraction = value.partition(".")
    canonical_whole = whole.lstrip("0") or "0"
    canonical_fraction = fraction.rstrip("0") if separator else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    return f"{canonical_whole}{decimal_places}"


DecimalText = Annotated[str, Field(strict=True), AfterValidator(_decimal_text)]
UtcDateTime = Annotated[datetime, Field(strict=True)]
FingerprintText = Annotated[str, Field(strict=True, pattern=_FINGERPRINT_PATTERN)]
StrictUuid = Annotated[UUID, Field(strict=True)]


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime, *, label: str) -> datetime:
    """Require one timezone-aware UTC timestamp and normalize its timezone object."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _require_utc_candle_boundary(value: datetime, *, label: str) -> datetime:
    """Require a timezone-aware UTC instant aligned to a whole minute (1m finest clock)."""
    normalized = _require_utc(value, label=label)
    if normalized.second or normalized.microsecond:
        raise ValueError(f"{label} must be an aligned UTC candle boundary")
    return normalized


def _utc_text(value: datetime) -> str:
    """Serialize a UTC timestamp using the canonical RFC 3339 Z suffix."""
    return value.isoformat().replace("+00:00", "Z")


class EvaluationWindow(_FrozenModel):
    """Half-open completed-candle interval whose signals will be evaluated."""

    starts_at: UtcDateTime
    ends_at: UtcDateTime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_hour_boundary(cls, value: datetime, info: object) -> datetime:
        """Require every evaluation boundary to align to a UTC candle start."""
        field_name = getattr(info, "field_name", "evaluation timestamp")
        return _require_utc_candle_boundary(value, label=str(field_name))

    @field_serializer("starts_at", "ends_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize evaluation boundaries canonically."""
        return _utc_text(value)

    @model_validator(mode="after")
    def require_nonempty_interval(self) -> Self:
        """Require at least one completed 1m candle in the evaluation interval."""
        if self.ends_at - self.starts_at < CandleInterval.ONE_MINUTE.duration:
            raise ValueError("evaluation interval must contain at least one candle")
        return self


class WarmupWindow(_FrozenModel):
    """Strategy-derived completed candles immediately preceding evaluation."""

    bars: int = Field(strict=True, ge=1, le=10_000)
    starts_at: UtcDateTime

    @field_validator("starts_at")
    @classmethod
    def require_hour_boundary(cls, value: datetime) -> datetime:
        """Require the warmup boundary to align to a UTC candle start."""
        return _require_utc_candle_boundary(value, label="warmup starts_at")

    @field_serializer("starts_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize the warmup boundary canonically."""
        return _utc_text(value)


class CapitalAssumptions(_FrozenModel):
    """Exact starting quote capital for one USD or USDC spot simulation."""

    quote_currency: SpotQuoteCurrency
    initial_quote_balance: DecimalText

    @field_validator("initial_quote_balance")
    @classmethod
    def require_positive_bounded_balance(cls, value: str) -> str:
        """Require positive capital within the supported research bound."""
        amount = Decimal(value)
        if amount <= 0 or amount > _MAX_INITIAL_QUOTE_BALANCE:
            raise ValueError("initial_quote_balance must be greater than 0 and at most 1e18")
        return value


class CostAssumptions(_FrozenModel):
    """Exact deterministic fee, fixed-slippage, and optional spread-stress assumptions.

    ``spread_bps`` is a disclosed constant total bid-ask spread stress (default zero). It is
    applied to marketable (taker) legs, stop triggers, and equity marks only; resting maker
    limits still fill at their posted price. It is never observed Coinbase book evidence.
    """

    maker_fee_rate: DecimalText
    taker_fee_rate: DecimalText
    fixed_slippage_bps: DecimalText
    spread_bps: DecimalText = "0"

    @field_validator("maker_fee_rate", "taker_fee_rate")
    @classmethod
    def require_bounded_fee(cls, value: str) -> str:
        """Bound each fee rate to the explicit zero-through-ten-percent range."""
        if Decimal(value) > _MAX_FEE_RATE:
            raise ValueError("fee rates must be at most 0.1")
        return value

    @field_validator("fixed_slippage_bps")
    @classmethod
    def require_bounded_slippage(cls, value: str) -> str:
        """Bound fixed slippage to a deliberately conservative maximum."""
        if Decimal(value) > _MAX_SLIPPAGE_BPS:
            raise ValueError("fixed_slippage_bps must be at most 1000")
        return value

    @field_validator("spread_bps")
    @classmethod
    def require_bounded_spread(cls, value: str) -> str:
        """Bound the constant spread stress to a deliberately conservative maximum."""
        if Decimal(value) > _MAX_SPREAD_BPS:
            raise ValueError("spread_bps must be at most 1000")
        return value

    @model_validator(mode="after")
    def require_maker_not_above_taker(self) -> Self:
        """Require the maker fee assumption not to exceed the taker fee assumption."""
        if Decimal(self.maker_fee_rate) > Decimal(self.taker_fee_rate):
            raise ValueError("maker_fee_rate must not exceed taker_fee_rate")
        return self


class IndicatorTimeframeDataset(_FrozenModel):
    """One extra indicator clock bound to a verified complete-only dataset."""

    timeframe: DatasetTimeframe
    dataset_fingerprint: FingerprintText


class AdditionalInstrumentDataset(_FrozenModel):
    """One extra covered product bound to verified complete-only datasets."""

    product_id: str = Field(pattern=SPOT_PRODUCT_ID_PATTERN)
    dataset_fingerprint: FingerprintText
    htf_dataset_fingerprint: FingerprintText | None = None
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )


class ResearchRunSpecification(_FrozenModel):
    """Immutable identity-bearing request for one deterministic unified backtest simulation."""

    schema_version: Literal["1.0"]
    run_id: StrictUuid
    created_at: UtcDateTime
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    htf_dataset_fingerprint: FingerprintText | None = None
    indicator_dataset_fingerprints: tuple[IndicatorTimeframeDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    additional_instrument_datasets: tuple[AdditionalInstrumentDataset, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    evaluation: EvaluationWindow
    warmup: WarmupWindow
    capital: CapitalAssumptions
    costs: CostAssumptions
    engine: BacktestEngine = BACKTEST_ENGINE
    random_seed: int = Field(strict=True, ge=0, le=2**63 - 1)

    @field_validator("run_id")
    @classmethod
    def require_uuid7(cls, value: UUID) -> UUID:
        """Use time-sortable UUIDv7 identifiers for run-request identity."""
        if value.version != 7:
            raise ValueError("run_id must be UUIDv7")
        return value

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Require a timezone-aware UTC creation instant."""
        return _require_utc(value, label="created_at")

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        """Serialize the creation instant canonically."""
        return _utc_text(value)

    @model_validator(mode="after")
    def require_run_id_creation_millisecond(self) -> Self:
        """Bind the UUIDv7 timestamp to the canonical request creation millisecond."""
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        elapsed = self.created_at - epoch
        created_at_milliseconds = (
            elapsed.days * 86_400_000 + elapsed.seconds * 1_000 + elapsed.microseconds // 1_000
        )
        if self.run_id.time != created_at_milliseconds:
            raise ValueError("run_id timestamp must match the created_at UTC millisecond")
        return self

    @model_validator(mode="after")
    def require_derived_warmup_range(self) -> Self:
        """Require warmup to end at evaluation start with ingested-venue bar spacing."""
        try:
            specification_bar_interval(self)
        except OverflowError as error:
            raise ValueError("warmup range cannot represent the declared bars") from error
        except ValueError as error:
            raise ValueError(str(error)) from error
        return self

    @model_validator(mode="after")
    def require_distinct_htf_dataset_identity(self) -> Self:
        """HTF dataset identity is optional and must not alias the decision dataset."""
        if (
            self.htf_dataset_fingerprint is not None
            and self.htf_dataset_fingerprint == self.dataset_fingerprint
        ):
            raise ValueError("htf_dataset_fingerprint must differ from dataset_fingerprint")
        return self

    @model_validator(mode="after")
    def require_distinct_indicator_dataset_identities(self) -> Self:
        """Extra indicator datasets must be unique clocks and distinct from LTF/HTF identities."""
        fingerprints = [item.dataset_fingerprint for item in self.indicator_dataset_fingerprints]
        timeframes = [item.timeframe for item in self.indicator_dataset_fingerprints]
        if len(timeframes) != len(set(timeframes)):
            raise ValueError("indicator_dataset_fingerprints timeframes must be unique")
        ordered = tuple(
            sorted(
                self.indicator_dataset_fingerprints,
                key=lambda item: int(
                    parse_candle_interval(item.timeframe).duration.total_seconds()
                ),
            )
        )
        if ordered != self.indicator_dataset_fingerprints:
            raise ValueError(
                "indicator_dataset_fingerprints must be ordered by increasing timeframe duration"
            )
        reserved = {self.dataset_fingerprint}
        if self.htf_dataset_fingerprint is not None:
            reserved.add(self.htf_dataset_fingerprint)
        if any(fingerprint in reserved for fingerprint in fingerprints):
            raise ValueError(
                "indicator_dataset_fingerprints must differ from dataset_fingerprint and "
                "htf_dataset_fingerprint"
            )
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("indicator_dataset_fingerprints identities must be unique")
        return self

    @model_validator(mode="after")
    def require_ordered_additional_instrument_datasets(self) -> Self:
        """Keep extra product bindings unique, lex-ordered, and distinct from the primary."""
        if not self.additional_instrument_datasets:
            return self
        product_ids = [item.product_id for item in self.additional_instrument_datasets]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("additional_instrument_datasets product_id values must be unique")
        ordered = tuple(
            sorted(self.additional_instrument_datasets, key=lambda item: item.product_id)
        )
        if ordered != self.additional_instrument_datasets:
            raise ValueError("additional_instrument_datasets must be ordered by product_id")
        reserved = {self.dataset_fingerprint}
        if self.htf_dataset_fingerprint is not None:
            reserved.add(self.htf_dataset_fingerprint)
        reserved.update(item.dataset_fingerprint for item in self.indicator_dataset_fingerprints)
        extra_fingerprints: list[str] = []
        for binding in self.additional_instrument_datasets:
            extra_fingerprints.append(binding.dataset_fingerprint)
            if binding.htf_dataset_fingerprint is not None:
                extra_fingerprints.append(binding.htf_dataset_fingerprint)
            extra_fingerprints.extend(
                item.dataset_fingerprint for item in binding.indicator_dataset_fingerprints
            )
        if any(fingerprint in reserved for fingerprint in extra_fingerprints):
            raise ValueError(
                "additional_instrument_datasets must differ from dataset_fingerprint and "
                "companion HTF/extra-TF identities"
            )
        if len(extra_fingerprints) != len(set(extra_fingerprints)):
            raise ValueError("additional_instrument_datasets identities must be unique")
        return self


def specification_bar_interval(specification: ResearchRunSpecification) -> CandleInterval:
    """Infer an ingested venue clock from warmup spacing. Does not invent unsupported intervals."""
    span = specification.evaluation.starts_at - specification.warmup.starts_at
    for interval in CandleInterval:
        if not interval.execution_supported:
            continue
        if span != interval.duration * specification.warmup.bars:
            continue
        for stamp in (
            specification.warmup.starts_at,
            specification.evaluation.starts_at,
            specification.evaluation.ends_at,
        ):
            if interval.align_closed_end(stamp) != stamp:
                raise ValueError(
                    "warmup and evaluation bounds must align to the inferred candle interval"
                )
        return interval
    raise ValueError(
        "warmup starts_at must equal evaluation starts_at minus the declared warmup bars"
    )


def warmup_starts_at(evaluation_starts_at: datetime, bars: int, timeframe: str) -> datetime:
    """Derive the warmup window start from one strategy timeframe."""
    interval = parse_candle_interval(timeframe)
    if not interval.execution_supported:
        raise ValueError("warmup windows require an ingested venue strategy timeframe.")
    return evaluation_starts_at - interval.duration * bars


def canonical_research_run_bytes(specification: ResearchRunSpecification) -> bytes:
    """Revalidate and serialize a run specification into deterministic canonical UTF-8 JSON."""
    validated = ResearchRunSpecification.model_validate(specification.model_dump(mode="python"))
    payload = validated.model_dump(mode="json", exclude_none=True)
    if not payload.get("additional_instrument_datasets"):
        payload.pop("additional_instrument_datasets", None)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def research_run_fingerprint(specification: ResearchRunSpecification) -> str:
    """Return the SHA-256 identity of the complete canonical run specification."""
    digest = sha256(canonical_research_run_bytes(specification)).hexdigest()
    return f"{_FINGERPRINT_PREFIX}{digest}"
