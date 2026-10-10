"""Aggregate component outcomes into report status and process exit codes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.exit_codes import EXIT_DEGRADED, EXIT_FAILED, EXIT_HEALTHY, EXIT_USAGE
from thytrader.operator.models import ComponentReport, ReportStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "EXIT_DEGRADED",
    "EXIT_FAILED",
    "EXIT_HEALTHY",
    "EXIT_USAGE",
    "aggregate_status",
    "exit_code_for",
    "recommend_next_action",
]

_RECOMMENDATIONS: dict[str, str] = {
    "DATABASE_UNCONFIGURED": "Set THYTRADER_DATABASE_URL and apply migrations, then re-run health.",
    "DATABASE_ENGINE_MISSING": (
        "Rebuild and restart with `make run` so the API can ping PostgreSQL."
    ),
    "DATABASE_UNREACHABLE": "Verify PostgreSQL is running and THYTRADER_DATABASE_URL is valid.",
    "API_NOT_READY": "Wait for thytrader-api startup, then GET /health/ready.",
    "API_UNREACHABLE": "Start thytrader-api on the configured loopback port and retry.",
    "NOT_READY": "Start the named worker process and confirm it is heartbeating.",
    "HEARTBEAT_UNAVAILABLE": (
        "Set THYTRADER_DATABASE_URL and apply migrations (0016) so operator health "
        "can see worker heartbeats."
    ),
    "HEARTBEAT_MISSING": (
        "Start the named worker process. Docker /tmp readiness files are not health."
    ),
    "HEARTBEAT_STALE": "Restart the named worker; its last heartbeat is older than two loops.",
    "CYCLE_SLOW": (
        "The execution worker cycle overran its interval, delaying entries, exits and "
        "protection checks. Read payload.execution_cycle of `thytrader-operator runtime` "
        "(slowest_phase, slowest_books, venue endpoints); do not restart a progressing worker."
    ),
    "CYCLE_TIMING_MISSING": (
        "Wait for the execution worker to finish its first cycle, or rebuild with `make run` "
        "if its image predates cycle timing."
    ),
    "CYCLE_TIMING_UNAVAILABLE": (
        "Verify PostgreSQL is reachable and migrated (0075) so health can read cycle timing."
    ),
    "RESEARCH_WORKER_MISSING": (
        "Start the research-worker service (`make run`); queued backtests, studies, and "
        "portfolio backtests wait until a research worker claims them."
    ),
    "RESEARCH_WORKER_STALE": (
        "Restart the research-worker service; no research worker heartbeated recently, so "
        "queued research is not running."
    ),
    "RESEARCH_WORKER_PARTIAL": (
        "Some research worker slots are not heartbeating (crash loop or OOM kill); inspect "
        "the research-worker logs. Live slots keep running queued research."
    ),
    "RESEARCH_QUEUE_UNAVAILABLE": (
        "Verify PostgreSQL is reachable and migrated (0057) so health can read research queues."
    ),
    "EXCHANGE_UNAVAILABLE": "Check Coinbase connectivity without printing credentials.",
    "MARKET_DATA_STATE_UNAVAILABLE": "Confirm the market-data worker can write PostgreSQL state.",
    "MARKET_DATA_NEVER_RUN": "Start thytrader-market-data-worker and wait for verified coverage.",
    "GAPS_PRESENT": "Inspect market-data ingestion diagnostics for the product.",
    "STALE": "Wait for the next verified 1h publication or inspect ingestion failures.",
    "RESULT_NOT_FOUND": "Pass a published result fingerprint from thytrader-operator performance.",
    "FINDINGS_PRESENT": "Inspect paused or mismatched deployments before starting new risk.",
    "DEPLOYMENT_NOT_FOUND": "Pass a deployment id from thytrader-operator strategies or runtime.",
    "MEMORY_STORAGE_UNAVAILABLE": (
        "Set THYTRADER_DATABASE_URL and apply migration 0023, then re-run monitor."
    ),
    "EXECUTION_UNAVAILABLE": "Set THYTRADER_DATABASE_URL so monitor can list deployments.",
    "NOTIFICATION_FAILED": (
        "Inspect recent notification delivery_status without printing webhook URLs."
    ),
    "DEPLOYMENT_PAUSED": "Inspect thytrader-operator runtime before new risk-increasing orders.",
    "DEPLOYMENT_MISMATCH": (
        "Inspect thytrader-operator reconciliation before new risk-increasing orders."
    ),
    "DAILY_LOSS_LIMIT": (
        "Daily-loss breaker paused risk-increasing orders. Exits continue. Resume after the "
        "UTC day recovers or publish a tighter/looser policy with --confirm."
    ),
    "FLEET_ENTRIES_BLOCKED": (
        "New entries are blocked fleet-wide. Run `thytrader-operator fleet-health` and repair "
        "or reset what payload.entries names (blocking_deployment_ids and each check's "
        "deployments); exits continue."
    ),
    "FLEET_ENTRIES_UNKNOWN": (
        "Fleet entry readiness could not be evaluated; run `thytrader-operator fleet-health` "
        "and inspect the unknown checks before trusting that entries can be admitted."
    ),
    "FLEET_ENTRY_CAPACITY_FULL": (
        "The fleet is fully invested (open-position slots, account or BTC-beta exposure cap) "
        "or the entry clustering window is full; an exit or the window frees room. No action "
        "unless that is unexpected; read the blocking checks of `thytrader-operator fleet-health`."
    ),
    "FLEET_ENTRY_ALERTS_UNAVAILABLE": (
        "Set THYTRADER_DATABASE_URL and run the execution worker so fleet entry block "
        "alerts are recorded; until then run `thytrader-operator fleet-health`."
    ),
    "SYSTEMIC_ENTRY_BLOCKERS": (
        "Recent decisions show a systemic entry blocker; read payload.decisions.systemic of "
        "`thytrader-operator fleet-health`."
    ),
    "STRATEGY_DRAWDOWN_LIMIT": (
        "Drawdown breaker paused this strategy's risk-increasing orders. Exits continue. "
        "Resume after equity recovers or publish a new risk policy with --confirm."
    ),
}


def aggregate_status(components: Sequence[ComponentReport]) -> ReportStatus:
    """Fail closed when telemetry is missing; otherwise take the worst component."""
    if not components:
        return ReportStatus.FAILED
    if any(component.status is ReportStatus.FAILED for component in components):
        return ReportStatus.FAILED
    if any(component.status is ReportStatus.DEGRADED for component in components):
        return ReportStatus.DEGRADED
    return ReportStatus.HEALTHY


def recommend_next_action(components: Sequence[ComponentReport]) -> str:
    """Suggest the first failed, then first degraded, diagnostic follow-up."""
    for desired in (ReportStatus.FAILED, ReportStatus.DEGRADED):
        match = next(
            (component for component in components if component.status is desired),
            None,
        )
        if match is not None:
            known = _RECOMMENDATIONS.get(match.reason_code)
            if known is not None:
                return known
            return f"Investigate {match.name} ({match.reason_code})."
    return "No action required."


def exit_code_for(status: ReportStatus) -> int:
    """Map overall status to a stable CLI exit code."""
    if status is ReportStatus.HEALTHY:
        return EXIT_HEALTHY
    if status is ReportStatus.DEGRADED:
        return EXIT_DEGRADED
    return EXIT_FAILED
