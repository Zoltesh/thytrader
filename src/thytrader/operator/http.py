"""HTTP fetch of versioned operator reports from the loopback API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from thytrader.agent_http import request_json
from thytrader.operator.alerts_report import AlertsReport
from thytrader.operator.data_health import DataHealthReport
from thytrader.operator.fleet_health_models import FleetHealthReport
from thytrader.operator.funding_report import FundingReport
from thytrader.operator.futures_account_history_report import FuturesAccountHistoryReport
from thytrader.operator.futures_account_report import FuturesAccountReport
from thytrader.operator.futures_books_report import FuturesBooksReport
from thytrader.operator.health_models import ConfigurationReport, ExchangeReport, HealthReport
from thytrader.operator.market_models import (
    DataCatalogReport,
    IndicatorsReport,
    MarketDataReport,
    ProductsReport,
)
from thytrader.operator.models import OPERATOR_API_PREFIX, OperatorEnvelope
from thytrader.operator.portfolio_models import FeesReport, PortfolioReport, PortfoliosReport
from thytrader.operator.readiness import ReadinessReport
from thytrader.operator.runtime_models import (
    DecisionsReport,
    MonitorReport,
    PerformanceReport,
    ReconciliationReport,
    RiskReport,
    RuntimeReport,
    StrategiesReport,
    StudiesReport,
    TradeReasonsReport,
)
from thytrader.operator.support_bundle_models import SupportBundleReport
from thytrader.operator.venue_reconciliation_models import VenueReconciliationReport

if TYPE_CHECKING:
    from collections.abc import Mapping

_REPORT_MODELS: dict[str, type[OperatorEnvelope]] = {
    "health": HealthReport,
    "configuration": ConfigurationReport,
    "exchange": ExchangeReport,
    "market-data": MarketDataReport,
    "data-catalog": DataCatalogReport,
    "data-health": DataHealthReport,
    "products": ProductsReport,
    "indicators": IndicatorsReport,
    "strategies": StrategiesReport,
    "performance": PerformanceReport,
    "risk": RiskReport,
    "reconciliation": ReconciliationReport,
    "runtime": RuntimeReport,
    "monitor": MonitorReport,
    "studies": StudiesReport,
    "trade-reasons": TradeReasonsReport,
    "decisions": DecisionsReport,
    "support-bundle": SupportBundleReport,
    "portfolio": PortfolioReport,
    "fees": FeesReport,
    "portfolios": PortfoliosReport,
    "readiness": ReadinessReport,
    "venue-reconciliation": VenueReconciliationReport,
    "alerts": AlertsReport,
    "funding": FundingReport,
    "futures-account": FuturesAccountReport,
    "futures-account/history": FuturesAccountHistoryReport,
    "futures-books": FuturesBooksReport,
    "fleet-health": FleetHealthReport,
}


def fetch_operator_report(
    *,
    base_url: str,
    command: str,
    query: Mapping[str, str | tuple[str, ...]] | None = None,
) -> OperatorEnvelope:
    """GET one operator report and validate its JSON against the v1 models.

    Tuple values become repeated query parameters (for example ``outcome``).
    JSON validation admits serialized timestamps in strict nested signal records
    without relaxing the models' Python or UTC validation rules.
    """
    model = _REPORT_MODELS.get(command)
    if model is None:
        message = f"unsupported operator command: {command}"
        raise AssertionError(message)
    url = f"{base_url}{OPERATOR_API_PREFIX}/{command}"
    if query:
        encoded = urlencode({key: value for key, value in query.items() if value}, doseq=True)
        if encoded:
            url = f"{url}?{encoded}"
    payload = request_json(method="GET", url=url)
    return model.model_validate_json(json.dumps(payload))
