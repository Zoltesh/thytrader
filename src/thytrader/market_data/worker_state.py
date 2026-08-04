"""Durable state contracts for supervised historical market-data ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from thytrader.market_data.models import CandleInterval


class MarketDataWorkerUnavailableError(RuntimeError):
    """Signal that durable market-data worker state is unavailable."""


class MarketDataWorkerError(ValueError):
    """Signal an unrepresentable market-data worker range or schedule."""


class MarketDataWorkerStatus(StrEnum):
    """Observable lifecycle outcome for the most recent ingestion attempt."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MarketDataMaintenanceKind(StrEnum):
    """Type of bounded work selected for the current maintenance cycle."""

    INITIAL_BACKFILL = "initial_backfill"
    INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class MarketDataWorkerAttempt:
    """Requested identity and range recorded before provider retrieval begins."""

    provider: str
    product_id: str
    timeframe: CandleInterval
    attempted_at: datetime
    requested_starts_at: datetime
    requested_ends_at: datetime
    maintenance_kind: MarketDataMaintenanceKind = MarketDataMaintenanceKind.INITIAL_BACKFILL
    expected_ends_at: datetime | None = None
    next_attempt_at: datetime | None = None
    expected_consecutive_failures: int = 0


@dataclass(frozen=True, slots=True)
class MarketDataWorkerSuccess:
    """Verified publication facts recorded after manifest read-back succeeds."""

    attempt: MarketDataWorkerAttempt
    covered_starts_at: datetime
    covered_ends_at: datetime
    expected_candle_count: int
    received_candle_count: int
    gap_count: int
    missing_intervals: int
    content_fingerprint: str
    advances_revision: bool = True


@dataclass(frozen=True, slots=True)
class MarketDataWorkerFailure:
    """Stable redacted failure facts for one unsuccessful attempt."""

    attempt: MarketDataWorkerAttempt
    code: str
    message: str
    next_retry_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MarketDataWorkerState:
    """Latest durable ingestion outcome plus last verified coverage evidence."""

    provider: str
    product_id: str
    timeframe: CandleInterval
    status: MarketDataWorkerStatus
    last_attempt_at: datetime
    last_success_at: datetime | None
    requested_starts_at: datetime
    requested_ends_at: datetime
    covered_starts_at: datetime | None
    covered_ends_at: datetime | None
    expected_candle_count: int | None
    received_candle_count: int | None
    gap_count: int | None
    missing_intervals: int | None
    complete: bool
    content_fingerprint: str | None
    failure_code: str | None
    failure_message: str | None
    consecutive_failures: int
    updated_at: datetime
    expected_ends_at: datetime | None = None
    next_retry_at: datetime | None = None
    dataset_revision: int = 0
    maintenance_kind: MarketDataMaintenanceKind = MarketDataMaintenanceKind.INITIAL_BACKFILL
    enabled: bool = True

    def __post_init__(self) -> None:
        """Reject malformed durable timestamp facts before they can influence worker decisions."""
        validate_market_data_worker_state(self)


def validate_market_data_worker_state(state: MarketDataWorkerState) -> MarketDataWorkerState:
    """Require every deserialized worker-state field to satisfy lifecycle invariants."""
    _require_nonempty_text(state.provider, "provider")
    _require_nonempty_text(state.product_id, "product_id")
    _require_instance(state.timeframe, CandleInterval, "timeframe")
    _require_instance(state.status, MarketDataWorkerStatus, "status")
    _require_instance(state.maintenance_kind, MarketDataMaintenanceKind, "maintenance_kind")
    _require_bool(state.complete, "complete")
    _require_bool(state.enabled, "enabled")
    _require_nonnegative_integer(state.consecutive_failures, "consecutive_failures")
    _require_nonnegative_integer(state.dataset_revision, "dataset_revision")

    timestamps = (
        ("last_attempt_at", state.last_attempt_at),
        ("last_success_at", state.last_success_at),
        ("requested_starts_at", state.requested_starts_at),
        ("requested_ends_at", state.requested_ends_at),
        ("covered_starts_at", state.covered_starts_at),
        ("covered_ends_at", state.covered_ends_at),
        ("updated_at", state.updated_at),
        ("expected_ends_at", state.expected_ends_at),
        ("next_retry_at", state.next_retry_at),
    )
    for field_name, value in timestamps:
        if value is not None:
            _require_utc_timestamp(value, field_name)
    _require_ordered_timestamps(state)
    _validate_coverage(state)
    _validate_failure_lifecycle(state)
    return state


