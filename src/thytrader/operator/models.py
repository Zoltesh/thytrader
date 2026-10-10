"""Versioned operator diagnostic report contracts: the shared report envelope.

Every operator report extends `OperatorEnvelope` and names a `report_kind` from
`REPORT_KINDS`. This module owns the schema version, status, component, and redaction
models the envelope carries; each report's payload models live in a sibling
`*_models` module (health, market, portfolio, runtime, support bundle).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.market_data.models import DatasetTimeframe

SCHEMA_VERSION: Literal["thytrader-operator-report-v1"] = "thytrader-operator-report-v1"
OPERATOR_API_PREFIX = "/api/v1/operator"
REPORT_KINDS: tuple[str, ...] = (
    "health",
    "configuration",
    "exchange",
    "market_data",
    "data_catalog",
    "data_health",
    "products",
    "indicators",
    "strategies",
    "performance",
    "risk",
    "reconciliation",
    "runtime",
    "monitor",
    "studies",
    "trade_reasons",
    "decisions",
    "support_bundle",
    "portfolio",
    "fees",
    "portfolios",
    "readiness",
    "venue_reconciliation",
    "alerts",
    "funding",
    "futures_account",
    "futures_books",
    "fleet_health",
)

SupportedTimeframe = DatasetTimeframe


class ReportStatus(StrEnum):
    """Overall and per-component diagnostic outcome."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ComponentReport(_FrozenModel):
    """One named subsystem with a stable reason code."""

    name: str = Field(min_length=1, max_length=64)
    status: ReportStatus
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    detail: str = Field(default="", max_length=500)


class RedactionMetadata(_FrozenModel):
    """Record what this report deliberately omitted."""

    secrets_redacted: bool
    raw_environment_omitted: bool
    account_identifiers_omitted: bool
    balances_omitted: bool


class OperatorEnvelope(_FrozenModel):
    """Fields required on every machine-readable operator report."""

    schema_version: Literal["thytrader-operator-report-v1"] = SCHEMA_VERSION
    application_version: str = Field(min_length=1, max_length=32)
    generated_at: datetime
    timezone: Literal["UTC"] = "UTC"
    overall_status: ReportStatus
    components: tuple[ComponentReport, ...]
    redaction: RedactionMetadata
    partial_result_warnings: tuple[str, ...] = ()
    recommended_next_action: str = Field(min_length=1, max_length=500)

    @field_validator("generated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Keep operator timestamps timezone-aware UTC after JSON round-trips."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("generated_at must be timezone-aware UTC")
        return value.astimezone(UTC)


STANDARD_REDACTION = RedactionMetadata(
    secrets_redacted=True,
    raw_environment_omitted=True,
    account_identifiers_omitted=True,
    balances_omitted=True,
)
PORTFOLIO_REDACTION = RedactionMetadata(
    secrets_redacted=True,
    raw_environment_omitted=True,
    account_identifiers_omitted=True,
    balances_omitted=False,
)
