"""Build versioned operator reports from existing application services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from urllib.error import URLError
from urllib.request import urlopen

from thytrader import __version__
from thytrader.execution.models import Deployment, DeploymentStatus, OrderStatus
from thytrader.market_data.freshness import FreshnessStatus, evaluate_freshness
from thytrader.market_data.models import CandleInterval, parse_candle_interval
from thytrader.market_data.watchlist import (
    INGEST_REQUEST_POLL_SECONDS,
    MarketDataWatchlistStore,
    MarketDataWatchlistUnavailableError,
    MarketDataWatchTarget,
)
from thytrader.market_data.worker_state import (
    MarketDataWorkerState,
    MarketDataWorkerStateStore,
    MarketDataWorkerUnavailableError,
)
from thytrader.operator.models import (
    STANDARD_REDACTION,
    ComponentReport,
    ConfigurationPayload,
    ConfigurationReport,
    DataCatalogPayload,
    DataCatalogReport,
    DatasetCoverageRow,
    DeploymentSummary,
    DraftSummary,
    ExchangePayload,
    ExchangeReport,
    HealthPayload,
    HealthReport,
    IndicatorCatalogEntry,
    IndicatorsPayload,
    IndicatorsReport,
    MarketDataPayload,
    MarketDataReport,
    PerformancePayload,
    PerformanceReport,
    ProductsPayload,
    ProductsReport,
    ProductSummary,
    PublicationSummary,
    ReconciliationFinding,
    ReconciliationPayload,
    ReconciliationReport,
    ReportStatus,
    RiskFinding,
    RiskPayload,
    RiskReport,
    RuntimePayload,
    RuntimeReport,
    StrategiesPayload,
    StrategiesReport,
    SupportBundlePayload,
    SupportBundleReport,
    SupportedTimeframe,
)
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.persistence.audit_events import AuditEventStore, AuditEventUnavailableError
from thytrader.persistence.backtest_results import (
    BacktestResultNotFoundError,
    BacktestResultReader,
    BacktestResultUnavailableError,
)
from thytrader.persistence.database import ping
from thytrader.persistence.portfolio_history import (
    PortfolioHistoryStore,
    PortfolioHistoryUnavailableError,
)
from thytrader.persistence.worker_heartbeats import WorkerHeartbeatUnavailableError
from thytrader.strategies.models import IndicatorKind
from thytrader.strategies.publication import StrategyPublicationCatalog, StrategyPublicationError

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.config import Settings
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.market_data.service import MarketDataService
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore, WorkerName
    from thytrader.portfolio.service import PortfolioService
    from thytrader.runtime import RuntimeState
    from thytrader.strategies.authoring import StrategyDraftStore


@dataclass(frozen=True, slots=True)
class OperatorDiagnostics:
    """Read-only diagnostics assembled from the same services the HTTP API uses."""

    settings: Settings
    portfolio: PortfolioService
    market_data_state: MarketDataWorkerStateStore
    history: PortfolioHistoryStore
    publications: StrategyPublicationCatalog
    drafts: StrategyDraftStore
    backtests: BacktestResultReader
    execution: ExecutionStore
    audit: AuditEventStore
    runtime: RuntimeState | None = None
    engine: AsyncEngine | None = None
    dataset_store: DatasetStore | None = None
    watchlist: MarketDataWatchlistStore | None = None
    market_data: MarketDataService | None = None
    heartbeat_store: WorkerHeartbeatStore | None = None

    async def health(self, *, probe_api: bool = False) -> HealthReport:
        """Summarize process, database, worker, and exchange health."""
        now = datetime.now(UTC)
        components = [
            await self._api_component(probe_api=probe_api),
            await self._database_component(),
            await self._history_component(),
            await self._worker_component("portfolio_worker"),
            await self._worker_component("market_data_worker"),
            await self._worker_component("execution_worker"),
            await self._exchange_component(),
        ]
        warnings: list[str] = []
        if self.settings.database_url is None:
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
                api_probed=probe_api or self.runtime is not None,
                database_configured=self.settings.database_url is not None,
                coinbase_credentials_configured=_credentials_configured(self.settings),
            ),
        )

    async def configuration(self) -> ConfigurationReport:
        """Return validated settings with secrets omitted."""
        now = datetime.now(UTC)
        components = [_configuration_component(self.settings)]
        overall = aggregate_status(components)
        return ConfigurationReport(
            application_version=__version__,
            generated_at=now,
            overall_status=overall,
            components=tuple(components),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action(components),
            payload=ConfigurationPayload(
                environment=self.settings.environment.value,
                api_host=str(self.settings.api_host),
                api_port=self.settings.api_port,
                containerized=self.settings.containerized,
                allow_remote_access=self.settings.allow_remote_access,
                log_level=self.settings.log_level,
                snapshot_interval_seconds=self.settings.snapshot_interval_seconds,
                market_data_worker_interval_seconds=(
                    self.settings.market_data_worker_interval_seconds
                ),
                market_data_worker_lookback_hours=self.settings.market_data_worker_lookback_hours,
                market_data_worker_product_id=self.settings.market_data_worker_product_id,
                market_data_dataset_root=str(self.settings.market_data_dataset_root),
                execution_worker_interval_seconds=self.settings.execution_worker_interval_seconds,
                database_configured=self.settings.database_url is not None,
                coinbase_credentials_configured=_credentials_configured(self.settings),
            ),
        )

    async def exchange(self) -> ExchangeReport:
        """Report Coinbase connection status and detected permissions."""
        now = datetime.now(UTC)
        component, payload = await self._exchange_snapshot()
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

    async def market_data_report(
        self,
        product_id: str | None = None,
        timeframe: str | None = None,
    ) -> MarketDataReport:
        """Report coverage and freshness for one USD spot product and timeframe."""
        now = datetime.now(UTC)
        target = product_id or self.settings.market_data_worker_product_id
        interval = _parse_timeframe(timeframe)
        component, payload, warnings = await self._market_data_snapshot(target, now, interval)
        components = [component]
        return MarketDataReport(
            application_version=__version__,
            generated_at=now,
            overall_status=aggregate_status(components),
            components=tuple(components),
            redaction=STANDARD_REDACTION,
            partial_result_warnings=tuple(warnings),
            recommended_next_action=recommend_next_action(components),
            payload=payload,
        )

    async def products(self) -> ProductsReport:
        """List enabled USD spot products from the current catalog."""
        now = datetime.now(UTC)
        if self.market_data is None:
            component = ComponentReport(
                name="products",
                status=ReportStatus.DEGRADED,
                reason_code="CATALOG_UNAVAILABLE",
                detail="Market-data service is not attached to diagnostics.",
            )
            return ProductsReport(
                application_version=__version__,
                generated_at=now,
                overall_status=ReportStatus.DEGRADED,
                components=(component,),
                redaction=STANDARD_REDACTION,
                recommended_next_action=recommend_next_action((component,)),
                payload=ProductsPayload(provider="unknown", products=()),
            )
        try:
            listed = await self.market_data.list_enabled_usd_spot_products()
        except Exception:  # noqa: BLE001 - catalog failures stay redacted.
            component = ComponentReport(
                name="products",
                status=ReportStatus.FAILED,
                reason_code="CATALOG_UNAVAILABLE",
                detail="The USD spot product catalog could not be loaded.",
            )
            return ProductsReport(
                application_version=__version__,
                generated_at=now,
                overall_status=ReportStatus.FAILED,
                components=(component,),
                redaction=STANDARD_REDACTION,
                recommended_next_action=recommend_next_action((component,)),
                payload=ProductsPayload(provider=_catalog_provider(self.settings), products=()),
            )
        component = ComponentReport(
            name="products",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=f"{len(listed)} enabled USD spot product(s).",
        )
        return ProductsReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.HEALTHY,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=ProductsPayload(
                provider=_catalog_provider(self.settings),
                products=tuple(
                    ProductSummary(
                        product_id=item.product_id,
                        base_currency=item.base_currency,
                        quote_currency=item.quote_currency,
                        trading_enabled=item.trading_enabled,
                    )
                    for item in listed
                ),
            ),
        )

    async def data_catalog(self) -> DataCatalogReport:
        """Join watchlist, worker state, and verified Parquet datasets."""
        now = datetime.now(UTC)
        warnings: list[str] = []
        components: list[ComponentReport] = []
        rows = await self._coverage_rows(now, components, warnings)
        if not components:
            components.append(
                ComponentReport(
                    name="data_catalog",
                    status=ReportStatus.HEALTHY,
                    reason_code="OK",
                    detail=f"{len(rows)} coverage row(s).",
                )
            )
        return DataCatalogReport(
            application_version=__version__,
            generated_at=now,
            overall_status=aggregate_status(components),
            components=tuple(components),
            redaction=STANDARD_REDACTION,
            partial_result_warnings=tuple(warnings),
            recommended_next_action=recommend_next_action(components),
            payload=DataCatalogPayload(datasets=rows),
        )

    async def indicators(self) -> IndicatorsReport:
        """List implemented indicator kinds; do not invent unsupported studies."""
        now = datetime.now(UTC)
        entries = _indicator_entries()
        component = ComponentReport(
            name="indicators",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=f"{len(entries)} implemented indicator kind(s).",
        )
        return IndicatorsReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.HEALTHY,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=IndicatorsPayload(indicators=entries),
        )

    async def strategies(self) -> StrategiesReport:
        """List drafts, publications, and deployments without cash or fills."""
        now = datetime.now(UTC)
        components: list[ComponentReport] = []
        warnings: list[str] = []
        drafts = await self._draft_summaries(components, warnings)
        publications = await self._publication_summaries(components, warnings)
        deployments = await self._deployment_summaries(components, warnings)
        if not components:
            components.append(
                ComponentReport(
                    name="strategies",
                    status=ReportStatus.HEALTHY,
                    reason_code="OK",
                    detail="Drafts, publications, and deployments were listed.",
                )
            )
        return StrategiesReport(
            application_version=__version__,
            generated_at=now,
            overall_status=aggregate_status(components),
            components=tuple(components),
            redaction=STANDARD_REDACTION,
            partial_result_warnings=tuple(warnings),
            recommended_next_action=recommend_next_action(components),
            payload=StrategiesPayload(
                drafts=drafts,
                publications=publications,
                deployments=deployments,
            ),
        )

    async def performance(
        self,
        *,
        result_fingerprint: str | None = None,
        deployment_id: UUID | None = None,
    ) -> PerformanceReport:
        """Return one backtest result or one paper/live runtime performance slice."""
        now = datetime.now(UTC)
        if result_fingerprint:
            return await self._backtest_performance(result_fingerprint, now)
        if deployment_id is not None:
            return await self._deployment_performance(deployment_id, now)
        component = ComponentReport(
            name="performance",
            status=ReportStatus.FAILED,
            reason_code="RESULT_NOT_FOUND",
            detail="Pass a result fingerprint or deployment id.",
        )
        return _empty_performance(now, (component,))

    async def risk(self) -> RiskReport:
        """Surface pause and mismatch findings; the risk-policy registry is not implemented."""
        now = datetime.now(UTC)
        warnings = ["The composable risk-policy registry is not implemented."]
        findings, components = await self._risk_findings()
        components = [
            ComponentReport(
                name="risk_policy_registry",
                status=ReportStatus.DEGRADED,
                reason_code="RISK_REGISTRY_UNAVAILABLE",
                detail="Typed risk policies are not yet a supported operator contract.",
            ),
            *components,
        ]
        return RiskReport(
            application_version=__version__,
            generated_at=now,
            overall_status=aggregate_status(components),
            components=tuple(components),
            redaction=STANDARD_REDACTION,
            partial_result_warnings=tuple(warnings),
            recommended_next_action=recommend_next_action(components),
            payload=RiskPayload(risk_policy_registry="unavailable", findings=findings),
        )

    async def reconciliation(self) -> ReconciliationReport:
        """List mismatch, unknown-order, and recent audit failure conditions."""
        now = datetime.now(UTC)
        findings, components, warnings = await self._reconciliation_findings()
        if not components:
            components.append(
                ComponentReport(
                    name="reconciliation",
                    status=ReportStatus.HEALTHY,
                    reason_code="OK",
                    detail="No mismatch or unknown-order findings were reported.",
                )
            )
        return ReconciliationReport(
            application_version=__version__,
            generated_at=now,
            overall_status=aggregate_status(components),
            components=tuple(components),
            redaction=STANDARD_REDACTION,
            partial_result_warnings=tuple(warnings),
            recommended_next_action=recommend_next_action(components),
            payload=ReconciliationPayload(findings=findings),
        )

    async def runtime_report(self, deployment_id: UUID | None = None) -> RuntimeReport:
        """Combine deployment status with risk and reconciliation findings."""
        now = datetime.now(UTC)
        strategies = await self.strategies()
        risk = await self.risk()
        reconciliation = await self.reconciliation()
        deployments, risk_findings, recon_findings, extra = _runtime_slice(
            strategies,
            risk,
            reconciliation,
            deployment_id,
        )
        components = (
            *strategies.components,
            *risk.components,
            *reconciliation.components,
            *extra,
        )
        return RuntimeReport(
            application_version=__version__,
            generated_at=now,
            overall_status=aggregate_status(components),
            components=components,
            redaction=STANDARD_REDACTION,
            partial_result_warnings=(
                *strategies.partial_result_warnings,
                *risk.partial_result_warnings,
                *reconciliation.partial_result_warnings,
            ),
            recommended_next_action=recommend_next_action(components),
            payload=RuntimePayload(
                deployments=deployments,
                risk_findings=risk_findings,
                reconciliation_findings=recon_findings,
            ),
        )

    async def support_bundle(self) -> SupportBundleReport:
        """Assemble the supported reports into one redacted bundle."""
        now = datetime.now(UTC)
        health = await self.health()
        configuration = await self.configuration()
        exchange = await self.exchange()
        market_data = await self.market_data_report()
        strategies = await self.strategies()
        risk = await self.risk()
        reconciliation = await self.reconciliation()
        nested = (
            health.overall_status,
            configuration.overall_status,
            exchange.overall_status,
            market_data.overall_status,
            strategies.overall_status,
            risk.overall_status,
            reconciliation.overall_status,
        )
        overall = _worst_status(nested)
        component = ComponentReport(
            name="support_bundle",
            status=overall,
            reason_code="ASSEMBLED" if overall is not ReportStatus.FAILED else "FAILED",
            detail="Bundle contains the supported operator reports.",
        )
        return SupportBundleReport(
            application_version=__version__,
            generated_at=now,
            overall_status=overall,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action(
                (
                    *health.components,
                    *configuration.components,
                    *exchange.components,
                    *market_data.components,
                    *strategies.components,
                    *risk.components,
                    *reconciliation.components,
                )
            ),
            payload=SupportBundlePayload(
                health=health,
                configuration=configuration,
                exchange=exchange,
                market_data=market_data,
                strategies=strategies,
                risk=risk,
                reconciliation=reconciliation,
            ),
        )

    async def _api_component(self, *, probe_api: bool) -> ComponentReport:
        """Describe API readiness from in-process state or an HTTP probe."""
        if self.runtime is not None:
            if self.runtime.ready:
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
        return _probe_api(self.settings)

    async def _database_component(self) -> ComponentReport:
        """Ping PostgreSQL when configured; otherwise report missing telemetry."""
        if self.settings.database_url is None:
            return ComponentReport(
                name="database",
                status=ReportStatus.DEGRADED,
                reason_code="DATABASE_UNCONFIGURED",
                detail="THYTRADER_DATABASE_URL is unset.",
            )
        if self.engine is None:
            return ComponentReport(
                name="database",
                status=ReportStatus.DEGRADED,
                reason_code="DATABASE_ENGINE_MISSING",
                detail="PostgreSQL is configured but this process has no database engine.",
            )
        try:
            await ping(self.engine)
        except Exception:  # noqa: BLE001 - connectivity failures are redacted at this boundary.
            return ComponentReport(
                name="database",
                status=ReportStatus.FAILED,
                reason_code="DATABASE_UNREACHABLE",
                detail="PostgreSQL did not answer a connectivity check.",
            )
        return ComponentReport(name="database", status=ReportStatus.HEALTHY, reason_code="READY")

    async def _worker_component(self, name: WorkerName) -> ComponentReport:
        """Prefer PostgreSQL heartbeats; fall back to readiness files only in local tests."""
        if self.heartbeat_store is None:
            return _readiness_component(name, self._readiness_path(name))
        try:
            last = await self.heartbeat_store.last_heartbeat(name)
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
        if age > self._heartbeat_stale_after(name):
            return ComponentReport(
                name=name,
                status=ReportStatus.DEGRADED,
                reason_code="HEARTBEAT_STALE",
                detail=f"The last heartbeat was {int(age)}s ago.",
            )
        return ComponentReport(name=name, status=ReportStatus.HEALTHY, reason_code="READY")

    def _readiness_path(self, name: WorkerName) -> Path | None:
        """Return the Docker healthcheck file for one named worker."""
        if name == "portfolio_worker":
            return self.settings.worker_readiness_file
        if name == "market_data_worker":
            return self.settings.market_data_worker_readiness_file
        return self.settings.execution_worker_readiness_file

    def _heartbeat_stale_after(self, name: WorkerName) -> int:
        """Allow two missed loops plus slack before a heartbeat is stale."""
        slack = 30
        if name == "portfolio_worker":
            return 2 * self.settings.snapshot_interval_seconds + slack
        if name == "market_data_worker":
            return (
                2
                * min(
                    self.settings.market_data_worker_interval_seconds,
                    INGEST_REQUEST_POLL_SECONDS,
                )
                + slack
            )
        return 2 * self.settings.execution_worker_interval_seconds + slack

    async def _strategy_timeframe(self, fingerprint: str) -> SupportedTimeframe:
        """Copy 1h/5m from the published strategy; default 1h when it cannot be loaded."""
        load = getattr(self.publications, "load", None)
        if not callable(load):
            return "1h"
        try:
            published = await load(fingerprint)
        except Exception:  # noqa: BLE001 - missing strategy evidence stays a 1h placeholder.
            return "1h"
        timeframe = published.definition.timeframe
        if timeframe in {"1h", "5m"}:
            return timeframe
        return "1h"

    async def _history_component(self) -> ComponentReport:
        """Treat missing portfolio history as incomplete telemetry, not health."""
        try:
            entries = await self.history.list_range(start=None, max_entries=1)
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

    async def _exchange_component(self) -> ComponentReport:
        """Classify exchange connectivity without returning balances."""
        component, _payload = await self._exchange_snapshot()
        return component

    async def _exchange_snapshot(self) -> tuple[ComponentReport, ExchangePayload]:
        """Fetch permissions and connection status from the portfolio service."""
        configured = _credentials_configured(self.settings)
        try:
            portfolio = await self.portfolio.get_portfolio()
        except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
            payload = ExchangePayload(
                provider="coinbase",
                connection_status="unavailable",
                demo=not configured,
                permissions=(),
                live_credentials_configured=configured,
            )
            return (
                ComponentReport(
                    name="exchange",
                    status=ReportStatus.FAILED,
                    reason_code="EXCHANGE_UNAVAILABLE",
                    detail="The exchange account could not be queried.",
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

    async def _market_data_snapshot(
        self,
        product_id: str,
        now: datetime,
        interval: CandleInterval,
    ) -> tuple[ComponentReport, MarketDataPayload, list[str]]:
        """Load durable worker state for demo or live provenance."""
        warnings: list[str] = []
        try:
            state, provider = await self._load_worker_state(product_id, interval)
        except MarketDataWorkerUnavailableError:
            payload = _empty_market_data(product_id, FreshnessStatus.UNKNOWN, interval)
            return (
                ComponentReport(
                    name="market_data",
                    status=ReportStatus.FAILED,
                    reason_code="MARKET_DATA_STATE_UNAVAILABLE",
                    detail="Durable market-data worker state could not be read.",
                ),
                payload,
                warnings,
            )
        if state is None:
            payload = _empty_market_data(product_id, FreshnessStatus.UNKNOWN, interval)
            return (
                ComponentReport(
                    name="market_data",
                    status=ReportStatus.DEGRADED,
                    reason_code="MARKET_DATA_NEVER_RUN",
                    detail=(f"No verified {interval.value} coverage exists for this product."),
                ),
                payload,
                warnings,
            )
        freshness = evaluate_freshness(
            product_id=product_id,
            newest_candle_at=state.covered_ends_at,
            now=now,
            interval=interval,
        )
        payload = MarketDataPayload(
            product_id=product_id,
            provider=provider,
            timeframe=interval.value,
            worker_status=state.status.value,
            complete=state.complete,
            freshness_status=freshness.status.value,
            newest_candle_at=freshness.newest_candle_at,
            age_seconds=freshness.age_seconds,
            gap_count=state.gap_count,
            missing_intervals=state.missing_intervals,
            expected_candle_count=state.expected_candle_count,
            received_candle_count=state.received_candle_count,
            content_fingerprint=state.content_fingerprint,
            covered_starts_at=state.covered_starts_at,
            covered_ends_at=state.covered_ends_at,
        )
        return _market_data_component(state, freshness, interval), payload, warnings

    async def _load_worker_state(
        self,
        product_id: str,
        interval: CandleInterval,
    ) -> tuple[MarketDataWorkerState | None, str | None]:
        """Prefer live Coinbase state, then demo, without inventing coverage."""
        last_unavailable = False
        for provider in ("coinbase", "demo"):
            try:
                state = await self.market_data_state.get(
                    provider,
                    product_id,
                    interval,
                )
            except MarketDataWorkerUnavailableError:
                last_unavailable = True
                continue
            if state is not None:
                return state, provider
        if last_unavailable:
            raise MarketDataWorkerUnavailableError("Market-data worker state is unavailable.")
        return None, None

    async def _coverage_rows(
        self,
        now: datetime,
        components: list[ComponentReport],
        warnings: list[str],
    ) -> tuple[DatasetCoverageRow, ...]:
        """Build one catalog row per watch, worker, or verified dataset identity."""
        watched: tuple[MarketDataWatchTarget, ...] = ()
        if self.watchlist is not None:
            try:
                watched = await self.watchlist.list_all()
            except MarketDataWatchlistUnavailableError:
                warnings.append("Watchlist is unavailable; catalog omits watch flags.")
                components.append(
                    ComponentReport(
                        name="watchlist",
                        status=ReportStatus.DEGRADED,
                        reason_code="WATCHLIST_UNAVAILABLE",
                        detail="The ingestion watchlist could not be read.",
                    )
                )
        worker_states: tuple[MarketDataWorkerState, ...] = ()
        try:
            worker_states = await self.market_data_state.list_all()
        except MarketDataWorkerUnavailableError:
            warnings.append("Worker state is unavailable; catalog omits ingestion status.")
            components.append(
                ComponentReport(
                    name="market_data",
                    status=ReportStatus.DEGRADED,
                    reason_code="MARKET_DATA_STATE_UNAVAILABLE",
                    detail="Durable market-data worker state could not be listed.",
                )
            )
        manifests = () if self.dataset_store is None else self.dataset_store.list_latest_verified()
        return _merge_coverage_rows(now, watched, worker_states, manifests)

    async def _draft_summaries(
        self,
        components: list[ComponentReport],
        warnings: list[str],
    ) -> tuple[DraftSummary, ...]:
        """List drafts or record a partial-result warning."""
        try:
            drafts = await self.drafts.list_drafts()
        except RuntimeError, TypeError, ValueError:
            components.append(
                ComponentReport(
                    name="drafts",
                    status=ReportStatus.DEGRADED,
                    reason_code="DRAFTS_UNAVAILABLE",
                    detail="Strategy drafts could not be listed.",
                )
            )
            warnings.append(
                "Draft listing failed; published and runtime rows may still be complete."
            )
            return ()
        return tuple(
            DraftSummary(
                strategy_id=draft.definition.strategy_id,
                name=draft.definition.name,
                version=draft.definition.version,
                revision=draft.revision,
                product_id=draft.definition.instrument.product_id,
                timeframe=draft.definition.timeframe,
            )
            for draft in drafts
        )

    async def _publication_summaries(
        self,
        components: list[ComponentReport],
        warnings: list[str],
    ) -> tuple[PublicationSummary, ...]:
        """List publications or record a partial-result warning."""
        try:
            entries = await self.publications.list_published(include_archived=True)
        except StrategyPublicationError, RuntimeError, TypeError, ValueError:
            components.append(
                ComponentReport(
                    name="publications",
                    status=ReportStatus.DEGRADED,
                    reason_code="PUBLICATIONS_UNAVAILABLE",
                    detail="Published strategies could not be listed.",
                )
            )
            warnings.append("Publication listing failed.")
            return ()
        return tuple(
            PublicationSummary(
                strategy_id=entry.definition.strategy_id,
                name=entry.definition.name,
                version=entry.definition.version,
                strategy_fingerprint=entry.strategy_fingerprint,
                product_id=entry.definition.instrument.product_id,
                timeframe=entry.definition.timeframe,
                archived=entry.archived_at is not None,
            )
            for entry in entries
        )

    async def _deployment_summaries(
        self,
        components: list[ComponentReport],
        warnings: list[str],
    ) -> tuple[DeploymentSummary, ...]:
        """List deployments without cash or order payloads."""
        del components, warnings
        deployments = await self.execution.list_deployments()
        return tuple(_deployment_summary(item) for item in deployments)

    async def _backtest_performance(
        self,
        result_fingerprint: str,
        now: datetime,
    ) -> PerformanceReport:
        """Load one reverified backtest summary as operator performance evidence."""
        try:
            result = await self.backtests.load(result_fingerprint)
        except BacktestResultNotFoundError:
            component = ComponentReport(
                name="performance",
                status=ReportStatus.FAILED,
                reason_code="RESULT_NOT_FOUND",
                detail="No immutable backtest result exists for that fingerprint.",
            )
            return _empty_performance(now, (component,))
        except BacktestResultUnavailableError:
            component = ComponentReport(
                name="performance",
                status=ReportStatus.FAILED,
                reason_code="BACKTESTS_UNAVAILABLE",
                detail="Backtest result storage is unavailable.",
            )
            return _empty_performance(now, (component,))
        summary = result.summary
        component = ComponentReport(
            name="performance",
            status=ReportStatus.HEALTHY,
            reason_code="BACKTEST",
            detail="Metrics are historical simulation evidence, not live fills.",
        )
        payload = PerformancePayload(
            mode="backtest",
            timeframe=await self._strategy_timeframe(result.strategy_fingerprint),
            strategy_fingerprint=result.strategy_fingerprint,
            dataset_fingerprint=result.dataset_fingerprint,
            engine_contract_version=result.engine_contract_version,
            fee_treatment="disclosed maker/taker rates on the immutable research run",
            result_fingerprint=result_fingerprint,
            deployment_id=None,
            trade_count=summary.trade_count,
            total_net_pnl=summary.total_net_pnl,
            total_return_fraction=summary.total_return_fraction,
            maximum_drawdown_fraction=summary.maximum_drawdown_fraction,
            total_spread_cost=summary.total_spread_cost,
            evaluation_bars=summary.evaluation_bars,
        )
        return PerformanceReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.HEALTHY,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=payload,
        )

    async def _deployment_performance(
        self,
        deployment_id: UUID,
        now: datetime,
    ) -> PerformanceReport:
        """Summarize paper or live fills without implying a full accounting engine."""
        try:
            snapshot = await self.execution.get_deployment(deployment_id)
        except Exception:  # noqa: BLE001 - store failures are redacted at this boundary.
            component = ComponentReport(
                name="performance",
                status=ReportStatus.FAILED,
                reason_code="DEPLOYMENT_UNAVAILABLE",
                detail="The deployment could not be loaded.",
            )
            return _empty_performance(now, (component,))
        deployment = snapshot.deployment
        component = ComponentReport(
            name="performance",
            status=ReportStatus.DEGRADED,
            reason_code="RUNTIME_SLICE",
            detail="Paper/live operator performance reports fill counts, not a full PnL engine.",
        )
        payload = PerformancePayload(
            mode="live" if deployment.mode.value == "live" else "paper",
            timeframe="1h",
            strategy_fingerprint=deployment.strategy_fingerprint,
            dataset_fingerprint=None,
            engine_contract_version=None,
            fee_treatment="venue or paper broker fees on recorded fills; PnL not computed here",
            result_fingerprint=None,
            deployment_id=deployment.id,
            trade_count=len(snapshot.fills),
            total_net_pnl=None,
            total_return_fraction=None,
            maximum_drawdown_fraction=None,
            total_spread_cost=None,
            evaluation_bars=None,
        )
        return PerformanceReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.DEGRADED,
            components=(component,),
            redaction=STANDARD_REDACTION,
            partial_result_warnings=(
                "Paper and live performance is a fill-count slice until a dedicated ledger exists.",
            ),
            recommended_next_action=recommend_next_action((component,)),
            payload=payload,
        )

    async def _risk_findings(self) -> tuple[tuple[RiskFinding, ...], list[ComponentReport]]:
        """Collect pause and mismatch observations from deployments."""
        components: list[ComponentReport] = []
        findings: list[RiskFinding] = []
        deployments = await self.execution.list_deployments()
        for deployment in deployments:
            if deployment.status is DeploymentStatus.PAUSED:
                findings.append(
                    RiskFinding(
                        reason_code="DEPLOYMENT_PAUSED",
                        deployment_id=deployment.id,
                        detail=(
                            deployment.mismatch_detail or "Operator or runtime pause is in effect."
                        ),
                    )
                )
            if deployment.mismatch_detail:
                findings.append(
                    RiskFinding(
                        reason_code="STATE_MISMATCH",
                        deployment_id=deployment.id,
                        detail=deployment.mismatch_detail[:500],
                    )
                )
        if findings:
            components.append(
                ComponentReport(
                    name="runtime_risk",
                    status=ReportStatus.DEGRADED,
                    reason_code="FINDINGS_PRESENT",
                    detail="One or more deployments are paused or mismatched.",
                )
            )
        return tuple(findings), components

    async def _reconciliation_findings(
        self,
    ) -> tuple[tuple[ReconciliationFinding, ...], list[ComponentReport], list[str]]:
        """Inspect deployments and recent audit failures without mutating state."""
        components: list[ComponentReport] = []
        warnings: list[str] = []
        findings: list[ReconciliationFinding] = []
        deployments = await self.execution.list_deployments()
        for deployment in deployments:
            if deployment.mismatch_detail:
                findings.append(
                    ReconciliationFinding(
                        reason_code="STATE_MISMATCH",
                        deployment_id=deployment.id,
                        detail=deployment.mismatch_detail[:500],
                    )
                )
            if deployment.status is not DeploymentStatus.STOPPED:
                await self._collect_unknown_orders(deployment, findings)
        try:
            events = await self.audit.list_recent(limit=20)
        except AuditEventUnavailableError:
            warnings.append("Audit events are unavailable; reconciliation is partial.")
            components.append(
                ComponentReport(
                    name="audit",
                    status=ReportStatus.DEGRADED,
                    reason_code="AUDIT_UNAVAILABLE",
                    detail="Recent audit events could not be listed.",
                )
            )
        else:
            failures = [event for event in events if event.outcome.value == "failure"]
            if failures:
                findings.append(
                    ReconciliationFinding(
                        reason_code="AUDIT_FAILURES",
                        deployment_id=None,
                        detail=f"{len(failures)} recent audit failure event(s) were recorded.",
                    )
                )
        if findings:
            components.append(
                ComponentReport(
                    name="reconciliation",
                    status=ReportStatus.DEGRADED,
                    reason_code="FINDINGS_PRESENT",
                    detail="Mismatch, unknown orders, or audit failures were reported.",
                )
            )
        return tuple(findings), components, warnings

    async def _collect_unknown_orders(
        self,
        deployment: Deployment,
        findings: list[ReconciliationFinding],
    ) -> None:
        """Append unknown-order findings for one active deployment."""
        try:
            snapshot = await self.execution.get_deployment(deployment.id)
        except Exception:  # noqa: BLE001 - snapshot failures stay partial.
            findings.append(
                ReconciliationFinding(
                    reason_code="SNAPSHOT_UNAVAILABLE",
                    deployment_id=deployment.id,
                    detail="Open orders could not be loaded for reconciliation.",
                )
            )
            return
        unknown = [order for order in snapshot.orders if order.status is OrderStatus.UNKNOWN]
        if unknown:
            findings.append(
                ReconciliationFinding(
                    reason_code="UNKNOWN_ORDERS",
                    deployment_id=deployment.id,
                    detail=f"{len(unknown)} order(s) remain in unknown status.",
                )
            )


def _credentials_configured(settings: Settings) -> bool:
    """True when both Coinbase secrets are present."""
    return (
        settings.coinbase_api_key_name is not None and settings.coinbase_api_private_key is not None
    )


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


def _empty_market_data(
    product_id: str,
    freshness: FreshnessStatus,
    interval: CandleInterval,
) -> MarketDataPayload:
    """Build an empty market-data payload when coverage is missing."""
    return MarketDataPayload(
        product_id=product_id,
        provider=None,
        timeframe=interval.value,
        worker_status=None,
        complete=None,
        freshness_status=freshness.value,
        newest_candle_at=None,
        age_seconds=None,
        gap_count=None,
        missing_intervals=None,
        expected_candle_count=None,
        received_candle_count=None,
        content_fingerprint=None,
        covered_starts_at=None,
        covered_ends_at=None,
    )


def _market_data_component(
    state: MarketDataWorkerState,
    freshness: object,
    interval: CandleInterval,
) -> ComponentReport:
    """Classify coverage completeness and candle freshness."""
    status_name = getattr(freshness, "status", FreshnessStatus.UNKNOWN)
    if not state.complete or (state.gap_count or 0) > 0 or (state.missing_intervals or 0) > 0:
        return ComponentReport(
            name="market_data",
            status=ReportStatus.DEGRADED,
            reason_code="GAPS_PRESENT",
            detail="Verified coverage is incomplete or contains gaps.",
        )
    if status_name is FreshnessStatus.STALE:
        return ComponentReport(
            name="market_data",
            status=ReportStatus.DEGRADED,
            reason_code="STALE",
            detail=(
                "The newest verified candle is older than the "
                f"{interval.value} freshness threshold."
            ),
        )
    if status_name is FreshnessStatus.UNKNOWN:
        return ComponentReport(
            name="market_data",
            status=ReportStatus.DEGRADED,
            reason_code="UNKNOWN",
            detail="Freshness could not be evaluated.",
        )
    return ComponentReport(
        name="market_data",
        status=ReportStatus.HEALTHY,
        reason_code="FRESH",
        detail=f"Verified {interval.value} coverage is complete and fresh.",
    )


def _deployment_summary(deployment: Deployment) -> DeploymentSummary:
    """Project one deployment without cash or quantities."""
    return DeploymentSummary(
        deployment_id=deployment.id,
        strategy_id=deployment.strategy_id,
        strategy_fingerprint=deployment.strategy_fingerprint,
        mode=deployment.mode.value,
        status=deployment.status.value,
        phase=deployment.phase.value,
        product_id=deployment.product_id,
        last_evaluated_bar=deployment.last_evaluated_bar,
        mismatch_present=bool(deployment.mismatch_detail),
        last_signal=deployment.last_signal,
    )


def _runtime_slice(
    strategies: StrategiesReport,
    risk: RiskReport,
    reconciliation: ReconciliationReport,
    deployment_id: UUID | None,
) -> tuple[
    tuple[DeploymentSummary, ...],
    tuple[RiskFinding, ...],
    tuple[ReconciliationFinding, ...],
    tuple[ComponentReport, ...],
]:
    """Optionally restrict runtime evidence to one deployment identity."""
    deployments = strategies.payload.deployments
    risk_findings = risk.payload.findings
    recon_findings = reconciliation.payload.findings
    if deployment_id is None:
        return deployments, risk_findings, recon_findings, ()
    selected = tuple(item for item in deployments if item.deployment_id == deployment_id)
    extra: tuple[ComponentReport, ...] = ()
    if not selected:
        extra = (
            ComponentReport(
                name="runtime",
                status=ReportStatus.FAILED,
                reason_code="DEPLOYMENT_NOT_FOUND",
                detail="No deployment matched the requested id.",
            ),
        )
    return (
        selected,
        tuple(item for item in risk_findings if item.deployment_id == deployment_id),
        tuple(item for item in recon_findings if item.deployment_id == deployment_id),
        extra,
    )


def _empty_performance(now: datetime, components: tuple[ComponentReport, ...]) -> PerformanceReport:
    """Return a failed or empty performance report with a placeholder payload."""
    payload = PerformancePayload(
        mode="backtest",
        timeframe="1h",
        strategy_fingerprint=None,
        dataset_fingerprint=None,
        engine_contract_version=None,
        fee_treatment="unspecified",
        result_fingerprint=None,
        deployment_id=None,
        trade_count=None,
        total_net_pnl=None,
        total_return_fraction=None,
        maximum_drawdown_fraction=None,
        total_spread_cost=None,
        evaluation_bars=None,
    )
    return PerformanceReport(
        application_version=__version__,
        generated_at=now,
        overall_status=aggregate_status(components),
        components=components,
        redaction=STANDARD_REDACTION,
        recommended_next_action=recommend_next_action(components),
        payload=payload,
    )


def _worst_status(statuses: tuple[ReportStatus, ...]) -> ReportStatus:
    """Return the most severe nested report status."""
    if ReportStatus.FAILED in statuses:
        return ReportStatus.FAILED
    if ReportStatus.DEGRADED in statuses:
        return ReportStatus.DEGRADED
    return ReportStatus.HEALTHY


def _parse_timeframe(value: str | None) -> CandleInterval:
    """Default operator market-data reports to 1h when unspecified."""
    if value is None or value == "":
        return CandleInterval.ONE_HOUR
    try:
        return parse_candle_interval(value)
    except ValueError:
        return CandleInterval.ONE_HOUR


def _catalog_provider(settings: Settings) -> str:
    """Label the current catalog as demo or coinbase without exposing secrets."""
    if settings.coinbase_api_key_name is None or settings.coinbase_api_private_key is None:
        return "demo"
    return "coinbase"


def _indicator_entries() -> tuple[IndicatorCatalogEntry, ...]:
    """Describe implemented indicator kinds and their canonical bounds."""
    return (
        IndicatorCatalogEntry(
            kind=IndicatorKind.EMA.value,
            inputs=("close",),
            period_min=2,
            period_max=500,
        ),
        IndicatorCatalogEntry(
            kind=IndicatorKind.SMA.value,
            inputs=("close",),
            period_min=2,
            period_max=500,
        ),
        IndicatorCatalogEntry(
            kind=IndicatorKind.RSI.value,
            inputs=("close",),
            period_min=2,
            period_max=100,
        ),
        IndicatorCatalogEntry(
            kind=IndicatorKind.ATR.value,
            inputs=("high", "low", "close"),
            period_min=2,
            period_max=100,
        ),
        IndicatorCatalogEntry(
            kind=IndicatorKind.VOLUME_SMA.value,
            inputs=("volume",),
            period_min=2,
            period_max=500,
        ),
    )


def _merge_coverage_rows(
    now: datetime,
    watched: tuple[MarketDataWatchTarget, ...],
    states: tuple[MarketDataWorkerState, ...],
    manifests: tuple[object, ...],
) -> tuple[DatasetCoverageRow, ...]:
    """Join watchlist, worker, and dataset identities into catalog rows."""
    watch_index = {(item.provider, item.product_id, item.timeframe.value): item for item in watched}
    state_index = {(item.provider, item.product_id, item.timeframe.value): item for item in states}
    manifest_index: dict[tuple[str, str, str], object] = {}
    for manifest in manifests:
        provider = getattr(manifest, "provider", None)
        product_id = getattr(manifest, "product_id", None)
        timeframe = getattr(manifest, "timeframe", None)
        if (
            isinstance(provider, str)
            and isinstance(product_id, str)
            and isinstance(timeframe, str)
            and _supported_timeframe_token(timeframe) is not None
        ):
            manifest_index[(provider, product_id, timeframe)] = manifest
    keys = sorted({*watch_index, *state_index, *manifest_index})
    return tuple(
        _coverage_row(
            key,
            now,
            watch_index.get(key),
            state_index.get(key),
            manifest_index.get(key),
        )
        for key in keys
    )


def _supported_timeframe_token(value: str) -> str | None:
    """Return 1h or 5m, otherwise omit the catalog row."""
    try:
        return parse_candle_interval(value).value
    except ValueError:
        return None


def _coverage_row(
    key: tuple[str, str, str],
    now: datetime,
    watched: MarketDataWatchTarget | None,
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> DatasetCoverageRow:
    """Build one catalog row from optional watch, worker, and dataset facts."""
    provider, product_id, timeframe = key
    interval = parse_candle_interval(timeframe)
    newest = state.covered_ends_at if state is not None else _manifest_end(manifest)
    freshness = evaluate_freshness(
        product_id=product_id,
        newest_candle_at=newest,
        now=now,
        interval=interval,
    )
    complete = _coverage_complete(state, manifest)
    gap_count = state.gap_count if state is not None else _manifest_int(manifest, "gap_count")
    if state is not None:
        missing = state.missing_intervals
    else:
        missing = _manifest_int(manifest, "missing_intervals")
    return DatasetCoverageRow(
        provider=provider,
        product_id=product_id,
        timeframe=interval.value,
        watched=watched is not None and watched.enabled,
        lookback_hours=watched.lookback_hours if watched is not None else None,
        worker_status=state.status.value if state is not None else None,
        complete=complete,
        freshness_status=freshness.status.value,
        covered_starts_at=_coverage_start(state, manifest),
        covered_ends_at=newest,
        expected_candle_count=_coverage_expected(state, manifest),
        received_candle_count=_coverage_received(state, manifest),
        gap_count=gap_count,
        missing_intervals=missing,
        content_fingerprint=_coverage_fingerprint(state, manifest),
        sparsity=_coverage_sparsity(complete, gap_count, missing),
    )


def _coverage_sparsity(
    complete: bool | None,
    gap_count: int | None,
    missing: int | None,
) -> Literal["none", "unknown", "gapped"]:
    """Classify local coverage holes without interpolating prices."""
    if complete and not gap_count and not missing:
        return "none"
    if gap_count or missing or complete is False:
        return "gapped"
    return "unknown"


def _coverage_complete(state: MarketDataWorkerState | None, manifest: object | None) -> bool | None:
    """Prefer worker completeness, then a verified manifest."""
    if state is not None:
        return state.complete
    if manifest is not None:
        return True
    return None


def _coverage_start(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> datetime | None:
    """Return covered range start from worker state or manifest text."""
    if state is not None:
        return state.covered_starts_at
    return _manifest_datetime(manifest, "starts_at")


def _coverage_expected(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> int | None:
    """Return expected candle count from worker state or manifest."""
    if state is not None:
        return state.expected_candle_count
    return _manifest_int(manifest, "expected_candle_count")


def _coverage_received(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> int | None:
    """Return received candle count from worker state or manifest."""
    if state is not None:
        return state.received_candle_count
    return _manifest_int(manifest, "received_candle_count")


def _coverage_fingerprint(
    state: MarketDataWorkerState | None,
    manifest: object | None,
) -> str | None:
    """Return the verified dataset fingerprint when present."""
    if state is not None:
        return state.content_fingerprint
    value = getattr(manifest, "content_fingerprint", None)
    return value if isinstance(value, str) else None


def _manifest_end(manifest: object | None) -> datetime | None:
    """Parse a manifest exclusive end instant."""
    return _manifest_datetime(manifest, "ends_at")


def _manifest_int(manifest: object | None, field: str) -> int | None:
    """Read one optional integer manifest field."""
    value = getattr(manifest, field, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _manifest_datetime(manifest: object | None, field: str) -> datetime | None:
    """Parse one optional UTC timestamp stored as ISO-8601 text."""
    value = getattr(manifest, field, None)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