def _require_utc_timestamp(value: object, field_name: str) -> None:
    """Reject one malformed worker-state timestamp with a controlled domain error."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        message = f"Market-data worker state field {field_name!r} must be timezone-aware UTC."
        raise MarketDataWorkerError(message)


def _require_nonempty_text(value: object, field_name: str) -> None:
    """Require one durable identity or diagnostic field to be a nonempty string."""
    if not isinstance(value, str) or not value:
        message = f"Market-data worker state field {field_name!r} must be a nonempty string."
        raise MarketDataWorkerError(message)


def _require_instance(value: object, expected_type: type[object], field_name: str) -> None:
    """Require one model-bypassed enum field to retain its domain type."""
    if not isinstance(value, expected_type):
        message = f"Market-data worker state field {field_name!r} has an invalid domain value."
        raise MarketDataWorkerError(message)


def _require_bool(value: object, field_name: str) -> None:
    """Require one persisted boolean without accepting integer lookalikes."""
    if type(value) is not bool:
        message = f"Market-data worker state field {field_name!r} must be a boolean."
        raise MarketDataWorkerError(message)


def _require_nonnegative_integer(value: object, field_name: str) -> None:
    """Require one persisted counter without accepting booleans or negative values."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        message = f"Market-data worker state field {field_name!r} must be a non-negative integer."
        raise MarketDataWorkerError(message)


def _require_optional_nonnegative_integer(value: object, field_name: str) -> None:
    """Require one optional persisted coverage count when it is present."""
    if value is not None:
        _require_nonnegative_integer(value, field_name)


def _require_ordered_timestamps(state: MarketDataWorkerState) -> None:
    """Require durable worker instants to preserve their recorded lifecycle order."""
    if state.requested_starts_at > state.requested_ends_at:
        message = "Market-data worker state has an inverted requested range."
        raise MarketDataWorkerError(message)
    if state.updated_at < state.last_attempt_at:
        message = "Market-data worker state was updated before its last attempt."
        raise MarketDataWorkerError(message)
    if state.last_success_at is not None and state.last_success_at > state.last_attempt_at:
        message = "Market-data worker state records success after its last attempt."
        raise MarketDataWorkerError(message)


def _validate_coverage(state: MarketDataWorkerState) -> None:
    """Require complete coverage evidence to be all-present, exact, and content-addressed."""
    coverage = (
        state.covered_starts_at,
        state.covered_ends_at,
        state.expected_candle_count,
        state.received_candle_count,
        state.gap_count,
        state.missing_intervals,
        state.content_fingerprint,
    )
    present = tuple(value is not None for value in coverage)
    if any(present) and not all(present):
        message = "Market-data worker state has incomplete coverage evidence."
        raise MarketDataWorkerError(message)
    if not any(present):
        if state.complete or state.last_success_at is not None:
            message = "Market-data worker state claims success without coverage evidence."
            raise MarketDataWorkerError(message)
        return

    _require_optional_nonnegative_integer(state.expected_candle_count, "expected_candle_count")
    _require_optional_nonnegative_integer(state.received_candle_count, "received_candle_count")
    _require_optional_nonnegative_integer(state.gap_count, "gap_count")
    _require_optional_nonnegative_integer(state.missing_intervals, "missing_intervals")
    if state.covered_starts_at is None or state.covered_ends_at is None:
        message = "Market-data worker state has incomplete coverage timestamps."
        raise MarketDataWorkerError(message)
    if state.covered_starts_at >= state.covered_ends_at:
        message = "Market-data worker state has an empty or inverted coverage range."
        raise MarketDataWorkerError(message)
    if (
        not state.complete
        or state.expected_candle_count is None
        or state.expected_candle_count < 1
        or state.received_candle_count != state.expected_candle_count
        or state.gap_count != 0
        or state.missing_intervals != 0
    ):
        message = "Market-data worker state has incomplete coverage facts."
        raise MarketDataWorkerError(message)
    _require_content_fingerprint(state.content_fingerprint)


def _require_content_fingerprint(value: object) -> None:
    """Require one complete coverage fingerprint to retain its canonical SHA-256 shape."""
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        message = "Market-data worker state has an invalid coverage fingerprint."
        raise MarketDataWorkerError(message)


