"""Fleet entry readiness supervision of the execution worker cycle (ADR 0130).

Once per cycle, after the per-book safety supervision, the worker evaluates whether the
entry gate would admit any new entry in each mode and quote scope, and raises, refreshes or
resolves the ``FLEET_ENTRIES_BLOCKED`` alert of each scope in the durable alert feed (ADR
0115). Delivery goes through the configured notify provider like every other alert. This
never changes a book: it only makes a fleet-wide block visible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.alerts.supervision_fleet import fleet_entry_evidence
from thytrader.execution.fleet_entry_evidence import load_fleet_entry_health
from thytrader.execution_worker.ports import _logger
from thytrader.risk.futures_collateral import bound_futures_account_store

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.alerts.service import AlertService
    from thytrader.market_data.service import MarketDataService
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.trading.store import ExecutionStore


async def _supervise_fleet_entries(
    *,
    alert_service: AlertService | None,
    store: ExecutionStore,
    market_data: MarketDataService,
    policy: RiskPolicyDefinition,
    observed_at: datetime,
) -> None:
    """Raise or resolve the fleet entry block alerts; never fail the worker cycle."""
    if alert_service is None:
        return
    try:
        health = await load_fleet_entry_health(
            store,
            policy,
            market_data=market_data,
            futures_account=bound_futures_account_store(),
        )
        previous = await alert_service.open_alerts()
        evidence = fleet_entry_evidence(health, prior_alerts=previous)
        application = await alert_service.apply(
            evidence.findings, now=observed_at, evaluated=evidence.evaluated, dispatch=False
        )
    except Exception as error:  # noqa: BLE001 - supervision must never fail the cycle.
        _logger.warning("fleet_entry_supervision_unavailable type=%s", type(error).__name__)
        return
    for change in application.changes:
        if change.created:
            _logger.warning(
                "fleet_entries_blocked subject=%s severity=%s",
                change.alert.subject,
                change.alert.severity.value,
            )
