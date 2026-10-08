"""Health, configuration, and exchange operator reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen

from sqlalchemy import text

from thytrader import __version__
from thytrader.config import Settings, default_api_base_url
from thytrader.credentials.service import credentials_are_configured
from thytrader.exchanges.read_errors import ExchangeReadError
from thytrader.operator.health_models import (
    ConfigurationPayload,
    ConfigurationReport,
    ExchangePayload,
    ExchangeReport,
    HealthPayload,
    HealthReport,
    ResearchWorkersPayload,
    current_ops_contract,
)
from thytrader.operator.models import STANDARD_REDACTION, ComponentReport, ReportStatus
from thytrader.operator.research_workers import research_worker_health, stale_after_seconds
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.persistence.database import ping
from thytrader.persistence.portfolio_history import PortfolioHistoryUnavailableError
from thytrader.persistence.postgres_research_queue import ResearchQueueUnavailableError
from thytrader.persistence.worker_heartbeats import WorkerHeartbeatUnavailableError
from thytrader.settings_yaml import default_settings_path

if TYPE_CHECKING:
    from pathlib import Path

    from thytrader.config import Settings
    from thytrader.operator.service import OperatorDiagnostics
    from thytrader.persistence.worker_heartbeats import WorkerName
    from thytrader.runtime import RuntimeState


async def build_health_report(
    diagnostics: OperatorDiagnostics, *, probe_api: bool = False
) -> HealthReport:
    """Summarize process, database, worker, research pool, and exchange health."""
    now = datetime.now(UTC)
    research_component, research_payload = await _research_worker_health(diagnostics)
    components = [
        await _api_component(diagnostics, probe_api=probe_api),
        await _database_component(diagnostics),
        await _history_component(diagnostics),
        await _worker_component(diagnostics, "portfolio_worker"),
        await _worker_component(diagnostics, "market_data_worker"),
        await _worker_component(diagnostics, "execution_worker"),
        research_component,
        await _exchange_component(diagnostics),
    ]
    warnings: list[str] = []
    if diagnostics.settings.database_url is None:
        warnings.append("PostgreSQL is unconfigured; durable reports are partial.")
    overall = aggregate_status(components)
    return HealthReport(
        application_version=__version__,
        generated_at=now,
        overall_status=overall,
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        partial_result_warnings=tuple(warnings),
        recommended_next_action=recommend_next_action(components),
        payload=HealthPayload(
            api_probed=probe_api or diagnostics.runtime is not None,
            database_configured=diagnostics.settings.database_url is not None,
            coinbase_credentials_configured=credentials_are_configured(diagnostics.settings),
            ops_contract=current_ops_contract(),
            applied_schema_revision=await _applied_schema_revision(diagnostics),
            research_workers=research_payload,
        ),
    )


async def _research_worker_health(
    diagnostics: OperatorDiagnostics,
) -> tuple[ComponentReport, ResearchWorkersPayload | None]:
    """Grade the research worker pool from its slot rows and queue depth.

    Without PostgreSQL there is no pool to read. Like the other workers, a process
    with no heartbeat store at all (local tests) falls back to the readiness file.
    """
    if diagnostics.research_queue is None:
        if diagnostics.heartbeat_store is None:
            path = diagnostics.settings.research_worker_readiness_file
            return _readiness_component("research_worker", path), None
        return (
            ComponentReport(
                name="research_worker",
                status=ReportStatus.DEGRADED,
                reason_code="HEARTBEAT_UNAVAILABLE",
                detail="Research worker heartbeats and queue depth require PostgreSQL.",
            ),
            None,
        )
    try:
        snapshot = await diagnostics.research_queue.snapshot()
    except ResearchQueueUnavailableError:
        return (
            ComponentReport(
                name="research_worker",
                status=ReportStatus.DEGRADED,
                reason_code="RESEARCH_QUEUE_UNAVAILABLE",
                detail="The research job queue could not be read.",
            ),
            None,
        )
    return research_worker_health(
        snapshot,
        now=datetime.now(UTC),
        stale_after=stale_after_seconds(float(diagnostics.settings.research_job_lease_seconds)),
    )


async def build_configuration_report(diagnostics: OperatorDiagnostics) -> ConfigurationReport:
    """Return validated settings with secrets omitted."""
    now = datetime.now(UTC)
    components = [_configuration_component(diagnostics.settings)]
    overall = aggregate_status(components)
    return ConfigurationReport(
        application_version=__version__,
        generated_at=now,
        overall_status=overall,
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=ConfigurationPayload(
            environment=diagnostics.settings.environment.value,
            api_host=str(diagnostics.settings.api_host),
            api_port=diagnostics.settings.api_port,
            containerized=diagnostics.settings.containerized,
            allow_remote_access=diagnostics.settings.allow_remote_access,
            log_level=diagnostics.settings.log_level,
            snapshot_interval_seconds=diagnostics.settings.snapshot_interval_seconds,
            market_data_worker_interval_seconds=(
                diagnostics.settings.market_data_worker_interval_seconds
            ),
            market_data_worker_lookback_hours=diagnostics.settings.market_data_worker_lookback_hours,
            market_data_worker_product_id=diagnostics.settings.market_data_worker_product_id,
            market_data_dataset_root=str(diagnostics.settings.market_data_dataset_root),
            execution_worker_interval_seconds=diagnostics.settings.execution_worker_interval_seconds,
            database_configured=diagnostics.settings.database_url is not None,
            coinbase_credentials_configured=credentials_are_configured(diagnostics.settings),
            yolo_enabled=diagnostics.settings.yolo_enabled,
            yolo_tiers=tuple(tier.value for tier in diagnostics.settings.yolo_tiers),
            notify_provider=diagnostics.settings.notify_provider.value,
            notify_webhook_configured=diagnostics.settings.notify_webhook_url is not None,
            settings_file=_yaml_settings_file(diagnostics.runtime),
            yaml_loaded=_yaml_settings_loaded(diagnostics.runtime),
            effective_api_base_url=default_api_base_url(diagnostics.settings),
        ),
    )


async def build_exchange_report(diagnostics: OperatorDiagnostics) -> ExchangeReport:
    """Report Coinbase connection status and detected permissions."""
    now = datetime.now(UTC)
    component, payload = await _exchange_snapshot(diagnostics)
    components = [component]
    return ExchangeReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=tuple(components),
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )


async def _api_component(diagnostics: OperatorDiagnostics, *, probe_api: bool) -> ComponentReport:
    """Describe API readiness from in-process state or an HTTP probe."""
    if diagnostics.runtime is not None:
        if diagnostics.runtime.ready:
            return ComponentReport(name="api", status=ReportStatus.HEALTHY, reason_code="READY")
        return ComponentReport(
            name="api",
            status=ReportStatus.FAILED,
            reason_code="API_NOT_READY",
            detail="The API process has not finished startup.",
        )
    if not probe_api:
        return ComponentReport(
            name="api",
            status=ReportStatus.DEGRADED,
            reason_code="API_NOT_PROBED",
            detail="In-process API state is unavailable; missing telemetry is not healthy.",
        )
    return _probe_api(diagnostics.settings)


async def _database_component(diagnostics: OperatorDiagnostics) -> ComponentReport:
    """Ping PostgreSQL when configured; otherwise report missing telemetry."""
    if diagnostics.settings.database_url is None:
        return ComponentReport(
            name="database",
            status=ReportStatus.DEGRADED,
            reason_code="DATABASE_UNCONFIGURED",
            detail="THYTRADER_DATABASE_URL is unset.",
        )
    if diagnostics.engine is None:
        return ComponentReport(
            name="database",
            status=ReportStatus.DEGRADED,
            reason_code="DATABASE_ENGINE_MISSING",
            detail="PostgreSQL is configured but this process has no database engine.",
        )
    try:
        await ping(diagnostics.engine)
    except Exception:  # noqa: BLE001 - connectivity failures are redacted at this boundary.
        return ComponentReport(
            name="database",
            status=ReportStatus.FAILED,
            reason_code="DATABASE_UNREACHABLE",
            detail="PostgreSQL did not answer a connectivity check.",
        )
    return ComponentReport(name="database", status=ReportStatus.HEALTHY, reason_code="READY")


async def _applied_schema_revision(diagnostics: OperatorDiagnostics) -> str | None:
    """Read Alembic's version_num without exposing connection strings."""
    if diagnostics.engine is None:
        return None
    try:
        async with diagnostics.engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
    except Exception:  # noqa: BLE001 - missing revision is reported as unknown, not a secret.
        return None
    if row is None:
        return None
    value = row[0]
    return value if isinstance(value, str) else None