def _validate_failure_lifecycle(state: MarketDataWorkerState) -> None:
    """Require failure diagnostics and counters to agree with the current lifecycle outcome."""
    has_failure_code = state.failure_code is not None
    has_failure_message = state.failure_message is not None
    if has_failure_code != has_failure_message:
        message = "Market-data worker state has incomplete failure diagnostics."
        raise MarketDataWorkerError(message)
    if has_failure_code:
        _require_nonempty_text(state.failure_code, "failure_code")
        _require_nonempty_text(state.failure_message, "failure_message")
    if state.status is MarketDataWorkerStatus.SUCCEEDED:
        if state.consecutive_failures != 0 or has_failure_code or not state.complete:
            message = "Market-data worker success state has inconsistent failure or coverage facts."
            raise MarketDataWorkerError(message)
    elif state.status is MarketDataWorkerStatus.FAILED:
        if state.consecutive_failures < 1 or not has_failure_code:
            message = "Market-data worker failure state has inconsistent failure facts."
            raise MarketDataWorkerError(message)
    elif (state.consecutive_failures == 0) != (not has_failure_code):
        message = "Market-data worker running state has inconsistent retained failure facts."
        raise MarketDataWorkerError(message)


@runtime_checkable
class MarketDataWorkerStateStore(Protocol):
    """Persist and read the latest state for each ingestion target."""

    async def record_attempt(self, attempt: MarketDataWorkerAttempt) -> bool:
        """Record a running attempt only when its planning snapshot remains current."""
        ...

    async def record_success(self, success: MarketDataWorkerSuccess) -> None:
        """Record verified coverage and clear prior failure state."""
        ...

    async def record_failure(self, failure: MarketDataWorkerFailure) -> None:
        """Record a redacted failure while preserving prior successful coverage."""
        ...

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWorkerState | None:
        """Return the latest state for one exact ingestion target."""
        ...


class DisabledMarketDataWorkerStateStore:
    """Reject diagnostics when PostgreSQL worker state is not configured."""

    async def record_attempt(self, attempt: MarketDataWorkerAttempt) -> bool:
        """Reject writes because durable state is unavailable."""
        del attempt
        raise MarketDataWorkerUnavailableError("Market-data worker state is unavailable.")

    async def record_success(self, success: MarketDataWorkerSuccess) -> None:
        """Reject writes because durable state is unavailable."""
        del success
        raise MarketDataWorkerUnavailableError("Market-data worker state is unavailable.")

    async def record_failure(self, failure: MarketDataWorkerFailure) -> None:
        """Reject writes because durable state is unavailable."""
        del failure
        raise MarketDataWorkerUnavailableError("Market-data worker state is unavailable.")

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWorkerState | None:
        """Reject reads so disabled state never appears as an idle worker."""
        del provider, product_id, timeframe
        raise MarketDataWorkerUnavailableError("Market-data worker state is unavailable.")


