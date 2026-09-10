"""Versioned operator diagnostic report contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION: Literal["thytrader-operator-report-v1"] = "thytrader-operator-report-v1"
OPERATOR_API_PREFIX = "/api/v1/operator"
REPORT_KINDS: tuple[str, ...] = (
    "health",
    "configuration",
    "exchange",
    "market_data",
    "data_catalog",
    "products",
    "indicators",
    "strategies",
    "performance",
    "risk",
    "reconciliation",
    "runtime",
    "support_bundle",
)

SupportedTimeframe = Literal["1h", "5m"]


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


class HealthPayload(_FrozenModel):
    """Process coverage included in the health report."""

    api_probed: bool
    database_configured: bool
    coinbase_credentials_configured: bool


class HealthReport(OperatorEnvelope):
    """API, workers, database, and exchange connectivity summary."""

    report_kind: Literal["health"] = "health"
    payload: HealthPayload


class ConfigurationPayload(_FrozenModel):
    """Redacted runtime settings safe for agents."""

    environment: str
    api_host: str
    api_port: int
    containerized: bool
    allow_remote_access: bool
    log_level: str
    snapshot_interval_seconds: int
    market_data_worker_interval_seconds: int
    market_data_worker_lookback_hours: int
    market_data_worker_product_id: str
    market_data_dataset_root: str
    execution_worker_interval_seconds: int
    database_configured: bool
    coinbase_credentials_configured: bool


class ConfigurationReport(OperatorEnvelope):
    """Validated configuration without secrets or raw environment values."""

    report_kind: Literal["configuration"] = "configuration"
    payload: ConfigurationPayload


class ExchangePayload(_FrozenModel):
    """Detected exchange connection facts without balances or account ids."""

    provider: str
    connection_status: str
    demo: bool
    permissions: tuple[str, ...]
    live_credentials_configured: bool


class ExchangeReport(OperatorEnvelope):
    """Coinbase connectivity and detected key permissions."""

    report_kind: Literal["exchange"] = "exchange"
    payload: ExchangePayload


class MarketDataPayload(_FrozenModel):
    """Durable 1h ingestion coverage for one product."""

    product_id: str
    provider: str | None
    timeframe: SupportedTimeframe = "1h"
    worker_status: str | None
    complete: bool | None
    freshness_status: str
    newest_candle_at: datetime | None
    age_seconds: int | None
    gap_count: int | None
    missing_intervals: int | None
    expected_candle_count: int | None
    received_candle_count: int | None
    content_fingerprint: str | None
    covered_starts_at: datetime | None
    covered_ends_at: datetime | None


class MarketDataReport(OperatorEnvelope):
    """Market-data freshness, gaps, and verified coverage."""

    report_kind: Literal["market_data"] = "market_data"
    payload: MarketDataPayload


class DraftSummary(_FrozenModel):
    """One editable draft identity safe for operator listing."""

    strategy_id: UUID
    name: str
    version: int
    revision: int
    product_id: str
    timeframe: SupportedTimeframe


class PublicationSummary(_FrozenModel):
    """One immutable published version without the full strategy body."""

    strategy_id: UUID
    name: str
    version: int
    strategy_fingerprint: str
    product_id: str
    timeframe: SupportedTimeframe
    archived: bool


class DeploymentSummary(_FrozenModel):
    """One paper or live runtime without cash, quantities, or order payloads."""

    deployment_id: UUID
    strategy_id: UUID
    strategy_fingerprint: str
    mode: str
    status: str
    phase: str
    product_id: str
    last_evaluated_bar: datetime | None
    mismatch_present: bool
    last_signal: str | None


class StrategiesPayload(_FrozenModel):
    """Published, draft, and runtime identities visible to operators."""

    drafts: tuple[DraftSummary, ...]
    publications: tuple[PublicationSummary, ...]
    deployments: tuple[DeploymentSummary, ...]


class StrategiesReport(OperatorEnvelope):
    """Strategy catalog and runtime status without trading authority."""

    report_kind: Literal["strategies"] = "strategies"
    payload: StrategiesPayload


class PerformancePayload(_FrozenModel):
    """One backtest, paper, or live performance slice with explicit provenance."""

    mode: Literal["backtest", "paper", "live"]
    timeframe: SupportedTimeframe
    currency: Literal["USD"] = "USD"
    strategy_fingerprint: str | None
    dataset_fingerprint: str | None
    engine_contract_version: str | None
    fee_treatment: str
    result_fingerprint: str | None
    deployment_id: UUID | None
    trade_count: int | None
    total_net_pnl: str | None
    total_return_fraction: str | None
    maximum_drawdown_fraction: str | None
    total_spread_cost: str | None
    evaluation_bars: int | None


class PerformanceReport(OperatorEnvelope):
    """Performance evidence that distinguishes backtest, paper, and live."""

    report_kind: Literal["performance"] = "performance"
    payload: PerformancePayload


class RiskFinding(_FrozenModel):
    """One operational risk observation with a stable code."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    deployment_id: UUID | None
    detail: str = Field(max_length=500)


