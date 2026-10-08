"""SQLAlchemy Core metadata for append-only operational records.

Tables are defined per domain under :mod:`thytrader.persistence.tables` on the single
shared :data:`~thytrader.persistence.schema_metadata.metadata`. Importing this module
registers every table and re-exports every table name, so
``from thytrader.persistence.schema import <table>`` remains the stable import path
(Alembic ``env.py`` uses ``metadata`` from here).
"""

from __future__ import annotations

# Registration order is part of the schema contract: ``metadata.tables`` iterates in
# insertion order, so these imports follow the original table definition order and
# must not be re-sorted.
# isort: off
from thytrader.persistence.schema_metadata import metadata
from thytrader.persistence.tables.campaigns import research_campaigns
from thytrader.persistence.tables.portfolio_history import portfolio_snapshots
from thytrader.persistence.tables.market_data import (
    market_data_watchlist,
    market_data_worker_state,
)
from thytrader.persistence.tables.worker_heartbeats import worker_heartbeats
from thytrader.persistence.tables.strategies import (
    strategies,
    strategy_dataset_bindings,
    strategy_snapshots,
)
from thytrader.persistence.tables.research import (
    published_backtest_results,
    published_research_run_specs,
    published_research_studies,
    research_jobs,
    research_study_strategies,
    research_workers,
)
from thytrader.persistence.tables.audit import audit_events
from thytrader.persistence.tables.execution import (
    deployment_twin_links,
    deployments,
    execution_fills,
    execution_instrument_state,
    execution_orders,
    execution_positions,
    market_feed_state,
    order_intents,
    user_order_feed_state,
)
from thytrader.persistence.tables.risk import (
    active_risk_policy,
    published_risk_policies,
)
from thytrader.persistence.tables.memory import (
    experiential_journal_entries,
    experiential_models,
    experiential_notifications,
    experiential_pattern_observations,
    experiential_sentiment_snapshots,
)
from thytrader.persistence.tables.decisions import (
    bar_decisions,
    trade_reason_records,
)
from thytrader.persistence.tables.alerts import (
    operator_alert_checks,
    operator_alerts,
)
from thytrader.persistence.tables.portfolios import (
    portfolio_backtest_jobs,
    portfolio_journal_entries,
    portfolio_proposals,
    portfolio_runtime,
    portfolio_sleeves,
    portfolios,
    published_portfolio_backtests,
)
from thytrader.persistence.tables.fleet import (
    fleet_control_operations,
    fleet_entry_inhibition,
)
# isort: on

__all__ = [
    "active_risk_policy",
    "audit_events",
    "bar_decisions",
    "deployment_twin_links",
    "deployments",
    "execution_fills",
    "execution_instrument_state",
    "execution_orders",
    "execution_positions",
    "experiential_journal_entries",
    "experiential_models",
    "experiential_notifications",
    "experiential_pattern_observations",
    "experiential_sentiment_snapshots",
    "fleet_control_operations",
    "fleet_entry_inhibition",
    "market_data_watchlist",
    "market_data_worker_state",
    "market_feed_state",
    "metadata",
    "operator_alert_checks",
    "operator_alerts",
    "order_intents",
    "portfolio_backtest_jobs",
    "portfolio_journal_entries",
    "portfolio_proposals",
    "portfolio_runtime",
    "portfolio_sleeves",
    "portfolio_snapshots",
    "portfolios",
    "published_backtest_results",
    "published_portfolio_backtests",
    "published_research_run_specs",
    "published_research_studies",
    "published_risk_policies",
    "research_campaigns",
    "research_jobs",
    "research_study_strategies",
    "research_workers",
    "strategies",
    "strategy_dataset_bindings",
    "strategy_snapshots",
    "trade_reason_records",
    "user_order_feed_state",
    "worker_heartbeats",
]