async def _worker_component(diagnostics: OperatorDiagnostics, name: WorkerName) -> ComponentReport:
    """Prefer PostgreSQL heartbeats; fall back to readiness files only in local tests."""
    if diagnostics.heartbeat_store is None:
        return _readiness_component(name, _readiness_path(diagnostics, name))
    try:
        last = await diagnostics.heartbeat_store.last_heartbeat(name)
    except WorkerHeartbeatUnavailableError:
        return ComponentReport(
            name=name,
            status=ReportStatus.DEGRADED,
            reason_code="HEARTBEAT_UNAVAILABLE",
            detail="Worker heartbeats require PostgreSQL.",
        )
    if last is None:
        return ComponentReport(
            name=name,
            status=ReportStatus.DEGRADED,
            reason_code="HEARTBEAT_MISSING",
            detail="The worker has not recorded a heartbeat.",
        )
    age = (datetime.now(UTC) - last.astimezone(UTC)).total_seconds()
    if age > _heartbeat_stale_after(diagnostics, name):
        return ComponentReport(
            name=name,
            status=ReportStatus.DEGRADED,
            reason_code="HEARTBEAT_STALE",
            detail=f"The last heartbeat was {int(age)}s ago.",
        )
    return ComponentReport(name=name, status=ReportStatus.HEALTHY, reason_code="READY")


