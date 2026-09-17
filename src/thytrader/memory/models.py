"""Versioned experiential-memory records: journals, sentiment, patterns, notify.

Journal row kinds and human/agent review surfaces stay on the shared
``thytrader-experiential-memory-v1`` document. This module does not add a parallel
trade-reason schema; training consumes attributed ``JournalEntry`` rows as stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
import re
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from thytrader.market_data.products import SPOT_PRODUCT_ID_PATTERN
from thytrader.memory.trade_reasons import TradeReasonRecord  # noqa: TC001 - Pydantic field type.

MEMORY_SCHEMA_VERSION: Literal["thytrader-experiential-memory-v1"] = (
    "thytrader-experiential-memory-v1"
)
MONITOR_SCHEMA_VERSION: Literal["thytrader-monitor-v1"] = "thytrader-monitor-v1"
MODEL_SCHEMA_VERSION: Literal["thytrader-experiential-model-v1"] = "thytrader-experiential-model-v1"
ADVISORY_SCHEMA_VERSION: Literal["thytrader-experiential-advisory-v1"] = (
    "thytrader-experiential-advisory-v1"
)
TRAIN_ENGINE_ID: Literal["thytrader-experiential-train-v1"] = "thytrader-experiential-train-v1"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_PATTERN_KEY = r"^[a-z][a-z0-9_]{1,62}$"


class ActorOrigin(StrEnum):
    """Who authored the experiential record so agents can study their own work."""

    HUMAN = "human"
    AGENT = "agent"


class JournalKind(StrEnum):
    """Operator-managed journal row kinds."""

    FACT = "fact"
    LESSON = "lesson"
    NOTE = "note"


class EvidenceKind(StrEnum):
    """Optional pointer at immutable evidence; not a substitute for that evidence."""

    NONE = "none"
    BACKTEST = "backtest"
    PAPER_FILL = "paper_fill"
    LIVE_FILL = "live_fill"
    DEPLOYMENT = "deployment"
    RESEARCH = "research"
    MARKET_DATA = "market_data"


class RuntimeMode(StrEnum):
    """Which loop the journal or notification is about."""

    NONE = "none"
    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"


class LessonOutcome(StrEnum):
    """How a lesson classifies the cited outcome. Facts and notes stay ``none``."""

    NONE = "none"
    SUCCESS = "success"
    MISTAKE = "mistake"
    MIXED = "mixed"


class SentimentLabel(StrEnum):
    """Operator- or agent-submitted sentiment. Not an external scrape or model."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class PatternStatus(StrEnum):
    """Lifecycle of one pattern-learning observation hook."""

    HYPOTHESIZED = "hypothesized"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    RETIRED = "retired"


class NotifyProvider(StrEnum):
    """Configured delivery backend. Default is no external send."""

    NONE = "none"
    LOG = "log"
    WEBHOOK = "webhook"


class NotifySeverity(StrEnum):
    """Operator-visible urgency for one notification request."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DeliveryStatus(StrEnum):
    """What happened after a confirmation-gated notify request."""

    SKIPPED = "skipped"
    LOGGED = "logged"
    DELIVERED = "delivered"
    FAILED = "failed"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def require_utc(value: datetime) -> datetime:
    """Reject naive and non-UTC datetimes; coerce JSON Z to the UTC singleton."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)


class JournalEntry(_FrozenModel):
    """One append-only fact, lesson, or note with required origin attribution."""

    schema_version: Literal["thytrader-experiential-memory-v1"] = MEMORY_SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    origin: ActorOrigin
    kind: JournalKind
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    evidence_kind: EvidenceKind = EvidenceKind.NONE
    evidence_id: str | None = Field(default=None, max_length=128)
    product_id: str | None = Field(default=None, max_length=32)
    runtime_mode: RuntimeMode = RuntimeMode.NONE
    lesson_outcome: LessonOutcome = LessonOutcome.NONE

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        """Keep journal times timezone-aware UTC."""
        return require_utc(value)

    @field_validator("product_id")
    @classmethod
    def require_spot_product(cls, value: str | None) -> str | None:
        """Require Coinbase-style USD spot ids when a product is named."""
        return _optional_product(value)

    @model_validator(mode="after")
    def require_lesson_and_evidence_invariants(self) -> Self:
        """Lessons need an outcome; evidence ids are required only with a kind."""
        if self.kind is JournalKind.LESSON and self.lesson_outcome is LessonOutcome.NONE:
            raise ValueError("lesson journals require lesson_outcome other than none")
        if self.kind is not JournalKind.LESSON and self.lesson_outcome is not LessonOutcome.NONE:
            raise ValueError("lesson_outcome is only valid on kind=lesson")
        _require_evidence(self.evidence_kind, self.evidence_id)
        return self