class RiskPayload(_FrozenModel):
    """Observed runtime risk plus an honest registry gap."""

    risk_policy_registry: Literal["unavailable"]
    findings: tuple[RiskFinding, ...]


class RiskReport(OperatorEnvelope):
    """Pause, mismatch, and other fail-closed runtime observations."""

    report_kind: Literal["risk"] = "risk"
    payload: RiskPayload


class ReconciliationFinding(_FrozenModel):
    """One order or runtime reconciliation anomaly."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    deployment_id: UUID | None
    detail: str = Field(max_length=500)


class ReconciliationPayload(_FrozenModel):
    """Local runtime mismatches that require operator attention."""

    findings: tuple[ReconciliationFinding, ...]


class ReconciliationReport(OperatorEnvelope):
    """Rejected, unknown, or unreconciled runtime conditions."""

    report_kind: Literal["reconciliation"] = "reconciliation"
    payload: ReconciliationPayload


class RuntimePayload(_FrozenModel):
    """Paper/live status plus risk and reconciliation findings, without cash."""

    deployments: tuple[DeploymentSummary, ...]
    risk_findings: tuple[RiskFinding, ...]
    reconciliation_findings: tuple[ReconciliationFinding, ...]


class RuntimeReport(OperatorEnvelope):
    """Read-only combined view of paper and live runtimes."""

    report_kind: Literal["runtime"] = "runtime"
    payload: RuntimePayload


class SupportBundlePayload(_FrozenModel):
    """Deterministic bundle of the other operator reports."""

    health: HealthReport
    configuration: ConfigurationReport
    exchange: ExchangeReport
    market_data: MarketDataReport
    strategies: StrategiesReport
    risk: RiskReport
    reconciliation: ReconciliationReport


class SupportBundleReport(OperatorEnvelope):
    """Redacted support bundle assembled from the supported report set."""

    report_kind: Literal["support_bundle"] = "support_bundle"
    payload: SupportBundlePayload


class ProductSummary(_FrozenModel):
    """One enabled USD spot product from the current catalog."""

    product_id: str
    base_currency: str
    quote_currency: str
    trading_enabled: bool


class ProductsPayload(_FrozenModel):
    """Coinbase or demo USD spot products visible to agents."""

    provider: str
    products: tuple[ProductSummary, ...]


class ProductsReport(OperatorEnvelope):
    """Read-only USD spot catalog without secrets."""

    report_kind: Literal["products"] = "products"
    payload: ProductsPayload


class DatasetCoverageRow(_FrozenModel):
    """Local verified coverage plus watchlist and worker facts for one target."""

    provider: str | None
    product_id: str
    timeframe: SupportedTimeframe
    watched: bool
    lookback_hours: int | None
    worker_status: str | None
    complete: bool | None
    freshness_status: str
    covered_starts_at: datetime | None
    covered_ends_at: datetime | None
    expected_candle_count: int | None
    received_candle_count: int | None
    gap_count: int | None
    missing_intervals: int | None
    content_fingerprint: str | None
    sparsity: Literal["none", "unknown", "gapped"]


class DataCatalogPayload(_FrozenModel):
    """Agent-visible dataset catalog for 1h and 5m coverage."""

    datasets: tuple[DatasetCoverageRow, ...]
    supported_timeframes: tuple[SupportedTimeframe, ...] = ("1h", "5m")


class DataCatalogReport(OperatorEnvelope):
    """Local Parquet coverage joined with watchlist and worker state."""

    report_kind: Literal["data_catalog"] = "data_catalog"
    payload: DataCatalogPayload


class IndicatorCatalogEntry(_FrozenModel):
    """One implemented indicator kind and its canonical input/period bounds."""

    kind: str
    inputs: tuple[str, ...]
    period_min: int
    period_max: int


class IndicatorsPayload(_FrozenModel):
    """Implemented indicator registry. MACD and others are not invented here."""

    indicators: tuple[IndicatorCatalogEntry, ...]


class IndicatorsReport(OperatorEnvelope):
    """Read-only list of strategy indicators the engine actually implements."""

    report_kind: Literal["indicators"] = "indicators"
    payload: IndicatorsPayload


STANDARD_REDACTION = RedactionMetadata(
    secrets_redacted=True,
    raw_environment_omitted=True,
    account_identifiers_omitted=True,
    balances_omitted=True,
)