def _readiness_path(diagnostics: OperatorDiagnostics, name: WorkerName) -> Path | None:
    """Return the Docker healthcheck file for one named worker."""
    if name == "portfolio_worker":
        return diagnostics.settings.worker_readiness_file
    if name == "market_data_worker":
        return diagnostics.settings.market_data_worker_readiness_file
    return diagnostics.settings.execution_worker_readiness_file


def _heartbeat_stale_after(diagnostics: OperatorDiagnostics, name: WorkerName) -> int:
    """Allow two missed loops plus slack before a heartbeat is stale."""
    slack = 30
    if name == "portfolio_worker":
        return 2 * diagnostics.settings.snapshot_interval_seconds + slack
    if name == "market_data_worker":
        return 2 * diagnostics.settings.market_data_worker_interval_seconds + slack
    return 2 * diagnostics.settings.execution_worker_interval_seconds + slack


async def _history_component(diagnostics: OperatorDiagnostics) -> ComponentReport:
    """Treat missing portfolio history as incomplete telemetry, not health."""
    try:
        entries = await diagnostics.history.list_range(start=None, max_entries=1)
    except PortfolioHistoryUnavailableError:
        return ComponentReport(
            name="portfolio_history",
            status=ReportStatus.DEGRADED,
            reason_code="HISTORY_UNAVAILABLE",
            detail="Portfolio history storage is unavailable.",
        )
    if not entries:
        return ComponentReport(
            name="portfolio_history",
            status=ReportStatus.DEGRADED,
            reason_code="HISTORY_EMPTY",
            detail="No portfolio snapshots have been recorded.",
        )
    return ComponentReport(
        name="portfolio_history",
        status=ReportStatus.HEALTHY,
        reason_code="READY",
        detail="At least one portfolio snapshot is stored.",
    )


async def _exchange_component(diagnostics: OperatorDiagnostics) -> ComponentReport:
    """Classify exchange connectivity without returning balances."""
    component, _payload = await _exchange_snapshot(diagnostics)
    return component


