"""Build versioned operator reports from existing application services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal  # noqa: TC003 - runtime marks map uses Decimal at runtime
from typing import TYPE_CHECKING, Literal

from thytrader import __version__
from thytrader.alerts.report import AlertsReport, build_alerts_report
from thytrader.config import Settings
from thytrader.credentials.service import credentials_are_configured
from thytrader.exchanges.fee_schedule import suggest_research_fee_rates
from thytrader.memory.recording import compose_trade_reasons
from thytrader.memory.service import storage_label
from thytrader.memory.store import DisabledExperientialMemoryStore, ExperientialMemoryStore
from thytrader.operator.decisions import decisions_report
from thytrader.operator.diagnostics.findings import (
    build_monitor_report,
    build_reconciliation_report,
    build_risk_report,
)
from thytrader.operator.diagnostics.health import (
    build_configuration_report,
    build_exchange_report,
    build_health_report,
)
from thytrader.operator.diagnostics.market_coverage import (
    build_data_catalog_report,
    build_market_data_report,
    build_products_report,
)
from thytrader.operator.diagnostics.performance import build_performance_report
from thytrader.operator.diagnostics.runtime import build_runtime_report, build_strategies_report
from thytrader.operator.indicator_report import indicator_catalog_entries
from thytrader.operator.models import (
    PORTFOLIO_REDACTION,
    STANDARD_REDACTION,
    ComponentReport,
    ConfigurationReport,
    DataCatalogReport,
    DecisionsReport,
    ExchangeReport,
    FeesPayload,
    FeesReport,
    HealthReport,
    IndicatorsPayload,
    IndicatorsReport,
    MarketDataReport,
    MonitorReport,
    OperatorMoneyPayload,
    OperatorPortfolioAssetPayload,
    PerformanceReport,
    PortfolioPayload,
    PortfolioReport,
    PortfoliosReport,
    ProductsReport,
    ReconciliationReport,
    ReportStatus,
    RiskReport,
    RuntimeReport,
    StrategiesReport,
    StudiesPayload,
    StudiesReport,
    SupportBundlePayload,
    SupportBundleReport,
    TradeReasonsPayload,
    TradeReasonsReport,
)
from thytrader.operator.portfolios_report import build_portfolios_report
from thytrader.operator.readiness import ReadinessReport, build_readiness_report
from thytrader.operator.status import aggregate_status, recommend_next_action
from thytrader.operator.venue_reconciliation import (
    VenueReconciliationReport,
    build_venue_reconciliation_report,
)
from thytrader.research.catalog import (
    DisabledResearchStudyCatalog,
    ResearchStudyCatalog,
    StudyCatalogIntegrityError,
    StudyCatalogUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.alerts.store import AlertStore
    from thytrader.config import Settings
    from thytrader.execution.decision_store import DecisionJournalStore
    from thytrader.execution.decisions import DecisionOutcome
    from thytrader.execution.store import ExecutionStore
    from thytrader.execution.user_feed_state import UserOrderFeedStateStore
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.market_data.service import MarketDataService
    from thytrader.market_data.watchlist import MarketDataWatchlistStore
    from thytrader.market_data.worker_state import MarketDataWorkerStateStore
    from thytrader.operator.research_workers import ResearchQueueSnapshotReader
    from thytrader.persistence.audit_events import AuditEventStore
    from thytrader.persistence.backtest_results import BacktestResultReader
    from thytrader.persistence.portfolio_history import PortfolioHistoryStore
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.portfolio.models import PortfolioAsset
    from thytrader.portfolio.service import PortfolioService
    from thytrader.portfolios.store import PortfolioStorage
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.runtime import RuntimeState
    from thytrader.strategies.library import StrategyStore
    from thytrader.strategies.snapshots import StrategySnapshotStore


@dataclass(frozen=True, slots=True)
class OperatorDiagnostics:
    """Read-only diagnostics assembled from the same services the HTTP API uses."""

    settings: Settings
    portfolio: PortfolioService
    market_data_state: MarketDataWorkerStateStore
    history: PortfolioHistoryStore
    publications: StrategySnapshotStore
    strategies_store: StrategyStore
    backtests: BacktestResultReader
    execution: ExecutionStore
    audit: AuditEventStore
    runtime: RuntimeState | None = None
    engine: AsyncEngine | None = None
    dataset_store: DatasetStore | None = None
    watchlist: MarketDataWatchlistStore | None = None
    market_data: MarketDataService | None = None
    heartbeat_store: WorkerHeartbeatStore | None = None
    risk_policies: RiskPolicyStore | None = None
    user_order_feed: UserOrderFeedStateStore | None = None
    memory_store: ExperientialMemoryStore | None = None
    research_studies: ResearchStudyCatalog | None = None
    decision_store: DecisionJournalStore | None = None
    portfolios: PortfolioStorage | None = None
    research_queue: ResearchQueueSnapshotReader | None = None
    alert_store: AlertStore | None = None

    async def health(self, *, probe_api: bool = False) -> HealthReport:
        """Summarize process, database, worker, research pool, and exchange health."""
        return await build_health_report(self, probe_api=probe_api)

    async def configuration(self) -> ConfigurationReport:
        """Return validated settings with secrets omitted."""
        return await build_configuration_report(self)

    async def exchange(self) -> ExchangeReport:
        """Report Coinbase connection status and detected permissions."""
        return await build_exchange_report(self)

    async def portfolio_report(self) -> PortfolioReport:
        """Return the current Coinbase or demo portfolio without credentials."""
        now = datetime.now(UTC)
        try:
            portfolio = await self.portfolio.get_portfolio()
        except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
            component = ComponentReport(
                name="portfolio",
                status=ReportStatus.FAILED,
                reason_code="PORTFOLIO_UNAVAILABLE",
                detail="The exchange account could not be queried.",
            )
            return PortfolioReport(
                application_version=__version__,
                generated_at=now,
                overall_status=ReportStatus.FAILED,
                components=(component,),
                redaction=PORTFOLIO_REDACTION,
                recommended_next_action=recommend_next_action((component,)),
                payload=PortfolioPayload(
                    as_of=now,
                    demo=not credentials_are_configured(self.settings),
                    connection_status="unavailable",
                    permissions=(),
                    total_value=OperatorMoneyPayload(amount="0", currency="USDC"),
                    assets=(),
                    unvalued_assets=(),
                ),
            )
        assets = tuple(_operator_portfolio_asset(asset) for asset in portfolio.assets)
        component = ComponentReport(
            name="portfolio",
            status=ReportStatus.HEALTHY,
            reason_code="DEMO_MODE" if portfolio.demo else "CONNECTED",
            detail=(
                "Coinbase credentials are absent; demo data is in use."
                if portfolio.demo
                else "Coinbase credentials are configured."
            ),
        )
        return PortfolioReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.HEALTHY,
            components=(component,),
            redaction=PORTFOLIO_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=PortfolioPayload(
                as_of=portfolio.as_of,
                demo=portfolio.demo,
                connection_status=portfolio.connection.status,
                permissions=portfolio.connection.permissions,
                total_value=_operator_money(
                    portfolio.total_value.amount, portfolio.total_value.currency
                ),
                assets=assets,
                unvalued_assets=portfolio.unvalued_assets,
            ),
        )

    async def fees_report(self) -> FeesReport:
        """Return the current fee tier and research-only suggested rates."""
        now = datetime.now(UTC)
        try:
            profile = await self.portfolio.get_fee_profile()
        except Exception:  # noqa: BLE001 - provider failures are redacted at this boundary.
            component = ComponentReport(
                name="fees",
                status=ReportStatus.FAILED,
                reason_code="FEES_UNAVAILABLE",
                detail="Fee profile is temporarily unavailable.",
            )
            return FeesReport(
                application_version=__version__,
                generated_at=now,
                overall_status=ReportStatus.FAILED,
                components=(component,),
                redaction=STANDARD_REDACTION,
                recommended_next_action=recommend_next_action((component,)),
                payload=FeesPayload(
                    taker_fee_rate="0",
                    maker_fee_rate="0",
                    usd_volume_30d="0",
                    fee_tier="unavailable",
                    as_of=now,
                    source="coinbase",
                    suggestion_source="unavailable",
                    suggestion_unavailable_reason="demo_or_missing_credentials",
                ),
            )
        suggestion = suggest_research_fee_rates(profile=profile, demo=self.portfolio.demo)
        component = ComponentReport(
            name="fees",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=f"Fee tier {profile.fee_tier}.",
        )
        return FeesReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.HEALTHY,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=FeesPayload(
                taker_fee_rate=format(profile.taker_fee_rate, "f"),
                maker_fee_rate=format(profile.maker_fee_rate, "f"),
                usd_volume_30d=format(profile.usd_volume_30d, "f"),
                fee_tier=profile.fee_tier,
                as_of=profile.as_of,
                source=profile.source,
                suggested_maker_fee_rate=(
                    format(suggestion.suggested_maker_fee_rate, "f")
                    if suggestion.suggested_maker_fee_rate is not None
                    else None
                ),
                suggested_taker_fee_rate=(
                    format(suggestion.suggested_taker_fee_rate, "f")
                    if suggestion.suggested_taker_fee_rate is not None
                    else None
                ),
                suggestion_source=suggestion.source,
                suggestion_unavailable_reason=suggestion.unavailable_reason,
                suggestion_fee_tier=suggestion.fee_tier,
                suggestion_schedule_tier_id=suggestion.schedule_tier_id,
                suggestion_schedule_version=suggestion.schedule_version,
                suggestion_schedule_as_of=(
                    suggestion.schedule_as_of.isoformat()
                    if suggestion.schedule_as_of is not None
                    else None
                ),
                schedule_maker_fee_rate=_optional_rate(suggestion.schedule_maker_fee_rate),
                schedule_taker_fee_rate=_optional_rate(suggestion.schedule_taker_fee_rate),
                suggestion_fetched_at=suggestion.fetched_at,
            ),
        )

    async def portfolios_report(self) -> PortfoliosReport:
        """List portfolios with sleeves, deployment and breaker state, and the newest backtest."""
        return await build_portfolios_report(self.portfolios, self.execution)

    async def readiness_report(
        self,
        deployment_id: UUID | None = None,
        portfolio_id: UUID | None = None,
    ) -> ReadinessReport:
        """Advisory allocation, cap, fee, and breaker preflight (ADR 0114).

        Read-only. It never tightens the published risk policy or changes a bot.
        """
        return await build_readiness_report(
            portfolio=self.portfolio,
            execution=self.execution,
            risk_policies=self.risk_policies,
            portfolios=self.portfolios,
            deployment_id=deployment_id,
            portfolio_id=portfolio_id,
        )

    async def venue_reconciliation_report(self) -> VenueReconciliationReport:
        """Compare managed live books with a fresh venue listing (ADR 0114).

        Read-only. It never creates, cancels, or replaces an order.
        """
        return await build_venue_reconciliation_report(
            portfolio=self.portfolio, execution=self.execution
        )

    async def market_data_report(
        self,
        product_id: str | None = None,
        timeframe: str | None = None,
    ) -> MarketDataReport:
        """Report coverage and freshness for one USD spot product and timeframe."""
        return await build_market_data_report(self, product_id, timeframe)

    async def products(self) -> ProductsReport:
        """List enabled USD spot products from the current catalog."""
        return await build_products_report(self)

    async def data_catalog(self) -> DataCatalogReport:
        """Join watchlist, worker state, and verified Parquet datasets."""
        return await build_data_catalog_report(self)

    async def indicators(self) -> IndicatorsReport:
        """List implemented indicator kinds; do not invent unsupported studies."""
        now = datetime.now(UTC)
        entries = indicator_catalog_entries()
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
        """List strategies and deployments without documents, cash, or fills."""
        return await build_strategies_report(self)

    async def performance(
        self,
        *,
        result_fingerprint: str | None = None,
        deployment_id: UUID | None = None,
    ) -> PerformanceReport:
        """Return one backtest result or one paper/live runtime performance slice."""
        return await build_performance_report(
            self, result_fingerprint=result_fingerprint, deployment_id=deployment_id
        )

    async def risk(self) -> RiskReport:
        """Surface pause/mismatch findings and the effective risk-policy registry."""
        return await build_risk_report(self)

    async def reconciliation(self) -> ReconciliationReport:
        """List mismatch, unknown-order, and recent audit failure conditions."""
        return await build_reconciliation_report(self)

    async def runtime_report(self, deployment_id: UUID | None = None) -> RuntimeReport:
        """Combine deployment status with risk and reconciliation findings."""
        return await build_runtime_report(self, deployment_id)

    async def alerts(self) -> AlertsReport:
        """Return the durable safety-alert feed without trading authority."""
        return await build_alerts_report(self.alert_store, self.settings)

    async def monitor(self) -> MonitorReport:
        """Watch deployments, recent journals, and notification delivery."""
        return await build_monitor_report(self)

    async def studies(self) -> StudiesReport:
        """List persisted composed research studies without child equity curves."""
        now = datetime.now(UTC)
        store = self.research_studies or DisabledResearchStudyCatalog()
        try:
            rows = await store.list_summaries(limit=50)
        except StudyCatalogUnavailableError:
            component = ComponentReport(
                name="studies",
                status=ReportStatus.DEGRADED,
                reason_code="STUDY_CATALOG_UNAVAILABLE",
                detail="Research study catalog storage is unavailable.",
            )
            return StudiesReport(
                application_version=__version__,
                generated_at=now,
                overall_status=ReportStatus.DEGRADED,
                components=(component,),
                redaction=STANDARD_REDACTION,
                partial_result_warnings=(
                    "Research study catalog storage is unavailable; "
                    "submitted studies are not listed.",
                ),
                recommended_next_action=recommend_next_action((component,)),
                payload=StudiesPayload(study_catalog="unavailable", studies=()),
            )
        except StudyCatalogIntegrityError:
            component = ComponentReport(
                name="studies",
                status=ReportStatus.FAILED,
                reason_code="STUDY_CATALOG_INTEGRITY",
                detail="Research study catalog storage failed integrity verification.",
            )
            return StudiesReport(
                application_version=__version__,
                generated_at=now,
                overall_status=ReportStatus.FAILED,
                components=(component,),
                redaction=STANDARD_REDACTION,
                partial_result_warnings=(
                    "Research study catalog storage failed integrity verification.",
                ),
                recommended_next_action=recommend_next_action((component,)),
                payload=StudiesPayload(study_catalog="unavailable", studies=()),
            )
        component = ComponentReport(
            name="studies",
            status=ReportStatus.HEALTHY,
            reason_code="OK",
            detail=f"{len(rows)} persisted research study row(s).",
        )
        return StudiesReport(
            application_version=__version__,
            generated_at=now,
            overall_status=ReportStatus.HEALTHY,
            components=(component,),
            redaction=STANDARD_REDACTION,
            recommended_next_action=recommend_next_action((component,)),
            payload=StudiesPayload(study_catalog="available", studies=rows),
        )

    async def trade_reasons(
        self,
        *,
        intent_id: UUID | None = None,
        deployment_id: UUID | None = None,
    ) -> TradeReasonsReport:
        """Return composed why-trade records without secrets or interpolated candles."""
        now = datetime.now(UTC)
        store = self.memory_store or DisabledExperientialMemoryStore()
        label = storage_label(store)
        rows = await store.list_trade_reasons(
            deployment_id=deployment_id,
            intent_id=intent_id,
            limit=50,
        )
        composed = await compose_trade_reasons(rows, self.execution)
        components = [
            ComponentReport(
                name="trade_reasons",
                status=ReportStatus.HEALTHY if label == "available" else ReportStatus.FAILED,
                reason_code="OK" if label == "available" else "MEMORY_STORAGE_UNAVAILABLE",
                detail=(
                    "Why-trade journals are available."
                    if label == "available"
                    else "Experiential memory has no durable store; why-trade records are empty."
                ),
            )
        ]
        warnings: list[str] = []
        if label == "unavailable":
            warnings.append(
                "Experiential memory storage is unavailable; why-trade reads are empty."
            )
        return TradeReasonsReport(
            application_version=__version__,
            generated_at=now,
            overall_status=aggregate_status(components),
            components=tuple(components),
            redaction=STANDARD_REDACTION,
            partial_result_warnings=tuple(warnings),
            recommended_next_action=recommend_next_action(components),
            payload=TradeReasonsPayload(storage=label, trade_reasons=composed),
        )

    async def decisions(
        self,
        *,
        deployment_id: UUID | None = None,
        strategy_id: UUID | None = None,
        outcomes: Sequence[DecisionOutcome] = (),
        limit: int = 50,
        cursor: str | None = None,
    ) -> DecisionsReport:
        """Return per-bar decisions for one bot, one strategy, or all bots, newest first."""
        return await decisions_report(
            self.decision_store,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            outcomes=outcomes,
            limit=limit,
            cursor=cursor,
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


def _worst_status(statuses: tuple[ReportStatus, ...]) -> ReportStatus:
    """Return the most severe nested report status."""
    if ReportStatus.FAILED in statuses:
        return ReportStatus.FAILED
    if ReportStatus.DEGRADED in statuses:
        return ReportStatus.DEGRADED
    return ReportStatus.HEALTHY


def _operator_money(amount: Decimal, currency: str) -> OperatorMoneyPayload:
    """Render exact money as canonical operator decimal strings."""
    if currency == "USD":
        quote: Literal["USD", "USDC", "USDT"] = "USD"
    elif currency == "USDT":
        quote = "USDT"
    else:
        quote = "USDC"
    return OperatorMoneyPayload(amount=format(amount, "f"), currency=quote)


def _operator_portfolio_asset(asset: PortfolioAsset) -> OperatorPortfolioAssetPayload:
    """Project one portfolio asset without account identifiers."""
    value = asset.value
    money = None
    if value is not None:
        money = _operator_money(value.amount, value.currency)
    return OperatorPortfolioAssetPayload(
        currency=str(asset.currency),
        name=str(asset.name),
        available=format(asset.available, "f"),
        hold=format(asset.hold, "f"),
        total=format(asset.total, "f"),
        value=money,
    )


def _optional_rate(value: Decimal | None) -> str | None:
    """Render one optional fee rate as plain decimal text."""
    return None if value is None else format(value, "f")