class SentimentSnapshot(_FrozenModel):
    """One submitted sentiment hook. No model training or venue scrape."""

    schema_version: Literal["thytrader-experiential-memory-v1"] = MEMORY_SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    origin: ActorOrigin
    label: SentimentLabel
    product_id: str | None = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=1000)
    journal_id: UUID | None = None

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        """Keep sentiment times timezone-aware UTC."""
        return require_utc(value)

    @field_validator("product_id")
    @classmethod
    def require_spot_product(cls, value: str | None) -> str | None:
        """Require Coinbase-style USD spot ids when a product is named."""
        return _optional_product(value)


class PatternObservation(_FrozenModel):
    """One append-only pattern-learning hook. Same pattern_key may repeat over time."""

    schema_version: Literal["thytrader-experiential-memory-v1"] = MEMORY_SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    origin: ActorOrigin
    pattern_key: str = Field(pattern=_PATTERN_KEY)
    name: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=2000)
    status: PatternStatus = PatternStatus.HYPOTHESIZED
    evidence_kind: EvidenceKind = EvidenceKind.NONE
    evidence_id: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=1000)

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        """Keep pattern times timezone-aware UTC."""
        return require_utc(value)

    @model_validator(mode="after")
    def require_evidence_invariant(self) -> Self:
        """Evidence ids are required only when a kind is named."""
        _require_evidence(self.evidence_kind, self.evidence_id)
        return self


class NotificationRecord(_FrozenModel):
    """One confirmation-gated notify attempt, including skipped default-off delivery."""

    schema_version: Literal["thytrader-experiential-memory-v1"] = MEMORY_SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    origin: ActorOrigin
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    severity: NotifySeverity = NotifySeverity.INFO
    provider: NotifyProvider
    delivery_status: DeliveryStatus
    detail: str = Field(default="", max_length=500)
    journal_id: UUID | None = None

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        """Keep notification times timezone-aware UTC."""
        return require_utc(value)


class MemoryCounts(_FrozenModel):
    """Bounded inventory of experiential rows."""

    journals: int = Field(ge=0)
    sentiment: int = Field(ge=0)
    patterns: int = Field(ge=0)
    notifications: int = Field(ge=0)
    trade_reasons: int = Field(ge=0, default=0)


class MemoryStatus(_FrozenModel):
    """Read-only memory surface: counts plus redacted notifier configuration."""

    schema_version: Literal["thytrader-experiential-memory-v1"] = MEMORY_SCHEMA_VERSION
    counts: MemoryCounts
    notify_provider: NotifyProvider
    notify_webhook_configured: bool
    notify_enabled: bool
    storage: Literal["available", "unavailable"]


class MonitorDeployment(_FrozenModel):
    """One paper/live runtime identity for the monitor snapshot, without cash."""

    deployment_id: UUID
    mode: str = Field(min_length=1, max_length=16)
    status: str = Field(min_length=1, max_length=16)
    product_id: str = Field(min_length=1, max_length=32)
    mismatch_present: bool