async def _exchange_snapshot(
    diagnostics: OperatorDiagnostics,
) -> tuple[ComponentReport, ExchangePayload]:
    """Fetch permissions and connection status from the portfolio service."""
    configured = credentials_are_configured(diagnostics.settings)
    try:
        portfolio = await diagnostics.portfolio.get_portfolio()
    except Exception as error:  # noqa: BLE001 - provider failures are redacted at this boundary.
        failure = error.failure if isinstance(error, ExchangeReadError) else None
        payload = ExchangePayload(
            provider="coinbase",
            connection_status="unavailable",
            demo=not configured,
            permissions=(),
            live_credentials_configured=configured,
            failure=failure,
        )
        return (
            ComponentReport(
                name="exchange",
                status=ReportStatus.FAILED,
                reason_code="EXCHANGE_UNAVAILABLE",
                detail=(
                    "The exchange account could not be queried."
                    if failure is None
                    else failure.summary()
                ),
            ),
            payload,
        )
    payload = ExchangePayload(
        provider=portfolio.connection.provider,
        connection_status=portfolio.connection.status,
        demo=portfolio.demo,
        permissions=portfolio.connection.permissions,
        live_credentials_configured=configured,
    )
    if portfolio.demo:
        return (
            ComponentReport(
                name="exchange",
                status=ReportStatus.HEALTHY,
                reason_code="DEMO_MODE",
                detail="Coinbase credentials are absent; demo data is in use.",
            ),
            payload,
        )
    return (
        ComponentReport(
            name="exchange",
            status=ReportStatus.HEALTHY,
            reason_code="CONNECTED",
            detail="Coinbase credentials are configured.",
        ),
        payload,
    )


def _yaml_settings_file(runtime: RuntimeState | None) -> str:
    """Return the YAML settings path advertised on configuration reports."""
    if runtime is not None and runtime.settings_store is not None:
        return str(runtime.settings_store.path)
    return str(default_settings_path())


def _yaml_settings_loaded(runtime: RuntimeState | None) -> bool:
    """True when this process attached a YAML file that currently exists."""
    if runtime is None or runtime.settings_store is None:
        return False
    return runtime.settings_store.yaml_loaded


def _readiness_component(name: str, path: Path | None) -> ComponentReport:
    """Interpret a worker readiness file without treating absence as health."""
    if path is None:
        return ComponentReport(
            name=name,
            status=ReportStatus.DEGRADED,
            reason_code="READINESS_FILE_UNCONFIGURED",
            detail="Worker readiness path is unset; missing telemetry is not healthy.",
        )
    if path.exists():
        return ComponentReport(name=name, status=ReportStatus.HEALTHY, reason_code="READY")
    return ComponentReport(
        name=name,
        status=ReportStatus.DEGRADED,
        reason_code="NOT_READY",
        detail="The worker readiness file is absent.",
    )


def _probe_api(settings: Settings) -> ComponentReport:
    """GET /health/ready on the configured loopback listener."""
    url = f"http://{settings.api_host}:{settings.api_port}/health/ready"
    try:
        with urlopen(url, timeout=1.0) as response:
            if 200 <= response.status < 300:
                return ComponentReport(name="api", status=ReportStatus.HEALTHY, reason_code="READY")
    except URLError:
        return ComponentReport(
            name="api",
            status=ReportStatus.DEGRADED,
            reason_code="API_UNREACHABLE",
            detail="The configured API listener did not answer /health/ready.",
        )
    except OSError:
        return ComponentReport(
            name="api",
            status=ReportStatus.DEGRADED,
            reason_code="API_UNREACHABLE",
            detail="The configured API listener did not answer /health/ready.",
        )
    return ComponentReport(
        name="api",
        status=ReportStatus.DEGRADED,
        reason_code="API_UNREACHABLE",
        detail="The API listener answered an unsuccessful readiness status.",
    )


def _configuration_component(settings: Settings) -> ComponentReport:
    """Flag remote-access and production-without-database as configuration risk."""
    if settings.allow_remote_access:
        return ComponentReport(
            name="configuration",
            status=ReportStatus.DEGRADED,
            reason_code="REMOTE_ACCESS_UNIMPLEMENTED",
            detail="allow_remote_access is set but protected remote access is not implemented.",
        )
    if settings.environment.value == "production" and settings.database_url is None:
        return ComponentReport(
            name="configuration",
            status=ReportStatus.FAILED,
            reason_code="DATABASE_UNCONFIGURED",
            detail="Production requires THYTRADER_DATABASE_URL.",
        )
    return ComponentReport(
        name="configuration",
        status=ReportStatus.HEALTHY,
        reason_code="VALID",
        detail="Settings loaded without exposing secrets.",
    )