class InMemoryMarketDataWorkerStateStore:
    """Deterministic state repository used only by behavior tests."""

    def __init__(self) -> None:
        """Initialize an empty target-state mapping."""
        self._states: dict[tuple[str, str, CandleInterval], MarketDataWorkerState] = {}

    async def record_attempt(self, attempt: MarketDataWorkerAttempt) -> bool:
        """Record an in-progress attempt without erasing prior verified coverage."""
        key = _key(attempt.provider, attempt.product_id, attempt.timeframe)
        prior = self._states.get(key)
        if prior is None:
            if attempt.expected_consecutive_failures != 0:
                return False
        elif (
            attempt.attempted_at <= prior.last_attempt_at
            or attempt.expected_consecutive_failures != prior.consecutive_failures
        ):
            return False
        self._states[key] = MarketDataWorkerState(
            provider=attempt.provider,
            product_id=attempt.product_id,
            timeframe=attempt.timeframe,
            status=MarketDataWorkerStatus.RUNNING,
            last_attempt_at=attempt.attempted_at,
            last_success_at=prior.last_success_at if prior is not None else None,
            requested_starts_at=attempt.requested_starts_at,
            requested_ends_at=attempt.requested_ends_at,
            covered_starts_at=prior.covered_starts_at if prior is not None else None,
            covered_ends_at=prior.covered_ends_at if prior is not None else None,
            expected_candle_count=prior.expected_candle_count if prior is not None else None,
            received_candle_count=prior.received_candle_count if prior is not None else None,
            gap_count=prior.gap_count if prior is not None else None,
            missing_intervals=prior.missing_intervals if prior is not None else None,
            complete=prior.complete if prior is not None else False,
            content_fingerprint=prior.content_fingerprint if prior is not None else None,
            failure_code=prior.failure_code if prior is not None else None,
            failure_message=prior.failure_message if prior is not None else None,
            consecutive_failures=prior.consecutive_failures if prior is not None else 0,
            updated_at=attempt.attempted_at,
            expected_ends_at=attempt.expected_ends_at or attempt.requested_ends_at,
            next_retry_at=None,
            dataset_revision=prior.dataset_revision if prior is not None else 0,
            maintenance_kind=attempt.maintenance_kind,
            enabled=True,
        )
        return True

    async def record_success(self, success: MarketDataWorkerSuccess) -> None:
        """Replace the current attempt with verified successful coverage."""
        attempt = success.attempt
        key = _key(attempt.provider, attempt.product_id, attempt.timeframe)
        prior = self._states.get(key)
        if prior is not None and (
            attempt.attempted_at < prior.last_attempt_at
            or (
                attempt.attempted_at == prior.last_attempt_at
                and prior.status is not MarketDataWorkerStatus.RUNNING
            )
            or (
                prior.covered_ends_at is not None
                and success.covered_ends_at < prior.covered_ends_at
            )
            or attempt.expected_consecutive_failures != prior.consecutive_failures
        ):
            return
        self._states[key] = MarketDataWorkerState(
            provider=attempt.provider,
            product_id=attempt.product_id,
            timeframe=attempt.timeframe,
            status=MarketDataWorkerStatus.SUCCEEDED,
            last_attempt_at=attempt.attempted_at,
            last_success_at=attempt.attempted_at,
            requested_starts_at=attempt.requested_starts_at,
            requested_ends_at=attempt.requested_ends_at,
            covered_starts_at=success.covered_starts_at,
            covered_ends_at=success.covered_ends_at,
            expected_candle_count=success.expected_candle_count,
            received_candle_count=success.received_candle_count,
            gap_count=success.gap_count,
            missing_intervals=success.missing_intervals,
            complete=True,
            content_fingerprint=success.content_fingerprint,
            failure_code=None,
            failure_message=None,
            consecutive_failures=0,
            updated_at=attempt.attempted_at,
            expected_ends_at=attempt.expected_ends_at or attempt.requested_ends_at,
            next_retry_at=attempt.next_attempt_at,
            dataset_revision=(prior.dataset_revision if prior is not None else 0)
            + int(success.advances_revision),
            maintenance_kind=attempt.maintenance_kind,
            enabled=True,
        )

    async def record_failure(self, failure: MarketDataWorkerFailure) -> None:
        """Record failure diagnostics without erasing prior verified coverage."""
        attempt = failure.attempt
        key = _key(attempt.provider, attempt.product_id, attempt.timeframe)
        prior = self._states.get(key)
        if prior is not None and (
            attempt.attempted_at < prior.last_attempt_at
            or (
                attempt.attempted_at == prior.last_attempt_at
                and prior.status is not MarketDataWorkerStatus.RUNNING
            )
            or attempt.expected_consecutive_failures != prior.consecutive_failures
        ):
            return
        self._states[key] = MarketDataWorkerState(
            provider=attempt.provider,
            product_id=attempt.product_id,
            timeframe=attempt.timeframe,
            status=MarketDataWorkerStatus.FAILED,
            last_attempt_at=attempt.attempted_at,
            last_success_at=prior.last_success_at if prior is not None else None,
            requested_starts_at=attempt.requested_starts_at,
            requested_ends_at=attempt.requested_ends_at,
            covered_starts_at=prior.covered_starts_at if prior is not None else None,
            covered_ends_at=prior.covered_ends_at if prior is not None else None,
            expected_candle_count=prior.expected_candle_count if prior is not None else None,
            received_candle_count=prior.received_candle_count if prior is not None else None,
            gap_count=prior.gap_count if prior is not None else None,
            missing_intervals=prior.missing_intervals if prior is not None else None,
            complete=prior.complete if prior is not None else False,
            content_fingerprint=prior.content_fingerprint if prior is not None else None,
            failure_code=failure.code,
            failure_message=failure.message,
            consecutive_failures=(prior.consecutive_failures if prior is not None else 0) + 1,
            updated_at=attempt.attempted_at,
            expected_ends_at=attempt.expected_ends_at or attempt.requested_ends_at,
            next_retry_at=failure.next_retry_at,
            dataset_revision=prior.dataset_revision if prior is not None else 0,
            maintenance_kind=attempt.maintenance_kind,
            enabled=True,
        )

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWorkerState | None:
        """Return the latest state for an exact target, if attempted."""
        state = self._states.get(_key(provider, product_id, timeframe))
        return validate_market_data_worker_state(state) if state is not None else None


def _key(
    provider: str,
    product_id: str,
    timeframe: CandleInterval,
) -> tuple[str, str, CandleInterval]:
    """Build the stable target identity used by in-memory tests."""
    return provider, product_id, timeframe