class MonitorFinding(_FrozenModel):
    """One monitor observation with a stable reason code."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    detail: str = Field(max_length=500)
    deployment_id: UUID | None = None


class MonitorSnapshot(_FrozenModel):
    """Composite watch of deployments, journals, why-trade records, and notify."""

    schema_version: Literal["thytrader-monitor-v1"] = MONITOR_SCHEMA_VERSION
    memory: MemoryStatus
    deployments: tuple[MonitorDeployment, ...]
    recent_journals: tuple[JournalEntry, ...]
    recent_notifications: tuple[NotificationRecord, ...]
    recent_trade_reasons: tuple[TradeReasonRecord, ...] = ()
    findings: tuple[MonitorFinding, ...]


class PatternScore(_FrozenModel):
    """Integer rank for one pattern_key after deterministic training."""

    pattern_key: str = Field(pattern=_PATTERN_KEY)
    name: str = Field(min_length=1, max_length=120)
    score: int
    support_count: int = Field(ge=0)
    contradict_count: int = Field(ge=0)


class ProductScore(_FrozenModel):
    """Integer rank for one BASE-USD product after deterministic training."""

    product_id: str = Field(min_length=1, max_length=32)
    score: int
    success_count: int = Field(ge=0)
    mistake_count: int = Field(ge=0)

    @field_validator("product_id")
    @classmethod
    def require_spot_product(cls, value: str) -> str:
        """Require Coinbase-style USD spot ids on scored products."""
        validated = _optional_product(value)
        if validated is None:
            raise ValueError("product_id must be a BASE-USD spot product")
        return validated


class ExperientialCorpus(_FrozenModel):
    """Sorted identities of rows that survived fail-closed evidence checks."""

    journal_ids: tuple[str, ...]
    pattern_ids: tuple[str, ...]
    sentiment_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    human_rows: int = Field(ge=0)
    agent_rows: int = Field(ge=0)


class ExperientialAdvisory(_FrozenModel):
    """Gated research hint. Never an order intent or live policy."""

    schema_version: Literal["thytrader-experiential-advisory-v1"] = ADVISORY_SCHEMA_VERSION
    suggested_pattern_keys: tuple[str, ...]
    caution_pattern_keys: tuple[str, ...]
    suggested_products: tuple[str, ...]
    caution_products: tuple[str, ...]
    notes: str = Field(min_length=1, max_length=500)


class ExperientialModel(_FrozenModel):
    """Immutable fingerprintable learner output from attributed local evidence."""

    schema_version: Literal["thytrader-experiential-model-v1"] = MODEL_SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    origin: ActorOrigin
    engine_id: Literal["thytrader-experiential-train-v1"] = TRAIN_ENGINE_ID
    seed: int = Field(ge=0, le=2_147_483_647)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    corpus: ExperientialCorpus
    pattern_scores: tuple[PatternScore, ...]
    product_scores: tuple[ProductScore, ...]
    advisory: ExperientialAdvisory

    @field_validator("recorded_at")
    @classmethod
    def require_utc_timestamps(cls, value: datetime) -> datetime:
        """Keep model times timezone-aware UTC."""
        return require_utc(value)


class ExperientialTrainWrite(_FrozenModel):
    """Operator-authored training request without server identity."""

    origin: ActorOrigin
    seed: int = Field(default=1, ge=0, le=2_147_483_647)


class JournalWrite(_FrozenModel):
    """Operator-authored journal fields without server identity."""

    origin: ActorOrigin
    kind: JournalKind
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    evidence_kind: EvidenceKind = EvidenceKind.NONE
    evidence_id: str | None = Field(default=None, max_length=128)
    product_id: str | None = Field(default=None, max_length=32)
    runtime_mode: RuntimeMode = RuntimeMode.NONE
    lesson_outcome: LessonOutcome = LessonOutcome.NONE


class SentimentWrite(_FrozenModel):
    """Operator-authored sentiment snapshot without server identity."""

    origin: ActorOrigin
    label: SentimentLabel
    product_id: str | None = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=1000)
    journal_id: UUID | None = None


class PatternWrite(_FrozenModel):
    """Operator-authored pattern observation without server identity."""

    origin: ActorOrigin
    pattern_key: str = Field(pattern=_PATTERN_KEY)
    name: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=2000)
    status: PatternStatus = PatternStatus.HYPOTHESIZED
    evidence_kind: EvidenceKind = EvidenceKind.NONE
    evidence_id: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=1000)


class NotificationWrite(_FrozenModel):
    """Operator-authored notify request without delivery outcome."""

    origin: ActorOrigin
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    severity: NotifySeverity = NotifySeverity.INFO
    journal_id: UUID | None = None


def _optional_product(value: str | None) -> str | None:
    """Validate an optional BASE-USD or BASE-USDC product id."""
    if value is None:
        return None
    if not re.fullmatch(SPOT_PRODUCT_ID_PATTERN, value):
        raise ValueError("product_id must be a BASE-USD or BASE-USDC spot product")
    return value


def _require_evidence(kind: EvidenceKind, evidence_id: str | None) -> None:
    """Require an evidence id exactly when a kind other than none is set."""
    if kind is EvidenceKind.NONE:
        if evidence_id is not None:
            raise ValueError("evidence_id requires evidence_kind other than none")
        return
    if evidence_id is None or not evidence_id.strip():
        raise ValueError("evidence_id is required when evidence_kind is set")
