"""Operator support bundle report: the redacted composite of the core reports.

Sits above the health, market, and runtime report models because its payload
embeds one report of each supported kind.
"""

from __future__ import annotations

from typing import Literal

from thytrader.operator.health_models import ConfigurationReport, ExchangeReport, HealthReport
from thytrader.operator.market_models import MarketDataReport
from thytrader.operator.models import OperatorEnvelope, _FrozenModel
from thytrader.operator.runtime_models import ReconciliationReport, RiskReport, StrategiesReport


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
