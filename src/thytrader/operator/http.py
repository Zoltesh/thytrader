"""HTTP fetch of versioned operator reports from the loopback API."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from thytrader.agent_http import request_json
from thytrader.operator.models import (
    OPERATOR_API_PREFIX,
    ConfigurationReport,
    DataCatalogReport,
    ExchangeReport,
    HealthReport,
    IndicatorsReport,
    MarketDataReport,
    OperatorEnvelope,
    PerformanceReport,
    ProductsReport,
    ReconciliationReport,
    RiskReport,
    RuntimeReport,
    StrategiesReport,
    SupportBundleReport,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_REPORT_MODELS: dict[str, type[OperatorEnvelope]] = {
    "health": HealthReport,
    "configuration": ConfigurationReport,
    "exchange": ExchangeReport,
    "market-data": MarketDataReport,
    "data-catalog": DataCatalogReport,
    "products": ProductsReport,
    "indicators": IndicatorsReport,
    "strategies": StrategiesReport,
    "performance": PerformanceReport,
    "risk": RiskReport,
    "reconciliation": ReconciliationReport,
    "runtime": RuntimeReport,
    "support-bundle": SupportBundleReport,
}


def fetch_operator_report(
    *,
    base_url: str,
    command: str,
    query: Mapping[str, str] | None = None,
) -> OperatorEnvelope:
    """GET one operator report and validate it against the v1 models."""
    model = _REPORT_MODELS.get(command)
    if model is None:
        message = f"unsupported operator command: {command}"
        raise AssertionError(message)
    url = f"{base_url}{OPERATOR_API_PREFIX}/{command}"
    if query:
        encoded = urlencode({key: value for key, value in query.items() if value})
        if encoded:
            url = f"{url}?{encoded}"
    payload = request_json(method="GET", url=url)
    return model.model_validate(payload)
